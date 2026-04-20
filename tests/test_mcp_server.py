"""
Tests — MCP Server

Teste les tools, resources et prompts MCP de Colaig.
Utilise l'accès direct aux fonctions enregistrées via FastMCP.
"""

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from colaig.models import (
    DocumentChunk,
    GeneratedResponse,
    SearchResult,
    WorkspaceConfig,
)
from colaig.mcp.server import ColaigMCPServer
from tests.conftest import MockAlbertClient, MockStorage


# === Mocks ===

class MockResolver:
    def __init__(self, workspaces=None):
        self._workspaces = workspaces or []

    @property
    def workspaces(self):
        return self._workspaces

    async def resolve(self, message):
        from colaig.models import ContextMode, WorkspaceContext
        ws = self._workspaces[0] if self._workspaces else None
        return WorkspaceContext(
            workspace=ws,
            mode=ContextMode.ASSISTANT if ws else ContextMode.CHATBOT,
            system_prompt="Test prompt.",
        )


class MockRetriever:
    def __init__(self, results=None):
        self._results = results or []

    async def retrieve(self, query, k=5, score_threshold=0.3, store=None):
        return self._results


class MockIndexer:
    def __init__(self):
        self.indexed_count = 3

    async def index_workspace(self, path):
        return self.indexed_count

    async def save_to_storage(self, path):
        pass

    async def load_from_storage(self, path):
        return False


class MockGenerator:
    async def generate(self, query, context, search_results, conversation_history=None):
        return GeneratedResponse(
            text="Réponse Phase 1.", sources=["doc.txt"], confidence=0.8,
        )


@pytest.fixture
def test_workspace():
    return WorkspaceConfig(
        workspace_id="test-ws",
        name="Workspace Test",
        storage_path="/espace-test/",
        description="Pour les tests",
        rag_enabled=True,
        tone="professional",
        language="fr",
        tools_enabled=["search", "summarize"],
    )


@pytest.fixture
def mcp_server(test_workspace):
    resolver = MockResolver(workspaces=[test_workspace])
    retriever = MockRetriever(results=[
        SearchResult(
            chunk=DocumentChunk(
                text="La procédure comporte 3 étapes.",
                source_path="/espace-test/guide.txt",
                source_name="guide.txt",
            ),
            score=0.85,
        ),
    ])
    return ColaigMCPServer(
        resolver=resolver,
        retriever=retriever,
        indexer=MockIndexer(),
        storage=MockStorage(),
        config=None,
        generator=MockGenerator(),
    )


class TestMCPServerInit:
    def test_creates_fastmcp(self, mcp_server):
        assert mcp_server.mcp is not None
        assert mcp_server.mcp.name == "colaig"

    def test_http_app_returns_asgi(self, mcp_server):
        app = mcp_server.http_app()
        assert app is not None


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_colaig_ask_phase1(self, mcp_server):
        """colaig_ask retourne une réponse Phase 1."""
        tools = mcp_server.mcp._tool_manager._tools
        ask_tool = tools["colaig_ask"]
        result = await ask_tool.fn(question="Quelle procédure ?")
        data = json.loads(result)
        assert "answer" in data
        assert data["answer"] == "Réponse Phase 1."
        assert data["sources"] == ["doc.txt"]

    @pytest.mark.asyncio
    async def test_colaig_search(self, mcp_server):
        """colaig_search retourne les résultats RAG."""
        tools = mcp_server.mcp._tool_manager._tools
        search_tool = tools["colaig_search"]
        result = await search_tool.fn(query="procédure")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["source"] == "guide.txt"
        assert data[0]["score"] == 0.85

    @pytest.mark.asyncio
    async def test_colaig_list_workspaces(self, mcp_server):
        """colaig_list_workspaces retourne les workspaces."""
        tools = mcp_server.mcp._tool_manager._tools
        list_tool = tools["colaig_list_workspaces"]
        result = await list_tool.fn()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["workspace_id"] == "test-ws"
        assert data[0]["name"] == "Workspace Test"

    @pytest.mark.asyncio
    async def test_colaig_reindex(self, mcp_server):
        """colaig_reindex lance la réindexation."""
        tools = mcp_server.mcp._tool_manager._tools
        reindex_tool = tools["colaig_reindex"]
        result = await reindex_tool.fn()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["workspace_id"] == "test-ws"
        assert data[0]["status"] == "ok"
        assert data[0]["indexed"] == 3

    @pytest.mark.asyncio
    async def test_colaig_reindex_specific_workspace(self, mcp_server):
        """colaig_reindex avec workspace_id spécifique."""
        tools = mcp_server.mcp._tool_manager._tools
        reindex_tool = tools["colaig_reindex"]
        result = await reindex_tool.fn(workspace_id="nonexistent")
        data = json.loads(result)
        assert len(data) == 0  # Pas de workspace trouvé


class TestMCPResources:
    @pytest.mark.asyncio
    async def test_workspaces_resource(self, mcp_server):
        """Resource colaig://workspaces liste les workspaces."""
        resource_manager = mcp_server.mcp._resource_manager
        resources = resource_manager._resources
        ws_resource = resources.get("colaig://workspaces")
        assert ws_resource is not None
        result = await ws_resource.fn()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["workspace_id"] == "test-ws"

    @pytest.mark.asyncio
    async def test_workspace_config_resource(self, mcp_server):
        """Resource colaig://workspace/{id}/config retourne la config."""
        resource_manager = mcp_server.mcp._resource_manager
        resources = resource_manager._resources
        config_resource = resources.get("colaig://workspace/test-ws/config")
        assert config_resource is not None
        result = await config_resource.fn()
        data = json.loads(result)
        assert data["workspace_id"] == "test-ws"
        assert data["tone"] == "professional"


class TestMCPPrompts:
    @pytest.mark.asyncio
    async def test_workspace_assistant_prompt(self, mcp_server):
        """Prompt workspace_assistant retourne un prompt contextuel."""
        prompt_manager = mcp_server.mcp._prompt_manager
        prompts = prompt_manager._prompts
        ws_prompt = prompts.get("workspace_assistant")
        assert ws_prompt is not None
        result = await ws_prompt.fn(workspace_id="test-ws", question="Comment faire ?")
        assert "Workspace Test" in result
        assert "Comment faire ?" in result

    @pytest.mark.asyncio
    async def test_workspace_assistant_prompt_no_workspace(self, mcp_server):
        """Prompt sans workspace → prompt généraliste."""
        prompt_manager = mcp_server.mcp._prompt_manager
        prompts = prompt_manager._prompts
        ws_prompt = prompts.get("workspace_assistant")
        result = await ws_prompt.fn(workspace_id="nonexistent")
        assert "généraliste" in result


# ── Helpers importés directement pour les tests unitaires ────────────────────

from colaig.mcp.server import _user_can_access_workspace


class TestUserCanAccessWorkspace:
    """Tests unitaires de _user_can_access_workspace."""

    def _ws(self, public=False, user_ids=None):
        from colaig.models import WorkspaceConfig
        return WorkspaceConfig(
            workspace_id="test",
            name="Test",
            storage_path="/test/",
            public=public,
            user_ids=user_ids or [],
        )

    def test_auth_disabled_always_true(self):
        """auth_enabled=False → accès libre à tout."""
        ws = self._ws(public=False, user_ids=[])
        assert _user_can_access_workspace(ws, "", auth_enabled=False) is True
        assert _user_can_access_workspace(ws, "random", auth_enabled=False) is True

    def test_public_workspace_accessible_without_auth(self):
        """workspace.public=True → accessible même sans user_id."""
        ws = self._ws(public=True)
        assert _user_can_access_workspace(ws, "", auth_enabled=True) is True

    def test_public_workspace_accessible_any_user(self):
        """workspace.public=True → accessible à tout user."""
        ws = self._ws(public=True)
        assert _user_can_access_workspace(ws, "@stranger:tchap.fr", auth_enabled=True) is True

    def test_private_workspace_blocks_unknown_user(self):
        """workspace.public=False, user non-membre → refus."""
        ws = self._ws(public=False, user_ids=["@alice:tchap.fr"])
        assert _user_can_access_workspace(ws, "@bob:tchap.fr", auth_enabled=True) is False

    def test_private_workspace_allows_member(self):
        """workspace.public=False, user membre → accès."""
        ws = self._ws(public=False, user_ids=["@alice:tchap.fr", "@bob:tchap.fr"])
        assert _user_can_access_workspace(ws, "@bob:tchap.fr", auth_enabled=True) is True

    def test_private_workspace_blocks_empty_user_id(self):
        """workspace.public=False, pas d'user (non authentifié) → refus."""
        ws = self._ws(public=False, user_ids=["@alice:tchap.fr"])
        assert _user_can_access_workspace(ws, "", auth_enabled=True) is False


class TestListWorkspacesFiltering:
    """Tests de colaig_list_workspaces avec filtrage public/privé."""

    @pytest.fixture
    def public_workspace(self):
        return WorkspaceConfig(
            workspace_id="public-ws",
            name="Workspace Public",
            storage_path="/public/",
            public=True,
            rag_enabled=True,
        )

    @pytest.fixture
    def private_workspace(self):
        return WorkspaceConfig(
            workspace_id="private-ws",
            name="Workspace Privé",
            storage_path="/private/",
            public=False,
            user_ids=["@alice:tchap.fr"],
            rag_enabled=True,
        )

    def _server_with_workspaces(self, workspaces, mcp_auth_enabled=True):
        """Construit un ColaigMCPServer avec les workspaces donnés et auth activée."""
        from colaig.models import ColaigConfig
        resolver = MockResolver(workspaces=workspaces)
        retriever = MockRetriever()
        config = ColaigConfig(
            storage_backend="local",
            messaging_backend="matrix",
            matrix_homeserver="https://matrix.test.local",
            matrix_username="@colaig:test.local",
            matrix_password="pass",
            albert_api_url="https://albert.test.local",
            albert_api_key="key",
            albert_model_chat="test-model",
            albert_model_embed="test-embed",
            data_dir="/tmp",
            mcp_auth_enabled=mcp_auth_enabled,
        )
        return ColaigMCPServer(
            resolver=resolver,
            retriever=retriever,
            indexer=MockIndexer(),
            storage=MockStorage(),
            config=config,
            generator=MockGenerator(),
        )

    @pytest.mark.asyncio
    async def test_unauthenticated_sees_only_public(self, public_workspace, private_workspace):
        """Sans token, seuls les workspaces publics sont listés."""
        from colaig.auth.tokens import set_current_token
        set_current_token(None)

        server = self._server_with_workspaces(
            [public_workspace, private_workspace], mcp_auth_enabled=True
        )
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_list_workspaces"].fn()
        data = json.loads(result)
        ids = [ws["workspace_id"] for ws in data]
        assert "public-ws" in ids
        assert "private-ws" not in ids

    @pytest.mark.asyncio
    async def test_member_sees_private_workspace(self, public_workspace, private_workspace):
        """Avec token (membre), le workspace privé est visible."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@alice:tchap.fr", scope="*"))

        server = self._server_with_workspaces(
            [public_workspace, private_workspace], mcp_auth_enabled=True
        )
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_list_workspaces"].fn()
        data = json.loads(result)
        ids = [ws["workspace_id"] for ws in data]
        assert "public-ws" in ids
        assert "private-ws" in ids

        # Nettoyage
        set_current_token(None)

    @pytest.mark.asyncio
    async def test_non_member_cannot_see_private(self, public_workspace, private_workspace):
        """Avec token d'un non-membre, le workspace privé reste caché."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@stranger:tchap.fr", scope="*"))

        server = self._server_with_workspaces(
            [public_workspace, private_workspace], mcp_auth_enabled=True
        )
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_list_workspaces"].fn()
        data = json.loads(result)
        ids = [ws["workspace_id"] for ws in data]
        assert "public-ws" in ids
        assert "private-ws" not in ids

        set_current_token(None)

    @pytest.mark.asyncio
    async def test_auth_disabled_all_visible(self, public_workspace, private_workspace):
        """auth_enabled=False → tous les workspaces listés sans filtrage."""
        from colaig.auth.tokens import set_current_token
        set_current_token(None)

        server = self._server_with_workspaces(
            [public_workspace, private_workspace], mcp_auth_enabled=False
        )
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_list_workspaces"].fn()
        data = json.loads(result)
        ids = [ws["workspace_id"] for ws in data]
        assert "public-ws" in ids
        assert "private-ws" in ids


# =============================================================================
# Tests sécurisation tools admin
# =============================================================================


def _make_admin_server(config_store=None):
    """Construit un ColaigMCPServer minimal pour tester les guards admin."""
    from colaig.models import ColaigConfig
    from unittest.mock import MagicMock

    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=None)
    resolver.get_workspace = MagicMock(return_value=None)
    resolver.list_workspaces = MagicMock(return_value=[])
    resolver.register_workspace = AsyncMock()

    return ColaigMCPServer(
        resolver=resolver,
        retriever=MagicMock(),
        indexer=MagicMock(),
        storage=MockStorage(),
        config=ColaigConfig(storage_backend="local", messaging_backend="matrix"),
        config_store=config_store,
    )


class TestAdminGatedTools:
    """Vérifie que les tools sensibles refusent l'accès sans rôle admin."""

    def setup_method(self):
        from colaig.auth.tokens import set_current_token
        set_current_token(None)

    def teardown_method(self):
        from colaig.auth.tokens import set_current_token
        set_current_token(None)

    # ── colaig_get_config ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_config_requires_auth(self):
        """Sans token → erreur d'authentification."""
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_get_config"].fn()
        data = json.loads(result)
        assert "error" in data
        assert "Authentification" in data["error"]

    @pytest.mark.asyncio
    async def test_get_config_requires_admin(self):
        """Token user (non admin) → erreur droits insuffisants."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@alice:tchap.fr", role="user"))
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_get_config"].fn()
        data = json.loads(result)
        assert "error" in data
        assert "administrateur" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_config_admin_allowed(self):
        """Token admin → accès autorisé (pas d'erreur)."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@admin:tchap.fr", role="admin"))
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_get_config"].fn()
        data = json.loads(result)
        assert "error" not in data
        assert "storage" in data

    # ── colaig_set_backend ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_backend_requires_auth(self):
        """Sans token → erreur d'authentification."""
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_set_backend"].fn(
            type="storage", backend="local", credentials="{}"
        )
        data = json.loads(result)
        assert "error" in data
        assert "Authentification" in data["error"]

    @pytest.mark.asyncio
    async def test_set_backend_requires_admin(self):
        """Token user → erreur droits insuffisants."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@alice:tchap.fr", role="user"))
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_set_backend"].fn(
            type="storage", backend="local", credentials="{}"
        )
        data = json.loads(result)
        assert "error" in data
        assert "administrateur" in data["error"].lower()

    # ── colaig_create_workspace ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_workspace_requires_auth(self):
        """Sans token → erreur d'authentification."""
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_create_workspace"].fn(
            storage_path="/test/", name="Test"
        )
        data = json.loads(result)
        assert "error" in data
        assert "Authentification" in data["error"]

    @pytest.mark.asyncio
    async def test_create_workspace_requires_admin(self):
        """Token user → erreur droits insuffisants."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@alice:tchap.fr", role="user"))
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        result = await tools["colaig_create_workspace"].fn(
            storage_path="/test/", name="Test"
        )
        data = json.loads(result)
        assert "error" in data
        assert "administrateur" in data["error"].lower()

    # ── colaig_onboard ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_onboard_requires_auth(self):
        """Sans token → erreur d'authentification (même via ctx mock)."""
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        ctx_mock = MagicMock()
        result = await tools["colaig_onboard"].fn(ctx=ctx_mock)
        data = json.loads(result)
        assert "error" in data
        assert "Authentification" in data["error"]

    @pytest.mark.asyncio
    async def test_onboard_requires_admin(self):
        """Token user → erreur droits insuffisants."""
        from colaig.auth.tokens import set_current_token, TokenContext
        set_current_token(TokenContext(user_id="@alice:tchap.fr", role="user"))
        server = _make_admin_server()
        tools = server.mcp._tool_manager._tools
        ctx_mock = MagicMock()
        result = await tools["colaig_onboard"].fn(ctx=ctx_mock)
        data = json.loads(result)
        assert "error" in data
        assert "administrateur" in data["error"].lower()
