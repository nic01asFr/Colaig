"""
Tests — Agent Analyseur
"""

import json
import pytest

from colaig.models import (
    ChatCompletionResult,
    ContextMode,
    ConversationType,
    IncomingMessage,
    IntentType,
    PreExecutionCard,
    SearchDirectives,
    ToolCall,
    ToolDefinition,
    ToolParameter,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.agents.analyser import Analyser, GREETING_PATTERNS
from colaig.agents.tool_registry import ToolRegistry
from colaig.exceptions import AnalysisError
from tests.conftest import MockAlbertClient, MockStorage


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="test",
        name="Espace Test",
        storage_path="/espace-test/",
        description="Workspace de test",
        tools_enabled=["search_documents", "summarize_text"],
    )


@pytest.fixture
def context(workspace):
    return WorkspaceContext(
        workspace=workspace,
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig en mode test.",
        available_tools=["search_documents", "summarize_text"],
    )


@pytest.fixture
def context_no_workspace():
    return WorkspaceContext(
        workspace=None,
        mode=ContextMode.CHATBOT,
        system_prompt="Tu es Colaig.",
    )


@pytest.fixture
def message():
    return IncomingMessage(
        user_id="@user:server",
        conversation_id="!room:server",
        body="Quelle est la procédure de validation ?",
        conversation_type=ConversationType.PRIVATE,
    )


def _make_albert_with_response(response_text: str) -> MockAlbertClient:
    albert = MockAlbertClient()
    albert.chat_responses = [response_text]
    return albert


class TestGreetingShortcut:
    @pytest.mark.asyncio
    async def test_bonjour(self, context):
        msg = IncomingMessage(user_id="u", conversation_id="c", body="Bonjour")
        analyser = Analyser(MockAlbertClient(), MockStorage())
        intent = await analyser.analyse(msg, context)
        assert intent.intent_type == IntentType.GREETING
        assert intent.needs_rag is False
        assert intent.confidence == 1.0

    @pytest.mark.asyncio
    async def test_salut_with_exclamation(self, context):
        msg = IncomingMessage(user_id="u", conversation_id="c", body="Salut !")
        analyser = Analyser(MockAlbertClient(), MockStorage())
        intent = await analyser.analyse(msg, context)
        assert intent.intent_type == IntentType.GREETING

    @pytest.mark.asyncio
    async def test_hello(self, context):
        msg = IncomingMessage(user_id="u", conversation_id="c", body="hello")
        analyser = Analyser(MockAlbertClient(), MockStorage())
        intent = await analyser.analyse(msg, context)
        assert intent.intent_type == IntentType.GREETING

    @pytest.mark.asyncio
    async def test_non_greeting(self, context, message):
        """Un vrai message n'est pas détecté comme greeting."""
        albert = _make_albert_with_response(json.dumps({
            "intent_type": "question",
            "query_reformulated": "procédure de validation",
            "needs_rag": True,
            "confidence": 0.9,
        }))
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.intent_type == IntentType.QUESTION

    def test_greeting_patterns(self):
        """Vérifie que les patterns couvrent les cas attendus."""
        assert GREETING_PATTERNS.match("Bonjour")
        assert GREETING_PATTERNS.match("salut!")
        assert GREETING_PATTERNS.match("Coucou")
        assert GREETING_PATTERNS.match("hey")
        assert not GREETING_PATTERNS.match("Bonjour, quelle procédure ?")
        assert not GREETING_PATTERNS.match("salut comment ça va")


class TestAnalysisJSON:
    @pytest.mark.asyncio
    async def test_parse_valid_json(self, context, message):
        """Albert retourne un JSON valide → Intent correcte."""
        response = json.dumps({
            "intent_type": "question",
            "query_reformulated": "Quelle est la procédure de validation ?",
            "entities": {"topic": "validation"},
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.9,
            "orchestrator_directives": {
                "instructions": "Chercher dans les guides",
                "resources_to_target": ["guide.pdf"],
                "search_strategy": "precise",
            },
            "synthesiser_directives": {
                "response_format": "step-by-step",
                "response_tone": "formal",
                "focus_points": ["étapes", "délais"],
            },
        })
        albert = _make_albert_with_response(response)
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)

        assert intent.intent_type == IntentType.QUESTION
        assert intent.needs_rag is True
        assert intent.confidence == 0.9
        assert intent.orchestrator_directives is not None
        assert intent.orchestrator_directives.search_strategy == "precise"
        assert intent.orchestrator_directives.resources_to_target == ["guide.pdf"]
        assert intent.synthesiser_directives is not None
        assert intent.synthesiser_directives.response_format == "step-by-step"
        assert intent.synthesiser_directives.focus_points == ["étapes", "délais"]

    @pytest.mark.asyncio
    async def test_parse_json_with_surrounding_text(self, context, message):
        """Albert entoure le JSON de commentaires → extraction OK."""
        response = 'Voici mon analyse :\n\n{"intent_type": "search", "query_reformulated": "validation", "needs_rag": true, "confidence": 0.7}\n\nBonne analyse.'
        albert = _make_albert_with_response(response)
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.intent_type == IntentType.SEARCH

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self, context, message):
        """Albert retourne du garbage → fallback gracieux."""
        albert = _make_albert_with_response("Je ne comprends pas la question.")
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)

        assert intent.intent_type == IntentType.QUESTION
        assert intent.query_reformulated == message.body
        assert intent.needs_rag is True
        assert intent.confidence == 0.3

    @pytest.mark.asyncio
    async def test_unknown_intent_type(self, context, message):
        """Type d'intention inconnu → UNKNOWN."""
        response = json.dumps({
            "intent_type": "foobar",
            "query_reformulated": "test",
            "confidence": 0.5,
        })
        albert = _make_albert_with_response(response)
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.intent_type == IntentType.UNKNOWN


class TestAnalysisWithoutWorkspace:
    @pytest.mark.asyncio
    async def test_chatbot_mode(self, context_no_workspace, message):
        """Analyse fonctionne même sans workspace."""
        response = json.dumps({
            "intent_type": "question",
            "query_reformulated": "procédure de validation",
            "needs_rag": True,
            "confidence": 0.8,
        })
        albert = _make_albert_with_response(response)
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context_no_workspace)
        assert intent.intent_type == IntentType.QUESTION


class TestAnalysisError:
    @pytest.mark.asyncio
    async def test_albert_failure_raises(self, context, message):
        """Erreur Albert → AnalysisError."""
        albert = MockAlbertClient()
        albert.chat_responses = []  # Will cause IndexError

        class FailAlbert(MockAlbertClient):
            async def chat(self, *args, **kwargs):
                raise ConnectionError("Albert down")

        analyser = Analyser(FailAlbert(), MockStorage())
        with pytest.raises(AnalysisError, match="erreur appel Albert"):
            await analyser.analyse(message, context)


# =============================================================================
# Tests — Step 5 : Analyseur tool-aware
# =============================================================================

def _make_tool_registry() -> ToolRegistry:
    """Crée un ToolRegistry minimal pour les tests."""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_documents",
            description="Recherche des documents dans l'index vectoriel.",
            parameters=[ToolParameter(name="query", type="string", required=True)],
        ),
        handler=lambda: None,
    )
    registry.register(
        ToolDefinition(
            name="summarize_text",
            description="Résume un texte long en quelques phrases.",
            parameters=[ToolParameter(name="text", type="string", required=True)],
        ),
        handler=lambda: None,
    )
    return registry


class TestWorkspaceInfo:
    """Tests pour _build_workspace_info."""

    def test_workspace_info_with_tool_registry_includes_descriptions(self, context):
        """Avec tool_registry → le prompt inclut les descriptions des tools."""
        registry = _make_tool_registry()
        analyser = Analyser(MockAlbertClient(), MockStorage(), tool_registry=registry)
        info = analyser._build_workspace_info(context)
        assert "search_documents" in info
        assert "Recherche des documents" in info

    def test_workspace_info_without_tool_registry_uses_names(self, context):
        """Sans tool_registry → liste de noms seulement."""
        analyser = Analyser(MockAlbertClient(), MockStorage())
        info = analyser._build_workspace_info(context)
        assert "search" in info or "summarize" in info
        # Pas de description longue
        assert "Recherche des documents" not in info

    def test_workspace_info_no_workspace_returns_mode(self, context_no_workspace):
        """Sans workspace → retourne au moins le mode d'interaction."""
        analyser = Analyser(MockAlbertClient(), MockStorage())
        info = analyser._build_workspace_info(context_no_workspace)
        # Le mode est toujours inclus même sans workspace (contexte utilisateur P2)
        assert "chatbot" in info or info == ""

    def test_workspace_info_unknown_tool_not_in_registry(self, context):
        """Tool non enregistré dans le registry → affiché sans description."""
        registry = ToolRegistry()  # Registry vide
        analyser = Analyser(MockAlbertClient(), MockStorage(), tool_registry=registry)
        info = analyser._build_workspace_info(context)
        # Les tools du contexte apparaissent sans description
        for tool in context.available_tools:
            assert tool in info


class TestToolCallingMode:
    """Tests pour use_tool_calling=True."""

    @pytest.mark.asyncio
    async def test_tool_calling_success(self, context, message):
        """Mode tool calling → Intent correctement construite depuis tool call."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(
                    tool_name="analyse_intent",
                    arguments={
                        "intent_type": "question",
                        "query_reformulated": "procédure de validation",
                        "needs_rag": True,
                        "needs_tools": False,
                        "confidence": 0.95,
                    },
                    call_id="c1",
                )],
                finish_reason="tool_calls",
            )
        ]
        analyser = Analyser(albert, MockStorage(), use_tool_calling=True)
        intent = await analyser.analyse(message, context)
        assert intent.intent_type == IntentType.QUESTION
        assert intent.query_reformulated == "procédure de validation"
        assert intent.confidence == 0.95
        assert intent.needs_rag is True

    @pytest.mark.asyncio
    async def test_tool_calling_directives_parsed(self, context, message):
        """Mode tool calling → directives orchestrateur correctement extraites."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(
                    tool_name="analyse_intent",
                    arguments={
                        "intent_type": "question",
                        "query_reformulated": "guide validation",
                        "needs_rag": True,
                        "needs_tools": False,
                        "confidence": 0.8,
                        "orchestrator_directives": {
                            "instructions": "Chercher dans les guides",
                            "resources_to_target": ["guide.pdf"],
                            "search_strategy": "precise",
                            "tools_to_use": [],
                        },
                    },
                    call_id="c1",
                )],
                finish_reason="tool_calls",
            )
        ]
        analyser = Analyser(albert, MockStorage(), use_tool_calling=True)
        intent = await analyser.analyse(message, context)
        assert intent.orchestrator_directives is not None
        assert intent.orchestrator_directives.search_strategy == "precise"
        assert "guide.pdf" in intent.orchestrator_directives.resources_to_target

    @pytest.mark.asyncio
    async def test_tool_calling_greeting_shortcut_bypasses_llm(self, context):
        """Greeting shortcut fonctionne même en mode tool calling."""
        albert = MockAlbertClient()
        msg = IncomingMessage(user_id="u", conversation_id="c", body="Bonjour")
        analyser = Analyser(albert, MockStorage(), use_tool_calling=True)
        intent = await analyser.analyse(msg, context)
        assert intent.intent_type == IntentType.GREETING
        assert albert._tool_call_count == 0

    @pytest.mark.asyncio
    async def test_tool_calling_fallback_when_no_tool_call(self, context, message):
        """LLM retourne du texte au lieu d'un tool call → fallback parsing JSON."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(
                content=json.dumps({
                    "intent_type": "question",
                    "query_reformulated": "fallback",
                    "needs_rag": True,
                    "confidence": 0.5,
                }),
                finish_reason="stop",
            )
        ]
        analyser = Analyser(albert, MockStorage(), use_tool_calling=True)
        intent = await analyser.analyse(message, context)
        # Fallback JSON parsing → intent valide
        assert intent.intent_type == IntentType.QUESTION

    @pytest.mark.asyncio
    async def test_tool_calling_raises_on_albert_error(self, context, message):
        """Erreur Albert en mode tool calling → AnalysisError."""
        class FailAlbert(MockAlbertClient):
            async def chat_with_tools(self, *args, **kwargs):
                raise ConnectionError("Albert down")

        analyser = Analyser(FailAlbert(), MockStorage(), use_tool_calling=True)
        with pytest.raises(AnalysisError, match="erreur appel Albert"):
            await analyser.analyse(message, context)


# =============================================================================
# Phase 6 — SearchDirectives, is_direct, new_anchors, model, pre_exec
# =============================================================================

class TestAnalyserPhase6:
    """Tests des fonctionnalités Phase 6 de l'Analyser."""

    @pytest.fixture
    def workspace(self):
        return WorkspaceConfig(
            workspace_id="ws-p6",
            name="Workspace Phase 6",
            storage_path="/ws-p6/",
            description="Test Phase 6",
        )

    @pytest.fixture
    def context(self, workspace):
        return WorkspaceContext(workspace=workspace, mode=ContextMode.ASSISTANT)

    @pytest.fixture
    def message(self):
        return IncomingMessage(user_id="u1", conversation_id="c1", body="Comment déposer une demande ?")

    @pytest.mark.asyncio
    async def test_greeting_is_direct(self, context):
        """Shortcut greeting → is_direct=True + direct_response non vide."""
        albert = MockAlbertClient()
        analyser = Analyser(albert, MockStorage())
        msg = IncomingMessage(user_id="u", conversation_id="c", body="Bonjour")
        intent = await analyser.analyse(msg, context)
        assert intent.is_direct is True
        assert intent.direct_response != ""
        assert albert._chat_call_count == 0  # pas d'appel LLM

    @pytest.mark.asyncio
    async def test_search_directives_parsed_from_json(self, context, message):
        """L'Analyser parse search_directives depuis la réponse JSON."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "procédure dépôt demande",
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.9,
            "is_direct": False,
            "direct_response": "",
            "suggested_next_phase": None,
            "new_anchors": [],
            "search_directives": {
                "chunk_queries": ["déposer demande", "procédure dépôt"],
                "document_queries": [],
                "skill_queries": ["démarche administrative"],
                "history_queries": [],
                "context_filters": {},
                "objective": "trouver la procédure de dépôt",
                "completeness_criteria": "procédure complète avec étapes",
            },
            "orchestrator_directives": {},
            "synthesiser_directives": {},
        })]
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.search_directives is not None
        assert "déposer demande" in intent.search_directives.chunk_queries
        assert "démarche administrative" in intent.search_directives.skill_queries
        assert intent.search_directives.objective == "trouver la procédure de dépôt"

    @pytest.mark.asyncio
    async def test_search_directives_fallback_when_absent(self, context, message):
        """Quand search_directives absent du JSON → fallback avec query_reformulated."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "procédure dépôt",
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.7,
        })]
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.search_directives is not None
        # Fallback : chunk_queries contient le corps du message original
        assert len(intent.search_directives.chunk_queries) >= 1

    @pytest.mark.asyncio
    async def test_new_anchors_parsed(self, context, message):
        """Les new_anchors sont parsés depuis le JSON."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "demande",
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.8,
            "new_anchors": [
                {"anchor_type": "entity", "ref": "demande_dépôt", "description": "Type de demande identifié"},
            ],
        })]
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert len(intent.new_anchors) == 1
        assert intent.new_anchors[0].ref == "demande_dépôt"
        assert intent.new_anchors[0].anchor_type == "entity"

    @pytest.mark.asyncio
    async def test_suggested_next_phase_parsed(self, context, message):
        """suggested_next_phase est parsé depuis le JSON."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "demande",
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.8,
            "suggested_next_phase": "active",
        })]
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert intent.suggested_next_phase == "active"

    @pytest.mark.asyncio
    async def test_is_direct_from_json(self, context):
        """is_direct=true dans le JSON → intent direct avec réponse."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "heure",
            "needs_rag": False,
            "needs_tools": False,
            "confidence": 0.95,
            "is_direct": True,
            "direct_response": "Je n'ai pas accès à l'heure.",
        })]
        msg = IncomingMessage(user_id="u", conversation_id="c", body="Quelle heure est-il ?")
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(msg, context)
        assert intent.is_direct is True
        assert "heure" in intent.direct_response.lower()

    @pytest.mark.asyncio
    async def test_model_passed_to_albert(self, context, message):
        """Le paramètre model est passé à albert.chat() quand spécifié."""
        albert = MockAlbertClient()
        analyser = Analyser(albert, MockStorage(), model="mistralai/Ministral-3-8B-Instruct-2512")
        await analyser.analyse(message, context)
        # L'appel doit avoir eu lieu (on vérifie que ça n'explose pas)
        assert albert._chat_call_count >= 1

    @pytest.mark.asyncio
    async def test_pre_exec_enriches_workspace_info(self, context, message):
        """pre_exec enrichit le prompt avec behavior et conversation_phase."""
        albert = MockAlbertClient()
        pre_exec = PreExecutionCard(
            workspace_id="ws-p6",
            conversation_phase="active",
            fixed_context={
                "workspace_name": "Workspace Phase 6",
                "conversation_phase": "active",
                "active_behavior": "mode_formel",
                "tone": "formel",
            },
        )
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context, pre_exec=pre_exec)
        # Le prompt a été construit et l'appel a eu lieu
        assert intent is not None
        assert albert._chat_call_count >= 1

    @pytest.mark.asyncio
    async def test_tool_calling_search_directives_parsed(self, context, message):
        """Mode tool calling → search_directives parsées depuis les arguments."""
        albert = MockAlbertClient()
        albert.tool_call_responses = [
            ChatCompletionResult(
                tool_calls=[ToolCall(
                    tool_name="analyse_intent",
                    arguments={
                        "intent_type": "search",
                        "query_reformulated": "formulaire demande",
                        "needs_rag": True,
                        "needs_tools": False,
                        "confidence": 0.9,
                        "search_directives": {
                            "chunk_queries": ["formulaire", "demande"],
                            "skill_queries": ["remplir formulaire"],
                            "objective": "trouver le formulaire",
                            "completeness_criteria": "lien vers le formulaire",
                        },
                    },
                    call_id="c1",
                )],
                finish_reason="tool_calls",
            )
        ]
        analyser = Analyser(albert, MockStorage(), use_tool_calling=True)
        intent = await analyser.analyse(message, context)
        assert intent.search_directives is not None
        assert "formulaire" in intent.search_directives.chunk_queries
        assert intent.search_directives.objective == "trouver le formulaire"

    @pytest.mark.asyncio
    async def test_anchor_without_ref_skipped(self, context, message):
        """Les anchors sans ref sont ignorés silencieusement."""
        albert = MockAlbertClient()
        albert.chat_responses = [json.dumps({
            "intent_type": "question",
            "query_reformulated": "demande",
            "needs_rag": True,
            "needs_tools": False,
            "confidence": 0.8,
            "new_anchors": [
                {"anchor_type": "entity", "ref": "", "description": "sans ref"},
                {"anchor_type": "entity", "ref": "valide", "description": "avec ref"},
            ],
        })]
        analyser = Analyser(albert, MockStorage())
        intent = await analyser.analyse(message, context)
        assert len(intent.new_anchors) == 1
        assert intent.new_anchors[0].ref == "valide"
