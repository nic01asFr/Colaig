"""
Tests — Handler Phase 2 (Pipeline Analyseur → Orchestrateur → Synthétiseur)
"""

import pytest

from colaig.messaging.handlers import MessageHandler
from colaig.models import (
    AgentDirectives,
    ContextCard,
    ContextMode,
    ConversationType,
    DocumentChunk,
    ExecutionPlan,
    ExecutionStep,
    GeneratedResponse,
    IncomingMessage,
    Intent,
    IntentType,
    PipelinePhase,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.conftest import MockStorage


# === Mocks Phase 1 (réutilisés) ===

class MockResolver:
    def __init__(self, mode=ContextMode.ASSISTANT):
        self._mode = mode

    async def resolve(self, message):
        ws = WorkspaceConfig(
            workspace_id="test", name="Test",
            storage_path="/test/",
            system_prompt="System prompt de test.",
        ) if self._mode == ContextMode.ASSISTANT else None
        return WorkspaceContext(
            workspace=ws, mode=self._mode,
            system_prompt="System prompt de test.",
        )


class MockRetriever:
    async def retrieve(self, query, k=5, score_threshold=0.3):
        return []

    def set_store(self, store):
        pass


class MockGenerator:
    async def generate(self, query, context, search_results, conversation_history=None, channel_format=None):
        return GeneratedResponse(text="Phase 1 response.", sources=[], confidence=0.5)


class MockMessaging:
    def __init__(self):
        self.messages_sent: list[tuple[str, str]] = []
        self.typing_events: list[tuple[str, bool]] = []

    async def send(self, conversation_id, text, formatted=None):
        self.messages_sent.append((conversation_id, text))

    async def send_typing(self, conversation_id, typing=True, timeout=10000):
        self.typing_events.append((conversation_id, typing))


# === Mocks Phase 2 ===

class MockAnalyser:
    def __init__(self, intent_type=IntentType.QUESTION):
        self._intent_type = intent_type
        self.analyse_called = False

    async def analyse(self, message, context, pre_exec=None):
        self.analyse_called = True
        return Intent(
            intent_type=self._intent_type,
            query_reformulated="question reformulée",
            needs_rag=True,
            confidence=0.9,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                search_strategy="precise",
            ),
            synthesiser_directives=AgentDirectives(
                target_agent="synthesiser",
                response_format="paragraph",
            ),
        )


class MockOrchestrator:
    def __init__(self, results=None):
        self._results = results or []
        self.execute_called = False

    async def execute(self, intent, context, pre_exec=None, reporter=None):
        self.execute_called = True
        return ExecutionPlan(
            intent=intent,
            steps=[ExecutionStep(step_type="rag_search", status="done")],
            search_results=self._results,
            context_card=ContextCard(mode="assistant", workspace_id="test"),
        )


class MockSynthesiser:
    def __init__(self, response_text="Phase 2 response."):
        self._response_text = response_text
        self.synthesise_called = False

    async def synthesise(self, plan, context, conversation_history=None, channel_format=None, pre_exec=None, **kwargs):
        self.synthesise_called = True
        return GeneratedResponse(
            text=self._response_text,
            sources=["doc.txt"],
            confidence=0.85,
            context_card=ContextCard(
                mode="assistant",
                pipeline_phases=["retrieving", "synthesizing", "complete"],
            ),
        )


@pytest.fixture
def message():
    return IncomingMessage(
        user_id="@user:test.local",
        conversation_id="!room:test.local",
        body="Quelle est la procédure ?",
        conversation_type=ConversationType.PRIVATE,
        message_id="$evt1",
    )


class TestPhase2Detection:
    def test_is_phase2_with_all_agents(self):
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        assert handler.is_phase2 is True

    def test_is_not_phase2_without_agents(self):
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
        )
        assert handler.is_phase2 is False

    def test_is_not_phase2_partial_agents(self):
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
        )
        assert handler.is_phase2 is False


class TestPhase2Pipeline:
    @pytest.mark.asyncio
    async def test_full_phase2_pipeline(self, message):
        """Pipeline Phase 2 complet : analyse → orchestration → synthèse → envoi."""
        messaging = MockMessaging()
        analyser = MockAnalyser()
        orchestrator = MockOrchestrator()
        synthesiser = MockSynthesiser()

        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )
        await handler.handle_message(message)

        assert analyser.analyse_called
        assert orchestrator.execute_called
        assert synthesiser.synthesise_called
        assert len(messaging.messages_sent) == 1
        assert messaging.messages_sent[0][1] == "Phase 2 response."

    @pytest.mark.asyncio
    async def test_phase_notifications(self, message):
        """Les phases sont notifiées dans l'ordre."""
        phases_received = []

        async def on_phase(phase, conv_id):
            phases_received.append(phase)

        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            on_phase_change=on_phase,
        )
        await handler.handle_message(message)

        assert phases_received == [
            PipelinePhase.THINKING,
            PipelinePhase.RETRIEVING,
            PipelinePhase.SYNTHESIZING,
            PipelinePhase.COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_typing_events_phase2(self, message):
        """Typing ON au début, OFF à la fin en Phase 2."""
        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        await handler.handle_message(message)

        typing_on = [t for t in messaging.typing_events if t[1] is True]
        typing_off = [t for t in messaging.typing_events if t[1] is False]
        assert len(typing_on) >= 1
        assert len(typing_off) >= 1


class TestPhase2ErrorHandling:
    @pytest.mark.asyncio
    async def test_analyser_error_sends_friendly_message(self, message):
        """Erreur analyseur → message d'erreur user-friendly."""

        class FailingAnalyser:
            async def analyse(self, msg, ctx, pre_exec=None):
                raise RuntimeError("analyse échouée")

        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=FailingAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        await handler.handle_message(message)

        assert len(messaging.messages_sent) == 1
        assert "problème technique" in messaging.messages_sent[0][1]

    @pytest.mark.asyncio
    async def test_synthesiser_error_sends_friendly_message(self, message):
        """Erreur synthétiseur → message d'erreur user-friendly."""

        class FailingSynthesiser:
            async def synthesise(self, plan, ctx, history=None, channel_format=None, pre_exec=None, **kwargs):
                raise RuntimeError("synthèse échouée")

        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=FailingSynthesiser(),
        )
        await handler.handle_message(message)

        assert len(messaging.messages_sent) == 1
        assert "problème technique" in messaging.messages_sent[0][1]


class TestPhase1BackwardCompat:
    @pytest.mark.asyncio
    async def test_phase1_still_works(self, message):
        """Sans agents Phase 2, le pipeline Phase 1 fonctionne."""
        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
        )
        await handler.handle_message(message)

        assert len(messaging.messages_sent) == 1
        assert messaging.messages_sent[0][1] == "Phase 1 response."


# =============================================================================
# Tests — ConversationMemory (Phase 5)
# =============================================================================

class MockConversationMemory:
    """Mock de ConversationMemory pour les tests."""

    def __init__(self, history=None):
        self._history = history or []
        self.load_calls: list[dict] = []
        self.save_calls: list[dict] = []

    async def load_relevant_history(self, workspace_path, conversation_id, current_query, **kwargs):
        self.load_calls.append({
            "workspace_path": workspace_path,
            "conversation_id": conversation_id,
            "current_query": current_query,
        })
        return self._history

    async def save_turn(self, workspace_path, conversation_id, user_message, assistant_response, existing_history):
        self.save_calls.append({
            "workspace_path": workspace_path,
            "conversation_id": conversation_id,
            "user_message": user_message,
            "assistant_response": assistant_response,
        })
        return existing_history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_response},
        ]


class TestConversationMemoryIntegration:
    @pytest.mark.asyncio
    async def test_handler_accepts_conversation_memory(self, message):
        """MessageHandler accepte conversation_memory sans erreur."""
        memory = MockConversationMemory()
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            conversation_memory=memory,
        )
        await handler.handle_message(message)
        assert len(memory.load_calls) == 1

    @pytest.mark.asyncio
    async def test_memory_load_called_with_correct_args(self, message):
        """load_relevant_history reçoit les bons arguments."""
        memory = MockConversationMemory()
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            conversation_memory=memory,
        )
        await handler.handle_message(message)
        assert memory.load_calls[0]["current_query"] == message.body
        assert memory.load_calls[0]["conversation_id"] == message.conversation_id

    @pytest.mark.asyncio
    async def test_memory_save_called_after_response(self, message):
        """save_turn est appelé après l'envoi de la réponse."""
        memory = MockConversationMemory()
        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            conversation_memory=memory,
        )
        await handler.handle_message(message)
        assert len(memory.save_calls) == 1
        assert memory.save_calls[0]["user_message"] == message.body
        assert memory.save_calls[0]["assistant_response"] == "Phase 2 response."

    @pytest.mark.asyncio
    async def test_memory_load_failure_does_not_crash(self, message):
        """Erreur lors du chargement → pipeline continue sans crash."""

        class FailingMemory(MockConversationMemory):
            async def load_relevant_history(self, *args, **kwargs):
                raise RuntimeError("load failed")

        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
            conversation_memory=FailingMemory(),
        )
        await handler.handle_message(message)
        # Pas de crash — réponse envoyée quand même
        assert len(messaging.messages_sent) == 1

    @pytest.mark.asyncio
    async def test_without_memory_backward_compat(self, message):
        """Sans conversation_memory → comportement Phase 2 inchangé."""
        messaging = MockMessaging()
        handler = MessageHandler(
            messaging, MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=MockSynthesiser(),
        )
        await handler.handle_message(message)
        assert len(messaging.messages_sent) == 1
        assert messaging.messages_sent[0][1] == "Phase 2 response."

    @pytest.mark.asyncio
    async def test_memory_loaded_history_injected_into_context(self, message):
        """L'historique chargé par ConversationMemory est passé au synthétiseur."""
        pre_loaded_history = [
            {"role": "user", "content": "ancienne question"},
            {"role": "assistant", "content": "ancienne réponse"},
        ]
        memory = MockConversationMemory(history=pre_loaded_history)

        received_history = []

        class CapturingSynthesiser(MockSynthesiser):
            async def synthesise(self, plan, context, conversation_history=None, channel_format=None, pre_exec=None, **kwargs):
                received_history.extend(conversation_history or [])
                return await super().synthesise(plan, context, conversation_history, channel_format, pre_exec)

        handler = MessageHandler(
            MockMessaging(), MockResolver(), MockRetriever(), MockGenerator(),
            MockStorage(),
            analyser=MockAnalyser(),
            orchestrator=MockOrchestrator(),
            synthesiser=CapturingSynthesiser(),
            conversation_memory=memory,
        )
        await handler.handle_message(message)
        assert received_history == pre_loaded_history
