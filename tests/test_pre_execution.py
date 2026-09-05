"""Tests — agents/pre_execution.py (Phase 6)"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.agents.pre_execution import PreExecutionBuilder, filter_tools
from colaig.agents.profile_service import ProfileService
from colaig.models import (
    BehaviorConfig,
    BehaviorToolsConfig,
    ContextAnchor,
    ContextMode,
    ConversationTrame,
    DocumentChunk,
    IncomingMessage,
    PreExecutionCard,
    SearchDirectives,
    SearchResult,
    ToolDefinition,
    WorkspaceConfig,
    WorkspaceContext,
)

# =============================================================================
# filter_tools
# =============================================================================

class TestFilterTools:
    def _make_tool(self, name, **kwargs) -> ToolDefinition:
        return ToolDefinition(name=name, description="desc", **kwargs)

    def test_passes_all_tools_by_default(self):
        tools = [self._make_tool("tool_a"), self._make_tool("tool_b")]
        result = filter_tools(tools, workspace=None)
        assert len(result) == 2

    def test_requires_workspace_filtered_when_absent(self):
        tools = [
            self._make_tool("tool_a", requires_workspace=True),
            self._make_tool("tool_b"),
        ]
        result = filter_tools(tools, workspace=None)
        assert len(result) == 1
        assert result[0].name == "tool_b"

    def test_requires_workspace_kept_when_present(self):
        tools = [self._make_tool("tool_a", requires_workspace=True)]
        ws = MagicMock()
        result = filter_tools(tools, workspace=ws)
        assert len(result) == 1

    def test_intent_type_filter(self):
        tools = [
            self._make_tool("tool_a", allowed_intent_types=["question", "search"]),
            self._make_tool("tool_b", allowed_intent_types=["action"]),
            self._make_tool("tool_c"),  # tous les intents
        ]
        result = filter_tools(tools, workspace=None, intent_type="question")
        names = [t.name for t in result]
        assert "tool_a" in names
        assert "tool_b" not in names
        assert "tool_c" in names

    def test_excluded_phases(self):
        trame = ConversationTrame(conv_id="c", workspace_id="/ws/", conversation_phase="concluded")
        tools = [
            self._make_tool("tool_a", excluded_phases=["concluded"]),
            self._make_tool("tool_b"),
        ]
        result = filter_tools(tools, workspace=None, trame=trame)
        assert len(result) == 1
        assert result[0].name == "tool_b"

    def test_behavior_disable(self):
        behavior = BehaviorConfig(
            name="draft_mode",
            tools=BehaviorToolsConfig(disable=["calculator"]),
        )
        tools = [
            self._make_tool("search"),
            self._make_tool("calculator"),
        ]
        result = filter_tools(tools, workspace=None, behavior=behavior)
        assert len(result) == 1
        assert result[0].name == "search"

    def test_no_trame_no_phase_filter(self):
        tools = [self._make_tool("tool_a", excluded_phases=["concluded"])]
        result = filter_tools(tools, workspace=None, trame=None)
        assert len(result) == 1

    def test_empty_allowed_intent_types_means_all(self):
        tools = [self._make_tool("tool_a", allowed_intent_types=[])]
        result = filter_tools(tools, workspace=None, intent_type="action")
        assert len(result) == 1


# =============================================================================
# PreExecutionBuilder.build
# =============================================================================

@pytest.fixture
def mock_storage():
    s = AsyncMock()
    s.exists = AsyncMock(return_value=False)
    s.list_files = AsyncMock(return_value=[])
    s.download = AsyncMock(side_effect=Exception("not found"))
    return s


@pytest.fixture
def mock_embeddings():
    e = AsyncMock()
    e.embed_text = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return e


@pytest.fixture
def mock_profile_service():
    ps = AsyncMock(spec=ProfileService)
    ps.load_profile = AsyncMock(return_value=None)
    ps.load_behaviors = AsyncMock(return_value=[])
    ps.resolve_behavior = AsyncMock(return_value=(None, 0.0))
    return ps


@pytest.fixture
def sample_workspace():
    return WorkspaceConfig(
        workspace_id="ws-test",
        name="Test Workspace",
        storage_path="/ws-test/",
        description="Workspace de test",
        language="fr",
    )


@pytest.fixture
def sample_workspace_context(sample_workspace):
    return WorkspaceContext(workspace=sample_workspace, mode=ContextMode.ASSISTANT)


@pytest.fixture
def sample_trame():
    return ConversationTrame(conv_id="conv-1", workspace_id="/ws-test/")


@pytest.fixture
def builder(mock_storage, mock_embeddings, mock_profile_service):
    return PreExecutionBuilder(
        storage=mock_storage,
        embeddings=mock_embeddings,
        profile_service=mock_profile_service,
        all_tools=[],
    )


class TestPreExecutionBuilderBuild:
    @pytest.mark.asyncio
    async def test_returns_pre_execution_card(self, builder, sample_trame, sample_workspace_context):
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="Bonjour")
        card = await builder.build(msg, sample_trame, sample_workspace_context)
        assert isinstance(card, PreExecutionCard)

    @pytest.mark.asyncio
    async def test_workspace_id_set(self, builder, sample_trame, sample_workspace_context):
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        card = await builder.build(msg, sample_trame, sample_workspace_context)
        assert card.workspace_id == "ws-test"

    @pytest.mark.asyncio
    async def test_embedding_computed(self, builder, mock_embeddings, sample_trame, sample_workspace_context):
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        card = await builder.build(msg, sample_trame, sample_workspace_context)
        mock_embeddings.embed_text.assert_awaited_once_with("test")
        assert card.message_embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embedding_failure_graceful(self, mock_storage, mock_profile_service):
        emb = AsyncMock()
        emb.embed_text = AsyncMock(side_effect=Exception("embed error"))
        builder = PreExecutionBuilder(
            storage=mock_storage,
            embeddings=emb,
            profile_service=mock_profile_service,
            all_tools=[],
        )
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        trame = ConversationTrame(conv_id="conv-1", workspace_id="/ws/")
        ctx = WorkspaceContext(workspace=None, mode=ContextMode.CHATBOT)
        card = await builder.build(msg, trame, ctx)
        assert card.message_embedding is None

    @pytest.mark.asyncio
    async def test_fixed_context_includes_workspace_info(self, builder, sample_trame, sample_workspace_context):
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        card = await builder.build(msg, sample_trame, sample_workspace_context)
        assert card.fixed_context["workspace_name"] == "Test Workspace"
        assert card.fixed_context["conversation_phase"] == "discovery"

    @pytest.mark.asyncio
    async def test_context_anchors_copied_from_trame(self, builder, sample_workspace_context):
        trame = ConversationTrame(conv_id="conv-1", workspace_id="/ws/")
        trame.context_anchors = [ContextAnchor(anchor_type="document", ref="/doc.pdf")]
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        card = await builder.build(msg, trame, sample_workspace_context)
        assert len(card.context_anchors) == 1
        assert card.context_anchors[0].ref == "/doc.pdf"

    @pytest.mark.asyncio
    async def test_available_tools_filtered(self, mock_storage, mock_embeddings, mock_profile_service, sample_workspace_context):
        tools = [
            ToolDefinition(name="search", description="search", requires_workspace=True),
            ToolDefinition(name="calc", description="calc"),
        ]
        builder = PreExecutionBuilder(
            storage=mock_storage,
            embeddings=mock_embeddings,
            profile_service=mock_profile_service,
            all_tools=tools,
        )
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="test")
        trame = ConversationTrame(conv_id="conv-1", workspace_id="/ws/")
        card = await builder.build(msg, trame, sample_workspace_context)
        # workspace présent → search disponible
        assert any(t.name == "search" for t in card.available_tools)
        assert any(t.name == "calc" for t in card.available_tools)

    @pytest.mark.asyncio
    async def test_empty_message_no_embed_call(self, builder, mock_embeddings, sample_workspace_context):
        msg = IncomingMessage(user_id="u1", conversation_id="conv-1", body="")
        trame = ConversationTrame(conv_id="conv-1", workspace_id="/ws/")
        card = await builder.build(msg, trame, sample_workspace_context)
        mock_embeddings.embed_text.assert_not_awaited()
        assert card.message_embedding is None


# =============================================================================
# PreExecutionBuilder.execute_retrieval
# =============================================================================

class TestExecuteRetrieval:
    @pytest.mark.asyncio
    async def test_returns_empty_without_retriever(self, builder, sample_workspace_context):
        search_dirs = SearchDirectives(chunk_queries=["test query"])
        pre_exec = MagicMock(spec=PreExecutionCard)
        pre_exec.selected_skills = []
        results = await builder.execute_retrieval(search_dirs, pre_exec, sample_workspace_context)
        assert results == {"chunks": [], "docs": [], "skills": [], "history": []}

    @pytest.mark.asyncio
    async def test_calls_retriever_for_chunk_queries(self, mock_storage, mock_embeddings, mock_profile_service, sample_workspace_context):
        chunk = DocumentChunk(text="contenu", source_path="/doc.pdf", source_name="doc.pdf")
        # Depuis L3.5, les reformulations sont groupees en UN aller-retour : le
        # constructeur appelle `retrieve_many` une fois, pas `retrieve` N fois.
        mock_retriever = AsyncMock()
        mock_retriever.retrieve_many = AsyncMock(
            return_value=[[SearchResult(chunk=chunk, score=0.9)]] * 2)
        builder = PreExecutionBuilder(
            storage=mock_storage,
            embeddings=mock_embeddings,
            profile_service=mock_profile_service,
            all_tools=[],
            retriever=mock_retriever,
        )
        search_dirs = SearchDirectives(chunk_queries=["query 1", "query 2"])
        pre_exec = MagicMock(spec=PreExecutionCard)
        pre_exec.selected_skills = []
        results = await builder.execute_retrieval(search_dirs, pre_exec, sample_workspace_context)
        assert mock_retriever.retrieve_many.await_count == 1, (
            "les deux reformulations doivent tenir en UN appel"
        )
        assert mock_retriever.retrieve_many.await_args.args[0] == ["query 1", "query 2"]
        assert len(results["chunks"]) >= 1

    @pytest.mark.asyncio
    async def test_deduplicates_chunks_by_source_path(self, mock_storage, mock_embeddings, mock_profile_service, sample_workspace_context):
        chunk = DocumentChunk(text="contenu", source_path="/doc.pdf", source_name="doc.pdf")
        mock_retriever = AsyncMock()
        # Même chunk rendu pour les deux reformulations — la deduplication par
        # source_path doit survivre au groupage.
        mock_retriever.retrieve_many = AsyncMock(
            return_value=[[SearchResult(chunk=chunk, score=0.9)],
                          [SearchResult(chunk=chunk, score=0.9)]])
        builder = PreExecutionBuilder(
            storage=mock_storage,
            embeddings=mock_embeddings,
            profile_service=mock_profile_service,
            all_tools=[],
            retriever=mock_retriever,
        )
        search_dirs = SearchDirectives(chunk_queries=["q1", "q2"])
        pre_exec = MagicMock(spec=PreExecutionCard)
        pre_exec.selected_skills = []
        results = await builder.execute_retrieval(search_dirs, pre_exec, sample_workspace_context)
        # Dédupliqué : 1 seul résultat
        assert len(results["chunks"]) == 1

    @pytest.mark.asyncio
    async def test_includes_skills_from_pre_exec(self, builder, sample_workspace_context):
        search_dirs = SearchDirectives(skill_queries=["rédaction"])
        pre_exec = MagicMock(spec=PreExecutionCard)
        pre_exec.selected_skills = [{"name": "redaction", "content": "..."}]
        results = await builder.execute_retrieval(search_dirs, pre_exec, sample_workspace_context)
        assert len(results["skills"]) == 1
        assert results["skills"][0]["name"] == "redaction"
