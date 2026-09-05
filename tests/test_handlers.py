"""Tests pour colaig/messaging/handlers.py — Pipeline de traitement des messages."""

import pytest

from colaig.messaging.handlers import MessageHandler
from colaig.models import (
    ContextMode,
    ConversationType,
    DocumentChunk,
    GeneratedResponse,
    IncomingMessage,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)


class MockResolver:
    """Mock du ContextResolver."""

    def __init__(self, mode=ContextMode.ASSISTANT, rag_enabled=True):
        self._mode = mode
        self._rag_enabled = rag_enabled

    async def resolve(self, message):
        ws = WorkspaceConfig(
            workspace_id="test", name="Test",
            storage_path="/test/",
            system_prompt="System prompt de test.",
            rag_enabled=self._rag_enabled,
        ) if self._mode == ContextMode.ASSISTANT else None

        return WorkspaceContext(
            workspace=ws,
            mode=self._mode,
            system_prompt="System prompt de test.",
        )


class MockRetriever:
    """Mock du Retriever."""

    def __init__(self, results=None):
        self._results = results or []
        self.retrieve_called = False
        self.last_query = None

    async def retrieve(self, query, k=5, score_threshold=0.3):
        self.retrieve_called = True
        self.last_query = query
        return self._results

    def set_store(self, store):
        pass


class MockGenerator:
    """Mock du Generator."""

    def __init__(self, response_text="Réponse de test."):
        self._response_text = response_text
        self.generate_called = False
        self.last_query = None
        self.last_search_results = None

    async def generate(self, query, context, search_results, conversation_history=None, channel_format=None):
        self.generate_called = True
        self.last_query = query
        self.last_search_results = search_results
        return GeneratedResponse(
            text=self._response_text,
            sources=["doc.txt"],
            confidence=0.8,
        )


class MockMessaging:
    """Mock du MessagingProtocol."""

    def __init__(self):
        self.messages_sent: list[tuple[str, str]] = []
        self.typing_events: list[tuple[str, bool]] = []

    async def send(self, conversation_id, text, formatted=None):
        self.messages_sent.append((conversation_id, text))

    async def send_typing(self, conversation_id, typing=True, timeout=10000):
        self.typing_events.append((conversation_id, typing))


@pytest.fixture
def message() -> IncomingMessage:
    return IncomingMessage(
        user_id="@user:test.local",
        conversation_id="!room:test.local",
        body="Quelle est la procédure ?",
        conversation_type=ConversationType.PRIVATE,
        message_id="$evt1",
        display_name="User",
    )


class TestHandleMessage:
    """Tests du pipeline Phase 1 complet."""

    async def test_full_pipeline(self, message, mock_storage):
        """Pipeline complet : résolution → RAG → génération → envoi."""
        messaging = MockMessaging()
        resolver = MockResolver()
        retriever = MockRetriever(results=[
            SearchResult(
                chunk=DocumentChunk(text="Contenu", source_path="/doc.txt", source_name="doc.txt"),
                score=0.9,
            ),
        ])
        generator = MockGenerator()

        handler = MessageHandler(messaging, resolver, retriever, generator, mock_storage)
        await handler.handle_message(message)

        assert retriever.retrieve_called
        assert generator.generate_called
        assert len(messaging.messages_sent) == 1
        assert messaging.messages_sent[0][0] == "!room:test.local"
        assert messaging.messages_sent[0][1] == "Réponse de test."

    async def test_typing_events(self, message, mock_storage):
        """Typing ON au début, OFF à la fin."""
        messaging = MockMessaging()
        handler = MessageHandler(messaging, MockResolver(), MockRetriever(), MockGenerator(), mock_storage)
        await handler.handle_message(message)

        # Au moins un typing ON et un typing OFF
        typing_on = [t for t in messaging.typing_events if t[1] is True]
        typing_off = [t for t in messaging.typing_events if t[1] is False]
        assert len(typing_on) >= 1
        assert len(typing_off) >= 1

    async def test_rag_disabled_skips_retrieval(self, message, mock_storage):
        """Si RAG désactivé → pas de recherche."""
        messaging = MockMessaging()
        resolver = MockResolver(rag_enabled=False)
        retriever = MockRetriever()
        generator = MockGenerator()

        handler = MessageHandler(messaging, resolver, retriever, generator, mock_storage)
        await handler.handle_message(message)

        assert not retriever.retrieve_called
        assert generator.generate_called

    async def test_chatbot_mode_no_rag(self, message, mock_storage):
        """Mode CHATBOT → pas de RAG."""
        messaging = MockMessaging()
        resolver = MockResolver(mode=ContextMode.CHATBOT)
        retriever = MockRetriever()
        generator = MockGenerator()

        handler = MessageHandler(messaging, resolver, retriever, generator, mock_storage)
        await handler.handle_message(message)

        assert not retriever.retrieve_called

    async def test_error_sends_friendly_message(self, message, mock_storage):
        """Exception → message d'erreur user-friendly envoyé."""

        class FailingResolver:
            async def resolve(self, msg):
                raise RuntimeError("boom")

        messaging = MockMessaging()
        handler = MessageHandler(messaging, FailingResolver(), MockRetriever(), MockGenerator(), mock_storage)
        await handler.handle_message(message)

        assert len(messaging.messages_sent) == 1
        assert "problème technique" in messaging.messages_sent[0][1]

    async def test_generator_query_matches_message(self, message, mock_storage):
        """Le generator reçoit le body du message comme query."""
        messaging = MockMessaging()
        generator = MockGenerator()
        handler = MessageHandler(messaging, MockResolver(), MockRetriever(), generator, mock_storage)
        await handler.handle_message(message)

        assert generator.last_query == "Quelle est la procédure ?"
