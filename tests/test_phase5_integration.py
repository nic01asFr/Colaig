"""
Tests — Phase 5 : Intégration end-to-end

Tests de bout en bout vérifiant que les composants Phase 5 fonctionnent
ensemble correctement : build_tool_registry, boucle agentique, mémoire
sémantique, config, pipeline complet Analyser → Orchestrateur → Synthétiseur.
"""

import pytest

from colaig.models import (
    ChatCompletionResult,
    ColaigConfig,
    ContextMode,
    ConversationType,
    IncomingMessage,
    Intent,
    IntentType,
    ToolCall,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.agents import Analyser, Orchestrator, Synthesiser, build_tool_registry
from colaig.agents.tool_registry import ToolRegistry
from colaig.context.conversation_memory import ConversationMemory

from tests.conftest import MockAlbertClient, MockStorage


# =============================================================================
# Fixtures partagées
# =============================================================================


@pytest.fixture
def storage():
    return MockStorage()


@pytest.fixture
def albert():
    return MockAlbertClient()


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="test-phase5",
        name="Espace Phase 5",
        storage_path="/espace-phase5/",
        tools_enabled=["search_documents", "fetch_document", "list_documents", "summarize_text"],
        max_results=5,
        similarity_threshold=0.3,
    )


@pytest.fixture
def context(workspace):
    return WorkspaceContext(
        workspace=workspace,
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig, assistant souverain.",
        available_tools=["search_documents", "summarize_text"],
    )


@pytest.fixture
def mock_retriever():
    """MockRetriever retournant des résultats vides (suffisant pour test intégration)."""
    from colaig.models import DocumentChunk, SearchResult

    class SimpleRetriever:
        async def retrieve(self, query: str, k: int = 5, score_threshold: float = 0.3, store=None):
            return [
                SearchResult(
                    chunk=DocumentChunk(
                        text="Contenu de test pour la procédure.",
                        source_path="/espace-phase5/doc.txt",
                        source_name="doc.txt",
                    ),
                    score=0.75,
                )
            ]

    return SimpleRetriever()


# =============================================================================
# Test 1 : build_tool_registry
# =============================================================================


class TestBuildToolRegistry:
    def test_registry_has_expected_tools(self, mock_retriever, storage, albert):
        """build_tool_registry enregistre les 4 outils built-in."""
        registry = build_tool_registry(mock_retriever, storage, albert)

        assert isinstance(registry, ToolRegistry)
        assert "search_documents" in registry
        assert "fetch_document" in registry
        assert "list_documents" in registry
        assert "summarize_text" in registry
        assert len(registry) == 4

    def test_registry_openai_schemas_valid(self, mock_retriever, storage, albert):
        """Les schémas OpenAI sont valides (name, description, parameters)."""
        registry = build_tool_registry(mock_retriever, storage, albert)
        schemas = registry.list_openai_schemas()

        assert len(schemas) == 4
        for schema in schemas:
            assert "type" in schema and schema["type"] == "function"
            assert "function" in schema
            func = schema["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_filter_by_names_works(self, mock_retriever, storage, albert):
        """filter_by_names retourne un sous-registry correct."""
        registry = build_tool_registry(mock_retriever, storage, albert)
        filtered = registry.filter_by_names(["search_documents", "summarize_text"])

        assert len(filtered) == 2
        assert "search_documents" in filtered
        assert "summarize_text" in filtered
        assert "fetch_document" not in filtered

    @pytest.mark.asyncio
    async def test_search_tool_executable(self, mock_retriever, storage, albert):
        """Le tool search_documents est exécutable via ToolRegistry.execute."""
        registry = build_tool_registry(mock_retriever, storage, albert)
        tool_call = ToolCall(
            tool_name="search_documents",
            arguments={"query": "procédure de validation"},
            call_id="test-c1",
        )
        result = await registry.execute(tool_call)

        assert result.success is True
        assert result.tool_name == "search_documents"
        assert "procédure" in result.result or result.result  # JSON non vide


# =============================================================================
# Test 2 : Pipeline agentique bout en bout
# =============================================================================


class TestAgenticPipelineEndToEnd:
    @pytest.mark.asyncio
    async def test_full_agentic_pipeline_tool_call_then_text(
        self, storage, albert, mock_retriever, workspace, context
    ):
        """Pipeline complet : Orchestrateur fait un appel tool puis répond.

        Analyser → Intent → Orchestrateur (tool call + texte) → Synthétiseur → texte
        """
        # Orchestrateur agentique configuré
        registry = build_tool_registry(mock_retriever, storage, albert)
        orch = Orchestrator(storage, mock_retriever, albert=albert, tool_registry=registry)

        # Albert : d'abord un tool call, puis une réponse texte
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[
                    ToolCall(
                        tool_name="search_documents",
                        arguments={"query": "procédure validation"},
                        call_id="tc-1",
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatCompletionResult(
                content="La procédure comporte 3 étapes selon le document.",
                finish_reason="stop",
            ),
        ]

        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="Quelle est la procédure de validation ?",
            needs_rag=True,
        )
        plan = await orch.execute(intent, context)

        # L'Orchestrateur a exécuté le tool et obtenu un raisonnement final
        assert any(s.step_type == "search_documents" for s in plan.steps)
        assert plan.orchestrator_reasoning == "La procédure comporte 3 étapes selon le document."

        # Synthétiseur utilise le plan enrichi
        synth = Synthesiser(albert, storage)
        # Reset pour que le synthétiseur utilise chat() normalement
        albert.tool_call_responses = []
        albert.chat_responses = ["Réponse finale synthétisée."]
        albert._chat_call_count = 0

        response = await synth.synthesise(plan, context)
        assert response.text == "Réponse finale synthétisée."
        assert "complete" in response.context_card.pipeline_phases

    @pytest.mark.asyncio
    async def test_orchestrator_reasoning_propagated_to_synthesiser_prompt(
        self, storage, albert, mock_retriever, workspace, context
    ):
        """orchestrator_reasoning dans le plan → inclus dans le prompt du synthétiseur."""
        registry = build_tool_registry(mock_retriever, storage, albert)
        orch = Orchestrator(storage, mock_retriever, albert=albert, tool_registry=registry)

        albert.tool_call_responses = [
            ChatCompletionResult(
                content="J'ai trouvé les informations nécessaires dans les documents.",
                finish_reason="stop",
            ),
        ]

        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="question de test",
            needs_rag=False,
        )
        plan = await orch.execute(intent, context)

        # Capturer les messages envoyés au synthétiseur
        captured_messages = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                captured_messages.extend(messages)
                return "réponse"

        synth = Synthesiser(CapturingAlbert(), storage)
        await synth.synthesise(plan, context)

        system_content = captured_messages[0]["content"]
        assert "J'ai trouvé les informations nécessaires" in system_content
        assert "Synthèse de l'Orchestrateur" in system_content


# =============================================================================
# Test 3 : ConversationMemory sémantique
# =============================================================================


class TestConversationMemoryEndToEnd:
    @pytest.mark.asyncio
    async def test_save_then_load_roundtrip(self, storage):
        """save_turn + load_relevant_history → retourne les messages sauvegardés."""
        memory = ConversationMemory(storage)

        await memory.save_turn(
            workspace_path="/espace/",
            conversation_id="!room-123:test.local",
            user_message="Qu'est-ce que la DINUM ?",
            assistant_response="La DINUM est la Direction Interministérielle du Numérique.",
            existing_history=[],
        )

        history = await memory.load_relevant_history(
            workspace_path="/espace/",
            conversation_id="!room-123:test.local",
            current_query="DINUM",
        )

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert "DINUM" in history[0]["content"]
        assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_semantic_selection_with_embeddings(self, storage, albert):
        """Avec embedding_service, la sélection sémantique fonctionne."""
        memory = ConversationMemory(storage, embedding_service=albert, max_retrieved=3)

        # Créer un historique de 10 messages (> max_retrieved=3)
        existing: list[dict] = []
        for i in range(5):
            await memory.save_turn(
                workspace_path="/espace/",
                conversation_id="!semantic-room:test.local",
                user_message=f"Question {i} sur le budget",
                assistant_response=f"Réponse {i} sur le budget.",
                existing_history=existing,
            )
            existing = await memory.load_relevant_history(
                "/espace/", "!semantic-room:test.local", f"Question {i}"
            )

        # Chargement avec requête sémantique
        result = await memory.load_relevant_history(
            workspace_path="/espace/",
            conversation_id="!semantic-room:test.local",
            current_query="procédure budget",
            max_messages=3,
        )

        # Doit retourner max 3 messages, triés chronologiquement
        assert len(result) <= 3
        for msg in result:
            assert "role" in msg
            assert "content" in msg


# =============================================================================
# Test 4 : Configuration Phase 5
# =============================================================================


class TestPhase5Config:
    def test_config_phase5_defaults(self):
        """ColaigConfig contient tous les champs Phase 5 avec leurs valeurs par défaut."""
        config = ColaigConfig(
            storage_backend="local",
            messaging_backend="matrix",
            matrix_homeserver="https://matrix.test.local",
            matrix_username="@bot:test.local",
            matrix_password="xxx",
            albert_api_url="https://albert-api.test.local",
            albert_api_key="test-key",
            albert_model_chat="test-model",
            albert_model_embed="test-embed",
        )

        # Champs Phase 5
        assert config.orchestrator_max_iterations == 5
        assert config.orchestrator_temperature == 0.1
        assert config.conversation_memory_max_stored == 100
        assert config.conversation_memory_max_retrieved == 10
        assert config.analyser_use_tool_calling is False

    def test_config_phase5_custom_values(self):
        """Les valeurs Phase 5 peuvent être surchargées."""
        config = ColaigConfig(
            storage_backend="local",
            messaging_backend="matrix",
            matrix_homeserver="https://matrix.test.local",
            matrix_username="@bot:test.local",
            matrix_password="xxx",
            albert_api_url="https://albert-api.test.local",
            albert_api_key="test-key",
            albert_model_chat="test-model",
            albert_model_embed="test-embed",
            orchestrator_max_iterations=3,
            orchestrator_temperature=0.2,
            conversation_memory_max_stored=50,
            conversation_memory_max_retrieved=5,
            analyser_use_tool_calling=True,
        )

        assert config.orchestrator_max_iterations == 3
        assert config.orchestrator_temperature == 0.2
        assert config.conversation_memory_max_stored == 50
        assert config.conversation_memory_max_retrieved == 5
        assert config.analyser_use_tool_calling is True


# =============================================================================
# Test 5 : Analyser tool-aware → Orchestrateur agentique
# =============================================================================


class TestAnalyserToAgenticOrchestrator:
    @pytest.mark.asyncio
    async def test_analyser_with_tool_registry_enriches_prompt(self, storage, workspace, context):
        """Analyser avec tool_registry inclut les descriptions des tools dans le prompt."""
        # Registry avec un outil
        registry = ToolRegistry()
        from colaig.models import ToolDefinition, ToolParameter
        registry.register(
            ToolDefinition(
                name="search_documents",
                description="Recherche dans les documents du workspace.",
                parameters=[ToolParameter(name="query", type="string", required=True)],
            ),
            lambda query: "résultats",
        )

        captured = []

        class CapturingAlbert(MockAlbertClient):
            async def chat(self, messages, **kwargs):
                captured.extend(messages)
                # Retourner JSON valide pour l'analyser
                return '{"intent_type": "question", "query_reformulated": "procédure", "needs_rag": true}'

        message = IncomingMessage(
            user_id="@test:test.local",
            conversation_id="!room:test.local",
            body="Quelle est la procédure de validation ?",
            conversation_type=ConversationType.PRIVATE,
        )

        analyser = Analyser(CapturingAlbert(), storage, tool_registry=registry)
        intent = await analyser.analyse(message, context)

        # L'intent est bien analysé
        assert intent.intent_type == IntentType.QUESTION
        assert intent.needs_rag is True

        # Le workspace_info (avec tools) est dans le message user (index 1)
        user_msg = captured[1]["content"]
        assert "search_documents" in user_msg

    @pytest.mark.asyncio
    async def test_agentic_max_iterations_respected(self, storage, albert, mock_retriever, context):
        """La boucle agentique s'arrête après max_iterations même si le LLM continue."""
        registry = build_tool_registry(mock_retriever, storage, albert)

        # LLM toujours en mode tool_calls → doit s'arrêter à max_iterations
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[
                    ToolCall(
                        tool_name="search_documents",
                        arguments={"query": f"requête itération {i}"},
                        call_id=f"tc-{i}",
                    )
                ],
                finish_reason="tool_calls",
            )
            for i in range(10)  # Plus que max_iterations
        ]

        orch = Orchestrator(
            storage, mock_retriever,
            albert=albert,
            tool_registry=registry,
            max_iterations=3,
        )

        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="test max iterations",
            needs_rag=True,
        )

        # Ne doit pas boucler indéfiniment
        plan = await orch.execute(intent, context)

        # Steps ≤ max_iterations (le dernier tour force tool_choice="none")
        assert len(plan.steps) <= 3
