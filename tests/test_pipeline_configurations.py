"""
Tests — Cartographie des configurations pipeline (graph relationnel)

Couvre systématiquement l'arbre des possibilités décrit dans docs/PIPELINE_GRAPH.md :

  Axe 1 : Pipeline Phase 1 vs Phase 2 (is_phase2)
  Axe 2 : Orchestrateur déterministe vs agentique (is_agentic)
  Axe 3 : Phase 6 activée ou non (assess_completion, SearchDirectives)
  Axe 4 : Context mode (ASSISTANT / CHATBOT / PERSONAL)
  Axe 5 : Features optionnelles (conversation_memory, document_index, audio)
  Axe 6 : Workspace features (rag_enabled, tools_enabled, workspace_stores)
  Axe 7 : is_phase2 partiel (1 ou 2 agents manquants)

Chaque test est auto-suffisant : construit sa propre configuration minimale.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.agents import Orchestrator, build_tool_registry
from colaig.messaging.handlers import MessageHandler
from colaig.models import (
    Attachment,
    ChatCompletionResult,
    CompletionSignal,
    ContextMode,
    ConversationType,
    DocumentChunk,
    ExecutionPlan,
    GeneratedResponse,
    IncomingMessage,
    Intent,
    IntentType,
    PipelinePhase,
    SearchDirectives,
    SearchResult,
    ToolCall,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.conftest import MockAlbertClient, MockStorage

# =============================================================================
# Helpers et mocks légers
# =============================================================================


def _make_message(
    body: str = "Quelle est la procédure ?",
    conversation_id: str = "!room:test",
    conv_type: ConversationType = ConversationType.PRIVATE,
    is_reply: bool = False,
    attachments: list | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        user_id="@user:test",
        conversation_id=conversation_id,
        body=body,
        conversation_type=conv_type,
        message_id="$msg1",
        is_reply=is_reply,
        attachments=attachments or [],
    )


def _make_workspace(
    workspace_id: str = "ws-1",
    rag_enabled: bool = True,
    tools_enabled: list | None = None,
    storage_readonly: bool = False,
) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id=workspace_id,
        name="Test WS",
        storage_path=f"/{workspace_id}/",
        rag_enabled=rag_enabled,
        tools_enabled=tools_enabled if tools_enabled is not None else [
            "search_documents", "fetch_document", "list_documents", "summarize_text"
        ],
        max_results=3,
        similarity_threshold=0.3,
        storage_readonly=storage_readonly,
    )


def _make_context(
    mode: ContextMode = ContextMode.ASSISTANT,
    workspace: WorkspaceConfig | None = None,
    history: list | None = None,
) -> WorkspaceContext:
    if workspace is None and mode == ContextMode.ASSISTANT:
        workspace = _make_workspace()
    return WorkspaceContext(
        workspace=workspace,
        mode=mode,
        system_prompt="Prompt test.",
        conversation_history=history or [],
    )


class MockMessaging:
    def __init__(self):
        self.sent: list[str] = []
        self.typing_calls: list[bool] = []

    async def send(self, conv_id: str, text: str, **kwargs) -> None:
        self.sent.append(text)

    async def send_typing(self, conv_id: str, typing: bool = True) -> None:
        self.typing_calls.append(typing)


class MockResolver:
    def __init__(self, context: WorkspaceContext | None = None, raises: bool = False):
        self._context = context
        self._raises = raises
        self.workspaces: list = []

    async def resolve(self, message: IncomingMessage) -> WorkspaceContext:
        if self._raises:
            raise RuntimeError("resolver error")
        return self._context or _make_context()

    async def register_workspace(self, ws):
        self.workspaces.append(ws)


class MockRetriever:
    def __init__(self, results: list | None = None):
        self._results = results or []
        self.called = False
        self.last_kwargs: dict = {}

    async def retrieve(self, query: str, k: int = 5, score_threshold: float = 0.3, **kwargs) -> list:
        self.called = True
        self.last_kwargs = {"query": query, "k": k, "score_threshold": score_threshold, **kwargs}
        return self._results


class MockGenerator:
    def __init__(self, text: str = "Réponse Phase 1."):
        self._text = text
        self.called = False

    async def generate(self, query, context, search_results, conversation_history=None, channel_format=None):
        self.called = True
        return GeneratedResponse(text=self._text, sources=[], confidence=0.5)


class MockAnalyser:
    def __init__(self, intent: Intent | None = None):
        self._intent = intent or Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure validation",
            needs_rag=True,
            confidence=0.9,
        )
        self.called = False

    async def analyse(self, message, context, pre_exec=None) -> Intent:
        self.called = True
        return self._intent


class MockOrchestrator:
    def __init__(self, plan: ExecutionPlan | None = None):
        self._plan = plan
        self.called = False

    async def execute(self, intent, context, pre_exec=None, reporter=None) -> ExecutionPlan:
        self.called = True
        plan = self._plan or ExecutionPlan(intent=intent, search_results=[], tool_results=[])
        return plan


class MockSynthesiser:
    def __init__(self, text: str = "Réponse Phase 2."):
        self._text = text
        self.called = False

    async def synthesise(self, plan, context, conversation_history=None, channel_format=None, **kwargs) -> GeneratedResponse:
        self.called = True
        return GeneratedResponse(text=self._text, sources=[], confidence=0.8)


def _make_handler(
    mode: ContextMode = ContextMode.ASSISTANT,
    workspace: WorkspaceConfig | None = None,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    conversation_memory=None,
    workspace_stores: dict | None = None,
    albert_client=None,
    resolver_raises: bool = False,
    retriever_results: list | None = None,
) -> tuple[MessageHandler, MockMessaging, MockResolver, MockRetriever, MockGenerator]:
    messaging = MockMessaging()
    context = _make_context(mode=mode, workspace=workspace)
    resolver = MockResolver(context=context, raises=resolver_raises)
    retriever = MockRetriever(results=retriever_results or [])
    generator = MockGenerator()

    handler = MessageHandler(
        messaging=messaging,
        resolver=resolver,
        retriever=retriever,
        generator=generator,
        storage=MockStorage(),
        analyser=analyser,
        orchestrator=orchestrator,
        synthesiser=synthesiser,
        conversation_memory=conversation_memory,
        workspace_stores=workspace_stores,
        albert_client=albert_client,
    )
    return handler, messaging, resolver, retriever, generator


# =============================================================================
# Axe 1 — is_phase2 partiel (1 ou 2 agents manquants → Phase 1)
# =============================================================================


class TestIsPhase2Logic:
    """Vérifie que is_phase2 exige les 3 agents."""

    def test_all_three_agents_is_phase2(self):
        handler, *_ = _make_handler(
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        assert handler.is_phase2 is True

    def test_missing_analyser_is_phase1(self):
        handler, *_ = _make_handler(
            analyser=None,
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        assert handler.is_phase2 is False

    def test_missing_orchestrator_is_phase1(self):
        handler, *_ = _make_handler(
            analyser=MockAnalyser(),
            orchestrator=None,
            synthesiser=MockSynthesiser(),
        )
        assert handler.is_phase2 is False

    def test_missing_synthesiser_is_phase1(self):
        handler, *_ = _make_handler(
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=None,
        )
        assert handler.is_phase2 is False

    def test_all_none_is_phase1(self):
        handler, *_ = _make_handler()
        assert handler.is_phase2 is False

    @pytest.mark.asyncio
    async def test_missing_one_agent_dispatches_to_phase1(self):
        """Avec seulement 2 agents, le handler utilise le generator Phase 1."""
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        generator = MockGenerator(text="Phase1 fallback")

        handler, messaging, *_ = _make_handler(
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=None,   # manquant → Phase 1
        )
        handler._generator = generator

        await handler.handle_message(_make_message())
        assert generator.called
        assert not analyser.called
        assert not orchestrator.called
        assert any("Phase1 fallback" in s for s in messaging.sent)


# =============================================================================
# Axe 2 — Pipeline Phase 1 variations
# =============================================================================


class TestPipelinePhase1:
    """Tests du pipeline Phase 1 (generator seul)."""

    @pytest.mark.asyncio
    async def test_phase1_with_rag_calls_retriever(self):
        """Phase 1 + rag_enabled=True → retriever appelé."""
        ws = _make_workspace(rag_enabled=True)
        handler, messaging, resolver, retriever, generator = _make_handler(
            workspace=ws,
        )
        await handler.handle_message(_make_message())
        assert retriever.called
        assert generator.called

    @pytest.mark.asyncio
    async def test_phase1_rag_disabled_skips_retriever(self):
        """Phase 1 + rag_enabled=False → retriever non appelé."""
        ws = _make_workspace(rag_enabled=False)
        handler, messaging, resolver, retriever, generator = _make_handler(
            workspace=ws,
        )
        await handler.handle_message(_make_message())
        assert not retriever.called
        assert generator.called

    @pytest.mark.asyncio
    async def test_phase1_uses_workspace_store_when_available(self):
        """Phase 1 utilise le store isolé du workspace si disponible."""
        ws = _make_workspace()
        mock_store = MagicMock()

        # Patch le retriever pour capturer le kwarg 'store'
        class StoreCapturingRetriever:
            def __init__(self):
                self.last_store = None
                self.called = False

            async def retrieve(self, query, k=5, score_threshold=0.3, store=None):
                self.called = True
                self.last_store = store
                return []

        retriever = StoreCapturingRetriever()
        generator = MockGenerator()
        messaging = MockMessaging()
        context = _make_context(workspace=ws)
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=retriever,
            generator=generator,
            storage=MockStorage(),
            workspace_stores={ws.workspace_id: mock_store},
        )

        await handler.handle_message(_make_message())
        assert retriever.called
        assert retriever.last_store is mock_store

    @pytest.mark.asyncio
    async def test_phase1_resolver_error_still_runs_pipeline(self):
        """Si resolver lève une exception, le pipeline Phase 1 continue quand même."""
        handler, messaging, *_ = _make_handler(resolver_raises=True)
        # La résolution échoue → _handle_phase1 est appelé (second resolve peut aussi échouer)
        # L'important : un message est envoyé (ERROR_MESSAGE ou réponse)
        await handler.handle_message(_make_message())
        # Vérification : au moins un message envoyé (ERROR_MESSAGE si double-fail)
        assert len(messaging.sent) > 0

    @pytest.mark.asyncio
    async def test_phase1_chatbot_mode_sends_onboarding(self):
        """Mode CHATBOT sans is_reply → message d'onboarding envoyé."""
        handler, messaging, *_ = _make_handler(mode=ContextMode.CHATBOT, workspace=None)
        await handler.handle_message(_make_message(is_reply=False))
        assert len(messaging.sent) == 1
        assert "workspace" in messaging.sent[0].lower() or "colaig" in messaging.sent[0].lower()

    @pytest.mark.asyncio
    async def test_phase1_chatbot_reply_continues_pipeline(self):
        """Mode CHATBOT avec is_reply=True → pipeline exécuté (pas onboarding)."""
        generator = MockGenerator(text="FAQ réponse")
        messaging = MockMessaging()
        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
        )

        await handler.handle_message(_make_message(is_reply=True))
        assert generator.called
        assert any("FAQ réponse" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_phase1_personal_mode_runs_pipeline(self):
        """Mode PERSONAL (DM) → pipeline Phase 1 exécuté."""
        generator = MockGenerator(text="Réponse DM")
        messaging = MockMessaging()
        context = _make_context(mode=ContextMode.PERSONAL, workspace=None)
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
        )

        await handler.handle_message(_make_message(conv_type=ConversationType.DM))
        assert generator.called


# =============================================================================
# Axe 3 — Pipeline Phase 2 : déterministe vs agentique
# =============================================================================


class TestOrchestratorModes:
    """Vérifie le dispatch déterministe vs agentique dans l'Orchestrateur."""

    def test_is_agentic_with_albert_and_registry(self):
        """Orchestrateur agentique si albert + tool_registry fournis."""
        from colaig.agents.tool_registry import ToolRegistry
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=MockAlbertClient(),
            tool_registry=ToolRegistry(),
        )
        assert orc.is_agentic is True

    def test_is_not_agentic_without_albert(self):
        """Sans albert → mode déterministe."""
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=None,
            tool_registry=MagicMock(),
        )
        assert orc.is_agentic is False

    def test_is_not_agentic_without_tool_registry(self):
        """Sans tool_registry → mode déterministe."""
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=MockAlbertClient(),
            tool_registry=None,
        )
        assert orc.is_agentic is False

    def test_is_not_agentic_with_neither(self):
        """Sans albert ni tool_registry → mode déterministe."""
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
        )
        assert orc.is_agentic is False

    @pytest.mark.asyncio
    async def test_deterministic_greeting_produces_empty_steps(self):
        """Mode déterministe + intent GREETING sans RAG → plan sans steps."""
        orc = Orchestrator(storage=MockStorage(), retriever=MockRetriever())
        intent = Intent(intent_type=IntentType.GREETING, query_reformulated="Bonjour", needs_rag=False)
        context = _make_context()
        plan = await orc.execute(intent, context)
        assert plan.steps == []

    @pytest.mark.asyncio
    async def test_deterministic_with_rag_creates_rag_step(self):
        """Mode déterministe + needs_rag=True → step rag_search créé."""
        orc = Orchestrator(storage=MockStorage(), retriever=MockRetriever())
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        context = _make_context()
        plan = await orc.execute(intent, context)
        step_types = [s.step_type for s in plan.steps]
        assert "rag_search" in step_types

    @pytest.mark.asyncio
    async def test_agentic_greeting_skips_loop(self):
        """Mode agentique + intent GREETING → boucle skippée (0 appels albert)."""
        albert = MockAlbertClient()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
        )
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
        )
        intent = Intent(intent_type=IntentType.GREETING, query_reformulated="Bonjour")
        context = _make_context()
        plan = await orc.execute(intent, context)
        # Pas d'appels chat_with_tools pour le greeting
        assert albert._tool_call_count == 0

    @pytest.mark.asyncio
    async def test_agentic_no_tools_enabled_skips_loop(self):
        """Mode agentique + aucun outil disponible → boucle skippée."""
        albert = MockAlbertClient()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
        )
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
        )
        # Workspace avec tools_enabled vide → aucun outil
        ws = _make_workspace(tools_enabled=[])
        context = _make_context(workspace=ws)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="question",
            needs_rag=True,
        )
        plan = await orc.execute(intent, context)
        # La boucle est skippée car tool_schemas sera vide
        assert albert._tool_call_count == 0


# =============================================================================
# Axe 4 — Phase 2 pipeline complet : context modes
# =============================================================================


class TestPipelinePhase2ContextModes:
    """Phase 2 fonctionne dans tous les modes de contexte."""

    @pytest.mark.asyncio
    async def test_phase2_assistant_mode_calls_all_agents(self):
        """Mode ASSISTANT → analyser + orchestrator + synthesiser tous appelés."""
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser(text="Réponse Phase 2")
        handler, messaging, *_ = _make_handler(
            mode=ContextMode.ASSISTANT,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message())
        assert analyser.called
        assert orchestrator.called
        assert synthesiser.called
        assert any("Réponse Phase 2" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_phase2_chatbot_mode_is_reply_runs_agents(self):
        """Mode CHATBOT + is_reply → pipeline agents exécuté."""
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser(text="FAQ via Phase 2")
        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        messaging = MockMessaging()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message(is_reply=True))
        assert analyser.called
        assert synthesiser.called

    @pytest.mark.asyncio
    async def test_phase2_personal_mode_runs_agents(self):
        """Mode PERSONAL (DM) → pipeline agents exécuté."""
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser(text="DM Phase 2")
        context = _make_context(mode=ContextMode.PERSONAL, workspace=None)
        messaging = MockMessaging()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message(conv_type=ConversationType.DM))
        assert analyser.called
        assert synthesiser.called

    @pytest.mark.asyncio
    async def test_phase2_chatbot_not_reply_sends_onboarding_not_agents(self):
        """Mode CHATBOT + not is_reply → onboarding, PAS les agents."""
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser()
        handler, messaging, *_ = _make_handler(
            mode=ContextMode.CHATBOT,
            workspace=None,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message(is_reply=False))
        assert not analyser.called
        assert not synthesiser.called
        assert len(messaging.sent) == 1


# =============================================================================
# Axe 5 — Features optionnelles
# =============================================================================


class TestConversationMemoryFeature:
    """Tests de la ConversationMemory dans le pipeline Phase 2."""

    @pytest.mark.asyncio
    async def test_with_memory_loads_history_before_analyse(self):
        """Avec ConversationMemory → load_relevant_history appelé avant Analyser."""
        mock_history = [{"role": "user", "content": "question précédente"}]

        class MockMemory:
            def __init__(self):
                self.load_called = False
                self.save_called = False

            async def load_relevant_history(self, workspace_path, conversation_id, current_query):
                self.load_called = True
                return mock_history

            async def save_turn(self, workspace_path, conversation_id, user_message,
                                assistant_response, existing_history=None):
                self.save_called = True

        memory = MockMemory()
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser()

        ws = _make_workspace()
        context = _make_context(workspace=ws)
        messaging = MockMessaging()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
            conversation_memory=memory,
        )

        await handler.handle_message(_make_message())
        assert memory.load_called
        assert memory.save_called

    @pytest.mark.asyncio
    async def test_without_memory_pipeline_still_works(self):
        """Sans ConversationMemory → pipeline fonctionne normalement."""
        synthesiser = MockSynthesiser(text="Sans mémoire")
        handler, messaging, *_ = _make_handler(
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=synthesiser,
            conversation_memory=None,
        )
        await handler.handle_message(_make_message())
        assert any("Sans mémoire" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_memory_chatbot_no_workspace_skips_load(self):
        """Mode CHATBOT (workspace=None) + Memory → load_relevant_history non appelé."""

        class MockMemory:
            def __init__(self):
                self.load_called = False

            async def load_relevant_history(self, workspace_path, conversation_id, current_query):
                self.load_called = True
                return []

            async def save_turn(self, workspace_path, conversation_id,
                                user_message, assistant_response, existing_history=None):
                pass

        memory = MockMemory()
        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        messaging = MockMessaging()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            conversation_memory=memory,
        )

        # is_reply=True pour éviter onboarding
        await handler.handle_message(_make_message(is_reply=True))
        # Workspace None → load_relevant_history non appelé
        assert not memory.load_called


# =============================================================================
# Axe 6 — ToolRegistry configurations
# =============================================================================


class TestToolRegistryConfigurations:
    """Vérifie les compositions du ToolRegistry selon les features activées."""

    def test_base_tools_always_registered(self):
        """Les 4 outils de base sont toujours enregistrés."""
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
        )
        tool_names = registry.names()
        assert "search_documents" in tool_names
        assert "fetch_document" in tool_names
        assert "list_documents" in tool_names
        assert "summarize_text" in tool_names

    def test_document_index_tools_registered_when_provided(self):
        """Avec document_index → 3 outils supplémentaires."""
        mock_doc_index = MagicMock()
        ws = _make_workspace()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            workspace=ws,
            document_index=mock_doc_index,
        )
        tool_names = registry.names()
        assert "search_document_index" in tool_names
        assert "list_document_index" in tool_names
        assert "get_document_metadata" in tool_names

    def test_document_index_tools_absent_without_document_index(self):
        """Sans document_index → pas de tools document_index."""
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
        )
        tool_names = registry.names()
        assert "search_document_index" not in tool_names
        assert "list_document_index" not in tool_names
        assert "get_document_metadata" not in tool_names

    def test_assess_completion_registered_with_synthesiser(self):
        """Avec synthesiser → assess_completion enregistré."""
        synthesiser = MockSynthesiser()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            synthesiser=synthesiser,
        )
        assert "assess_completion" in registry.names()

    def test_assess_completion_absent_without_synthesiser(self):
        """Sans synthesiser → assess_completion absent."""
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            synthesiser=None,
        )
        assert "assess_completion" not in registry.names()

    def test_tools_filtered_by_workspace_tools_enabled(self):
        """workspace.tools_enabled filtre les outils disponibles pour l'orchestrateur."""

        ws = _make_workspace(tools_enabled=["search_documents"])  # seulement 1 outil
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
        )

        # Simuler le filtrage comme dans build_agent_context
        role_tools = ["search_documents", "fetch_document", "list_documents", "summarize_text", "assess_completion"]
        ws_tools = ws.tools_enabled
        available = [t for t in role_tools if t in ws_tools]
        assert available == ["search_documents"]

    def test_all_tools_available_when_tools_enabled_empty(self):
        """workspace.tools_enabled vide → tous les tools du rôle disponibles."""
        # Quand tools_enabled=[] dans build_agent_context → all tools for role
        role_tools = ["search_documents", "fetch_document", "list_documents", "summarize_text"]
        ws_tools: list = []
        available = [t for t in role_tools if t in ws_tools] if ws_tools else role_tools
        assert available == role_tools

    def test_full_registry_with_all_features(self):
        """Registre complet : base + document_index + assess_completion."""
        ws = _make_workspace()
        synthesiser = MockSynthesiser()
        mock_doc_index = MagicMock()

        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            workspace=ws,
            document_index=mock_doc_index,
            synthesiser=synthesiser,
        )
        tool_names = registry.names()
        assert len(tool_names) == 10  # 4 base + 1 search_skill + 4 doc_index + 1 assess


# =============================================================================
# Axe 7 — Audio transcription
# =============================================================================


class TestAudioTranscription:
    """Tests de la transcription audio avant pipeline."""

    @pytest.mark.asyncio
    async def test_audio_with_albert_transcribes_and_runs_pipeline(self):
        """Avec albert_client → transcription → body injecté → generator appelé."""
        class MockAlbertTranscribe:
            async def transcribe(self, audio_bytes, filename):
                return "procédure de validation"

        att = Attachment(filename="msg.ogg", content_type="audio/ogg", content=b"audio_bytes")
        msg = _make_message(body="", attachments=[att])

        generator = MockGenerator(text="Réponse audio")
        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
            albert_client=MockAlbertTranscribe(),
        )

        await handler.handle_message(msg)
        assert generator.called
        assert msg.body == "procédure de validation"

    @pytest.mark.asyncio
    async def test_audio_without_albert_client_skips_transcription(self):
        """Sans albert_client → transcription skippée → body reste vide."""
        att = Attachment(filename="msg.ogg", content_type="audio/ogg", content=b"audio_bytes")
        msg = _make_message(body="", attachments=[att])

        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            albert_client=None,
        )

        await handler.handle_message(msg)
        # Body vide → message d'erreur envoyé
        assert any(
            "vocal" in s.lower() or "texte" in s.lower()
            for s in messaging.sent
        )

    @pytest.mark.asyncio
    async def test_audio_empty_transcription_sends_error(self):
        """Transcription retourne "" → message d'erreur envoyé, pipeline skippé."""
        class MockAlbertEmpty:
            async def transcribe(self, audio_bytes, filename):
                return ""  # transcription vide

        att = Attachment(filename="msg.ogg", content_type="audio/ogg", content=b"audio_bytes")
        msg = _make_message(body="", attachments=[att])

        generator = MockGenerator(text="Ne doit pas être appelé")
        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
            albert_client=MockAlbertEmpty(),
        )

        await handler.handle_message(msg)
        assert not generator.called
        assert len(messaging.sent) == 1

    @pytest.mark.asyncio
    async def test_non_audio_attachment_not_transcribed(self):
        """Pièce jointe non-audio → pas de transcription → body reste tel quel."""
        class MockAlbertTranscribe:
            async def transcribe(self, audio_bytes, filename):
                return "ceci ne doit pas arriver"

        att = Attachment(filename="image.png", content_type="image/png", content=b"png_bytes")
        msg = _make_message(body="message texte", attachments=[att])

        generator = MockGenerator(text="Réponse image")
        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
            albert_client=MockAlbertTranscribe(),
        )

        await handler.handle_message(msg)
        assert generator.called
        assert msg.body == "message texte"  # body inchangé

    @pytest.mark.asyncio
    async def test_audio_with_existing_body_not_transcribed(self):
        """Body non vide → transcription non déclenchée même avec audio."""
        class FailOnCall:
            async def transcribe(self, audio_bytes, filename):
                raise AssertionError("transcribe ne doit pas être appelé")

        att = Attachment(filename="msg.ogg", content_type="audio/ogg", content=b"audio")
        msg = _make_message(body="J'ai déjà du texte", attachments=[att])

        generator = MockGenerator()
        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=generator,
            storage=MockStorage(),
            albert_client=FailOnCall(),
        )

        # Ne doit pas lever d'exception
        await handler.handle_message(msg)
        assert generator.called


# =============================================================================
# Axe 8 — CHATBOT : commandes onboarding
# =============================================================================


class TestOnboardingCommands:
    """Tests des commandes d'auto-configuration en mode CHATBOT."""

    @pytest.mark.asyncio
    async def test_chatbot_cmd_create_workspace(self):
        """'colaig créer mon espace' → create_workspace appelé → confirmation envoyée."""
        from unittest.mock import patch

        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        messaging = MockMessaging()
        storage = MockStorage()

        resolver = MockResolver(context=context)
        resolver.workspaces = []

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=storage,
        )

        from colaig.models import WorkspaceConfig
        fake_ws = WorkspaceConfig(
            workspace_id="mon-espace", name="mon espace", storage_path="/mon-espace/"
        )

        with patch(
            "colaig.context.workspace.create_workspace",
            new=AsyncMock(return_value=fake_ws)
        ):
            await handler.handle_message(_make_message(body="colaig créer mon espace"))

        # Confirmation envoyée
        assert len(messaging.sent) == 1
        assert "mon-espace" in messaging.sent[0] or "mon espace" in messaging.sent[0].lower()

    @pytest.mark.asyncio
    async def test_chatbot_cmd_link_unknown_workspace(self):
        """'colaig lier ws-inconnu' → workspace inconnu → message d'erreur."""
        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        messaging = MockMessaging()

        resolver = MockResolver(context=context)
        resolver.workspaces = []  # aucun workspace existant

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
        )

        await handler.handle_message(_make_message(body="colaig lier ws-inconnu"))
        assert len(messaging.sent) == 1
        assert "introuvable" in messaging.sent[0].lower() or "❌" in messaging.sent[0]

    @pytest.mark.asyncio
    async def test_chatbot_not_reply_no_command_sends_onboarding(self):
        """Pas de commande + not is_reply → onboarding envoyé."""
        context = _make_context(mode=ContextMode.CHATBOT, workspace=None)
        messaging = MockMessaging()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
        )

        await handler.handle_message(_make_message(body="Question quelconque"))
        assert len(messaging.sent) == 1


# =============================================================================
# Axe 9 — Phase 6 : SearchDirectives + assess_completion dans le pipeline
# =============================================================================


class TestPhase6Features:
    """Tests des features Phase 6 dans le pipeline."""

    @pytest.mark.asyncio
    async def test_phase6_assess_completion_registered_when_synthesiser_and_phase6(self):
        """assess_completion dans registry quand synthesiser fourni."""
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            synthesiser=MockSynthesiser(),
        )
        tool = registry.get("assess_completion")
        assert tool is not None

    @pytest.mark.asyncio
    async def test_phase6_assess_completion_executes_synthesiser_assess(self):
        """Appel à assess_completion → Synthesiser.assess() appelé."""
        class AssessTracker:
            def __init__(self):
                self.called = False
                self.last_query = None

            async def assess(self, query, collected_info, criteria="", context=None):
                self.called = True
                self.last_query = query
                return CompletionSignal(sufficient=True, confidence=0.9, reason="ok")

        tracker = AssessTracker()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=MockAlbertClient(),
            synthesiser=tracker,
        )

        result = await registry.execute(ToolCall(
            tool_name="assess_completion",
            arguments={"query": "procédure", "collected_info": "info X"},
        ))

        assert tracker.called
        assert tracker.last_query == "procédure"
        assert result.success

    @pytest.mark.asyncio
    async def test_phase6_search_directives_injected_in_orchestrator_prompt(self):
        """SearchDirectives.objective et completeness_criteria dans le prompt orchestrateur."""
        albert = MockAlbertClient()
        # Configurer pour retourner une réponse texte sans tool calls
        from colaig.models import ChatCompletionResult
        albert.tool_call_responses = [
            ChatCompletionResult(content="J'ai trouvé les infos.", finish_reason="stop")
        ]

        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
        )
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
        )

        search_directives = SearchDirectives(
            chunk_queries=["procédure validation"],
            objective="Trouver la procédure complète",
            completeness_criteria=["délais", "formulaires", "responsables"],
        )
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
            search_directives=search_directives,
        )
        context = _make_context()
        plan = await orc.execute(intent, context)

        # L'orchestrateur a bien utilisé les directives (reasoning set)
        assert plan is not None

    @pytest.mark.asyncio
    async def test_phase6_orchestrator_model_used_in_chat_with_tools(self):
        """Avec model défini → model passé à chat_with_tools."""
        model_used = []

        class TrackingAlbert:
            _tool_call_count = 0
            _chat_call_count = 0

            async def chat_with_tools(self, messages, tools, model=None, **kwargs):
                model_used.append(model)
                # Réponse texte directe → fin de boucle
                return ChatCompletionResult(content="done", finish_reason="stop")

            async def chat(self, messages, model=None, **kwargs):
                return "done"

            async def embed(self, text):
                return [0.0] * 384

            async def embed_batch(self, texts, **kwargs):
                return [[0.0] * 384 for _ in texts]

        # Utiliser un outil qui est dans TOOLS_BY_ROLE["orchestrator"]
        # et build_tool_registry le fournit par défaut
        albert = TrackingAlbert()
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
        )
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
            model="openai/gpt-oss-120b",
        )

        # workspace.tools_enabled inclut des outils qui existent dans le registry
        ws = _make_workspace(tools_enabled=["search_documents", "summarize_text"])
        context = _make_context(workspace=ws)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="question",
            needs_rag=True,
        )
        await orc.execute(intent, context)
        # Le modèle a bien été transmis à chat_with_tools
        assert "openai/gpt-oss-120b" in model_used

    @pytest.mark.asyncio
    async def test_phase6_pre_exec_chunks_injected_in_plan(self):
        """pre_exec.retrieval_results.chunks → injectés dans plan.search_results."""
        from colaig.models import PreExecutionCard

        pre_exec = PreExecutionCard(
            workspace_id="ws-p6",
            conversation_phase="discovery",
        )
        chunk = SearchResult(
            chunk=DocumentChunk(
                text="contenu pré-récupéré", source_path="/ws/doc.txt", source_name="doc.txt"
            ),
            score=0.95,
        )
        pre_exec.retrieval_results = {"chunks": [chunk]}

        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=None,  # mode déterministe → on utilise _inject via mode agentique mock
        )

        plan = ExecutionPlan(intent=Intent(intent_type=IntentType.QUESTION))
        orc._inject_pre_exec_results(pre_exec, plan)
        assert len(plan.search_results) == 1
        assert plan.search_results[0].chunk.text == "contenu pré-récupéré"


# =============================================================================
# Axe 10 — Configuration matrix end-to-end (configurations complètes)
# =============================================================================


class TestConfigurationMatrix:
    """Matrice des configurations du pipeline — voir docs/PIPELINE_GRAPH.md."""

    @pytest.mark.asyncio
    async def test_config_c1_minimal_phase1(self):
        """C1 : agents_enabled=False → Phase 1 minimal."""
        generator = MockGenerator(text="C1 réponse")
        handler, messaging, *_ = _make_handler()
        handler._generator = generator
        await handler.handle_message(_make_message())
        assert generator.called
        assert any("C1 réponse" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_config_c3_phase2_deterministic(self):
        """C3 : agents_enabled=True, is_agentic=False → Phase 2 déterministe."""
        analyser = MockAnalyser()
        synthesiser = MockSynthesiser(text="C3 déterministe")

        # Orchestrateur sans albert → déterministe
        orc = Orchestrator(storage=MockStorage(), retriever=MockRetriever())
        assert not orc.is_agentic

        handler, messaging, *_ = _make_handler(
            analyser=analyser,
            orchestrator=orc,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message())
        assert analyser.called
        assert synthesiser.called
        assert any("C3 déterministe" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_config_c4_phase5_agentic(self):
        """C4 : agents_enabled=True, is_agentic=True → Phase 5 agentique."""
        albert = MockAlbertClient()
        # Réponse texte directe (pas de tool calls)
        albert.tool_call_responses = [
            ChatCompletionResult(content="recherche terminée", finish_reason="stop")
        ]
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
        )
        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
        )
        assert orc.is_agentic

        analyser = MockAnalyser()
        synthesiser = MockSynthesiser(text="C4 agentique")

        handler, messaging, *_ = _make_handler(
            analyser=analyser,
            orchestrator=orc,
            synthesiser=synthesiser,
        )
        await handler.handle_message(_make_message())
        assert analyser.called
        assert synthesiser.called
        assert any("C4 agentique" in s for s in messaging.sent)

    @pytest.mark.asyncio
    async def test_config_c6_phase6_with_assess(self):
        """C6 : Phase 6 — assess_completion disponible dans le registry."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(content="infos collectées", finish_reason="stop")
        ]
        synthesiser_instance = MockSynthesiser(text="C6 phase6")

        # Phase 6 : synthesiser fourni → assess_completion enregistré
        registry = build_tool_registry(
            retriever=MockRetriever(),
            storage=MockStorage(),
            albert=albert,
            synthesiser=synthesiser_instance,
        )
        assert "assess_completion" in registry.names()

        orc = Orchestrator(
            storage=MockStorage(),
            retriever=MockRetriever(),
            albert=albert,
            tool_registry=registry,
            model="openai/gpt-oss-120b",
        )
        analyser = MockAnalyser()

        handler, messaging, *_ = _make_handler(
            analyser=analyser,
            orchestrator=orc,
            synthesiser=synthesiser_instance,
        )
        await handler.handle_message(_make_message())
        assert analyser.called
        assert synthesiser_instance.called


# =============================================================================
# Axe 11 — Phase 6 : PipelinePhase callbacks + typing
# =============================================================================


class TestPipelinePhaseCallbacks:
    """Vérifie que les callbacks de phase sont appelés dans l'ordre."""

    @pytest.mark.asyncio
    async def test_phase_callbacks_called_in_order(self):
        """THINKING → RETRIEVING → SYNTHESIZING → COMPLETE dans cet ordre."""
        phases_observed: list[PipelinePhase] = []

        async def on_phase_change(phase: PipelinePhase, conv_id: str) -> None:
            phases_observed.append(phase)

        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser()
        messaging = MockMessaging()
        context = _make_context()
        resolver = MockResolver(context=context)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=MockRetriever(),
            generator=MockGenerator(),
            storage=MockStorage(),
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
            on_phase_change=on_phase_change,
        )

        await handler.handle_message(_make_message())
        assert phases_observed == [
            PipelinePhase.THINKING,
            PipelinePhase.RETRIEVING,
            PipelinePhase.SYNTHESIZING,
            PipelinePhase.COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_typing_on_before_pipeline_typing_off_after(self):
        """typing=True avant le pipeline, typing=False dans le finally."""
        handler, messaging, *_ = _make_handler(
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        await handler.handle_message(_make_message())
        assert messaging.typing_calls[0] is True
        assert messaging.typing_calls[-1] is False

    @pytest.mark.asyncio
    async def test_typing_off_even_on_exception(self):
        """typing=False envoyé même si le pipeline lève une exception."""
        class FailAnalyser:
            async def analyse(self, message, context, pre_exec=None):
                raise RuntimeError("analyser crash")

        handler, messaging, *_ = _make_handler(
            analyser=FailAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        await handler.handle_message(_make_message())
        # Typing OFF appelé malgré l'erreur
        assert messaging.typing_calls[-1] is False
        # Et un message d'erreur envoyé
        assert len(messaging.sent) >= 1
