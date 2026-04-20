"""
Colaig — Context Builder pour les agents du pipeline

Construit un AgentContext spécifique pour chaque agent (Analyseur, Orchestrateur,
Synthétiseur) à partir du workspace et de ses fichiers .colaig/.

Chaque agent a un prompt par défaut intégré, surchargeable par l'utilisateur
via .colaig/prompts/{role}.md dans le workspace.
"""

from __future__ import annotations

import logging
from typing import Optional

from colaig.models import AgentContext, AgentDirectives, WorkspaceConfig
from colaig.agents.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# Prompts par défaut — intégrés, surchargés par .colaig/prompts/{role}.md
# =============================================================================

DEFAULT_ANALYSER_PROMPT = """\
Tu analyses les messages reçus par un assistant IA d'administration publique.

Ta mission : comprendre l'intention de l'utilisateur et produire un JSON structuré avec :
- Le type d'intention (question, action, search, summary, greeting, etc.)
- La question reformulée pour optimiser la recherche documentaire
- Les entités clés extraites (dates, noms, références)
- Des directives ciblées pour l'orchestrateur (ressources à cibler, stratégie de recherche)
- Des directives ciblées pour le synthétiseur (format de réponse, ton, points de focus)

Tu dois être précis et concis. Ne génère jamais de réponse à la question — uniquement l'analyse.\
"""

DEFAULT_ORCHESTRATOR_PROMPT = """\
Tu coordonnes l'exécution de tâches pour répondre à une intention utilisateur.

Tu reçois une intention analysée avec des directives ciblées. Tu exécutes les étapes
séquentiellement, chaque résultat enrichissant le contexte pour l'étape suivante.

Tes capacités :
- Recherche documentaire (RAG) dans les index du workspace
- Récupération de fichiers spécifiques depuis le storage
- Exécution de tools MCP (si disponibles)

Tu ne génères pas de réponse — tu collectes les données nécessaires au synthétiseur.\
"""

DEFAULT_SYNTHESISER_PROMPT = """\
Tu formules la réponse finale d'un assistant IA d'administration publique.

Tu reçois :
- Les résultats de recherche documentaire
- Les résultats d'exécution de tools
- Des directives sur le format, le ton et les points de focus

Ta mission :
- Formuler une réponse claire, structurée et sourcée
- Citer les documents sources entre crochets [nom_fichier.ext]
- Respecter le ton et le format demandés
- Être factuel : ne jamais inventer d'information absente des sources\
"""

DEFAULT_PROMPTS = {
    "analyser": DEFAULT_ANALYSER_PROMPT,
    "orchestrator": DEFAULT_ORCHESTRATOR_PROMPT,
    "synthesiser": DEFAULT_SYNTHESISER_PROMPT,
}

# Tools par rôle — noms des tools disponibles pour chaque rôle
# Correspond aux ToolDefinition.name enregistrées dans le ToolRegistry
TOOLS_BY_ROLE = {
    "analyser": [],  # L'analyseur n'exécute pas de tools
    "orchestrator": [
        "search_documents",
        "fetch_document",
        "list_documents",
        "summarize_text",
        "search_skill",               # Recherche sémantique dans les procédures/compétences (.colaig/skills/)
        "search_document_index",      # Recherche sémantique sur documents entiers (DocumentIndex)
        "list_document_index",        # Liste avec filtres structurés (DocumentIndex)
        "get_document_metadata",      # Métadonnées IA d'un fichier (DocumentIndex)
        "get_classified_documents",   # Documents classifiés avec virtual_path (Classification)
        "assess_completion",          # Phase 6 : évaluation complétude (dialogue Orchestrateur ↔ Synthétiseur)
        "ask_workspace",              # Délégation inter-workspace avec ACL (injecté dynamiquement par l'orchestrateur)
        "find_workspace",             # Découverte sémantique de workspaces (injecté dynamiquement si workspace_directory)
        "create_background_task",     # Mode C : créer une tâche planifiée (injecté dynamiquement en mode PERSONAL)
    ],
    "synthesiser": [],  # Le synthétiseur n'exécute pas de tools
}

# Tools injectés en session de tâche autonome (Mode C)
BACKGROUND_TASK_TOOLS = [
    "search_documents",
    "fetch_document",
    "list_documents",
    "summarize_text",
    "ask_workspace",         # Cross-workspace RAG
    "find_workspace",        # Découverte sémantique de workspaces fédérés
    "run_subtask",           # Sous-agent pipeline complet dans un workspace cible
    "update_plan",           # Mettre à jour current/plan.json
    "report_to_user",        # Livrer résultat via messaging
    "create_document",       # Livrer résultat via storage
    "pause_and_ask_user",    # Suspendre en attente réponse utilisateur (human-in-the-loop)
    "assess_completion",     # Évaluation complétude (si synthétiseur disponible)
]


# =============================================================================
# API publique
# =============================================================================

async def build_agent_context(
    storage,
    workspace: Optional[WorkspaceConfig],
    agent_role: str,
    directives: Optional[AgentDirectives] = None,
    selected_skills: Optional[list] = None,
) -> AgentContext:
    """Construit le contexte spécifique à un agent.

    1. Charge le prompt par défaut pour le rôle
    2. Tente de charger l'override depuis .colaig/prompts/{role}.md
    3. Skills : utilise selected_skills (top-k sémantique du PreExecutionBuilder) si fournis,
       sinon charge tous les skills depuis .colaig/skills/*.md (fallback Phase 5)
    4. Filtre les tools/resources selon le rôle

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace: Configuration du workspace (None si mode chatbot).
        agent_role: Rôle de l'agent ("analyser", "orchestrator", "synthesiser").
        directives: Directives reçues de l'analyseur (None pour l'analyseur lui-même).
        selected_skills: Skills pré-sélectionnés par PreExecutionBuilder (top-k sémantique).
            Si fournis, court-circuite le chargement en bloc depuis le storage.

    Returns:
        AgentContext complet pour l'agent.
    """
    # 1. Prompt par défaut
    default_prompt = DEFAULT_PROMPTS.get(agent_role, "")

    # 2. Override workspace
    override_prompt = None
    if workspace and workspace.storage_path and storage:
        override_prompt = await _load_agent_prompt_override(
            storage, workspace.storage_path, agent_role
        )

    system_prompt = override_prompt if override_prompt else default_prompt

    # 3. Skills (pour orchestrateur et synthétiseur)
    # Priorité : selected_skills (top-k sémantique, Phase 6) > chargement en bloc (Phase 5)
    skills = []
    if agent_role in ("orchestrator", "synthesiser"):
        if selected_skills is not None:
            # Phase 6 : skills déjà filtrés sémantiquement par PreExecutionBuilder
            skills = selected_skills
        elif workspace and storage:
            # Phase 5 fallback : charger tous les skills depuis .colaig/skills/*.md
            skills = await _load_workspace_skills(storage, workspace.storage_path)

    # 4. Tools filtrés par rôle
    role_tools = TOOLS_BY_ROLE.get(agent_role, [])
    if workspace:
        # Intersection entre tools du rôle et tools activés dans le workspace
        ws_tools = workspace.tools_enabled or []
        available_tools = [t for t in role_tools if t in ws_tools] if ws_tools else role_tools
    else:
        available_tools = role_tools

    # 5. Resources disponibles
    available_resources = []
    if workspace and workspace.priority_documents:
        available_resources = list(workspace.priority_documents)

    return AgentContext(
        agent_role=agent_role,
        system_prompt=system_prompt,
        workspace_config=workspace,
        available_tools=available_tools,
        available_resources=available_resources,
        skills=skills,
        directives=directives,
    )


# =============================================================================
# Fonctions internes
# =============================================================================

async def _load_agent_prompt_override(
    storage, workspace_path: str, role: str
) -> Optional[str]:
    """Charge .colaig/prompts/{role}.md si existant.

    Returns:
        Contenu du fichier override, ou None si inexistant.
    """
    path = f"{workspace_path.rstrip('/')}/.colaig/prompts/{role}.md"
    try:
        content = await storage.download(path)
        text = content.decode("utf-8").strip()
        if text:
            logger.info("override prompt chargé pour %s: %s", role, path)
            return text
    except Exception:
        pass  # Fichier inexistant = pas d'override, c'est normal
    return None


def build_tool_registry(
    retriever,
    storage,
    albert,
    workspace: Optional[WorkspaceConfig] = None,
    document_index=None,    # DocumentIndexProtocol, optionnel
    synthesiser=None,       # Synthesiser, optionnel (Phase 6 : outil assess_completion)
    index_registry=None,    # FaissIndexRegistry, optionnel (N1 : search_skill sémantique)
) -> ToolRegistry:
    """Construit un ToolRegistry avec les outils built-in.

    Tous les outils sont construits comme closures sur retriever/storage/albert.
    Zéro database — le registry est in-memory, reconstruit à chaque démarrage.

    Args:
        retriever: RetrieverProtocol pour le tool search_documents.
        storage: StorageProtocol pour les tools fetch_document et list_documents.
        albert: AlbertClientProtocol pour le tool summarize_text.
        workspace: Configuration workspace pour la résolution des chemins.
        document_index: DocumentIndexProtocol pour les 3 outils DocumentIndex (optionnel).
        synthesiser: Synthesiser (Phase 6) — enregistre l'outil assess_completion si fourni.
        index_registry: FaissIndexRegistry — active la recherche sémantique dans search_skill.

    Returns:
        ToolRegistry prêt à être utilisé par l'Orchestrateur.
    """
    from colaig.agents.tools.rag_tools import (
        SEARCH_DOCUMENTS_DEFINITION,
        create_search_handler,
    )
    from colaig.agents.tools.storage_tools import (
        FETCH_DOCUMENT_DEFINITION,
        LIST_DOCUMENTS_DEFINITION,
        create_fetch_handler,
        create_list_handler,
    )
    from colaig.agents.tools.summarize_tools import (
        SUMMARIZE_TEXT_DEFINITION,
        create_summarize_handler,
    )

    registry = ToolRegistry()
    registry.register(SEARCH_DOCUMENTS_DEFINITION, create_search_handler(retriever))
    registry.register(FETCH_DOCUMENT_DEFINITION, create_fetch_handler(storage, workspace))
    registry.register(LIST_DOCUMENTS_DEFINITION, create_list_handler(storage, workspace))
    registry.register(SUMMARIZE_TEXT_DEFINITION, create_summarize_handler(albert))

    # N1 — search_skill : requête sémantique (ou keyword) dans .colaig/skills/
    if workspace is not None and workspace.storage_path:
        from colaig.agents.tools.skill_tools import (
            SEARCH_SKILL_DEFINITION,
            create_search_skill_handler,
        )
        registry.register(
            SEARCH_SKILL_DEFINITION,
            create_search_skill_handler(
                storage=storage,
                workspace_path=workspace.storage_path,
                albert=albert,
                index_registry=index_registry,
            ),
        )

    # Outils DocumentIndex — enregistrés uniquement si le service est disponible
    if document_index is not None and workspace is not None:
        from colaig.agents.tools.document_index_tools import (
            SEARCH_DOCUMENT_INDEX_DEFINITION,
            LIST_DOCUMENT_INDEX_DEFINITION,
            GET_DOCUMENT_METADATA_DEFINITION,
            GET_CLASSIFIED_DOCUMENTS_DEFINITION,
            create_search_document_index_handler,
            create_list_document_index_handler,
            create_get_document_metadata_handler,
            create_get_classified_documents_handler,
        )
        ws_path = workspace.storage_path
        registry.register(
            SEARCH_DOCUMENT_INDEX_DEFINITION,
            create_search_document_index_handler(document_index, ws_path),
        )
        registry.register(
            LIST_DOCUMENT_INDEX_DEFINITION,
            create_list_document_index_handler(document_index, ws_path),
        )
        registry.register(
            GET_DOCUMENT_METADATA_DEFINITION,
            create_get_document_metadata_handler(document_index, ws_path),
        )
        registry.register(
            GET_CLASSIFIED_DOCUMENTS_DEFINITION,
            create_get_classified_documents_handler(document_index, ws_path),
        )

    # Outil assess_completion — Phase 6 : dialogue Orchestrateur ↔ Synthétiseur
    if synthesiser is not None:
        from colaig.models import ToolDefinition, ToolParameter

        assess_definition = ToolDefinition(
            name="assess_completion",
            description=(
                "Évalue si les informations collectées sont suffisantes pour répondre "
                "à la question. Appelle le Synthétiseur pour un verdict structuré. "
                "Utilise cet outil avant chaque itération pour décider de continuer ou d'arrêter."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="La question originale ou reformulée de l'utilisateur.",
                    required=True,
                ),
                ToolParameter(
                    name="collected_info",
                    type="string",
                    description="Résumé des informations collectées jusqu'ici (résultats RAG, tools).",
                    required=False,
                ),
                ToolParameter(
                    name="criteria",
                    type="string",
                    description="Critères de complétude attendus (depuis les directives de l'Analyseur).",
                    required=False,
                ),
            ],
            category="meta",
        )

        _synthesiser = synthesiser

        async def _assess_handler(
            query: str = "",
            collected_info: str = "",
            criteria: str = "",
            **kwargs,
        ) -> dict:
            signal = await _synthesiser.assess(
                query=query,
                collected_info=collected_info,
                criteria=criteria,
            )
            return {
                "sufficient": signal.sufficient,
                "confidence": signal.confidence,
                "missing_elements": signal.missing_elements,
                "suggested_directions": signal.suggested_directions,
                "reason": signal.reason,
            }

        registry.register(assess_definition, _assess_handler)

    return registry


def build_background_tool_registry(
    storage,
    task,
    session_state,
    all_workspaces: list,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    messaging=None,
    workspace_stores: Optional[dict] = None,
    bm25_stores: Optional[dict] = None,
    conversation_memory=None,
    workspace_directory=None,
) -> ToolRegistry:
    """Construit le ToolRegistry pour une session de tâche autonome (Mode C).

    Tools enregistrés :
        - search_documents, fetch_document, list_documents, summarize_text
          (tools RAG standard sur le workspace personnel)
        - ask_workspace  (cross-workspace RAG, avec ACL)
        - find_workspace (découverte sémantique de workspaces fédérés, si workspace_directory)
        - run_subtask    (sous-agent pipeline complet dans un workspace cible)
        - update_plan    (heartbeat + current/plan.json)
        - report_to_user (livraison messaging)
        - create_document (livraison document)
        - assess_completion (si synthesiser fourni)

    Args:
        storage          : StorageProtocol.
        task             : TaskDefinition de la session courante.
        session_state    : TaskSessionState (heartbeat, compteurs).
        all_workspaces   : Liste complète des workspaces (pour ask_workspace + run_subtask).
        analyser/orchestrator/synthesiser : Pipeline agents (pour run_subtask + assess).
        retriever/generator : Fallback Phase 1 (pour run_subtask).
        messaging        : MessagingProtocol (pour report_to_user).
        workspace_stores : dict workspace_id → FaissStore.
        bm25_stores      : dict workspace_id → BM25Store.
        conversation_memory : ConversationMemory (pour run_subtask).

    Returns:
        ToolRegistry prêt pour l'orchestrateur en session de tâche.
    """
    from colaig.agents.tools.rag_tools import (
        SEARCH_DOCUMENTS_DEFINITION,
        create_search_handler,
    )
    from colaig.agents.tools.storage_tools import (
        FETCH_DOCUMENT_DEFINITION,
        LIST_DOCUMENTS_DEFINITION,
        create_fetch_handler,
        create_list_handler,
    )
    from colaig.agents.tools.summarize_tools import (
        SUMMARIZE_TEXT_DEFINITION,
        create_summarize_handler,
    )
    from colaig.agents.tools.delegate_tools import (
        ASK_WORKSPACE_DEFINITION,
        create_ask_workspace_handler,
    )
    from colaig.agents.tools.task_tools import (
        RUN_SUBTASK_DEFINITION,
        UPDATE_PLAN_DEFINITION,
        REPORT_TO_USER_DEFINITION,
        CREATE_DOCUMENT_DEFINITION,
        PAUSE_AND_ASK_USER_DEFINITION,
        create_run_subtask_handler,
        create_update_plan_handler,
        create_report_to_user_handler,
        create_document_handler,
        create_pause_handler,
    )

    # Workspace personnel du user (workspace de la tâche)
    ws = next((w for w in all_workspaces if w.storage_path == task.workspace_path), None)

    registry = ToolRegistry()

    # ── Tools RAG standard ──────────────────────────────────────────────────
    if retriever:
        registry.register(SEARCH_DOCUMENTS_DEFINITION, create_search_handler(retriever))
    if storage and ws:
        registry.register(FETCH_DOCUMENT_DEFINITION, create_fetch_handler(storage, ws))
        registry.register(LIST_DOCUMENTS_DEFINITION, create_list_handler(storage, ws))

    # ── summarize_text (si un agent albert est disponible) ──────────────────
    # On récupère le client Albert depuis l'orchestrateur ou l'analyseur
    albert = None
    if orchestrator and hasattr(orchestrator, "_albert"):
        albert = orchestrator._albert
    elif analyser and hasattr(analyser, "_albert"):
        albert = analyser._albert
    if albert:
        registry.register(SUMMARIZE_TEXT_DEFINITION, create_summarize_handler(albert))

    # ── ask_workspace (cross-workspace RAG) ─────────────────────────────────
    registry.register(
        ASK_WORKSPACE_DEFINITION,
        create_ask_workspace_handler(
            user_id=task.user_id,
            all_workspaces=all_workspaces,
            retriever=retriever,
            workspace_stores=workspace_stores,
            bm25_stores=bm25_stores,
            workspace_directory=workspace_directory,
        ),
    )

    # ── find_workspace (découverte sémantique de workspaces fédérés) ────────
    if workspace_directory is not None and workspace_directory.is_loaded():
        from colaig.agents.tools.delegate_tools import (
            FIND_WORKSPACE_DEFINITION,
            create_find_workspace_handler,
        )
        registry.register(
            FIND_WORKSPACE_DEFINITION,
            create_find_workspace_handler(
                workspace_directory,
                user_id=task.user_id,
                all_workspaces=all_workspaces,
                auth_enabled=True,
            ),
        )

    # ── run_subtask (sous-agent pipeline complet) ───────────────────────────
    registry.register(
        RUN_SUBTASK_DEFINITION,
        create_run_subtask_handler(
            user_id=task.user_id,
            all_workspaces=all_workspaces,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
            retriever=retriever,
            generator=generator,
            conversation_memory=conversation_memory,
            storage=storage,
            task=task,
            session_state=session_state,
        ),
    )

    # ── update_plan (heartbeat + plan.json) ─────────────────────────────────
    registry.register(
        UPDATE_PLAN_DEFINITION,
        create_update_plan_handler(storage=storage, task=task, session_state=session_state),
    )

    # ── report_to_user (livraison messaging) ────────────────────────────────
    if messaging and task.delivery_type == "messaging":
        registry.register(
            REPORT_TO_USER_DEFINITION,
            create_report_to_user_handler(
                messaging=messaging,
                delivery_target=task.delivery_target,
            ),
        )

    # ── create_document (livraison document) ────────────────────────────────
    if task.delivery_type == "document":
        registry.register(
            CREATE_DOCUMENT_DEFINITION,
            create_document_handler(storage=storage),
        )

    # ── pause_and_ask_user (human-in-the-loop) ──────────────────────────────
    if messaging:
        registry.register(
            PAUSE_AND_ASK_USER_DEFINITION,
            create_pause_handler(
                storage=storage,
                task=task,
                session_state=session_state,
                messaging=messaging,
            ),
        )

    # ── assess_completion (Phase 6) ─────────────────────────────────────────
    if synthesiser is not None:
        from colaig.models import ToolDefinition, ToolParameter

        assess_definition = ToolDefinition(
            name="assess_completion",
            description=(
                "Évalue si les informations collectées sont suffisantes pour répondre "
                "à l'objectif de la tâche."
            ),
            parameters=[
                ToolParameter(name="query", type="string", description="L'objectif de la tâche.", required=True),
                ToolParameter(name="collected_info", type="string", description="Résumé des infos collectées.", required=False),
                ToolParameter(name="criteria", type="string", description="Critères de complétude.", required=False),
            ],
            category="meta",
        )
        _synthesiser = synthesiser

        async def _assess_handler(query="", collected_info="", criteria="", **kwargs):
            signal = await _synthesiser.assess(query=query, collected_info=collected_info, criteria=criteria)
            return {
                "sufficient": signal.sufficient,
                "confidence": signal.confidence,
                "missing_elements": signal.missing_elements,
                "reason": signal.reason,
            }

        registry.register(assess_definition, _assess_handler)

    return registry


async def _load_workspace_skills(storage, workspace_path: str) -> list[dict]:
    """Charge les fichiers .colaig/skills/*.md comme knowledge.

    Chaque fichier devient un dict {"name": "filename", "content": "..."}.

    Returns:
        Liste de skills chargés. Vide si pas de dossier skills.
    """
    skills_dir = f"{workspace_path.rstrip('/')}/.colaig/skills/"
    skills = []

    try:
        files = await storage.list_files(skills_dir)
        for f in files:
            if f.is_directory or not f.name.endswith(".md"):
                continue
            try:
                content = await storage.download(f.path)
                text = content.decode("utf-8").strip()
                if text:
                    skills.append({
                        "name": f.name.removesuffix(".md"),
                        "content": text,
                    })
            except Exception:
                logger.warning("impossible de charger le skill: %s", f.path)
    except Exception:
        pass  # Pas de dossier skills = pas de skills, c'est normal

    return skills
