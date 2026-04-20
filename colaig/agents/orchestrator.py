"""
Colaig — Agent Orchestrateur

Implémente OrchestratorProtocol.
Deuxième maillon du pipeline Phase 2+.

Mode agentique (si albert + tool_registry fournis) :
- Boucle LLM avec tool calling (format OpenAI)
- L'Orchestrateur décide quels outils appeler, observe les résultats, itère
- Max iterations configurable, dernier tour force tool_choice="none"

Mode déterministe (fallback, identique à Phase 4) :
- Coordination pure, zéro appel LLM
- Planification basée sur les directives de l'Analyseur
- Backward compatible : Orchestrator(storage, retriever) → mode déterministe

Phase 6 :
- model : modèle LLM explicite (albert_model_chat = gpt-oss-120b)
- reporter : ProgressReporter pour les mises à jour temps réel
- pre_exec : PreExecutionCard optionnelle — injecte les chunks pré-récupérés
- SearchDirectives : enrichit le prompt avec objective + completeness_criteria
- assess_completion : tool Synthétiseur déclenché depuis la boucle agentique
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Awaitable

from colaig.context.workspace import personal_workspace_path as _personal_ws_path
from colaig.exceptions import OrchestrationError
from colaig.models import (
    AgentContext,
    ContextCard,
    ContextMode,
    ExecutionPlan,
    ExecutionStep,
    Intent,
    IntentType,
    PreExecutionCard,
    ToolCall,
    ToolResult,
    WorkspaceContext,
)
from colaig.agents.context_builder import build_agent_context

logger = logging.getLogger(__name__)

# Système prompt par défaut pour l'orchestrateur agentique
ORCHESTRATOR_SYSTEM_TEMPLATE = """\
Tu es l'Orchestrateur de Colaig, un agent IA d'administration publique.

Ta mission : collecter les informations nécessaires pour répondre à la demande de l'utilisateur.
Tu as accès aux outils suivants. Utilise-les de manière séquentielle et efficace.
Quand tu as suffisamment d'information, cesse d'appeler des outils et fournis un résumé de ce que tu as trouvé.

Intention de l'utilisateur : {intent_type}
Requête : {query}
{directives_text}{objective_text}{completeness_text}{assess_hint}
"""


class Orchestrator:
    """Agent Orchestrateur — boucle agentique ou coordination déterministe.

    Si `albert` et `tool_registry` sont fournis : mode agentique.
    Sinon : mode déterministe (Phase 4, backward compatible).

    Args:
        storage: Backend de stockage (StorageProtocol).
        retriever: Service de recherche RAG (RetrieverProtocol).
        albert: Client Albert API (AlbertClientProtocol) — requis pour mode agentique.
        tool_registry: Registre des outils (ToolRegistry) — requis pour mode agentique.
        max_iterations: Nombre max d'itérations de la boucle agentique (défaut : 5).
        temperature: Température LLM pour l'orchestrateur agentique.
        on_step_complete: Callback optionnel appelé après chaque step.
    """

    def __init__(
        self,
        storage,
        retriever,
        albert=None,
        tool_registry=None,
        max_iterations: int = 5,
        temperature: float = 0.1,
        on_step_complete: Optional[Callable[[ExecutionStep, ExecutionPlan], Awaitable[None]]] = None,
        workspace_stores=None,     # dict[workspace_id, FaissStore] — isolation par workspace
        model: Optional[str] = None,   # Phase 6 : modèle LLM explicite (ex: gpt-oss-120b)
        reporter=None,                 # Phase 6 : ProgressReporter — mises à jour temps réel
        workspace_resolver=None,       # ContextResolver — source de workspaces (ask_workspace ACL)
        bm25_stores=None,              # dict[workspace_id, BM25Store] — pour ask_workspace hybrid
        index_registry=None,           # FaissIndexRegistry — auto-discovery intra-fédération
        workspace_directory=None,      # WorkspaceDirectory — routage sémantique cross-workspace
    ) -> None:
        self._storage = storage
        self._retriever = retriever
        self._albert = albert
        self._tool_registry = tool_registry
        self._max_iterations = max_iterations
        self._temperature = temperature
        self._on_step_complete = on_step_complete
        self._workspace_stores = workspace_stores  # dict[str, FaissStore] | None
        self._model = model
        self._reporter = reporter
        self._workspace_resolver = workspace_resolver  # .workspaces toujours frais pour ACL
        self._bm25_stores = bm25_stores               # dict[str, BM25Store] | None
        self._index_registry = index_registry          # FaissIndexRegistry | None
        self._workspace_directory = workspace_directory  # WorkspaceDirectory | None

    @property
    def is_agentic(self) -> bool:
        """True si le mode agentique est disponible."""
        return self._albert is not None and self._tool_registry is not None

    async def execute(
        self,
        intent: Intent,
        context: WorkspaceContext,
        pre_exec: Optional[PreExecutionCard] = None,
        reporter=None,
    ) -> ExecutionPlan:
        """Planifie et exécute les étapes pour répondre à l'intention.

        Dispatche vers le mode agentique ou déterministe selon la configuration.

        Args:
            intent: Intention analysée (avec directives).
            context: Contexte résolu (workspace, mode, etc.).
            pre_exec: PreExecutionCard (Phase 6) — chunks pré-récupérés, skills, behavior.
            reporter: ProgressReporter per-call (prioritaire sur self._reporter).

        Returns:
            ExecutionPlan complet avec tous les résultats.
        """
        if self.is_agentic:
            return await self._execute_agentic(intent, context, pre_exec, reporter)
        return await self._execute_deterministic(intent, context, pre_exec)

    # ------------------------------------------------------------------
    # Mode agentique — boucle LLM + tool calling

    async def _execute_agentic(
        self,
        intent: Intent,
        context: WorkspaceContext,
        pre_exec: Optional[PreExecutionCard] = None,
        reporter=None,
    ) -> ExecutionPlan:
        """Boucle agentique LLM-driven avec tool calling natif OpenAI.

        1. Construit les messages initiaux (system + intent + search_directives)
        2. Injecte les chunks pré-récupérés depuis pre_exec (Phase 6)
        3. Filtre les tools disponibles selon le rôle + workspace
        4. Boucle : appel Albert → tool_calls → exécution → feed results → repeat
        5. Stop si texte ou max_iterations atteint
        6. Dernier tour : tool_choice="none" pour forcer réponse finale
        """
        # reporter per-call prioritaire sur self._reporter (évite la contamination multi-room)
        _reporter = reporter or self._reporter

        start = time.monotonic()

        agent_ctx = await build_agent_context(
            self._storage,
            context.workspace,
            "orchestrator",
            directives=intent.orchestrator_directives,
            selected_skills=pre_exec.selected_skills if pre_exec else None,
        )

        plan = ExecutionPlan(intent=intent)

        # Phase 6 : injecter les chunks pré-récupérés par PreExecutionBuilder
        if pre_exec and hasattr(pre_exec, "retrieval_results"):
            self._inject_pre_exec_results(pre_exec, plan)

        # Outils disponibles filtrés par rôle et workspace
        if pre_exec and pre_exec.available_tools:
            # Phase 6 : utiliser les tools filtrés par PreExecutionBuilder
            tool_names = [t.name for t in pre_exec.available_tools]
            available_tools = self._tool_registry.filter_by_names(tool_names)
        else:
            available_tools = self._tool_registry.filter_by_names(agent_ctx.available_tools)

        # Navigation contextuelle post-Intent : second filtrage basé sur l'intention analysée
        # Réduit le ToolRegistry au sous-ensemble pertinent (Principe 1 — Couche 1)
        available_tools = self._filter_registry_for_intent(available_tools, intent)

        # Isolation workspace : remplacer search_documents par un handler lié au store du workspace
        if self._workspace_stores and context.workspace:
            ws_store = self._workspace_stores.get(context.workspace.workspace_id)
            if ws_store is not None and available_tools.get("search_documents"):
                from colaig.agents.tools.rag_tools import SEARCH_DOCUMENTS_DEFINITION, create_search_handler
                available_tools.register(
                    SEARCH_DOCUMENTS_DEFINITION,
                    create_search_handler(self._retriever, store=ws_store),
                )

        # Délégation inter-workspace : injecter ask_workspace avec le user_id courant.
        # Mode PERSONAL : l'agent DM peut interroger tous les workspaces accessibles.
        # Mode ASSISTANT : disponible si le workspace déclare des mcp_connectors (inter-fédération)
        #                  ou si index_registry permet l'auto-discovery (intra-fédération).
        _ws_has_connectors = (
            context.mode == ContextMode.ASSISTANT
            and context.workspace is not None
            and bool(context.workspace.mcp_connectors)
        )
        if (
            (context.mode == ContextMode.PERSONAL or _ws_has_connectors)
            and context.user_id
            and self._workspace_resolver is not None
            and "ask_workspace" in (agent_ctx.available_tools or [])
        ):
            from colaig.agents.tools.delegate_tools import (
                ASK_WORKSPACE_DEFINITION,
                create_ask_workspace_handler,
            )
            available_tools.register(
                ASK_WORKSPACE_DEFINITION,
                create_ask_workspace_handler(
                    user_id=context.user_id,
                    all_workspaces=self._workspace_resolver.workspaces,  # toujours frais
                    retriever=self._retriever,
                    workspace_stores=self._workspace_stores,
                    bm25_stores=self._bm25_stores,
                    calling_workspace=context.workspace,            # niveau 3 : mcp_connectors
                    index_registry=self._index_registry,            # niveau 2 : auto-discovery
                    workspace_directory=self._workspace_directory,  # niveau 4 : fédération décentralisée
                ),
            )

        # Répertoire vectoriel : injecter find_workspace si disponible et user connu.
        # Permet à l'agent de découvrir les workspaces fédérés/publics sans connaître leurs IDs.
        if (
            (context.mode == ContextMode.PERSONAL or _ws_has_connectors)
            and context.user_id
            and self._workspace_directory is not None
            and self._workspace_directory.is_loaded()
            and "find_workspace" in (agent_ctx.available_tools or [])
        ):
            from colaig.agents.tools.delegate_tools import (
                FIND_WORKSPACE_DEFINITION,
                create_find_workspace_handler,
            )
            available_tools.register(
                FIND_WORKSPACE_DEFINITION,
                create_find_workspace_handler(
                    self._workspace_directory,
                    user_id=context.user_id,
                    all_workspaces=self._workspace_resolver.workspaces if self._workspace_resolver else [],
                    auth_enabled=bool(self._workspace_resolver),
                ),
            )

        # Mode C : injecter create_background_task en mode PERSONAL si le storage est disponible
        # Permet à l'utilisateur de créer des tâches planifiées directement depuis son DM.
        if (
            context.mode == ContextMode.PERSONAL
            and context.user_id
            and self._storage is not None
            and "create_background_task" in (agent_ctx.available_tools or [])
        ):
            from colaig.agents.tools.task_tools import (
                CREATE_BACKGROUND_TASK_DEFINITION,
                create_task_handler,
            )
            ws = context.workspace
            available_tools.register(
                CREATE_BACKGROUND_TASK_DEFINITION,
                create_task_handler(
                    storage=self._storage,
                    user_id=context.user_id,
                    workspace_path=ws.storage_path if ws else _personal_ws_path(context.user_id),
                    source_conversation_id=getattr(context, "conversation_id", ""),
                ),
            )

        # MCP Connectors — découverte dynamique des outils externes (Phase 6)
        # Pour chaque connector activé avec expose_tools=True dans le workspace,
        # appel tools/list → enregistrement dans available_tools.
        # C4 : appel initialize pour récupérer server_instructions → injecter dans le prompt.
        _mcp_instructions: list[str] = []
        if (
            context.workspace is not None
            and context.workspace.mcp_connectors
        ):
            from colaig.integrations.mcp_connector import MCPConnectorClient
            for connector in context.workspace.mcp_connectors:
                if not connector.enabled or not connector.expose_tools:
                    continue
                try:
                    client = MCPConnectorClient(connector)
                    external_tools = await client.list_tools()
                    for definition, handler in external_tools:
                        available_tools.register(definition, handler)
                    # C4 — server_instructions
                    instructions = await client.get_server_instructions()
                    if instructions:
                        _mcp_instructions.append(
                            f"### {connector.name}\n{instructions.strip()}"
                        )
                except Exception:
                    logger.warning(
                        "orchestrator: échec discovery outils MCP connector '%s'",
                        connector.name, exc_info=True,
                    )

        # C4 — Injecter les instructions des serveurs MCP dans le system prompt de l'agent
        if _mcp_instructions and agent_ctx is not None:
            agent_ctx.system_prompt += (
                "\n\n## Instructions des serveurs MCP connectés\n"
                + "\n\n".join(_mcp_instructions)
            )

        tool_schemas = available_tools.list_openai_schemas()

        if not tool_schemas or (intent.intent_type == IntentType.GREETING and not intent.needs_rag):
            # Pas d'outils ou salutation pure (sans RAG) → pas de boucle
            plan.execution_time_ms = int((time.monotonic() - start) * 1000)
            plan.context_card = self._build_context_card(context, plan)
            return plan

        # Phase 6 : rapport de début
        if _reporter:
            try:
                await _reporter.report("retrieving", "Recherche en cours...")
            except Exception:
                pass

        # Messages initiaux
        messages = self._build_initial_messages(intent, agent_ctx, pre_exec)

        _force_final = False  # Navigation contextuelle intra-boucle : assess_completion → fast-exit

        for iteration in range(self._max_iterations):
            # Dernier tour ou assess_completion sufficient=True → force réponse textuelle
            tool_choice = "none" if (iteration == self._max_iterations - 1 or _force_final) else "auto"

            _temperature = self._temperature
            if pre_exec and pre_exec.agent_overrides.get("orchestrator"):
                _temperature = pre_exec.agent_overrides["orchestrator"].get("temperature", _temperature)

            kwargs: dict = {
                "messages": messages,
                "tools": tool_schemas,
                "temperature": _temperature,
                "tool_choice": tool_choice,
            }
            if self._model:
                kwargs["model"] = self._model

            result = await self._albert.chat_with_tools(**kwargs)

            if not result.has_tool_calls:
                # L'agent a terminé — résumé textuel
                plan.orchestrator_reasoning = result.content
                break

            # Exécuter chaque tool call
            assistant_tool_calls_raw = []
            tool_results_messages = []
            _iteration_sufficient = False  # Flag assess_completion pour cette itération

            for tool_call in result.tool_calls:
                step = ExecutionStep(
                    step_type=tool_call.tool_name,
                    description=f"Tool call: {tool_call.tool_name}",
                    params=tool_call.arguments,
                    status="running",
                )
                plan.steps.append(step)

                # Phase 6 : rapport de tool use
                if _reporter:
                    try:
                        await _reporter.report_tool_use(tool_call.tool_name)
                    except Exception:
                        pass

                tool_result = await self._execute_tool_call(tool_call, available_tools, plan, context)

                # Navigation contextuelle intra-boucle : assess_completion sufficient → fast-exit
                if tool_call.tool_name == "assess_completion" and tool_result.success:
                    try:
                        assessment = (
                            json.loads(tool_result.result)
                            if isinstance(tool_result.result, str)
                            else tool_result.result
                        )
                        if isinstance(assessment, dict) and assessment.get("sufficient"):
                            _iteration_sufficient = True
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

                step.status = "done" if tool_result.success else "error"
                step.result = {"output": tool_result.result}
                if not tool_result.success:
                    step.error = tool_result.error

                # Callback
                if self._on_step_complete:
                    try:
                        await self._on_step_complete(step, plan)
                    except Exception:
                        pass

                # Construire le message tool_call OpenAI format
                assistant_tool_calls_raw.append({
                    "id": tool_call.call_id or f"call_{len(plan.steps)}",
                    "type": "function",
                    "function": {
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                })

                # Message de résultat
                content = tool_result.result if tool_result.success else f"Erreur : {tool_result.error}"
                tool_results_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.call_id or f"call_{len(plan.steps)}",
                    "content": content,
                })

            # Ajouter au thread de messages
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_tool_calls_raw,
            })
            messages.extend(tool_results_messages)

            # assess_completion a signalé sufficient=True → prochain appel = réponse finale
            if _iteration_sufficient:
                _force_final = True

        plan.execution_time_ms = int((time.monotonic() - start) * 1000)
        plan.context_card = self._build_context_card(context, plan)
        return plan

    def _filter_registry_for_intent(self, available_tools, intent: Intent):
        """Second filtrage post-Intent : navigation contextuelle (Principe 1 — Couche 1).

        Réduit le ToolRegistry au sous-ensemble pertinent selon l'intention analysée.
        Appelé après le filtrage large de PreExecutionBuilder (pré-intent).

        Règles appliquées :
        - needs_rag=False → retirer tous les outils de recherche documentaire
        - IntentType.SUMMARY → garder fetch_document + summarize_text uniquement
        - orchestrator_directives.tools_to_use spécifié → restreindre à cette liste
        - assess_completion toujours conservé (méta-outil de contrôle de boucle)

        Args:
            available_tools: ToolRegistry après filtrage pré-intent.
            intent: Intent produit par l'Analyseur.

        Returns:
            ToolRegistry réduit au sous-ensemble pertinent.
        """
        SEARCH_TOOLS = {
            "search_documents", "search_document_index",
            "list_document_index", "list_documents", "get_classified_documents",
        }
        ALWAYS_KEEP = {"assess_completion"}

        # orchestrator_directives.tools_to_use explicite → liste définitive
        if intent.orchestrator_directives:
            explicit = getattr(intent.orchestrator_directives, "tools_to_use", None)
            if explicit:
                allowed = set(explicit) | ALWAYS_KEEP
                remaining = [n for n in available_tools.names() if n in allowed]
                return available_tools.filter_by_names(remaining)

        remove: set[str] = set()

        # Pas de RAG nécessaire → retirer tous les outils de recherche
        if not intent.needs_rag:
            remove.update(SEARCH_TOOLS)

        # Résumé → fetch + summarize suffisent, pas besoin d'exploration indexée
        if intent.intent_type == IntentType.SUMMARY:
            remove.update({"search_document_index", "list_document_index",
                           "get_document_metadata", "get_classified_documents"})

        if not remove:
            return available_tools

        remaining = [n for n in available_tools.names() if n not in remove]
        return available_tools.filter_by_names(remaining)

    def _inject_pre_exec_results(
        self, pre_exec: PreExecutionCard, plan: ExecutionPlan
    ) -> None:
        """Injecte les résultats pré-récupérés dans le plan (Phase 6)."""
        results = getattr(pre_exec, "retrieval_results", {})
        if not results:
            return
        chunks = results.get("chunks", [])
        if chunks:
            plan.search_results.extend(chunks)
            logger.debug("orchestrator: %d chunks pré-récupérés injectés", len(chunks))

    def _build_initial_messages(
        self,
        intent: Intent,
        agent_ctx: AgentContext,
        pre_exec: Optional[PreExecutionCard] = None,
    ) -> list[dict]:
        """Construit les messages initiaux pour la boucle agentique.

        Phase 6 : enrichit avec search_directives.objective + completeness_criteria
        depuis l'intent ou depuis le pre_exec.
        """
        directives_text = ""
        if intent.orchestrator_directives:
            d = intent.orchestrator_directives
            parts = []
            if d.search_strategy:
                parts.append(f"Stratégie de recherche : {d.search_strategy}")
            if d.resources_to_target:
                parts.append(f"Documents à cibler : {', '.join(d.resources_to_target)}")
            if d.instructions:
                parts.append(f"Instructions : {d.instructions}")
            if parts:
                directives_text = "\nDirectives :\n" + "\n".join(f"- {p}" for p in parts)

        # Phase 6 : objectif et critères de complétude depuis SearchDirectives
        objective_text = ""
        completeness_text = ""
        if intent.search_directives:
            sd = intent.search_directives
            if sd.objective:
                objective_text = f"\nObjectif : {sd.objective}"
            if sd.completeness_criteria:
                completeness_text = f"\nCritères de complétude : {sd.completeness_criteria}"

        # Phase 6 : hint assess_completion si le tool est disponible
        has_assess_tool = self._tool_registry and self._tool_registry.get("assess_completion") is not None
        assess_hint = (
            "\n\nTu peux appeler assess_completion à tout moment pour vérifier "
            "si les informations collectées sont suffisantes avant de continuer."
        ) if has_assess_tool else ""

        system = ORCHESTRATOR_SYSTEM_TEMPLATE.format(
            intent_type=intent.intent_type.value,
            query=intent.query_reformulated or "",
            directives_text=directives_text,
            objective_text=objective_text,
            completeness_text=completeness_text,
            assess_hint=assess_hint,
        )

        # Ajouter les skills du workspace si présents
        if agent_ctx.skills:
            skills_text = "\n\nRessources disponibles (workspace) :\n"
            for skill in agent_ctx.skills[:3]:  # Max 3 skills pour token budget
                skills_text += f"\n### {skill['name']}\n{skill['content'][:500]}\n"
            system += skills_text

        # Couche 0 — Artefacts déjà connus (Principe 0 : éviter les re-retrievals)
        # Les ContextAnchors de la trame signalent les documents trouvés aux tours précédents
        if pre_exec and pre_exec.context_anchors:
            doc_anchors = [
                a for a in pre_exec.context_anchors if a.anchor_type == "document"
            ]
            if doc_anchors:
                now = datetime.now(tz=timezone.utc)
                anchors_lines = "\n".join(
                    _anchor_line(a, now) for a in doc_anchors
                )
                system += (
                    "\n\nDocuments déjà identifiés lors des échanges précédents "
                    "(ne pas re-chercher, utiliser fetch_document si nécessaire) :\n"
                    + anchors_lines
                )

        # Override système si défini dans le workspace
        if agent_ctx.system_prompt:
            system = agent_ctx.system_prompt + "\n\n" + system

        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Intention : {intent.intent_type.value}\n"
                    f"Question : {intent.query_reformulated or ''}\n"
                    "Collecte les informations nécessaires via les outils disponibles."
                ),
            },
        ]

    async def _execute_tool_call(
        self,
        tool_call: ToolCall,
        available_tools,
        plan: ExecutionPlan,
        context: WorkspaceContext,
    ) -> ToolResult:
        """Exécute un tool call via le ToolRegistry.

        Pour les tools MCP externes (catégorie mcp_external), injecte le
        _session_id dans les arguments selon le session_scope du connector.
        """
        # Session ID pour les tools MCP externes
        entry = available_tools.get(tool_call.tool_name)
        if entry is not None:
            defn = entry[0]
            if defn.category == "mcp_external" and context.workspace:
                # Déterminer le session_id selon le scope configuré
                for connector in (context.workspace.mcp_connectors or []):
                    if tool_call.tool_name.startswith(f"{connector.name}__"):
                        if connector.session_scope == "conversation":
                            tool_call.arguments["_session_id"] = getattr(
                                context, "conversation_id", ""
                            )
                        elif connector.session_scope == "user":
                            tool_call.arguments["_session_id"] = context.user_id or ""
                        break

        tool_result = await available_tools.execute(tool_call)

        # Accumuler les résultats RAG dans plan.search_results
        if tool_call.tool_name == "search_documents" and tool_result.success:
            try:
                raw = json.loads(tool_result.result)
                if isinstance(raw, list):
                    # Convertir en SearchResult-like pour compatibilité synthesiser
                    plan.tool_results.append({
                        "tool": "search_documents",
                        "query": tool_call.arguments.get("query", ""),
                        "results": raw,
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        elif tool_result.success:
            plan.tool_results.append({
                "tool": tool_call.tool_name,
                "arguments": tool_call.arguments,
                "result": tool_result.result,
            })

        return tool_result

    # ------------------------------------------------------------------
    # Mode déterministe — backward compatible Phase 4

    async def _execute_deterministic(
        self,
        intent: Intent,
        context: WorkspaceContext,
        pre_exec: Optional[PreExecutionCard] = None,
    ) -> ExecutionPlan:
        """Coordination pure basée sur les directives de l'Analyseur.

        Code identique à la Phase 4 — zéro appel LLM.
        Utilisé quand albert ou tool_registry ne sont pas fournis.
        """
        start = time.monotonic()

        agent_ctx = await build_agent_context(
            self._storage,
            context.workspace,
            "orchestrator",
            directives=intent.orchestrator_directives,
        )

        steps = self._plan_steps(intent, agent_ctx)
        plan = ExecutionPlan(intent=intent, steps=steps)

        for step in plan.steps:
            step.status = "running"
            try:
                await self._execute_step_deterministic(step, plan, context)
                step.status = "done"
            except Exception as e:
                step.status = "error"
                step.error = str(e)
                logger.warning("step %s échoué: %s", step.step_type, e)

            if self._on_step_complete:
                try:
                    await self._on_step_complete(step, plan)
                except Exception:
                    pass

        plan.execution_time_ms = int((time.monotonic() - start) * 1000)
        plan.context_card = self._build_context_card(context, plan)
        return plan

    def _plan_steps(self, intent: Intent, agent_ctx: AgentContext) -> list[ExecutionStep]:
        """Planifie les étapes selon l'intention et les directives (mode déterministe)."""
        steps: list[ExecutionStep] = []

        if intent.intent_type == IntentType.GREETING and not intent.needs_rag:
            return steps

        if intent.needs_rag:
            strategy = ""
            if intent.orchestrator_directives:
                strategy = intent.orchestrator_directives.search_strategy
            steps.append(ExecutionStep(
                step_type="rag_search",
                description="Recherche documentaire RAG",
                params={"query": intent.query_reformulated or "", "strategy": strategy},
            ))

        if intent.orchestrator_directives:
            resources = intent.orchestrator_directives.resources_to_target
            if resources:
                steps.append(ExecutionStep(
                    step_type="storage_fetch",
                    description="Récupération de documents ciblés",
                    params={"resources": resources},
                ))

        if intent.needs_tools and intent.orchestrator_directives:
            tools = intent.orchestrator_directives.tools_to_use
            for tool_name in tools:
                if tool_name not in ("search_documents", "summarize_text", "fetch_document", "list_documents"):
                    steps.append(ExecutionStep(
                        step_type="mcp_tool",
                        description=f"Exécution tool: {tool_name}",
                        params={"tool": tool_name},
                    ))

        return steps

    async def _execute_step_deterministic(
        self,
        step: ExecutionStep,
        plan: ExecutionPlan,
        context: WorkspaceContext,
    ) -> None:
        """Exécute une étape du plan (mode déterministe)."""
        if step.step_type == "rag_search":
            await self._execute_rag_search(step, plan, context)
        elif step.step_type == "storage_fetch":
            await self._execute_storage_fetch(step, plan, context)
        elif step.step_type == "mcp_tool":
            await self._execute_mcp_tool_placeholder(step, plan)
        else:
            step.error = f"type de step inconnu: {step.step_type}"
            step.status = "error"

    async def _execute_rag_search(
        self, step: ExecutionStep, plan: ExecutionPlan, context: WorkspaceContext
    ) -> None:
        """Exécute une recherche RAG."""
        query = step.params.get("query", "")
        if not query:
            step.result = {"count": 0, "results": []}
            return

        k = context.workspace.max_results if context.workspace else 5
        threshold = context.workspace.similarity_threshold if context.workspace else 0.3

        retrieve_kwargs: dict = dict(query=query, k=k, score_threshold=threshold)
        # Isolation par workspace : utiliser le store spécifique si disponible
        if self._workspace_stores and context.workspace:
            ws_store = self._workspace_stores.get(context.workspace.workspace_id)
            if ws_store is not None:
                retrieve_kwargs["store"] = ws_store
        results = await self._retriever.retrieve(**retrieve_kwargs)
        plan.search_results.extend(results)
        step.result = {
            "count": len(results),
            "sources": list({r.chunk.source_name for r in results}),
        }

    async def _execute_storage_fetch(
        self, step: ExecutionStep, plan: ExecutionPlan, context: WorkspaceContext
    ) -> None:
        """Récupère des fichiers spécifiques depuis le storage."""
        resources = step.params.get("resources", [])
        fetched = []
        for resource_path in resources:
            if context.workspace and not resource_path.startswith("/"):
                full_path = f"{context.workspace.storage_path.rstrip('/')}/{resource_path}"
            else:
                full_path = resource_path
            try:
                exists = await self._storage.exists(full_path)
                fetched.append({"path": full_path, "status": "found" if exists else "not_found"})
            except Exception as e:
                fetched.append({"path": full_path, "status": "error", "error": str(e)})
        step.result = {"fetched": fetched}

    async def _execute_mcp_tool_placeholder(
        self, step: ExecutionStep, plan: ExecutionPlan
    ) -> None:
        """Placeholder pour les tools MCP non-built-in."""
        tool_name = step.params.get("tool", "")
        step.result = {
            "tool": tool_name,
            "status": "not_implemented",
            "message": f"MCP tool '{tool_name}' — non disponible dans ce mode",
        }
        plan.tool_results.append(step.result)

    def _build_context_card(
        self, context: WorkspaceContext, plan: ExecutionPlan
    ) -> ContextCard:
        """Construit la ContextCard à partir des résultats du plan."""
        sources = []
        for r in plan.search_results:
            name = r.chunk.source_name
            if name and name not in sources:
                sources.append(name)

        phases = []
        for step in plan.steps:
            if step.step_type in ("rag_search", "search_documents"):
                phases.append("retrieving")
            elif step.step_type in ("storage_fetch", "mcp_tool", "fetch_document", "list_documents"):
                phases.append("executing")

        return ContextCard(
            mode=context.mode.value if context.mode else "",
            workspace_id=context.workspace.workspace_id if context.workspace else "",
            workspace_name=context.workspace.name if context.workspace else "",
            available_tools=context.available_tools,
            sources_used=sources,
            pipeline_phases=phases,
            confidence=(
                sum(r.score for r in plan.search_results) / len(plan.search_results)
                if plan.search_results else 0.0
            ),
        )


# =============================================================================
# Helpers module-level
# =============================================================================

def _anchor_line(anchor, now: datetime) -> str:
    """Formate une ligne d'anchor pour le prompt orchestrateur, avec étiquette temporelle."""
    line = f"- {anchor.ref}"
    if anchor.description:
        line += f" : {anchor.description}"
    if anchor.established_at is not None:
        try:
            ts = anchor.established_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta_s = (now - ts).total_seconds()
            if delta_s < 90:
                time_label = "à l'instant"
            elif delta_s < 3600:
                time_label = f"il y a {int(delta_s / 60)} min"
            elif delta_s < 86400:
                time_label = f"il y a {int(delta_s / 3600)}h"
            elif delta_s < 172800:
                time_label = "hier"
            else:
                time_label = f"il y a {int(delta_s / 86400)} jours"
            line += f" ({time_label})"
        except Exception:
            pass
    return line

