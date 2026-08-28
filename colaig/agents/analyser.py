"""
Colaig — Agent Analyseur

Implémente AnalyserProtocol.
Premier maillon du pipeline Phase 2. Analyse le message utilisateur et produit :
- Une Intent structurée (type, reformulation, entités)
- Des AgentDirectives ciblées pour l'Orchestrateur et le Synthétiseur
- (Phase 6) Des SearchDirectives multi-sources pour le PreExecutionBuilder

Utilise 1 appel Albert (température basse) pour l'analyse JSON.
Shortcut regex pour les salutations (pas d'appel LLM).

Mode use_tool_calling=True : utilise chat_with_tools avec l'outil analyse_intent
pour obtenir un JSON structuré garanti (pas de parsing de texte libre).

Phase 6 :
- Modèle light (Ministral 3B) pour l'analyse (1 appel rapide)
- SearchDirectives dans l'Intent (plan de retrieval multi-source)
- is_direct + direct_response pour les réponses sans RAG
- suggested_next_phase + new_anchors pour la trame vivante
- pre_exec optionnel dans analyse() pour enrichir le prompt avec le contexte fixe
"""

from __future__ import annotations

import json
import logging
import re
import time

from colaig.agents.context_builder import build_agent_context
from colaig.exceptions import AnalysisError
from colaig.models import (
    AgentDirectives,
    ContextAnchor,
    IncomingMessage,
    Intent,
    IntentType,
    PreExecutionCard,
    SearchDirectives,
    WorkspaceContext,
)
from colaig.security.prompt_sanitizer import sanitize_description
from colaig.security.wrap import CONSIGNE, baliser, contient_balise

logger = logging.getLogger(__name__)

# Regex pour détecter les salutations simples (pas d'appel LLM nécessaire)
GREETING_PATTERNS = re.compile(
    r"^(bonjour|bonsoir|salut|hello|hi|hey|coucou|yo)\s*[!.\s]*$",
    re.IGNORECASE,
)

# Prompt JSON structuré demandé à Albert
ANALYSIS_PROMPT_TEMPLATE = """\
Analyse le message utilisateur suivant et retourne un JSON structuré.

Message : "{message}"

{workspace_context}

Retourne UNIQUEMENT un JSON valide avec cette structure exacte :
{{
  "intent_type": "question|action|search|summary|configuration|greeting|clarification|unknown",
  "query_reformulated": "question reformulée pour la recherche documentaire",
  "entities": {{"key": "value"}},
  "needs_rag": true,
  "needs_tools": false,
  "confidence": 0.8,
  "is_direct": false,
  "direct_response": "",
  "suggested_next_phase": null,
  "new_anchors": [],
  "search_directives": {{
    "chunk_queries": ["reformulation 1", "reformulation 2"],
    "document_queries": [],
    "skill_queries": [],
    "history_queries": [],
    "context_filters": {{}},
    "objective": "objectif principal de la recherche",
    "completeness_criteria": "critères pour valider que la réponse est complète"
  }},
  "orchestrator_directives": {{
    "instructions": "consignes pour l'orchestrateur",
    "resources_to_target": [],
    "tools_to_use": [],
    "search_strategy": "broad|precise|multi-step"
  }},
  "synthesiser_directives": {{
    "instructions": "consignes pour le synthétiseur",
    "response_format": "paragraph|list|table|step-by-step",
    "response_tone": "",
    "focus_points": []
  }}
}}

Règles :
- is_direct=true uniquement si la réponse ne nécessite PAS de recherche documentaire (salutation, question générale simple).
- direct_response : texte de réponse directe si is_direct=true, sinon vide.
- suggested_next_phase : "discovery"|"active"|"concluding"|"concluded" ou null si inchangé.
- new_anchors : liste d'objets {{"anchor_type": "...", "ref": "...", "description": "..."}} si des éléments clés sont identifiés.
- search_directives.chunk_queries : 2-3 reformulations variées pour la recherche sémantique.
- search_directives.skill_queries : requêtes pour trouver des procédures/compétences pertinentes.\
"""

# Schéma OpenAI pour le mode use_tool_calling=True
ANALYSE_INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "analyse_intent",
        "description": "Analyse l'intention de l'utilisateur et retourne une structure JSON complète.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent_type": {
                    "type": "string",
                    "enum": ["question", "action", "search", "summary", "configuration", "greeting", "clarification", "unknown"],
                    "description": "Type d'intention détecté.",
                },
                "query_reformulated": {
                    "type": "string",
                    "description": "Question reformulée pour la recherche documentaire.",
                },
                "entities": {
                    "type": "object",
                    "description": "Entités clés extraites (dates, noms, références).",
                },
                "needs_rag": {
                    "type": "boolean",
                    "description": "True si une recherche documentaire est nécessaire.",
                },
                "needs_tools": {
                    "type": "boolean",
                    "description": "True si des outils externes sont nécessaires.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Niveau de confiance de l'analyse (0→1).",
                },
                "orchestrator_directives": {
                    "type": "object",
                    "description": "Directives pour l'Orchestrateur.",
                    "properties": {
                        "instructions": {"type": "string"},
                        "resources_to_target": {"type": "array", "items": {"type": "string"}},
                        "tools_to_use": {"type": "array", "items": {"type": "string"}},
                        "search_strategy": {"type": "string"},
                    },
                },
                "synthesiser_directives": {
                    "type": "object",
                    "description": "Directives pour le Synthétiseur.",
                    "properties": {
                        "instructions": {"type": "string"},
                        "response_format": {"type": "string"},
                        "response_tone": {"type": "string"},
                        "focus_points": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "is_direct": {
                    "type": "boolean",
                    "description": "True si la réponse ne nécessite pas de recherche documentaire.",
                },
                "direct_response": {
                    "type": "string",
                    "description": "Texte de réponse directe si is_direct=true.",
                },
                "suggested_next_phase": {
                    "type": "string",
                    "description": "Phase de conversation suggérée : discovery|active|concluding|concluded.",
                    "enum": ["discovery", "active", "concluding", "concluded"],
                },
                "new_anchors": {
                    "type": "array",
                    "description": "Nouveaux éléments clés identifiés à ancrer dans la trame.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "anchor_type": {"type": "string"},
                            "ref": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
                "search_directives": {
                    "type": "object",
                    "description": "Plan de retrieval multi-source pour le PreExecutionBuilder.",
                    "properties": {
                        "chunk_queries": {"type": "array", "items": {"type": "string"}},
                        "document_queries": {"type": "array", "items": {"type": "string"}},
                        "skill_queries": {"type": "array", "items": {"type": "string"}},
                        "history_queries": {"type": "array", "items": {"type": "string"}},
                        "context_filters": {"type": "object"},
                        "objective": {"type": "string"},
                        "completeness_criteria": {"type": "string"},
                    },
                },
            },
            "required": ["intent_type", "query_reformulated", "needs_rag", "needs_tools", "confidence"],
        },
    },
}


def _enrobe(morceaux: list[str]) -> str:
    """Entoure d'une balise unique tout ce qui vient de l'exterieur.

    Rend une chaine vide si la liste l'est : une balise vide declarerait une donnee
    qui n'existe pas, et changerait le prompt de tous les tours ordinaires pour rien.
    """
    if not morceaux:
        return ""
    return baliser("\n".join(morceaux), source="conversation et espace",
                   nature="contexte")


class Analyser:
    """Agent Analyseur — comprend l'intention du message.

    Args:
        albert: Client Albert API (LLMClientProtocol).
        storage: Backend de stockage (StorageProtocol).
        temperature: Température pour l'appel LLM d'analyse.
        use_tool_calling: Si True, utilise chat_with_tools pour un JSON garanti.
        tool_registry: ToolRegistry optionnel — inclut les descriptions des tools dans le prompt.
        model: Modèle Albert à utiliser (Phase 6 : albert_model_light). Si None, Albert choisit le défaut.
    """

    def __init__(
        self,
        albert,
        storage,
        temperature: float = 0.1,
        use_tool_calling: bool = False,
        tool_registry=None,
        model: str | None = None,
    ) -> None:
        self._albert = albert
        self._storage = storage
        self._temperature = temperature
        self._use_tool_calling = use_tool_calling
        self._tool_registry = tool_registry
        self._model = model

    async def analyse(
        self,
        message: IncomingMessage,
        context: WorkspaceContext,
        pre_exec: PreExecutionCard | None = None,
    ) -> Intent:
        """Analyse un message et produit une Intent avec directives ciblées.

        Args:
            message: Message entrant.
            context: Contexte résolu (workspace, mode, etc.).
            pre_exec: PreExecutionCard optionnelle (Phase 6) — enrichit le prompt
                      avec le contexte fixe (behavior, skills, phase en cours).

        Returns:
            Intent structurée avec directives pour orchestrateur et synthétiseur.
        """
        # Shortcut : salutations simples — réponse directe sans appel LLM
        if GREETING_PATTERNS.match(message.body.strip()):
            return Intent(
                intent_type=IntentType.GREETING,
                query_reformulated="",
                needs_rag=False,
                confidence=1.0,
                is_direct=True,
                direct_response="Bonjour ! Comment puis-je vous aider ?",
            )

        if self._use_tool_calling:
            return await self._analyse_with_tool_calling(message, context, pre_exec)
        return await self._analyse_with_json_prompt(message, context, pre_exec)

    async def _analyse_with_json_prompt(
        self,
        message: IncomingMessage,
        context: WorkspaceContext,
        pre_exec: PreExecutionCard | None = None,
    ) -> Intent:
        """Analyse via prompt JSON libre (mode par défaut)."""
        start = time.monotonic()

        # Construire le contexte agent
        agent_ctx = await build_agent_context(
            self._storage, context.workspace, "analyser"
        )

        workspace_info = self._build_workspace_info(context, pre_exec)
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            message=message.body,
            workspace_context=workspace_info,
        )

        # La CONSIGNE explique les balises que `workspace_info` vient peut-etre de
        # poser. Sans elle, `<untrusted>` n'est qu'un caractere de plus dans le prompt.
        #
        # AJOUTEE SEULEMENT S'IL Y A UN BLOC : une consigne posee sans bloc parlerait
        # d'une donnee absente, et changerait le prompt des tours ordinaires — ceux-la
        # memes que la reference mesure.
        systeme = agent_ctx.system_prompt
        if contient_balise(workspace_info):
            systeme = f"{systeme}\n\n{CONSIGNE}"

        # Historique conversationnel — permet à l'Analyser de contextualiser
        # les reformulations dans les conversations multi-tours
        messages = [{"role": "system", "content": systeme}]
        for hist_msg in context.conversation_history[-8:]:
            if hist_msg.get("role") in ("user", "assistant") and hist_msg.get("content"):
                messages.append({"role": hist_msg["role"], "content": hist_msg["content"]})
        messages.append({"role": "user", "content": prompt})

        # Appliquer les overrides behavior (temperature, max_tokens) si présents
        temperature = self._temperature
        max_tokens = 1024
        if pre_exec and pre_exec.agent_overrides.get("analyser"):
            ov = pre_exec.agent_overrides["analyser"]
            temperature = ov.get("temperature", temperature)
            max_tokens = ov.get("max_tokens", max_tokens)

        kwargs: dict = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self._model:
            kwargs["model"] = self._model

        try:
            raw = await self._albert.chat(**kwargs)
        except Exception as e:
            raise AnalysisError(f"erreur appel Albert pour analyse: {e}") from e

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.debug("analyse (json-prompt) en %dms (temp=%.2f)", elapsed_ms, temperature)

        return self._parse_analysis(raw, message.body)

    async def _analyse_with_tool_calling(
        self,
        message: IncomingMessage,
        context: WorkspaceContext,
        pre_exec: PreExecutionCard | None = None,
    ) -> Intent:
        """Analyse via tool calling — retourne un JSON garanti sans parsing.

        Utilise chat_with_tools avec l'outil analyse_intent et tool_choice='required'
        pour forcer le LLM à retourner un JSON structuré.
        """
        start = time.monotonic()

        agent_ctx = await build_agent_context(
            self._storage, context.workspace, "analyser"
        )

        workspace_info = self._build_workspace_info(context, pre_exec)
        user_content = (
            f"Analyse ce message utilisateur.\n\n"
            f"Message : \"{message.body}\"\n\n"
            f"{workspace_info}"
        )

        # La CONSIGNE explique les balises que `workspace_info` vient peut-etre de
        # poser. Sans elle, `<untrusted>` n'est qu'un caractere de plus dans le prompt.
        #
        # AJOUTEE SEULEMENT S'IL Y A UN BLOC : une consigne posee sans bloc parlerait
        # d'une donnee absente, et changerait le prompt des tours ordinaires — ceux-la
        # memes que la reference mesure.
        systeme = agent_ctx.system_prompt
        if contient_balise(workspace_info):
            systeme = f"{systeme}\n\n{CONSIGNE}"

        # Historique conversationnel — idem json_prompt pour la cohérence multi-turns
        messages = [{"role": "system", "content": systeme}]
        for hist_msg in context.conversation_history[-8:]:
            if hist_msg.get("role") in ("user", "assistant") and hist_msg.get("content"):
                messages.append({"role": hist_msg["role"], "content": hist_msg["content"]})
        messages.append({"role": "user", "content": user_content})

        temperature = self._temperature
        max_tokens = 1024
        if pre_exec and pre_exec.agent_overrides.get("analyser"):
            ov = pre_exec.agent_overrides["analyser"]
            temperature = ov.get("temperature", temperature)
            max_tokens = ov.get("max_tokens", max_tokens)

        kwargs: dict = {
            "messages": messages,
            "tools": [ANALYSE_INTENT_TOOL],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tool_choice": "required",
        }
        if self._model:
            kwargs["model"] = self._model

        try:
            result = await self._albert.chat_with_tools(**kwargs)
        except Exception as e:
            raise AnalysisError(f"erreur appel Albert (tool_calling) pour analyse: {e}") from e

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.debug("analyse (tool-calling) en %dms", elapsed_ms)

        # Extraire les arguments du tool call
        if result.has_tool_calls and result.tool_calls[0].tool_name == "analyse_intent":
            return self._parse_analysis_from_dict(result.tool_calls[0].arguments, message.body)

        # Fallback si le LLM n'a pas utilisé le tool
        logger.warning("analyse_with_tool_calling: pas de tool call dans la réponse, fallback JSON")
        content = result.content or "{}"
        return self._parse_analysis(content, message.body)

    def _build_workspace_info(
        self,
        context: WorkspaceContext,
        pre_exec: PreExecutionCard | None = None,
    ) -> str:
        """Construit la section workspace info pour le prompt d'analyse.

        Si un tool_registry est disponible, inclut les descriptions des tools.
        Phase 6 : enrichit avec les infos du pre_exec (behavior, phase, skills).
        Toujours : injecte le contexte utilisateur (display_name, domain, mode, mémoire).
        """
        # CE QUI EST DÉCLARÉ COMME DONNÉE, ET CE QUI NE L'EST PAS (L2.1c, principe 4)
        # ---------------------------------------------------------------------------
        # `externe` rassemble tout ce qui dérive d'un document, d'un tour de
        # conversation ou d'un choix d'utilisateur. Il sort de cette fonction entouré
        # d'UN SEUL couple de balises : la déclaration est la même pour tous les
        # champs, et en poser une par champ gonflerait le prompt sans rien ajouter.
        #
        # Reste HORS balise ce que l'instance énonce en son nom propre — `name`,
        # `description`, `language` du `config.yaml`, et le mode d'interaction. Les
        # baliser dirait au modèle de ne pas en tenir compte, ce qui viderait de leur
        # fonction des paramètres que le propriétaire de l'espace a posés délibérément.
        externe: list[str] = []

        # Contexte utilisateur — injecté quel que soit le mode
        user_parts = []
        if context.user_display_name:
            user_parts.append(f"Nom affiché : {sanitize_description(context.user_display_name)}")
        if context.user_domain:
            user_parts.append(f"Domaine : {sanitize_description(context.user_domain)}")
        # Faits mémoire issus du pre_exec (Phase 6 ou chargés par handlers). Ils
        # entraient BRUTS — sans même l'assainissement que recevaient leurs voisins.
        if pre_exec and pre_exec.fixed_context and pre_exec.fixed_context.get("user_memory"):
            facts = [sanitize_description(f) for f in pre_exec.fixed_context["user_memory"][:5]]
            user_parts.append("Mémoire utilisateur : " + " | ".join(facts))
        if user_parts:
            externe.append("Utilisateur :\n" + "\n".join(f"  {p}" for p in user_parts))

        mode_part = f"Mode d'interaction : {context.mode.value}" if context.mode else ""

        if not context.workspace:
            # Sans workspace : retourner au moins le contexte utilisateur + pre_exec si dispo
            if pre_exec and pre_exec.fixed_context:
                pre_exec_info = self._build_pre_exec_info(pre_exec)
                if pre_exec_info:
                    externe.append(pre_exec_info)
            return "\n\n".join(filter(None, [mode_part, _enrobe(externe)]))

        ws = context.workspace
        parts = [
            f"Contexte workspace : {sanitize_description(ws.name)}",
            f"Description : {sanitize_description(ws.description)}",
            f"Langue : {sanitize_description(ws.language)}",
        ]
        if mode_part:
            parts.append(mode_part)

        # Phase 6 : enrichissement avec le contexte fixe du pre_exec
        if pre_exec and pre_exec.fixed_context:
            fc = pre_exec.fixed_context
            if fc.get("conversation_phase"):
                externe.append(f"Phase conversation : {sanitize_description(fc['conversation_phase'])}")
            if fc.get("active_behavior"):
                externe.append(f"Behavior actif : {sanitize_description(fc['active_behavior'])}")
            if fc.get("domain"):
                externe.append(f"Domaine : {sanitize_description(fc['domain'])}")
            if fc.get("tone"):
                externe.append(f"Ton attendu : {sanitize_description(fc['tone'])}")
            if fc.get("vocabulary_terms"):
                termes = ", ".join(fc["vocabulary_terms"][:10])
                externe.append(f"Vocabulaire métier : {sanitize_description(termes)}")
            if fc.get("known_documents"):
                docs = [sanitize_description(d) for d in fc["known_documents"][:5]]
                externe.append(f"Documents connus dans la conversation : {', '.join(docs)}")
            if pre_exec.selected_skills:
                skill_names = [sanitize_description(s.get("name", ""))
                               for s in pre_exec.selected_skills if s.get("name")]
                if skill_names:
                    externe.append(f"Procédures/compétences disponibles : {', '.join(skill_names)}")

        # Anchors de conversation (Phase 6 — éléments établis dans le fil)
        if context.context_anchors:
            # LE canal qui compte. Une ancre peut naître des `new_anchors` émises par le
            # SYNTHÉTISEUR, qui a lu le contenu des documents : un texte injecté dans un
            # document ressort ici, dans le prompt de l'Analyseur, un tour plus tard —
            # et la trame est partagée par tout le salon.
            #
            # C'est le seul chemin par lequel un contenu documentaire atteint le verdict
            # `needs_tools`, donc le catalogue d'outils (L2.5b).
            anchor_summaries = [
                sanitize_description(a.description or a.ref)
                for a in context.context_anchors[:5]
                if a.description or a.ref
            ]
            if anchor_summaries:
                externe.append(f"Éléments établis dans la conversation : {', '.join(anchor_summaries)}")

        bloc_externe = _enrobe(externe)
        if bloc_externe:
            parts.append(bloc_externe)

        # Inclure descriptions des tools si tool_registry disponible
        tools_source = (
            [t.name for t in pre_exec.available_tools]
            if pre_exec and pre_exec.available_tools
            else context.available_tools
        )
        if self._tool_registry and tools_source:
            tools_lines = []
            for name in tools_source:
                entry = self._tool_registry.get(name)
                if entry:
                    defn = entry[0]
                    tools_lines.append(f"- {name} : {defn.description}")
                else:
                    tools_lines.append(f"- {name}")
            parts.append("Tools disponibles pour l'Orchestrateur :\n" + "\n".join(tools_lines))
        elif tools_source:
            parts.append(f"Tools disponibles : {', '.join(tools_source)}")

        return "\n".join(parts)

    def _build_pre_exec_info(self, pre_exec: PreExecutionCard) -> str:
        """Construit le contexte minimal à partir du pre_exec seul (mode sans workspace)."""
        parts = []
        fc = pre_exec.fixed_context
        if fc.get("conversation_phase"):
            parts.append(f"Phase conversation : {fc['conversation_phase']}")
        if fc.get("active_behavior"):
            parts.append(f"Behavior actif : {fc['active_behavior']}")
        return "\n".join(parts) if parts else ""

    def _parse_analysis_from_dict(self, data: dict, original_query: str) -> Intent:
        """Construit un Intent à partir d'un dict déjà parsé (mode tool calling)."""
        intent_type_str = data.get("intent_type", "unknown")
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.UNKNOWN

        orch_data = data.get("orchestrator_directives", {})
        orchestrator_directives = AgentDirectives(
            target_agent="orchestrator",
            instructions=orch_data.get("instructions", ""),
            resources_to_target=orch_data.get("resources_to_target", []),
            tools_to_use=orch_data.get("tools_to_use", []),
            search_strategy=orch_data.get("search_strategy", "broad"),
        ) if orch_data else None

        synth_data = data.get("synthesiser_directives", {})
        synthesiser_directives = AgentDirectives(
            target_agent="synthesiser",
            instructions=synth_data.get("instructions", ""),
            response_format=synth_data.get("response_format", "paragraph"),
            response_tone=synth_data.get("response_tone", ""),
            focus_points=synth_data.get("focus_points", []),
        ) if synth_data else None

        search_directives = self._parse_search_directives(data.get("search_directives"), original_query)
        new_anchors = self._parse_anchors(data.get("new_anchors", []))

        return Intent(
            intent_type=intent_type,
            query_reformulated=data.get("query_reformulated", original_query),
            entities=data.get("entities", {}),
            needs_rag=data.get("needs_rag", True),
            needs_tools=data.get("needs_tools", False),
            confidence=data.get("confidence", 0.5),
            orchestrator_directives=orchestrator_directives,
            synthesiser_directives=synthesiser_directives,
            search_directives=search_directives,
            is_direct=bool(data.get("is_direct", False)),
            direct_response=data.get("direct_response", ""),
            suggested_next_phase=data.get("suggested_next_phase") or None,
            new_anchors=new_anchors,
        )

    def _parse_analysis(self, raw: str, original_query: str) -> Intent:
        """Parse la réponse JSON d'Albert en Intent structurée.

        Fallback gracieux si le JSON est invalide.
        """
        try:
            # Extraire le premier objet JSON complet depuis la réponse d'Albert
            # (qui peut entourer le JSON de commentaires ou de markdown)
            # Utilise un parser à profondeur plutôt qu'une regex pour gérer
            # les valeurs string contenant des accolades.
            json_str = _extract_json_object(raw)
            if not json_str:
                raise ValueError("pas de JSON trouvé")

            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            logger.warning("analyse JSON invalide, fallback: %s", raw[:200])
            return Intent(
                intent_type=IntentType.QUESTION,
                query_reformulated=original_query,
                needs_rag=True,
                confidence=0.3,
                raw_analysis=raw,
            )

        # Construire l'Intent
        intent_type_str = data.get("intent_type", "unknown")
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.UNKNOWN

        # Construire les directives orchestrateur
        orch_data = data.get("orchestrator_directives", {})
        orchestrator_directives = AgentDirectives(
            target_agent="orchestrator",
            instructions=orch_data.get("instructions", ""),
            resources_to_target=orch_data.get("resources_to_target", []),
            tools_to_use=orch_data.get("tools_to_use", []),
            search_strategy=orch_data.get("search_strategy", "broad"),
        ) if orch_data else None

        # Construire les directives synthétiseur
        synth_data = data.get("synthesiser_directives", {})
        synthesiser_directives = AgentDirectives(
            target_agent="synthesiser",
            instructions=synth_data.get("instructions", ""),
            response_format=synth_data.get("response_format", "paragraph"),
            response_tone=synth_data.get("response_tone", ""),
            focus_points=synth_data.get("focus_points", []),
        ) if synth_data else None

        search_directives = self._parse_search_directives(data.get("search_directives"), original_query)
        new_anchors = self._parse_anchors(data.get("new_anchors", []))

        return Intent(
            intent_type=intent_type,
            query_reformulated=data.get("query_reformulated", original_query),
            entities=data.get("entities", {}),
            needs_rag=data.get("needs_rag", True),
            needs_tools=data.get("needs_tools", False),
            confidence=data.get("confidence", 0.5),
            raw_analysis=raw,
            orchestrator_directives=orchestrator_directives,
            synthesiser_directives=synthesiser_directives,
            search_directives=search_directives,
            is_direct=bool(data.get("is_direct", False)),
            direct_response=data.get("direct_response", ""),
            suggested_next_phase=data.get("suggested_next_phase") or None,
            new_anchors=new_anchors,
        )

    # -------------------------------------------------------------------------
    # Helpers Phase 6
    # -------------------------------------------------------------------------

    def _parse_search_directives(
        self, raw_sd: dict | None, fallback_query: str
    ) -> SearchDirectives | None:
        """Construit des SearchDirectives depuis le dict JSON ou génère un fallback minimal."""
        if not raw_sd:
            # Fallback : 1 query = la reformulation originale
            return SearchDirectives(chunk_queries=[fallback_query]) if fallback_query else None
        return SearchDirectives(
            chunk_queries=raw_sd.get("chunk_queries", [fallback_query]),
            document_queries=raw_sd.get("document_queries", []),
            skill_queries=raw_sd.get("skill_queries", []),
            history_queries=raw_sd.get("history_queries", []),
            context_filters=raw_sd.get("context_filters", {}),
            objective=raw_sd.get("objective", ""),
            completeness_criteria=raw_sd.get("completeness_criteria", ""),
        )

    def _parse_anchors(self, raw_anchors: list) -> list[ContextAnchor]:
        """Construit une liste de ContextAnchor depuis le JSON."""
        anchors = []
        for item in raw_anchors:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref", "")
            if not ref:
                continue
            anchors.append(ContextAnchor(
                anchor_type=item.get("anchor_type", "entity"),
                ref=ref,
                description=item.get("description", ""),
            ))
        return anchors


# =============================================================================
# Utilitaires module-level
# =============================================================================

def _extract_json_object(text: str) -> str | None:
    """Extrait le premier objet JSON complet depuis un texte libre.

    Contrairement à une regex `{.*}`, gère correctement les accolades
    présentes dans les valeurs string (ex: prompt contenant `{}`).

    Args:
        text: Texte brut retourné par le LLM (peut contenir du markdown, des commentaires...).

    Returns:
        Sous-chaîne JSON valide, ou None si non trouvée.
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
