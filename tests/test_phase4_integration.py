"""
Tests — Intégration Phase 4

Tests end-to-end validant le pipeline complet Phase 2
et la configuration conditionnelle des agents/MCP.
"""

import json

import pytest

from colaig.agents import Analyser, Orchestrator, Synthesiser
from colaig.messaging.handlers import MessageHandler
from colaig.models import (
    ColaigConfig,
    ContextMode,
    ConversationType,
    DocumentChunk,
    GeneratedResponse,
    IncomingMessage,
    PipelinePhase,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.conftest import MockAlbertClient, MockStorage

# === Mocks ===

class MockResolver:
    def __init__(self, workspace=None):
        self._workspace = workspace

    @property
    def workspaces(self):
        return [self._workspace] if self._workspace else []

    async def resolve(self, message):
        return WorkspaceContext(
            workspace=self._workspace,
            mode=ContextMode.ASSISTANT if self._workspace else ContextMode.CHATBOT,
            system_prompt="System prompt test.",
            available_tools=["search"] if self._workspace else [],
        )

    async def refresh_cache(self):
        pass


class MockRetriever:
    def __init__(self, results=None):
        self._results = results or []

    async def retrieve(self, query, k=5, score_threshold=0.3):
        return self._results

    def set_store(self, store):
        pass


class MockGenerator:
    async def generate(self, query, context, search_results, conversation_history=None):
        return GeneratedResponse(text="Phase 1.", sources=[], confidence=0.5)


class MockMessaging:
    def __init__(self):
        self.messages_sent = []
        self.typing_events = []

    async def send(self, conversation_id, text, formatted=None):
        self.messages_sent.append((conversation_id, text))

    async def send_typing(self, conversation_id, typing=True, timeout=10000):
        self.typing_events.append((conversation_id, typing))


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="integ-test",
        name="Intégration Test",
        storage_path="/integ-test/",
        rag_enabled=True,
        tools_enabled=["search", "summarize"],
    )


@pytest.fixture
def search_results():
    return [
        SearchResult(
            chunk=DocumentChunk(
                text="La procédure comporte 3 étapes.",
                source_path="/integ-test/guide.txt",
                source_name="guide.txt",
            ),
            score=0.85,
        ),
    ]


@pytest.fixture
def albert():
    """Albert qui retourne un JSON d'analyse valide puis une réponse de synthèse."""
    albert = MockAlbertClient()
    albert.chat_responses = [
        json.dumps({
            "intent_type": "question",
            "query_reformulated": "Quelle est la procédure de validation ?",
            "needs_rag": True,
            "confidence": 0.9,
            "orchestrator_directives": {"search_strategy": "precise"},
            "synthesiser_directives": {"response_format": "step-by-step"},
        }),
        "La procédure comporte 3 étapes : 1. Soumettre 2. Valider 3. Archiver. [guide.txt]",
    ]
    return albert


class TestFullPhase2Pipeline:
    @pytest.mark.asyncio
    async def test_end_to_end_phase2(self, workspace, search_results, albert):
        """Pipeline complet Phase 2 : message → analyse → orchestration → synthèse → envoi."""
        storage = MockStorage()
        messaging = MockMessaging()
        retriever = MockRetriever(search_results)
        resolver = MockResolver(workspace)

        analyser = Analyser(albert, storage, temperature=0.1)
        orchestrator = Orchestrator(storage, retriever)
        synthesiser = Synthesiser(albert, storage, temperature=0.3)

        handler = MessageHandler(
            messaging=messaging,
            resolver=resolver,
            retriever=retriever,
            generator=MockGenerator(),
            storage=storage,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )

        msg = IncomingMessage(
            user_id="@user:test",
            conversation_id="!room:test",
            body="Quelle est la procédure de validation ?",
            conversation_type=ConversationType.PRIVATE,
        )
        await handler.handle_message(msg)

        # Vérifier qu'une réponse a été envoyée
        assert len(messaging.messages_sent) == 1
        response_text = messaging.messages_sent[0][1]
        assert "3 étapes" in response_text

    @pytest.mark.asyncio
    async def test_phase2_with_phase_tracking(self, workspace, search_results, albert):
        """Les phases sont tracées dans l'ordre correct."""
        storage = MockStorage()
        messaging = MockMessaging()
        phases = []

        async def track_phase(phase, conv_id):
            phases.append(phase)

        handler = MessageHandler(
            messaging=messaging,
            resolver=MockResolver(workspace),
            retriever=MockRetriever(search_results),
            generator=MockGenerator(),
            storage=storage,
            analyser=Analyser(albert, storage),
            orchestrator=Orchestrator(storage, MockRetriever(search_results)),
            synthesiser=Synthesiser(albert, storage),
            on_phase_change=track_phase,
        )

        msg = IncomingMessage(
            user_id="@u:s", conversation_id="!r:s",
            body="Question test",
        )
        await handler.handle_message(msg)

        assert PipelinePhase.THINKING in phases
        assert PipelinePhase.RETRIEVING in phases
        assert PipelinePhase.SYNTHESIZING in phases
        assert PipelinePhase.COMPLETE in phases


class TestConfigConditional:
    def test_config_agents_disabled_by_default(self):
        config = ColaigConfig()
        assert config.agents_enabled is False
        assert config.mcp_enabled is False

    def test_config_agents_enabled(self):
        config = ColaigConfig(agents_enabled=True, mcp_enabled=True)
        assert config.agents_enabled is True
        assert config.mcp_enabled is True

    def test_handler_phase1_when_no_agents(self):
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(),
            MockGenerator(), MockStorage(),
        )
        assert handler.is_phase2 is False

    def test_handler_phase2_when_agents_provided(self):
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(),
            MockGenerator(), MockStorage(),
            analyser=Analyser(MockAlbertClient(), MockStorage()),
            orchestrator=Orchestrator(MockStorage(), MockRetriever()),
            synthesiser=Synthesiser(MockAlbertClient(), MockStorage()),
        )
        assert handler.is_phase2 is True


class TestMCPIntegration:
    def test_mcp_server_creates_with_agents(self, workspace, search_results):
        """Le serveur MCP se crée correctement avec les agents."""
        from colaig.mcp import ColaigMCPServer

        albert = MockAlbertClient()
        storage = MockStorage()

        class MockIndexer:
            async def index_workspace(self, path): return 0
            async def save_to_storage(self, path): pass

        server = ColaigMCPServer(
            resolver=MockResolver(workspace),
            retriever=MockRetriever(search_results),
            indexer=MockIndexer(),
            storage=storage,
            config=ColaigConfig(),
            generator=MockGenerator(),
            analyser=Analyser(albert, storage),
            orchestrator=Orchestrator(storage, MockRetriever(search_results)),
            synthesiser=Synthesiser(albert, storage),
        )
        assert server.mcp.name == "colaig"

    def test_web_app_mounts_mcp(self, workspace, search_results):
        """L'application web monte le serveur MCP si fourni."""
        from colaig.mcp import ColaigMCPServer
        from colaig.web.routes import create_app

        class MockIndexer:
            async def index_workspace(self, path): return 0
            async def save_to_storage(self, path): pass

        mcp_server = ColaigMCPServer(
            resolver=MockResolver(workspace),
            retriever=MockRetriever(search_results),
            indexer=MockIndexer(),
            storage=MockStorage(),
            config=ColaigConfig(),
        )

        app = create_app(
            resolver=MockResolver(workspace),
            config=ColaigConfig(),
            mcp_server=mcp_server,
        )
        # Verify MCP is mounted
        mount_paths = [route.path for route in app.routes if hasattr(route, 'path')]
        assert "/mcp" in mount_paths or any("/mcp" in str(getattr(r, 'path', '')) for r in app.routes)
