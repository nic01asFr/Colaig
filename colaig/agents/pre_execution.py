"""
Colaig — PreExecutionBuilder (Phase 6)

Construit la PreExecutionCard avant que l'Analyser ne tourne.
Centralise : embedding message, behavior actif, skills lazy-loaded, tools filtrés.

Exécute aussi le plan de récupération multi-source (execute_retrieval)
après que l'Analyser ait produit les SearchDirectives.
"""

from __future__ import annotations

import asyncio
import logging

from colaig import paths
from colaig.agents.profile_service import ProfileService
from colaig.models import (
    BehaviorConfig,
    ConversationTrame,
    IncomingMessage,
    PreExecutionCard,
    SearchDirectives,
    ToolDefinition,
    WorkspaceContext,
    WorkspaceProfile,
)
from colaig.protocols import (
    EmbeddingServiceProtocol,
    RetrieverProtocol,
    StorageProtocol,
)

logger = logging.getLogger(__name__)


def filter_tools(
    tools: list[ToolDefinition],
    workspace: object,
    trame: ConversationTrame | None = None,
    behavior: BehaviorConfig | None = None,
    intent_type: str | None = None,
) -> list[ToolDefinition]:
    """Filtre les tools disponibles sans LLM (évaluation booléenne déclarative).

    Applique dans l'ordre :
    1. requires_workspace : écarte si workspace requis mais absent
    2. allowed_intent_types : écarte si intent_type connu et non dans la liste
    3. excluded_phases : écarte si la phase courante de la trame est exclue
    4. behavior.tools.disable : écarte si le behavior actif désactive ce tool

    Args:
        tools: Liste complète des ToolDefinition disponibles.
        workspace: WorkspaceConfig ou None (mode chatbot/personnel).
        trame: Trame vivante courante (pour conversation_phase).
        behavior: Behavior actif (pour tools.disable).
        intent_type: Type d'intent (si connu, sinon None — pré-analyse).

    Returns:
        Sous-liste filtrée, ordre conservé.
    """
    result: list[ToolDefinition] = []
    for tool in tools:
        # 1. Workspace requis
        if tool.requires_workspace and not workspace:
            continue
        # 2. Intent type (seulement si connu et liste non vide)
        if intent_type and tool.allowed_intent_types:
            if intent_type not in tool.allowed_intent_types:
                continue
        # 3. Phase exclue
        if trame and tool.excluded_phases:
            if trame.conversation_phase in tool.excluded_phases:
                continue
        # 4. Behavior disable list
        if behavior and tool.name in behavior.tools.disable:
            continue
        result.append(tool)
    return result


class PreExecutionBuilder:
    """Construit la PreExecutionCard avant le pipeline (Phase 6).

    Args:
        storage: Backend de stockage.
        embeddings: Service d'embeddings Albert.
        profile_service: Service de chargement du profil workspace.
        all_tools: Liste complète des ToolDefinition enregistrés.
        retriever: Retriever RAG pour execute_retrieval (optionnel).
        conversation_memory: ConversationMemory pour history_queries (optionnel).
    """

    def __init__(
        self,
        storage: StorageProtocol,
        embeddings: EmbeddingServiceProtocol,
        profile_service: ProfileService,
        all_tools: list[ToolDefinition],
        retriever: RetrieverProtocol | None = None,
        conversation_memory=None,
        index_registry=None,   # FaissIndexRegistry optionnel (multi-index concurrent)
        user_memory=None,      # UserMemory optionnel (mémoire per-user)
    ) -> None:
        self._storage = storage
        self._embeddings = embeddings
        self._profile_service = profile_service
        self._all_tools = all_tools
        self._retriever = retriever
        self._conversation_memory = conversation_memory
        self._index_registry = index_registry
        self._user_memory = user_memory

    async def build(
        self,
        message: IncomingMessage,
        trame: ConversationTrame,
        workspace_context: WorkspaceContext,
    ) -> PreExecutionCard:
        """Construit la PreExecutionCard avant l'Analyser.

        Opérations (toutes tolérantes aux erreurs avec fallback) :
        1. embed(message.body) → 1 seul appel Albert, réutilisé pour behaviors + skills
        2. Charger profil workspace (identity.yaml)
        3. Charger behaviors (behaviors/*.yaml)
        4. Résoudre behavior actif (behaviors.faiss ou trame.active_behavior)
        5. Lazy-load skills top-k (skills.faiss ou tous les skills si absent)
        6. Filtrer tools (déclaratif : requires_workspace, excluded_phases, behavior.disable)
        7. Construire fixed_context (config + identity + behavior)

        Args:
            message: Message entrant.
            trame: Trame vivante courante de la conversation.
            workspace_context: Contexte workspace résolu.

        Returns:
            PreExecutionCard complète.
        """
        workspace = workspace_context.workspace
        workspace_path = workspace.storage_path if workspace else ""
        workspace_id = workspace.workspace_id if workspace else None

        # 1. Embedding du message (1 seul appel Albert, réutilisé)
        message_embedding: list[float] | None = None
        if message.body.strip():
            try:
                message_embedding = await self._embeddings.embed_text(message.body)
            except Exception as e:
                logger.warning("embed message échoué: %s", e)

        # 2-3. Profil, behaviors et mémoire user — en parallèle
        profile: WorkspaceProfile | None = None
        behaviors: list = []
        user_facts: list = []

        if workspace_path:
            # Lance profil + behaviors + user_memory en parallèle
            coros = [
                self._profile_service.load_profile(workspace_path),
                self._profile_service.load_behaviors(workspace_path),
            ]
            user_id = message.user_id
            if self._user_memory and message_embedding and user_id:
                coros.append(
                    self._user_memory.read(user_id, workspace_path, message_embedding, k=5)
                )
            gathered = await asyncio.gather(*coros, return_exceptions=True)
            profile = gathered[0] if not isinstance(gathered[0], Exception) else None
            behaviors = gathered[1] if not isinstance(gathered[1], Exception) else []
            if len(gathered) > 2:
                user_facts = gathered[2] if not isinstance(gathered[2], Exception) else []

        # 4. Behavior actif
        active_behavior: BehaviorConfig | None = None
        active_score: float = 0.0
        if message_embedding and behaviors:
            active_behavior, active_score = await self._profile_service.resolve_behavior(
                message_embedding=message_embedding,
                behaviors=behaviors,
                workspace_path=workspace_path,
                active_behavior_name=trame.active_behavior,
            )

        # 5. Lazy-load skills
        selected_skills: list[dict] = []
        selected_scores: list[float] = []
        if workspace_path:
            selected_skills, selected_scores = await self._load_skills(
                workspace_path=workspace_path,
                message_embedding=message_embedding,
                behavior=active_behavior,
            )

        # 6. Filtrer tools (pré-intent : intent_type inconnu à cette étape)
        available_tools = filter_tools(
            tools=self._all_tools,
            workspace=workspace,
            trame=trame,
            behavior=active_behavior,
            intent_type=None,
        )

        # Overrides agents depuis behavior.agents.*
        agent_overrides: dict[str, dict] = {}
        if active_behavior:
            for agent_name, cfg in active_behavior.agents.items():
                overrides: dict = {}
                if cfg.temperature is not None:
                    overrides["temperature"] = cfg.temperature
                if cfg.max_tokens is not None:
                    overrides["max_tokens"] = cfg.max_tokens
                if cfg.format:
                    overrides["format"] = cfg.format
                if overrides:
                    agent_overrides[agent_name] = overrides

        # 7. Contexte fixe (transmis directement à Agent 1 sans retrieval)
        fixed_context: dict = {}
        if workspace:
            fixed_context["workspace_name"] = workspace.name
            fixed_context["workspace_description"] = workspace.description
            fixed_context["language"] = workspace.language
        if profile:
            fixed_context["domain"] = profile.domain
            fixed_context["tone"] = profile.tone
            if profile.vocabulary.terms:
                fixed_context["vocabulary_terms"] = profile.vocabulary.terms[:20]
        if active_behavior:
            fixed_context["active_behavior"] = active_behavior.name
            fixed_context["behavior_description"] = active_behavior.description
            if active_behavior.output.get("tone"):
                fixed_context["tone"] = active_behavior.output["tone"]
        fixed_context["conversation_phase"] = trame.conversation_phase
        known_docs = [a.ref for a in trame.context_anchors if a.anchor_type == "document"]
        if known_docs:
            fixed_context["known_documents"] = known_docs
        if user_facts:
            fixed_context["user_memory"] = [getattr(f, "text", str(f)) for f in user_facts]

        return PreExecutionCard(
            workspace_id=workspace_id,
            conversation_phase=trame.conversation_phase,
            context_anchors=list(trame.context_anchors),
            active_behavior_name=active_behavior.name if active_behavior else None,
            active_behavior_config=active_behavior,
            active_behavior_score=active_score,
            selected_skills=selected_skills,
            selected_skills_scores=selected_scores,
            available_tools=available_tools,
            agent_overrides=agent_overrides,
            workspace_profile=profile,
            fixed_context=fixed_context,
            message_embedding=message_embedding,
        )

    async def execute_retrieval(
        self,
        search_directives: SearchDirectives,
        pre_exec: PreExecutionCard,
        workspace_context: WorkspaceContext,
    ) -> dict:
        """Exécute le plan de récupération multi-source après l'Analyser.

        Sources interrogées selon SearchDirectives :
        - chunk_queries → chunks.faiss (via Retriever)
        - document_queries → chunks.faiss avec filtre document-level (future)
        - skill_queries → skills déjà chargés dans pre_exec (pas de nouvel appel)
        - history_queries → ConversationMemory sémantique

        Args:
            search_directives: Plan produit par l'Analyser.
            pre_exec: PreExecutionCard (skills déjà chargés).
            workspace_context: Contexte workspace.

        Returns:
            Dict {"chunks": [...], "docs": [...], "skills": [...], "history": [...]}
        """
        results: dict = {"chunks": [], "docs": [], "skills": [], "history": []}

        # Chunks RAG (chunks.faiss)
        if self._retriever and search_directives.chunk_queries:
            seen_paths: set[str] = set()
            for query in search_directives.chunk_queries:
                try:
                    found = await self._retriever.retrieve(query=query, k=5, score_threshold=0.3)
                    for r in found:
                        path = getattr(getattr(r, "chunk", None), "source_path", "")
                        if path not in seen_paths:
                            seen_paths.add(path)
                            results["chunks"].append(r)
                except Exception as e:
                    logger.warning("retrieve chunks (query_len=%d): %s", len(query), e)

        # Documents (document_queries identiques à chunks pour l'instant)
        if self._retriever and search_directives.document_queries:
            seen_paths_doc: set[str] = set()
            for query in search_directives.document_queries:
                try:
                    found = await self._retriever.retrieve(query=query, k=3, score_threshold=0.4)
                    for r in found:
                        path = getattr(getattr(r, "chunk", None), "source_path", "")
                        if path not in seen_paths_doc:
                            seen_paths_doc.add(path)
                            results["docs"].append(r)
                except Exception as e:
                    logger.debug("retrieve docs (query_len=%d): %s", len(query), e)

        # Skills sélectionnés (déjà chargés dans pre_exec — pas de nouvel appel)
        if search_directives.skill_queries:
            results["skills"] = pre_exec.selected_skills

        # Historique conversationnel sémantique
        if self._conversation_memory and search_directives.history_queries:
            workspace = workspace_context.workspace
            conv_id = workspace.workspace_id if workspace else ""
            for query in search_directives.history_queries:
                try:
                    history = await self._conversation_memory.retrieve(
                        query=query, conversation_id=conv_id
                    )
                    results["history"].extend(history)
                except Exception as e:
                    logger.debug("retrieve history (query_len=%d): %s", len(query), e)

        logger.debug(
            "execute_retrieval: chunks=%d docs=%d skills=%d history=%d",
            len(results["chunks"]),
            len(results["docs"]),
            len(results["skills"]),
            len(results["history"]),
        )
        return results

    # -------------------------------------------------------------------------
    # Helpers privés
    # -------------------------------------------------------------------------

    async def _load_skills(
        self,
        workspace_path: str,
        message_embedding: list[float] | None,
        behavior: BehaviorConfig | None,
    ) -> tuple[list[dict], list[float]]:
        """Lazy-load skills pertinents via skills.faiss ou fallback liste complète."""
        skills_dir = paths.skills_dir(workspace_path)
        selected_names: list[str] = []
        selected_scores: list[float] = []

        from colaig.rag.colaig_index import ColaigIndex
        faiss_path, meta_path = ColaigIndex.skills_paths(workspace_path)

        try:
            if message_embedding:
                if self._index_registry is not None:
                    key = ColaigIndex.skills_key(workspace_path)
                    store = await self._index_registry.get_or_load(
                        key,
                        lambda: ColaigIndex.load_store(
                            self._storage, faiss_path, meta_path, len(message_embedding)
                        ),
                    )
                else:
                    store = await ColaigIndex.load_store(
                        self._storage, faiss_path, meta_path, len(message_embedding)
                    )

                if store is None:
                    raise FileNotFoundError("skills.faiss absent")

                results = store.search(message_embedding, k=3)

                selected_names = [
                    getattr(r.chunk, "section", "") for r in results
                    if getattr(r.chunk, "section", "")
                ]
                selected_scores = [r.score for r in results if getattr(r.chunk, "section", "")]
            else:
                raise FileNotFoundError("skills.faiss absent")
        except Exception:
            # Fallback : charger tous les skills (comportement Phase 5)
            try:
                files = await self._storage.list_files(skills_dir)
                selected_names = [f.name.removesuffix(".md") for f in files if f.name.endswith(".md")]
                selected_scores = [1.0] * len(selected_names)
            except Exception:
                return [], []

        # Skills boost depuis behavior (priorité forcée)
        if behavior:
            for name in behavior.skills_boost:
                if name not in selected_names:
                    selected_names.insert(0, name)
                    selected_scores.insert(0, 1.0)

        # Cap à 5 skills max + chargement contenu
        selected_skills: list[dict] = []
        for name in selected_names[:5]:
            try:
                content = await self._storage.download(f"{skills_dir}{name}.md")
                selected_skills.append({"name": name, "content": content.decode("utf-8")})
            except Exception:
                pass

        return selected_skills, selected_scores[: len(selected_skills)]
