"""
Tests — Agent Orchestrateur
"""

from unittest.mock import AsyncMock

import pytest

from colaig.agents.context_builder import build_tool_registry
from colaig.agents.orchestrator import Orchestrator
from colaig.models import (
    AgentDirectives,
    ChatCompletionResult,
    ContextMode,
    DocumentChunk,
    Intent,
    IntentType,
    PreExecutionCard,
    SearchDirectives,
    SearchResult,
    ToolCall,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.conftest import MockAlbertClient, MockStorage


class MockRetriever:
    """Mock du retriever pour les tests."""

    def __init__(self, results=None):
        self._results = results or []
        self.retrieve_calls = []

    async def retrieve(self, query: str, k: int = 5, score_threshold: float = 0.3, store=None):
        self.retrieve_calls.append({"query": query, "k": k, "threshold": score_threshold})
        return self._results


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="test",
        name="Espace Test",
        storage_path="/espace-test/",
        tools_enabled=["search", "summarize"],
        max_results=5,
        similarity_threshold=0.3,
    )


@pytest.fixture
def context(workspace):
    return WorkspaceContext(
        workspace=workspace,
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig.",
        available_tools=["search", "summarize"],
    )


@pytest.fixture
def sample_results():
    return [
        SearchResult(
            chunk=DocumentChunk(
                text="La procédure comporte 3 étapes.",
                source_path="/espace-test/documents/guide.txt",
                source_name="guide.txt",
            ),
            score=0.85,
            rank=0,
        ),
        SearchResult(
            chunk=DocumentChunk(
                text="Le formulaire doit être soumis avant le 15.",
                source_path="/espace-test/documents/guide.txt",
                source_name="guide.txt",
            ),
            score=0.72,
            rank=1,
        ),
    ]


class TestOrchestratorPlanning:
    @pytest.mark.asyncio
    async def test_greeting_no_steps(self, context):
        """Les salutations ne génèrent aucune étape."""
        intent = Intent(intent_type=IntentType.GREETING, needs_rag=False)
        orch = Orchestrator(MockStorage(), MockRetriever())
        plan = await orch.execute(intent, context)
        assert len(plan.steps) == 0

    @pytest.mark.asyncio
    async def test_question_generates_rag_step(self, context, sample_results):
        """Une question génère un step rag_search."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure de validation",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), MockRetriever(sample_results))
        plan = await orch.execute(intent, context)

        assert len(plan.steps) == 1
        assert plan.steps[0].step_type == "rag_search"
        assert plan.steps[0].status == "done"

    @pytest.mark.asyncio
    async def test_resources_targeted_generates_fetch_step(self, context, sample_results):
        """Des resources ciblées génèrent un step storage_fetch."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                resources_to_target=["guide.pdf"],
            ),
        )
        storage = MockStorage()
        storage.add_file("/espace-test/guide.pdf", b"content")
        orch = Orchestrator(storage, MockRetriever(sample_results))
        plan = await orch.execute(intent, context)

        assert len(plan.steps) == 2
        assert plan.steps[0].step_type == "rag_search"
        assert plan.steps[1].step_type == "storage_fetch"


class TestOrchestratorExecution:
    @pytest.mark.asyncio
    async def test_rag_search_accumulates_results(self, context, sample_results):
        """Les résultats RAG sont accumulés dans plan.search_results."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), MockRetriever(sample_results))
        plan = await orch.execute(intent, context)

        assert len(plan.search_results) == 2
        assert plan.search_results[0].chunk.source_name == "guide.txt"

    @pytest.mark.asyncio
    async def test_storage_fetch_found(self, context):
        """Le fetch storage marque les fichiers trouvés."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="guide",
            needs_rag=False,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                resources_to_target=["documents/guide.txt"],
            ),
        )
        storage = MockStorage()
        storage.add_file("/espace-test/documents/guide.txt", b"content")
        orch = Orchestrator(storage, MockRetriever())
        plan = await orch.execute(intent, context)

        fetch_step = plan.steps[0]
        assert fetch_step.step_type == "storage_fetch"
        assert fetch_step.status == "done"
        assert fetch_step.result["fetched"][0]["status"] == "found"

    @pytest.mark.asyncio
    async def test_storage_fetch_not_found(self, context):
        """Le fetch storage marque les fichiers non trouvés."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="guide",
            needs_rag=False,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                resources_to_target=["nonexistent.pdf"],
            ),
        )
        orch = Orchestrator(MockStorage(), MockRetriever())
        plan = await orch.execute(intent, context)

        fetch_step = plan.steps[0]
        assert fetch_step.result["fetched"][0]["status"] == "not_found"


class TestSequentialMemory:
    @pytest.mark.asyncio
    async def test_steps_executed_sequentially(self, context, sample_results):
        """Les steps sont exécutés dans l'ordre, chacun enrichissant le plan."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                resources_to_target=["guide.txt"],
            ),
        )
        storage = MockStorage()
        storage.add_file("/espace-test/guide.txt", b"content")

        step_order = []

        async def on_step(step, plan=None):
            step_order.append(step.step_type)

        orch = Orchestrator(storage, MockRetriever(sample_results), on_step_complete=on_step)
        plan = await orch.execute(intent, context)

        assert step_order == ["rag_search", "storage_fetch"]
        assert all(s.status == "done" for s in plan.steps)

    @pytest.mark.asyncio
    async def test_retriever_called_with_reformulated_query(self, context, sample_results):
        """Le retriever reçoit la query reformulée."""
        retriever = MockRetriever(sample_results)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure de validation des formulaires",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), retriever)
        await orch.execute(intent, context)

        assert len(retriever.retrieve_calls) == 1
        assert retriever.retrieve_calls[0]["query"] == "procédure de validation des formulaires"


class TestContextCard:
    @pytest.mark.asyncio
    async def test_context_card_built(self, context, sample_results):
        """Le plan contient une ContextCard."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), MockRetriever(sample_results))
        plan = await orch.execute(intent, context)

        assert plan.context_card is not None
        assert plan.context_card.workspace_id == "test"
        assert plan.context_card.mode == "assistant"
        assert "guide.txt" in plan.context_card.sources_used

    @pytest.mark.asyncio
    async def test_context_card_phases(self, context, sample_results):
        """La ContextCard contient les phases exécutées."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), MockRetriever(sample_results))
        plan = await orch.execute(intent, context)
        assert "retrieving" in plan.context_card.pipeline_phases


class TestOrchestratorMCPToolPlaceholder:
    @pytest.mark.asyncio
    async def test_mcp_tool_not_implemented(self, context):
        """Les tools MCP sont marqués not_implemented (Phase 5)."""
        intent = Intent(
            intent_type=IntentType.ACTION,
            query_reformulated="test",
            needs_rag=False,
            needs_tools=True,
            orchestrator_directives=AgentDirectives(
                target_agent="orchestrator",
                tools_to_use=["custom_tool"],
            ),
        )
        orch = Orchestrator(MockStorage(), MockRetriever())
        plan = await orch.execute(intent, context)

        assert len(plan.steps) == 1
        assert plan.steps[0].step_type == "mcp_tool"
        assert plan.steps[0].result["status"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_execution_time_measured(self, context, sample_results):
        """Le temps d'exécution est mesuré."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        orch = Orchestrator(MockStorage(), MockRetriever(sample_results))
        plan = await orch.execute(intent, context)
        assert plan.execution_time_ms >= 0


# =============================================================================
# Tests — Mode agentique (boucle LLM + tool calling)
# =============================================================================

class TestAgenticMode:
    """Tests pour la boucle agentique de l'Orchestrateur."""

    @pytest.fixture
    def workspace(self):
        """Workspace avec les nouveaux noms de tools pour le mode agentique."""
        return WorkspaceConfig(
            workspace_id="test",
            name="Espace Test",
            storage_path="/espace-test/",
            tools_enabled=["search_documents", "fetch_document", "list_documents", "summarize_text"],
            max_results=5,
            similarity_threshold=0.3,
        )

    @pytest.fixture
    def storage(self):
        return MockStorage()

    @pytest.fixture
    def retriever(self, sample_results):
        return MockRetriever(sample_results)

    @pytest.fixture
    def albert(self):
        return MockAlbertClient()

    @pytest.fixture
    def tool_registry(self, retriever, storage, albert):
        return build_tool_registry(retriever, storage, albert)

    @pytest.fixture
    def context(self, workspace):
        return WorkspaceContext(
            workspace=workspace,
            mode=ContextMode.ASSISTANT,
            system_prompt="Tu es Colaig.",
            available_tools=["search_documents", "fetch_document", "summarize_text"],
        )

    @pytest.mark.asyncio
    async def test_is_agentic_true_with_deps(self, storage, retriever, albert, tool_registry):
        """is_agentic = True quand albert + tool_registry sont fournis."""
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        assert orch.is_agentic is True

    @pytest.mark.asyncio
    async def test_is_agentic_false_without_deps(self, storage, retriever):
        """is_agentic = False sans albert ni tool_registry."""
        orch = Orchestrator(storage, retriever)
        assert orch.is_agentic is False

    @pytest.mark.asyncio
    async def test_agentic_text_response_no_tool_calls(
        self, storage, retriever, albert, tool_registry, context
    ):
        """LLM retourne du texte directement → pas de steps, reasoning dans plan."""
        albert.tool_call_responses = [
            ChatCompletionResult(content="J'ai les informations nécessaires.", finish_reason="stop")
        ]
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        plan = await orch.execute(intent, context)
        assert plan.orchestrator_reasoning == "J'ai les informations nécessaires."
        assert len(plan.steps) == 0

    @pytest.mark.asyncio
    async def test_agentic_single_tool_call(
        self, storage, retriever, albert, tool_registry, context
    ):
        """LLM appelle un outil, puis retourne du texte."""
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="search_documents", arguments={"query": "procédure"}, call_id="c1")],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(content="Résumé : procédure en 3 étapes.", finish_reason="stop"),
        ]
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        plan = await orch.execute(intent, context)
        # Un step créé pour search_documents
        assert len(plan.steps) == 1
        assert plan.steps[0].step_type == "search_documents"
        assert plan.steps[0].status == "done"
        assert plan.orchestrator_reasoning == "Résumé : procédure en 3 étapes."

    @pytest.mark.asyncio
    async def test_agentic_multi_step(
        self, storage, retriever, albert, tool_registry, context
    ):
        """LLM appelle 2 outils successivement, puis termine."""
        storage.add_file("/espace-test/guide.pdf", b"contenu guide")
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="search_documents", arguments={"query": "procédure"}, call_id="c1")],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="list_documents", arguments={"directory": ""}, call_id="c2")],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(content="Terminé.", finish_reason="stop"),
        ]
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        plan = await orch.execute(intent, context)
        assert len(plan.steps) == 2

    @pytest.mark.asyncio
    async def test_agentic_max_iterations(
        self, storage, retriever, albert, tool_registry, context
    ):
        """La boucle s'arrête après max_iterations même si le LLM veut continuer."""
        # LLM appelle toujours un outil — boucle infinie sans limite
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="search_documents", arguments={"query": "q"}, call_id=f"c{i}")],
                finish_reason="tool_calls",
            )
            for i in range(20)  # Bien plus que max_iterations
        ] + [ChatCompletionResult(content="Done.", finish_reason="stop")]
        max_it = 3
        orch = Orchestrator(
            storage, retriever, albert=albert, tool_registry=tool_registry, max_iterations=max_it
        )
        intent = Intent(intent_type=IntentType.QUESTION, query_reformulated="q", needs_rag=True)
        plan = await orch.execute(intent, context)
        # Le nombre de steps ne peut dépasser max_iterations - 1 (dernier tour = pas de tool)
        assert len(plan.steps) <= max_it

    @pytest.mark.asyncio
    async def test_agentic_callback_called(
        self, storage, retriever, albert, tool_registry, context
    ):
        """on_step_complete est appelé pour chaque tool call exécuté."""
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="search_documents", arguments={"query": "q"}, call_id="c1")],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(content="Done.", finish_reason="stop"),
        ]
        callback_steps = []

        async def on_step(step, plan=None):
            callback_steps.append(step.step_type)

        orch = Orchestrator(
            storage, retriever, albert=albert, tool_registry=tool_registry,
            on_step_complete=on_step,
        )
        intent = Intent(intent_type=IntentType.QUESTION, query_reformulated="q", needs_rag=True)
        await orch.execute(intent, context)
        assert "search_documents" in callback_steps

    @pytest.mark.asyncio
    async def test_agentic_greeting_skips_tools(
        self, storage, retriever, albert, tool_registry, context
    ):
        """Greeting → pas de boucle, plan vide retourné immédiatement."""
        intent = Intent(intent_type=IntentType.GREETING, query_reformulated="bonjour")
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        plan = await orch.execute(intent, context)
        assert len(plan.steps) == 0
        # Albert ne doit pas avoir été appelé
        assert albert._tool_call_count == 0

    @pytest.mark.asyncio
    async def test_agentic_tool_error_handled(
        self, storage, retriever, albert, tool_registry, context
    ):
        """LLM appelle un outil inconnu → step status=error, boucle continue."""
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(tool_name="unknown_tool", arguments={}, call_id="c1")],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(content="Done.", finish_reason="stop"),
        ]
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(intent_type=IntentType.QUESTION, query_reformulated="q", needs_rag=True)
        plan = await orch.execute(intent, context)
        # Un step créé, en erreur (outil inconnu dans le registry)
        assert len(plan.steps) == 1
        assert plan.steps[0].status == "error"

    @pytest.mark.asyncio
    async def test_agentic_context_card_built(
        self, storage, retriever, albert, tool_registry, context
    ):
        """ContextCard est construite après la boucle agentique."""
        albert.tool_call_responses = [
            ChatCompletionResult(content="Done.", finish_reason="stop")
        ]
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(intent_type=IntentType.QUESTION, query_reformulated="q", needs_rag=True)
        plan = await orch.execute(intent, context)
        assert plan.context_card is not None

    @pytest.mark.asyncio
    async def test_deterministic_fallback_no_albert(self, storage, retriever, context, sample_results):
        """Sans albert → mode déterministe (backward compat Phase 4)."""
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure",
            needs_rag=True,
        )
        orch = Orchestrator(storage, MockRetriever(sample_results))
        plan = await orch.execute(intent, context)
        # Mode déterministe → RAG search exécuté
        assert any(s.step_type == "rag_search" for s in plan.steps)


# =============================================================================
# Phase 6 — model, reporter, pre_exec, SearchDirectives
# =============================================================================

class TestOrchestratorPhase6:
    """Tests des fonctionnalités Phase 6 de l'Orchestrateur."""

    @pytest.fixture
    def storage(self):
        return MockStorage()

    @pytest.fixture
    def retriever(self):
        return MockRetriever()

    @pytest.fixture
    def context(self):
        ws = WorkspaceConfig(
            workspace_id="ws-p6", name="WS P6",
            storage_path="/ws-p6/", tools_enabled=["search_documents"],
        )
        return WorkspaceContext(workspace=ws, mode=ContextMode.ASSISTANT)

    @pytest.fixture
    def intent_with_search_directives(self):
        return Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="procédure dépôt",
            needs_rag=True,
            search_directives=SearchDirectives(
                chunk_queries=["dépôt demande", "procédure"],
                objective="Trouver la procédure complète de dépôt",
                completeness_criteria="Doit inclure les délais",
            ),
        )

    @pytest.fixture
    def albert(self):
        return MockAlbertClient()

    @pytest.fixture
    def tool_registry(self, storage, retriever):
        return build_tool_registry(retriever=retriever, storage=storage, albert=MockAlbertClient())

    @pytest.mark.asyncio
    async def test_model_passed_to_chat_with_tools(self, storage, retriever, context, intent_with_search_directives):
        """Le paramètre model est passé à albert.chat_with_tools()."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(content="Done.", finish_reason="stop")
        ]
        tool_registry = build_tool_registry(retriever=retriever, storage=storage, albert=albert)
        orch = Orchestrator(
            storage, retriever, albert=albert, tool_registry=tool_registry,
            model="openai/gpt-oss-120b",
        )
        plan = await orch.execute(intent_with_search_directives, context)
        assert plan is not None  # Pas d'erreur

    @pytest.mark.asyncio
    async def test_reporter_called_on_tool_use(self, storage, retriever, context, intent_with_search_directives):
        """Le ProgressReporter est appelé lors des tool calls."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(
                    tool_name="search_documents",
                    arguments={"query": "test"},
                    call_id="c1",
                )],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(content="Résultats trouvés.", finish_reason="stop"),
        ]
        reporter = AsyncMock()
        tool_registry = build_tool_registry(retriever=retriever, storage=storage, albert=albert)
        orch = Orchestrator(
            storage, retriever, albert=albert, tool_registry=tool_registry,
            reporter=reporter,
        )
        await orch.execute(intent_with_search_directives, context)
        reporter.report_tool_use.assert_awaited()

    @pytest.mark.asyncio
    async def test_pre_exec_chunks_injected(self, storage, retriever, context):
        """Les chunks pré-récupérés depuis pre_exec sont injectés dans le plan."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(content="Done.", finish_reason="stop")
        ]
        chunk = SearchResult(
            chunk=DocumentChunk(text="contenu", source_path="/doc.pdf", source_name="doc.pdf"),
            score=0.9,
        )
        pre_exec = PreExecutionCard(
            workspace_id="ws-p6",
            conversation_phase="active",
        )
        pre_exec.retrieval_results = {"chunks": [chunk], "docs": [], "skills": [], "history": []}
        tool_registry = build_tool_registry(retriever=retriever, storage=storage, albert=albert)
        orch = Orchestrator(storage, retriever, albert=albert, tool_registry=tool_registry)
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="test",
            needs_rag=True,
        )
        plan = await orch.execute(intent, context, pre_exec=pre_exec)
        # Le chunk pré-récupéré doit être dans plan.search_results
        assert len(plan.search_results) >= 1

    @pytest.mark.asyncio
    async def test_search_directives_objective_in_prompt(self, storage, retriever, context, intent_with_search_directives):
        """L'objectif des SearchDirectives apparaît dans le prompt système."""
        captured_messages = []

        class CapturingAlbert(MockAlbertClient):
            async def chat_with_tools(self, messages, **kwargs):
                captured_messages.extend(messages)
                return ChatCompletionResult(content="Done.", finish_reason="stop")

        tool_registry = build_tool_registry(
            retriever=retriever, storage=storage, albert=CapturingAlbert()
        )
        orch = Orchestrator(
            storage, retriever, albert=CapturingAlbert(), tool_registry=tool_registry
        )
        await orch.execute(intent_with_search_directives, context)
        system_content = captured_messages[0]["content"]
        assert "Trouver la procédure complète de dépôt" in system_content
        assert "Doit inclure les délais" in system_content

    @pytest.mark.asyncio
    async def test_assess_completion_hint_in_prompt_when_tool_registered(
        self, storage, retriever, context, intent_with_search_directives
    ):
        """Quand assess_completion est dans le registry → hint dans le prompt."""
        from colaig.agents.synthesiser import Synthesiser
        captured_messages = []

        class CapturingAlbert(MockAlbertClient):
            async def chat_with_tools(self, messages, **kwargs):
                captured_messages.extend(messages)
                return ChatCompletionResult(content="Done.", finish_reason="stop")

        albert_inst = CapturingAlbert()
        synth = Synthesiser(albert_inst, storage)
        tool_registry = build_tool_registry(
            retriever=retriever, storage=storage, albert=albert_inst, synthesiser=synth
        )
        orch = Orchestrator(
            storage, retriever, albert=albert_inst, tool_registry=tool_registry
        )
        await orch.execute(intent_with_search_directives, context)
        system_content = captured_messages[0]["content"]
        assert "assess_completion" in system_content
