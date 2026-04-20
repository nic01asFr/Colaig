"""
Tests — MCPTokenMiddleware

Vérifie le comportement du middleware d'authentification MCP :
- Pas de token → call_next appelé (pass-through)
- Token valide → TokenContext positionné dans le ContextVar
- Token invalide → 401 JSON immédiat (pas de call_next)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware

from colaig.auth.middleware import MCPTokenMiddleware
from colaig.auth.tokens import TokenContext, get_current_token, set_current_token


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_app(token_manager):
    """Construit une mini Starlette app avec MCPTokenMiddleware pour les tests."""

    async def mcp_endpoint(request):
        """Endpoint /mcp qui retourne le TokenContext courant."""
        tok = get_current_token()
        if tok:
            return JSONResponse({"authenticated": True, "user_id": tok.user_id, "scope": tok.scope})
        return JSONResponse({"authenticated": False})

    async def other_endpoint(request):
        """Endpoint hors /mcp — le middleware ne doit pas y toucher."""
        return JSONResponse({"path": "other"})

    app = Starlette(
        routes=[
            Route("/mcp/test", mcp_endpoint),
            Route("/health", other_endpoint),
        ],
        middleware=[
            Middleware(MCPTokenMiddleware, token_manager=token_manager),
        ],
    )
    return app


class MockTokenManager:
    """Mock TokenManager avec une table token→TokenContext."""

    def __init__(self, valid_tokens: dict[str, TokenContext]):
        self._tokens = valid_tokens

    async def resolve(self, raw_token: str):
        return self._tokens.get(raw_token)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMCPTokenMiddleware:
    def test_no_auth_header_passthrough(self):
        """Sans Authorization header → call_next appelé, réponse normale."""
        manager = MockTokenManager({})
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        # Réinitialiser le ContextVar (isolation entre tests)
        set_current_token(None)

        resp = client.get("/mcp/test")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}

    def test_valid_token_sets_context(self):
        """Token valide → TokenContext positionné, réponse 200."""
        ctx = TokenContext(user_id="@alice:tchap.fr", scope="*", name="Test")
        manager = MockTokenManager({"colaig_alice_abc123": ctx})
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        set_current_token(None)

        resp = client.get(
            "/mcp/test",
            headers={"Authorization": "Bearer colaig_alice_abc123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["user_id"] == "@alice:tchap.fr"
        assert data["scope"] == "*"

    def test_invalid_token_returns_401(self):
        """Token présent mais invalide → 401 JSON, call_next NON appelé."""
        manager = MockTokenManager({})  # Aucun token valide
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        set_current_token(None)

        resp = client.get(
            "/mcp/test",
            headers={"Authorization": "Bearer colaig_bad_token"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert "invalide" in data["error"].lower() or "expiré" in data["error"].lower()

    def test_non_mcp_path_not_affected(self):
        """Chemin hors /mcp → middleware ignoré même avec token invalide."""
        manager = MockTokenManager({})
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get(
            "/health",
            headers={"Authorization": "Bearer colaig_bad_token"},
        )
        # Le middleware ne touche pas /health, donc call_next est appelé
        assert resp.status_code == 200
        assert resp.json() == {"path": "other"}

    def test_workspace_scoped_token(self):
        """Token avec scope workspace-id → scope transmis dans le contexte."""
        ctx = TokenContext(user_id="@bob:tchap.fr", scope="espace-rh", name="Bob RH")
        manager = MockTokenManager({"colaig_bob_xyz": ctx})
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        set_current_token(None)

        resp = client.get(
            "/mcp/test",
            headers={"Authorization": "Bearer colaig_bob_xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["scope"] == "espace-rh"

    def test_bearer_prefix_required(self):
        """Header Authorization sans préfixe 'Bearer ' → pass-through (non authentifié)."""
        ctx = TokenContext(user_id="@alice:tchap.fr", scope="*")
        manager = MockTokenManager({"colaig_alice_abc": ctx})
        app = _build_app(manager)
        client = TestClient(app, raise_server_exceptions=True)

        set_current_token(None)

        # Token fourni sans préfixe Bearer → traité comme absence de header
        resp = client.get(
            "/mcp/test",
            headers={"Authorization": "colaig_alice_abc"},
        )
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
