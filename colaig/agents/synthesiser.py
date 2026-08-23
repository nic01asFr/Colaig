"""
Colaig — Agent Synthétiseur

Implémente SynthesiserProtocol.
Dernier maillon du pipeline Phase 2. Formule la réponse finale.

Reçoit :
- Le plan d'exécution complet (résultats RAG + tools)
- Les directives du Synthétiseur (format, ton, focus) produites par l'Analyseur
- Le contexte agent construit depuis le workspace

Produit une GeneratedResponse sourcée et formatée, avec ContextCard enrichie.
1 appel Albert.

Phase 6 :
- assess() : évalue si les infos collectées sont suffisantes (CompletionSignal)
  → Appelé par l'Orchestrateur dans sa boucle agentique
- synthesise_stream() : version streaming (AsyncIterator[str])
- channel_format + pre_exec optionnels dans synthesise()
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

try:
    from zoneinfo import ZoneInfo
    _PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:
    _PARIS_TZ = None  # fallback UTC

from colaig.agents.context_builder import build_agent_context
from colaig.exceptions import SynthesisError
from colaig.models import (
    AgentContext,
    ChannelFormat,
    CompletionSignal,
    ContextCard,
    ExecutionPlan,
    GeneratedResponse,
    IncomingMessage,
    PreExecutionCard,
    WorkspaceContext,
)
from colaig.security.wrap import CONSIGNE, baliser, formater_skills

logger = logging.getLogger(__name__)


class Synthesiser:
    """Agent Synthétiseur — formule la réponse finale.

    Args:
        albert: Client Albert API (LLMClientProtocol).
        storage: Backend de stockage (StorageProtocol).
        model: Modèle Albert à utiliser. Si None, utilise le défaut.
        temperature: Température de génération.
        max_tokens: Nombre max de tokens en sortie.
    """

    def __init__(
        self,
        albert,
        storage,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> None:
        self._albert = albert
        self._storage = storage
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def synthesise(
        self,
        plan: ExecutionPlan,
        context: WorkspaceContext,
        conversation_history: list[dict] | None = None,
        channel_format: ChannelFormat | None = None,
        pre_exec: PreExecutionCard | None = None,
        message: IncomingMessage | None = None,
    ) -> GeneratedResponse:
        """Synthétise les résultats en réponse finale.

        Args:
            plan: Plan d'exécution avec résultats (Orchestrateur).
            context: Contexte résolu (workspace, mode, etc.).
            conversation_history: Historique de conversation.
            channel_format: Format du canal de sortie (Phase 6) — adapte la mise en forme.
            pre_exec: PreExecutionCard (Phase 6) — skills déjà chargés, overrides agents.

        Returns:
            GeneratedResponse avec texte, sources, et ContextCard enrichie.
        """
        start = time.monotonic()

        # Construire le contexte agent
        # selected_skills depuis pre_exec si disponible (top-k sémantique, Phase 6)
        agent_ctx = await build_agent_context(
            self._storage,
            context.workspace,
            "synthesiser",
            directives=plan.intent.synthesiser_directives,
            selected_skills=pre_exec.selected_skills if pre_exec else None,
        )

        # Construire les messages pour Albert
        messages = self._build_messages(
            plan, context, agent_ctx, conversation_history, channel_format, message=message,
        )

        temperature = self._temperature
        max_tokens = self._max_tokens
        if pre_exec and pre_exec.agent_overrides.get("synthesiser"):
            ov = pre_exec.agent_overrides["synthesiser"]
            temperature = ov.get("temperature", temperature)
            max_tokens = ov.get("max_tokens", max_tokens)

        try:
            text = await self._albert.chat(
                messages=messages,
                model=self._model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            raise SynthesisError(f"erreur appel Albert pour synthèse: {e}") from e

        # Sécurité : masquer d'éventuels secrets avant envoi à l'utilisateur.
        from colaig.security.secrets_filter import mask_secrets
        text = mask_secrets(text)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Extraire les sources — cherche d'abord dans search_results (Phase 1),
        # puis dans tool_results["search_documents"] (mode agentique Phase 2+)
        sources = _extract_sources(plan.search_results) or _extract_sources_from_tool_results(plan.tool_results)

        # Confiance — indicateur qualitatif normalisé
        # Les scores FAISS (cosine) sont dans [0,1] → moyenne directe utilisable.
        # Les scores du reranker bge-reranker-v2-m3 sont des sigmoid ~0.001-0.005 pour
        # du contenu pertinent → on détecte et normalise pour éviter "2% de confiance".
        if plan.search_results:
            scores = [r.score for r in plan.search_results]
            raw_confidence = sum(scores) / len(scores)
            # Si tous les scores sont < 0.01 → reranker utilisé → normaliser sur 0-0.01
            if max(scores) < 0.01:
                confidence = min(raw_confidence / 0.01, 1.0)
            else:
                confidence = raw_confidence
        else:
            confidence = _confidence_from_tool_results(plan.tool_results)

        # Audit anti-hallucination : citations sans source → log + confiance pénalisée.
        from colaig.security.citation_checker import audit_and_adjust
        confidence = audit_and_adjust(text, sources, confidence)

        # Enrichir la ContextCard
        context_card = self._enrich_context_card(plan, sources, confidence)

        return GeneratedResponse(
            text=text,
            sources=sources,
            confidence=confidence,
            model_used=self._model or "",
            generation_time_ms=elapsed_ms,
            context_card=context_card,
        )

    def _build_messages(
        self,
        plan: ExecutionPlan,
        context: WorkspaceContext,
        agent_ctx: AgentContext,
        conversation_history: list[dict] | None,
        channel_format: ChannelFormat | None = None,
        message: IncomingMessage | None = None,
    ) -> list[dict]:
        """Construit les messages pour l'appel Albert.

        Structure :
        1. System prompt (agent + directives + skills + documents RAG)
        2. Historique de conversation
        3. Message synthèse (intent + résultats)
        """
        messages: list[dict] = []

        # 1. System prompt enrichi
        system_prompt = agent_ctx.system_prompt

        # Ajouter les directives de format/ton
        directives = plan.intent.synthesiser_directives
        if directives:
            parts = []
            if directives.response_format:
                parts.append(f"Format de réponse : {directives.response_format}")
            if directives.response_tone:
                parts.append(f"Ton : {directives.response_tone}")
            if directives.focus_points:
                parts.append(f"Points de focus : {', '.join(directives.focus_points)}")
            if directives.instructions:
                parts.append(f"Instructions : {directives.instructions}")
            if parts:
                system_prompt = f"{system_prompt}\n\n## Directives\n" + "\n".join(f"- {p}" for p in parts)

        # Ajouter les skills
        if agent_ctx.skills:
            # Un skill est un `.md` déposé dans `.colaig/skills/` : pour qui a accès en
            # écriture à l'espace, c'est un fichier comme un autre. Il entrait ici
            # INTÉGRALEMENT dans le message system, sous un titre le présentant comme une
            # connaissance métier de l'instance — nul besoin de forger une clôture, il
            # suffisait d'écrire l'instruction (L2.1).
            skills_text = formater_skills(agent_ctx.skills)
            system_prompt = (
                f"{system_prompt}\n\n## Connaissances métier de l'espace\n\n"
                f"{CONSIGNE}\n\n{skills_text}"
            )

        # Ajouter les documents de référence
        # Phase 1 : search_results remplis directement par le retriever
        # Phase 2+ (mode agentique) : résultats dans tool_results["search_documents"]
        _agentic_docs = _search_docs_from_tool_results(plan.tool_results)
        if plan.search_results:
            docs_text = _format_documents(plan.search_results)
            system_prompt = (
                f"{system_prompt}\n\n"
                f"## Documents de référence\n\n"
                f"Utilise les documents suivants pour répondre. "
                f"Cite tes sources entre crochets [nom_du_fichier].\n"
                f"IMPORTANT : {CONSIGNE}\n\n"
                f"{docs_text}"
            )
        elif _agentic_docs:
            # Mode agentique : les documents proviennent des tool_results
            docs_text = _format_agentic_docs(_agentic_docs)
            system_prompt = (
                f"{system_prompt}\n\n"
                f"## Documents de référence\n\n"
                f"Utilise les documents suivants pour répondre. "
                f"Cite tes sources entre crochets [nom_du_fichier].\n"
                f"IMPORTANT : {CONSIGNE}\n\n"
                f"{docs_text}"
            )
        else:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"Aucun document de référence n'est disponible. "
                f"Réponds avec tes connaissances générales et indique clairement "
                f"que tu ne t'appuies pas sur des documents spécifiques."
            )

        # Ajouter les résultats des autres tools (mode agentique — Phase 5)
        # (search_documents déjà affiché comme documents de référence ci-dessus)
        other_tool_results = [r for r in plan.tool_results if r.get("tool") != "search_documents"]
        if other_tool_results:
            tools_text = _format_tool_results(other_tool_results)
            system_prompt = f"{system_prompt}\n\n## Résultats d'outils\n\n{tools_text}"

        # Ajouter le raisonnement de l'orchestrateur (mode agentique — Phase 5)
        if plan.orchestrator_reasoning:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"## Synthèse de l'Orchestrateur\n\n"
                f"{plan.orchestrator_reasoning}"
            )

        # Contexte utilisateur — personnalisation adressage et ton
        user_ctx_parts = []
        if context.user_display_name:
            user_ctx_parts.append(f"Prénom/nom affiché : {context.user_display_name}")
        if context.user_domain:
            user_ctx_parts.append(f"Organisation : {context.user_domain}")
        if context.mode:
            _mode_labels = {
                "assistant": "espace de travail collaboratif",
                "personal": "conversation directe (DM)",
                "chatbot": "salon public non configuré",
            }
            mode_label = _mode_labels.get(context.mode.value, context.mode.value)
            user_ctx_parts.append(f"Mode d'interaction : {mode_label}")
        if user_ctx_parts:
            system_prompt = (
                f"{system_prompt}\n\n## Contexte utilisateur\n"
                + "\n".join(f"- {p}" for p in user_ctx_parts)
            )

        # Contraintes de format canal — guide le LLM sur la structure de réponse optimale
        if channel_format:
            hints: list[str] = []
            if not channel_format.supports_tables:
                hints.append("Évite les tableaux — utilise des listes à puces à la place.")
            if channel_format.max_length > 0:
                hints.append(f"Limite ta réponse à {channel_format.max_length} caractères maximum.")
            if channel_format.reply_style == "json":
                hints.append("Réponds en JSON structuré uniquement.")
            if hints:
                system_prompt = (
                    f"{system_prompt}\n\n## Contraintes de format\n"
                    + "\n".join(f"- {h}" for h in hints)
                )

        # Contexte temporel — calibre ton et rythme de réponse
        history = conversation_history or context.conversation_history
        temporal_hint = _temporal_context_hint(
            message_ts=message.timestamp if message else None,
            history=history,
            conversation_phase=context.conversation_phase,
        )
        if temporal_hint:
            system_prompt = f"{system_prompt}\n\n## Contexte de la conversation\n{temporal_hint}"

        # Fil chronologique des anchors — éléments déjà établis dans la conversation
        if context.context_anchors:
            ref_ts_for_anchors = (
                message.timestamp if message else datetime.utcnow()
            )
            anchor_lines: list[str] = []
            for anchor in context.context_anchors:
                label_type = {
                    "document": "doc", "decision": "décision",
                    "entity": "concept", "constraint": "contrainte",
                }.get(anchor.anchor_type, anchor.anchor_type)
                line = f"- [{label_type}] {anchor.description or anchor.ref}"
                if anchor.established_at:
                    time_label = _relative_time_label(anchor.established_at, ref_ts_for_anchors)
                    if time_label:
                        line += f" — {time_label}"
                anchor_lines.append(line)
            if anchor_lines:
                system_prompt = (
                    f"{system_prompt}\n\n## Éléments établis dans la conversation\n"
                    + "\n".join(anchor_lines)
                )

        messages.append({"role": "system", "content": system_prompt})

        # 2. Historique avec étiquettes temporelles contextuelles
        ref_ts_for_history = message.timestamp if message else datetime.utcnow()
        for msg in history:
            if "role" in msg and "content" in msg and msg["content"]:
                content = msg["content"]
                ts_str = msg.get("ts")
                if ts_str:
                    try:
                        msg_ts = datetime.fromisoformat(ts_str)
                        label = _relative_time_label(msg_ts, ref_ts_for_history)
                        if label:
                            content = f"[{label}] {content}"
                    except (ValueError, TypeError):
                        pass
                messages.append({"role": msg["role"], "content": content})

        # 3. La question reformulée — toujours ajouter un message user final
        # (Albert retourne vide si le dernier message est assistant ou absent)
        query = plan.intent.query_reformulated or ""
        if not query:
            query = "Réponds à la question de l'utilisateur."
        messages.append({"role": "user", "content": query})

        return messages

    async def assess(
        self,
        query: str,
        collected_info: str,
        criteria: str = "",
        context: WorkspaceContext | None = None,
    ) -> CompletionSignal:
        """Évalue si les informations collectées sont suffisantes pour répondre (Phase 6).

        Appelé par l'Orchestrateur dans sa boucle agentique pour décider de continuer.
        Appel Albert court (température 0.1, max 256 tokens).

        Args:
            query: Question reformulée de l'utilisateur.
            collected_info: Résumé des informations collectées jusqu'ici.
            criteria: Critères de complétude (depuis SearchDirectives.completeness_criteria).
            context: Contexte workspace (optionnel).

        Returns:
            CompletionSignal avec verdict ("complete" | "incomplete" | "partial").
        """
        criteria_section = f"\nCritères de complétude : {criteria}" if criteria else ""
        prompt = (
            f"Évalue si les informations collectées sont suffisantes pour répondre.\n\n"
            f"Question : {query}\n\n"
            f"Informations collectées :\n{collected_info or '(aucune)'}"
            f"{criteria_section}\n\n"
            f"Réponds UNIQUEMENT en JSON :\n"
            f'{{"sufficient": true, '
            f'"confidence": 0.8, '
            f'"missing_elements": ["élément manquant si non sufficient"], '
            f'"suggested_directions": ["piste supplémentaire"], '
            f'"reason": "courte explication"}}'
        )

        messages = [
            {"role": "system", "content": "Tu évalues la complétude des informations pour répondre à une question."},
            {"role": "user", "content": prompt},
        ]

        kwargs: dict = {"messages": messages, "temperature": 0.1, "max_tokens": 256}
        if self._model:
            kwargs["model"] = self._model

        try:
            raw = await self._albert.chat(**kwargs)
            return self._parse_completion_signal(raw)
        except Exception as e:
            logger.warning("assess() erreur Albert: %s", e)
            return CompletionSignal(sufficient=True, reason="erreur assess, on continue", confidence=0.0)

    async def synthesise_stream(
        self,
        plan: ExecutionPlan,
        context: WorkspaceContext,
        conversation_history: list[dict] | None = None,
        channel_format: ChannelFormat | None = None,
    ) -> AsyncIterator[str]:
        """Version streaming de synthesise() — yield des chunks de texte (Phase 6).

        Si albert.chat_stream() est disponible : streaming réel.
        Sinon : fallback sur synthesise() (yield unique de la réponse complète).

        Args:
            plan: Plan d'exécution avec résultats.
            context: Contexte workspace.
            conversation_history: Historique de conversation.
            channel_format: Format du canal (pour adapater la mise en forme).

        Yields:
            Chunks de texte au fur et à mesure de la génération.
        """
        if hasattr(self._albert, "chat_stream"):
            agent_ctx = await build_agent_context(
                self._storage,
                context.workspace,
                "synthesiser",
                directives=plan.intent.synthesiser_directives,
            )
            messages = self._build_messages(plan, context, agent_ctx, conversation_history)
            kwargs: dict = {
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }
            if self._model:
                kwargs["model"] = self._model
            async for chunk in self._albert.chat_stream(**kwargs):
                yield chunk
        else:
            # Fallback : synthèse complète, yield unique
            response = await self.synthesise(plan, context, conversation_history, channel_format)
            yield response.text

    def _parse_completion_signal(self, raw: str) -> CompletionSignal:
        """Parse la réponse JSON d'assess() en CompletionSignal."""
        try:
            json_str = _extract_json_object(raw)
            if not json_str:
                raise ValueError("pas de JSON trouvé")
            data = json.loads(json_str)
        except Exception:
            logger.debug("assess() réponse non-JSON: %s", raw[:100])
            return CompletionSignal(sufficient=True, reason="parse error", confidence=0.0)

        return CompletionSignal(
            sufficient=bool(data.get("sufficient", True)),
            confidence=float(data.get("confidence", 0.5)),
            missing_elements=data.get("missing_elements", []),
            suggested_directions=data.get("suggested_directions", []),
            reason=data.get("reason", ""),
        )

    def _enrich_context_card(
        self,
        plan: ExecutionPlan,
        sources: list[str],
        confidence: float,
    ) -> ContextCard:
        """Enrichit la ContextCard avec les infos de synthèse."""
        base_card = plan.context_card or ContextCard()

        # Ajouter la phase synthesizing
        phases = list(base_card.pipeline_phases)
        if "synthesizing" not in phases:
            phases.append("synthesizing")
        phases.append("complete")

        return ContextCard(
            mode=base_card.mode,
            workspace_id=base_card.workspace_id,
            workspace_name=base_card.workspace_name,
            available_tools=base_card.available_tools,
            suggested_resources=base_card.suggested_resources,
            workflow_hint=base_card.workflow_hint,
            sources_used=sources,
            pipeline_phases=phases,
            confidence=confidence,
            metadata=base_card.metadata,
        )


# =============================================================================
# Contexte temporel — calibrage ton et format
# =============================================================================

_MONTH_FR: dict[int, str] = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril",
    5: "mai", 6: "juin", 7: "juillet", 8: "août",
    9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}


def _relative_time_label(msg_ts: datetime, ref_ts: datetime) -> str:
    """Calcule une étiquette temporelle relative lisible (français).

    Exemples : "il y a 5 min", "hier", "il y a 3 jours", "le 3 mars".
    Retourne "" si le delta est négatif ou inférieur à 90s (quasi-immédiat).
    """
    # Normaliser en UTC pour la comparaison
    if msg_ts.tzinfo is None:
        msg_ts = msg_ts.replace(tzinfo=UTC)
    if ref_ts.tzinfo is None:
        ref_ts = ref_ts.replace(tzinfo=UTC)

    delta_s = (ref_ts - msg_ts).total_seconds()
    if delta_s < 90:
        return ""  # trop récent → pas de label (contexte immédiat)

    if delta_s < 3600:
        minutes = int(delta_s / 60)
        return f"il y a {minutes} min"
    if delta_s < 86400:
        hours = int(delta_s / 3600)
        return f"il y a {hours}h"
    if delta_s < 172800:
        return "hier"
    if delta_s < 604800:
        days = int(delta_s / 86400)
        return f"il y a {days} jours"
    if delta_s < 2592000:
        weeks = int(delta_s / 604800)
        return f"il y a {weeks} semaine{'s' if weeks > 1 else ''}"

    # Plus d'un mois → date absolue en français
    local_ts = msg_ts.astimezone(_PARIS_TZ) if _PARIS_TZ else msg_ts
    ref_local = ref_ts.astimezone(_PARIS_TZ) if _PARIS_TZ else ref_ts
    month_name = _MONTH_FR[local_ts.month]
    if local_ts.year == ref_local.year:
        return f"le {local_ts.day} {month_name}"
    return f"le {local_ts.day} {month_name} {local_ts.year}"


def _temporal_context_hint(
    message_ts: datetime | None,
    history: list[dict],
    conversation_phase: str | None,
) -> str:
    """Génère des indications contextuelles pour calibrer le ton et le format.

    Trois signaux complémentaires :
    1. Période de la journée (heure locale Paris) → niveau de concision attendu.
    2. Rythme de l'échange (délais entre messages via champ "ts") → urgence perçue.
    3. Phase macro de la conversation (trame vivante) → posture pédagogique/opérationnelle.

    Retourne une chaîne de hints à injecter dans le system prompt,
    ou "" si aucun signal significatif.
    """
    hints: list[str] = []

    # ── 1. Période de la journée ─────────────────────────────────────────────
    if message_ts is not None:
        try:
            # Convertir en heure locale Paris (UTC+1/UTC+2 selon DST)
            if _PARIS_TZ is not None:
                if message_ts.tzinfo is None:
                    # Timestamp naïf → supposer UTC
                    ts_utc = message_ts.replace(tzinfo=UTC)
                else:
                    ts_utc = message_ts
                local_ts = ts_utc.astimezone(_PARIS_TZ)
            else:
                local_ts = message_ts  # fallback sans conversion TZ

            hour = local_ts.hour
            weekday = local_ts.weekday()  # 0=lundi … 6=dimanche

            if 7 <= hour < 9:
                hints.append(
                    "Début de matinée (prise en main de la journée) : "
                    "privilégie les réponses courtes et orientées action."
                )
            elif 9 <= hour < 12:
                hints.append(
                    "Matinée de travail : format standard, complet si la question le justifie."
                )
            elif 12 <= hour < 14:
                hints.append(
                    "Pause méridienne : l'utilisateur dispose de peu de temps, va à l'essentiel."
                )
            elif 14 <= hour < 18:
                hints.append(
                    "Après-midi de travail : format complet et structuré bienvenu."
                )
            elif 18 <= hour < 22:
                hints.append(
                    "Soirée (fin de journée) : réponse concise, l'utilisateur décroche bientôt."
                )
            else:
                hints.append(
                    "Hors horaires habituels (nuit) : message probablement urgent, "
                    "sois bref et direct."
                )

            if weekday >= 5:
                hints.append("Jour de weekend : ton légèrement plus informel si approprié.")
        except Exception:
            pass  # timestamp invalide → pas de hint temporel

    # ── 2. Rythme de l'échange ───────────────────────────────────────────────
    ts_values: list[datetime] = []
    for msg in history[-10:]:
        ts_str = msg.get("ts")
        if ts_str:
            try:
                ts_values.append(datetime.fromisoformat(ts_str))
            except (ValueError, TypeError):
                pass

    if len(ts_values) >= 3:
        deltas = [
            (ts_values[i + 1] - ts_values[i]).total_seconds()
            for i in range(len(ts_values) - 1)
            if ts_values[i + 1] > ts_values[i]
        ]
        if deltas:
            avg_delta = sum(deltas) / len(deltas)
            if avg_delta < 60:
                hints.append(
                    "Échange rapide en cours (< 1 min entre messages) : "
                    "sois direct et bref, évite les longs paragraphes."
                )
            elif avg_delta > 3600:
                hints.append(
                    "Échanges asynchrones (> 1h entre messages) : "
                    "tu peux contextualiser davantage, l'utilisateur reprend le fil."
                )

    # ── 3. Phase macro de la conversation ────────────────────────────────────
    _PHASE_HINTS: dict[str, str] = {
        "discovery": (
            "Phase de découverte : adopte une posture pédagogique, "
            "propose des pistes et des questions ouvertes si pertinent."
        ),
        "analysis": (
            "Phase d'analyse active : structure ta réponse, "
            "sois précis et factuel."
        ),
        "drafting": (
            "Phase de production/rédaction : direct et concis, "
            "l'utilisateur est en train de produire quelque chose."
        ),
        "review": (
            "Phase de relecture/validation : attentif aux détails, "
            "signale proactivement les incohérences ou points à vérifier."
        ),
        "concluded": (
            "Conversation en phase de clôture : synthèse rapide si besoin, "
            "ne relance pas inutilement."
        ),
    }
    if conversation_phase and conversation_phase in _PHASE_HINTS:
        hints.append(_PHASE_HINTS[conversation_phase])

    return "\n".join(f"- {h}" for h in hints)


# =============================================================================
# Fonctions utilitaires (réutilisent le pattern de generator.py)
# =============================================================================

def _format_tool_results(tool_results: list) -> str:
    """Formate les résultats d'outils pour le prompt.

    Gère les deux formats :
    - search_documents : {"tool": "search_documents", "query": ..., "results": [...]}
    - autres tools : {"tool": ..., "arguments": ..., "result": ...}
    - placeholder MCP : {"tool": ..., "status": ..., "message": ...}
    """
    parts: list[str] = []
    for r in tool_results:
        tool = r.get("tool", "?")
        if tool == "search_documents":
            query = r.get("query", "")
            results = r.get("results", [])
            if results:
                sources = ", ".join(f"[{res.get('source', '?')}]" for res in results[:3])
                parts.append(f"- {tool}({query!r}) → {len(results)} résultats : {sources}")
            else:
                parts.append(f"- {tool}({query!r}) → aucun résultat")
        elif "result" in r:
            # Le résultat d'un outil est du contenu distant, pas une observation du
            # système : un serveur MCP écrit ce qu'il veut dans sa réponse (L2.1).
            parts.append(
                f"- {tool} →\n" + baliser(str(r["result"])[:200], source=str(tool),
                                          nature="outil")
            )
        elif "status" in r:
            parts.append(f"- {tool} → statut: {r.get('status', '?')}")
        else:
            parts.append(f"- {tool} → exécuté")
    return "\n".join(parts)


def _format_documents(search_results: list) -> str:
    """Formate les résultats RAG en contexte textuel pour le prompt."""
    parts: list[str] = []
    for i, result in enumerate(search_results, 1):
        chunk = result.chunk
        source_info = chunk.source_name
        if chunk.section:
            source_info = f"{source_info} > {chunk.section}"
        # Balisage par `security/wrap.py` (L2.1). Ce site portait la deuxième des trois
        # copies du motif forgeable : le contenu était inséré tel quel entre deux
        # marqueurs qu'un document pouvait écrire lui-même pour clore sa balise.
        parts.append(
            f"### Document {i} (score: {result.score:.2f})\n"
            + baliser(chunk.text, source=source_info, nature="document")
        )
    return "\n\n".join(parts)


def _extract_sources(search_results: list) -> list[str]:
    """Extrait les noms de fichiers uniques des résultats RAG (Phase 1)."""
    seen: set[str] = set()
    sources: list[str] = []
    for result in search_results:
        name = result.chunk.source_name
        if name and name not in seen:
            seen.add(name)
            sources.append(name)
    return sources


def _search_docs_from_tool_results(tool_results: list) -> list[dict]:
    """Extrait les entrées search_documents des tool_results (mode agentique).

    Returns:
        Liste fusionnée de tous les résultats RAG des différents appels search_documents.
    """
    docs: list[dict] = []
    for r in tool_results:
        if r.get("tool") == "search_documents":
            docs.extend(r.get("results", []))
    return docs


def _extract_sources_from_tool_results(tool_results: list) -> list[str]:
    """Extrait les sources uniques depuis tool_results (mode agentique Phase 2+)."""
    seen: set[str] = set()
    sources: list[str] = []
    for item in _search_docs_from_tool_results(tool_results):
        name = item.get("source", "")
        if name and name not in seen:
            seen.add(name)
            sources.append(name)
    return sources


def _confidence_from_tool_results(tool_results: list) -> float:
    """Calcule la confiance moyenne depuis les scores tool_results (mode agentique)."""
    docs = _search_docs_from_tool_results(tool_results)
    if not docs:
        return 0.0
    scores = [d.get("score", 0.0) for d in docs]
    return sum(scores) / len(scores)


def _extract_json_object(text: str) -> str | None:
    """Extrait le premier objet JSON complet depuis un texte libre.

    Gère les accolades dans les valeurs string (contrairement à une regex simple).
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _format_agentic_docs(docs: list[dict]) -> str:
    """Formate les résultats RAG issus des tool_results pour le prompt du synthétiseur."""
    parts: list[str] = []
    for i, doc in enumerate(docs, 1):
        source = doc.get("source", "?")
        score = doc.get("score", 0.0)
        text = doc.get("text", "")
        section = doc.get("section", "")
        source_info = f"{source} > {section}" if section else source
        # Troisième copie du motif forgeable. Celle-ci recevait son texte du JSON d'un
        # tool result, donc d'un chemin encore moins contrôlé que le précédent.
        parts.append(
            f"### Document {i} (score: {score:.2f})\n"
            + baliser(text, source=source_info, nature="document")
        )
    return "\n\n".join(parts)
