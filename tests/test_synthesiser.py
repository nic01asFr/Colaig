"""
Tests — Agent Synthétiseur
"""

import pytest

from colaig.models import (
    AgentDirectives,
    ChannelFormat,
    CompletionSignal,
    ContextCard,
    ContextMode,
    DocumentChunk,
    ExecutionPlan,
    ExecutionStep,
    Intent,
    IntentType,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.agents.synthesiser import Synthesiser, _extract_sources, _format_documents, _format_tool_results
from colaig.exceptions import SynthesisError
from tests.conftest import MockAlbertClient, MockStorage


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="test",
        name="Espace Test",
        storage_path="/espace-test/",
        tools_enabled=["search", "summarize"],
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
        ),
    ]


@pytest.fixture
def plan_empty():
    return ExecutionPlan(
        intent=Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="question de test",
            needs_rag=False,
        ),
        steps=[],
        search_results=[],
        context_card=ContextCard(mode="assistant", workspace_id="test"),
    )


@pytest.fixture
def plan_with_results(sample_results):
    return ExecutionPlan(
        intent=Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="Quelle est la procédure de validation ?",
            needs_rag=True,
            synthesiser_directives=AgentDirectives(
                target_agent="synthesiser",
                response_format="step-by-step",
                response_tone="formal",
                focus_points=["étapes", "délais"],
            ),
        ),
        steps=[
            ExecutionStep(step_type="rag_search", status="done"),
        ],
        search_results=sample_results,
        context_card=ContextCard(
            mode="assistant",
            workspace_id="test",
            workspace_name="Espace Test",
            pipeline_phases=["retrieving"],
        ),
    )


@pytest.fixture
def plan_greeting():
    return ExecutionPlan(
        intent=Intent(
            intent_type=IntentType.GREETING,
            query_reformulated="",
            needs_rag=False,
        ),
        steps=[],
    )


class TestSynthesiserBasic:
    @pytest.mark.asyncio
    async def test_generates_response(self, context, plan_with_results):
        albert = MockAlbertClient()
        synth = Synthesiser(albert, MockStorage())
        resp = await synth.synthesise(plan_with_results, context)

        assert resp.text  # Non vide
        assert resp.sources == ["guide.txt"]
        assert resp.confidence == 0.85

    @pytest.mark.asyncio
    async def test_context_card_enriched(self, context, plan_with_results):
        albert = MockAlbertClient()
        synth = Synthesiser(albert, MockStorage())
        resp = await synth.synthesise(plan_with_results, context)

        assert resp.context_card is not None
        assert "synthesizing" in resp.context_card.pipeline_phases
        assert "complete" in resp.context_card.pipeline_phases
        assert resp.context_card.workspace_id == "test"

    @pytest.mark.asyncio
    async def test_generation_time_measured(self, context, plan_with_results):
        albert = MockAlbertClient()
        synth = Synthesiser(albert, MockStorage())
        resp = await synth.synthesise(plan_with_results, context)
        assert resp.generation_time_ms >= 0


class TestSynthesiserDirectives:
    @pytest.mark.asyncio
    async def test_directives_included_in_prompt(self, context, plan_with_results):
        """Les directives du synthétiseur sont incluses dans le system prompt."""
        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "Réponse test."

        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.synthesise(plan_with_results, context)

        system_msg = calls[0][0]["content"]
        assert "step-by-step" in system_msg
        assert "formal" in system_msg
        assert "étapes" in system_msg

    @pytest.mark.asyncio
    async def test_no_directives_still_works(self, context, sample_results):
        """Sans directives, la synthèse fonctionne normalement."""
        plan = ExecutionPlan(
            intent=Intent(
                intent_type=IntentType.QUESTION,
                query_reformulated="procédure",
            ),
            search_results=sample_results,
        )
        albert = MockAlbertClient()
        synth = Synthesiser(albert, MockStorage())
        resp = await synth.synthesise(plan, context)
        assert resp.text


class TestSynthesiserSkills:
    @pytest.mark.asyncio
    async def test_skills_included_in_prompt(self, context, plan_with_results):
        """Les skills du workspace sont inclus dans le system prompt."""
        storage = MockStorage()
        from colaig.models import StorageFile
        storage.add_file(
            "/espace-test/.colaig/skills/glossaire.md",
            "DGFiP = Direction Générale des Finances Publiques".encode(),
        )

        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "Réponse test."

        synth = Synthesiser(CapturingAlbert(), storage)
        await synth.synthesise(plan_with_results, context)

        system_msg = calls[0][0]["content"]
        assert "DGFiP" in system_msg
        assert "glossaire" in system_msg


class TestSynthesiserHistory:
    @pytest.mark.asyncio
    async def test_conversation_history_included(self, context, plan_with_results):
        """L'historique de conversation est inclus dans les messages."""
        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "Réponse test."

        history = [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Bonjour !"},
        ]
        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.synthesise(plan_with_results, context, conversation_history=history)

        # System + 2 history + 1 user query = 4 messages
        assert len(calls[0]) == 4
        assert calls[0][1]["content"] == "Bonjour"


class TestSynthesiserErrors:
    @pytest.mark.asyncio
    async def test_albert_failure_raises(self, context, plan_with_results):
        class FailAlbert(MockAlbertClient):
            async def chat(self, *args, **kwargs):
                raise ConnectionError("Albert down")

        synth = Synthesiser(FailAlbert(), MockStorage())
        with pytest.raises(SynthesisError, match="erreur appel Albert"):
            await synth.synthesise(plan_with_results, context)


class TestUtilityFunctions:
    def test_extract_sources(self, sample_results):
        sources = _extract_sources(sample_results)
        assert sources == ["guide.txt"]

    def test_extract_sources_dedup(self):
        results = [
            SearchResult(chunk=DocumentChunk(text="a", source_path="/a", source_name="doc.txt"), score=0.9),
            SearchResult(chunk=DocumentChunk(text="b", source_path="/a", source_name="doc.txt"), score=0.8),
        ]
        sources = _extract_sources(results)
        assert sources == ["doc.txt"]

    def test_format_documents(self, sample_results):
        text = _format_documents(sample_results)
        assert "guide.txt" in text
        assert "0.85" in text
        assert "procédure" in text


# =============================================================================
# Tests — Step 7 : Synthétiseur enrichi (orchestrator_reasoning + tool results)
# =============================================================================

class TestOrchestratorReasoning:
    @pytest.mark.asyncio
    async def test_orchestrator_reasoning_in_prompt(self, context, plan_empty):
        """orchestrator_reasoning présent → inclus dans le system prompt."""
        plan_empty.orchestrator_reasoning = "J'ai trouvé 2 documents pertinents sur la procédure."

        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "réponse"

        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.synthesise(plan_empty, context)

        system_content = calls[0][0]["content"]
        assert "J'ai trouvé 2 documents pertinents" in system_content
        assert "Synthèse de l'Orchestrateur" in system_content

    @pytest.mark.asyncio
    async def test_no_orchestrator_reasoning_no_section(self, context, plan_empty):
        """Sans orchestrator_reasoning → pas de section dans le prompt."""
        plan_empty.orchestrator_reasoning = ""

        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "réponse"

        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.synthesise(plan_empty, context)

        system_content = calls[0][0]["content"]
        assert "Synthèse de l'Orchestrateur" not in system_content


class TestFormatToolResults:
    def test_format_search_documents_with_results(self):
        """search_documents → format avec nb résultats et sources."""
        tool_results = [{
            "tool": "search_documents",
            "query": "procédure",
            "results": [
                {"source": "guide.txt", "text": "...", "score": 0.9},
                {"source": "faq.txt", "text": "...", "score": 0.8},
            ],
        }]
        text = _format_tool_results(tool_results)
        assert "search_documents" in text
        assert "2 résultats" in text
        assert "guide.txt" in text

    def test_format_search_documents_no_results(self):
        """search_documents sans résultats → indique aucun résultat."""
        tool_results = [{"tool": "search_documents", "query": "inconnu", "results": []}]
        text = _format_tool_results(tool_results)
        assert "aucun résultat" in text

    def test_format_other_tool_with_result(self):
        """Autre tool avec result → affiche le résultat."""
        tool_results = [{"tool": "summarize_text", "result": "Résumé : 3 points clés."}]
        text = _format_tool_results(tool_results)
        assert "summarize_text" in text
        assert "3 points clés" in text

    def test_format_mcp_tool_placeholder(self):
        """Tool MCP placeholder → affiche le statut."""
        tool_results = [{"tool": "my_tool", "status": "not_implemented", "message": "Non dispo"}]
        text = _format_tool_results(tool_results)
        assert "my_tool" in text
        assert "not_implemented" in text

    @pytest.mark.asyncio
    async def test_tool_results_included_in_prompt(self, context, plan_empty):
        """plan.tool_results → inclus dans le system prompt du synthétiseur."""
        plan_empty.tool_results = [{"tool": "fetch_document", "result": "contenu doc"}]

        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return "réponse"

        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.synthesise(plan_empty, context)

        system_content = calls[0][0]["content"]
        assert "Résultats d'outils" in system_content
        assert "fetch_document" in system_content


# =============================================================================
# Phase 6 — assess(), synthesise_stream(), channel_format
# =============================================================================

class TestSynthesiserPhase6:
    """Tests des fonctionnalités Phase 6 du Synthétiseur."""

    @pytest.fixture
    def context(self, workspace):
        return WorkspaceContext(workspace=workspace, mode=ContextMode.ASSISTANT)

    @pytest.fixture
    def workspace(self):
        return WorkspaceConfig(
            workspace_id="ws-p6",
            name="Workspace Phase 6",
            storage_path="/ws-p6/",
        )

    @pytest.mark.asyncio
    async def test_assess_returns_completion_signal(self, context):
        """assess() retourne un CompletionSignal avec les bons champs."""
        albert = MockAlbertClient()
        albert.chat_responses = [
            '{"sufficient": true, "confidence": 0.9, '
            '"missing_elements": [], "suggested_directions": [], "reason": "Informations suffisantes"}'
        ]
        synth = Synthesiser(albert, MockStorage())
        signal = await synth.assess(
            query="Comment déposer une demande ?",
            collected_info="La procédure est décrite dans le guide.pdf",
        )
        assert isinstance(signal, CompletionSignal)
        assert signal.sufficient is True
        assert signal.confidence == 0.9

    @pytest.mark.asyncio
    async def test_assess_insufficient_verdict(self, context):
        """assess() avec sufficient=false retourne les éléments manquants."""
        albert = MockAlbertClient()
        albert.chat_responses = [
            '{"sufficient": false, "confidence": 0.3, '
            '"missing_elements": ["délai de traitement"], "suggested_directions": [], "reason": "Manque le délai"}'
        ]
        synth = Synthesiser(albert, MockStorage())
        signal = await synth.assess(
            query="Quel est le délai de traitement ?",
            collected_info="Aucun document trouvé.",
        )
        assert signal.sufficient is False
        assert "délai de traitement" in signal.missing_elements

    @pytest.mark.asyncio
    async def test_assess_fallback_on_error(self, context):
        """Erreur Albert dans assess() → CompletionSignal(sufficient=True) sans exception."""
        class FailAlbert(MockAlbertClient):
            async def chat(self, *args, **kwargs):
                raise ConnectionError("Albert down")

        synth = Synthesiser(FailAlbert(), MockStorage())
        signal = await synth.assess("query", "info")
        assert signal.sufficient is True  # fallback gracieux
        assert signal.confidence == 0.0

    @pytest.mark.asyncio
    async def test_assess_with_criteria(self, context):
        """assess() transmet le paramètre criteria à Albert."""
        calls = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                calls.append(messages)
                return '{"verdict": "complete", "confidence": 0.8, "can_partial_answer": true}'

        synth = Synthesiser(CapturingAlbert(), MockStorage())
        await synth.assess(
            query="procédure",
            collected_info="info",
            criteria="lien vers le formulaire officiel",
        )
        user_msg = calls[0][1]["content"]
        assert "lien vers le formulaire officiel" in user_msg

    @pytest.mark.asyncio
    async def test_assess_invalid_json_graceful(self, context):
        """assess() avec JSON invalide → CompletionSignal(sufficient=True) par défaut."""
        albert = MockAlbertClient()
        albert.chat_responses = ["Désolé, je ne sais pas."]
        synth = Synthesiser(albert, MockStorage())
        signal = await synth.assess("query", "info")
        assert signal.sufficient is True  # fallback parse error

    @pytest.mark.asyncio
    async def test_synthesise_stream_fallback(self, context, plan_empty):
        """synthesise_stream() sans chat_stream → yield unique de la réponse complète."""
        class NoStreamAlbert:
            """Albert minimal sans chat_stream → active le fallback synthesise_stream."""
            async def chat(self, messages, model=None, temperature=0.3, max_tokens=2048):
                return "Réponse complète."

        synth = Synthesiser(NoStreamAlbert(), MockStorage())
        chunks = []
        async for chunk in synth.synthesise_stream(plan_empty, context):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0] == "Réponse complète."

    @pytest.mark.asyncio
    async def test_synthesise_stream_with_chat_stream(self, context, plan_empty):
        """synthesise_stream() avec chat_stream disponible → streaming réel."""
        class StreamingAlbert(MockAlbertClient):
            async def chat_stream(self, messages, **kwargs):
                for chunk in ["Voici ", "la ", "réponse."]:
                    yield chunk

        synth = Synthesiser(StreamingAlbert(), MockStorage())
        chunks = []
        async for chunk in synth.synthesise_stream(plan_empty, context):
            chunks.append(chunk)
        assert chunks == ["Voici ", "la ", "réponse."]

    @pytest.mark.asyncio
    async def test_synthesise_accepts_channel_format(self, context, plan_empty):
        """synthesise() accepte channel_format sans lever d'exception."""
        albert = MockAlbertClient()
        synth = Synthesiser(albert, MockStorage())
        channel = ChannelFormat(supports_markdown=True, max_length=4096)
        response = await synth.synthesise(plan_empty, context, channel_format=channel)
        assert response is not None
        assert response.text != ""
