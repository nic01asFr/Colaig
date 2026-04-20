"""Tests pour colaig/auth/tokens.py — TokenManager et utilitaires."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from colaig.auth.tokens import (
    TokenContext,
    TokenManager,
    _parse_token,
    _personal_ws_slug,
    _token_hash,
    _mcp_config_path,
    _token_path,
    get_current_token,
    set_current_token,
    require_admin,
)


# =============================================================================
# Utilitaires de chemin
# =============================================================================

class TestPersonalWsSlug:
    def test_basic_matrix_id(self):
        slug = _personal_ws_slug("@alice:tchap.fr")
        # @ et : remplacés, . remplacé, résultat minuscule
        assert slug == "alice_tchap_fr"

    def test_handles_underscores(self):
        slug = _personal_ws_slug("@bob.dupont:matrix.example.fr")
        assert slug  # non vide
        # Seuls a-z, 0-9, _, - autorisés
        assert re.search(r"[^a-z0-9_\-]", slug) is None

    def test_empty_gives_user(self):
        slug = _personal_ws_slug("")
        assert slug == "user"

    def test_stable(self):
        """Même user_id → même slug (déterministe)."""
        uid = "@alice:tchap.fr"
        assert _personal_ws_slug(uid) == _personal_ws_slug(uid)


import re


class TestParseToken:
    def _make_token(self, slug="alice-tchap-fr"):
        import secrets
        secret = secrets.token_hex(32)
        raw = f"colaig_{slug}_{secret}"
        return raw, slug, secret

    def test_valid_token_parsed(self):
        raw, slug, secret = self._make_token()
        result = _parse_token(raw)
        assert result is not None
        assert result[0] == slug
        assert result[1] == secret

    def test_wrong_prefix(self):
        assert _parse_token("invalid_alice-tchap-fr_" + "a" * 64) is None

    def test_short_secret(self):
        assert _parse_token("colaig_alice-tchap-fr_toocourt") is None

    def test_non_hex_secret(self):
        assert _parse_token("colaig_alice-tchap-fr_" + "z" * 64) is None

    def test_no_slug(self):
        assert _parse_token("colaig__" + "a" * 64) is None


# =============================================================================
# ContextVar
# =============================================================================

class TestContextVar:
    def test_default_none(self):
        # Aucun token positionné → None
        assert get_current_token() is None

    def test_set_and_get(self):
        ctx = TokenContext(user_id="@alice:tchap.fr", scope="*", name="Test")
        set_current_token(ctx)
        assert get_current_token() is ctx
        set_current_token(None)  # reset

    def test_default_role_is_user(self):
        """TokenContext sans role explicite → role='user' (backward compat)."""
        ctx = TokenContext(user_id="@alice:tchap.fr")
        assert ctx.role == "user"

    def test_admin_role_explicit(self):
        ctx = TokenContext(user_id="@alice:tchap.fr", role="admin")
        assert ctx.role == "admin"


# =============================================================================
# TokenManager
# =============================================================================

def _make_storage() -> MagicMock:
    storage = MagicMock()
    storage.mkdir = AsyncMock()
    storage.upload = AsyncMock()
    storage.download = AsyncMock()
    storage.delete = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    storage.list_files = AsyncMock(return_value=[])
    return storage


class TestTokenManagerCreate:
    @pytest.mark.asyncio
    async def test_returns_valid_token_format(self):
        storage = _make_storage()
        tm = TokenManager(storage=storage, base_url="https://colaig.example.fr")
        raw = await tm.create("@alice:tchap.fr", name="Claude Desktop")
        assert raw.startswith("colaig_")
        parsed = _parse_token(raw)
        assert parsed is not None

    @pytest.mark.asyncio
    async def test_uploads_token_json(self):
        storage = _make_storage()
        tm = TokenManager(storage=storage)
        raw = await tm.create("@alice:tchap.fr", name="Test")
        # Deux uploads : token JSON + config MCP
        assert storage.upload.call_count == 2

    @pytest.mark.asyncio
    async def test_token_json_content(self):
        storage = _make_storage()
        uploads: list = []
        async def capture_upload(path, content):
            uploads.append((path, content))
        storage.upload = capture_upload
        tm = TokenManager(storage=storage)
        raw = await tm.create("@alice:tchap.fr", name="Test", scope="espace-rh")
        # Premier upload = token JSON
        path, content = uploads[0]
        data = json.loads(content)
        assert data["user_id"] == "@alice:tchap.fr"
        assert data["scope"] == "espace-rh"
        assert data["name"] == "Test"
        assert "created_at" in data
        assert data["role"] == "user"  # role persisté dans le JSON

    @pytest.mark.asyncio
    async def test_mcp_config_json_content(self):
        storage = _make_storage()
        uploads: list = []
        async def capture_upload(path, content):
            uploads.append((path, content))
        storage.upload = capture_upload
        tm = TokenManager(storage=storage, base_url="https://colaig.example.fr")
        raw = await tm.create("@alice:tchap.fr", name="Claude Desktop")
        # Second upload = config MCP
        path, content = uploads[1]
        cfg = json.loads(content)
        assert "mcpServers" in cfg
        server_cfg = list(cfg["mcpServers"].values())[0]
        assert server_cfg["url"] == "https://colaig.example.fr/mcp"
        assert raw in server_cfg["headers"]["Authorization"]


class TestTokenManagerResolve:
    @pytest.mark.asyncio
    async def test_valid_token_resolves(self):
        storage = _make_storage()
        tm = TokenManager(storage=storage)
        # Simuler un fichier token valide
        token_data = {
            "user_id": "@alice:tchap.fr",
            "scope": "*",
            "name": "Test",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        import secrets
        raw = "colaig_alice-tchap-fr_" + secrets.token_hex(32)
        ctx = await tm.resolve(raw)
        assert ctx is not None
        assert ctx.user_id == "@alice:tchap.fr"
        assert ctx.scope == "*"
        assert ctx.role == "user"  # token sans role → backward compat → "user"

    @pytest.mark.asyncio
    async def test_valid_token_resolves_admin_role(self):
        """Un token JSON avec role='admin' est résolu en TokenContext admin."""
        storage = _make_storage()
        tm = TokenManager(storage=storage)
        token_data = {
            "user_id": "@admin:tchap.fr",
            "role": "admin",
            "scope": "*",
            "name": "Admin",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        import secrets
        raw = "colaig_admin-tchap-fr_" + secrets.token_hex(32)
        ctx = await tm.resolve(raw)
        assert ctx is not None
        assert ctx.role == "admin"

    @pytest.mark.asyncio
    async def test_invalid_format_returns_none(self):
        storage = _make_storage()
        tm = TokenManager(storage=storage)
        assert await tm.resolve("not-a-token") is None

    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self):
        storage = _make_storage()
        storage.download = AsyncMock(side_effect=FileNotFoundError("absent"))
        tm = TokenManager(storage=storage)
        import secrets
        raw = "colaig_alice-tchap-fr_" + secrets.token_hex(32)
        assert await tm.resolve(raw) is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self):
        storage = _make_storage()
        tm = TokenManager(storage=storage)
        token_data = {
            "user_id": "@alice:tchap.fr",
            "scope": "*",
            "name": "Expired",
            "created_at": "2020-01-01T00:00:00+00:00",
            "expires_at": "2020-06-01T00:00:00+00:00",  # dans le passé
        }
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        import secrets
        raw = "colaig_alice-tchap-fr_" + secrets.token_hex(32)
        ctx = await tm.resolve(raw)
        assert ctx is None


class TestTokenManagerListRevoke:
    @pytest.mark.asyncio
    async def test_list_returns_masked_tokens(self):
        storage = _make_storage()
        token_data = {
            "user_id": "@alice:tchap.fr",
            "scope": "*",
            "name": "Claude Desktop",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        # Simuler un fichier listé
        fake_file = MagicMock()
        fake_file.path = "/alice_tchap_fr/.colaig/tokens/abc123.json"
        storage.list_files = AsyncMock(return_value=[fake_file])
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        tm = TokenManager(storage=storage)
        tokens = await tm.list_tokens("@alice:tchap.fr")
        assert len(tokens) == 1
        assert tokens[0]["name"] == "Claude Desktop"
        assert "user_id" not in tokens[0]  # secret non exposé

    @pytest.mark.asyncio
    async def test_revoke_existing_token(self):
        storage = _make_storage()
        token_data = {
            "user_id": "@alice:tchap.fr",
            "scope": "*",
            "name": "Claude Desktop",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }
        fake_file = MagicMock()
        fake_file.path = "/alice_tchap_fr/.colaig/tokens/abc123.json"
        storage.list_files = AsyncMock(return_value=[fake_file])
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        storage.delete = AsyncMock()
        tm = TokenManager(storage=storage)
        revoked = await tm.revoke("@alice:tchap.fr", "Claude Desktop")
        assert revoked is True
        assert storage.delete.called

    @pytest.mark.asyncio
    async def test_revoke_missing_token_returns_false(self):
        storage = _make_storage()
        storage.list_files = AsyncMock(return_value=[])
        tm = TokenManager(storage=storage)
        revoked = await tm.revoke("@alice:tchap.fr", "Inexistant")
        assert revoked is False


# =============================================================================
# require_admin
# =============================================================================


class TestRequireAdmin:
    def test_none_token_returns_error(self):
        """Pas de token → erreur d'authentification."""
        err = require_admin(None)
        assert err is not None
        data = json.loads(err)
        assert "error" in data
        assert "Authentification" in data["error"]

    def test_user_role_returns_error(self):
        """Token user → erreur droits insuffisants."""
        ctx = TokenContext(user_id="@alice:tchap.fr", role="user")
        err = require_admin(ctx)
        assert err is not None
        data = json.loads(err)
        assert "administrateur" in data["error"].lower()

    def test_admin_role_returns_none(self):
        """Token admin → None (accès autorisé)."""
        ctx = TokenContext(user_id="@alice:tchap.fr", role="admin")
        assert require_admin(ctx) is None

    def test_default_role_is_user(self):
        """TokenContext sans role → role='user' → accès refusé."""
        ctx = TokenContext(user_id="@alice:tchap.fr")
        assert require_admin(ctx) is not None


# =============================================================================
# TokenManager — rôle admin via COLAIG_ADMIN_USER_IDS
# =============================================================================


class TestAdminRole:
    @pytest.mark.asyncio
    async def test_admin_user_gets_admin_role(self, monkeypatch):
        """User dans COLAIG_ADMIN_USER_IDS → token avec role='admin'."""
        monkeypatch.setenv("COLAIG_ADMIN_USER_IDS", "@admin:tchap.fr")
        storage = _make_storage()
        uploads: list = []
        async def capture(path, content):
            uploads.append((path, content))
        storage.upload = capture
        tm = TokenManager(storage=storage)
        await tm.create("@admin:tchap.fr", name="Admin Token")
        path, content = uploads[0]
        data = json.loads(content)
        assert data["role"] == "admin"

    @pytest.mark.asyncio
    async def test_non_admin_user_gets_user_role(self, monkeypatch):
        """User absent de COLAIG_ADMIN_USER_IDS → token avec role='user'."""
        monkeypatch.setenv("COLAIG_ADMIN_USER_IDS", "@admin:tchap.fr")
        storage = _make_storage()
        uploads: list = []
        async def capture(path, content):
            uploads.append((path, content))
        storage.upload = capture
        tm = TokenManager(storage=storage)
        await tm.create("@alice:tchap.fr", name="User Token")
        path, content = uploads[0]
        data = json.loads(content)
        assert data["role"] == "user"

    @pytest.mark.asyncio
    async def test_empty_admin_list_gives_user_role(self, monkeypatch):
        """COLAIG_ADMIN_USER_IDS vide → tout le monde est user."""
        monkeypatch.delenv("COLAIG_ADMIN_USER_IDS", raising=False)
        storage = _make_storage()
        uploads: list = []
        async def capture(path, content):
            uploads.append((path, content))
        storage.upload = capture
        tm = TokenManager(storage=storage)
        await tm.create("@alice:tchap.fr", name="Test")
        path, content = uploads[0]
        data = json.loads(content)
        assert data["role"] == "user"

    @pytest.mark.asyncio
    async def test_multiple_admins(self, monkeypatch):
        """Liste de plusieurs admins séparés par virgule."""
        monkeypatch.setenv("COLAIG_ADMIN_USER_IDS", "@alice:tchap.fr,@bob:tchap.fr")
        storage = _make_storage()
        uploads: list = []
        async def capture(path, content):
            uploads.append((path, content))
        storage.upload = capture
        tm = TokenManager(storage=storage)
        await tm.create("@bob:tchap.fr", name="Bob Admin")
        path, content = uploads[0]
        data = json.loads(content)
        assert data["role"] == "admin"

    @pytest.mark.asyncio
    async def test_backward_compat_token_without_role(self):
        """Token JSON sans champ 'role' → resolve() retourne role='user'."""
        storage = _make_storage()
        token_data = {
            "user_id": "@alice:tchap.fr",
            "scope": "*",
            "name": "Old Token",
            "created_at": "2025-01-01T00:00:00+00:00",
            "expires_at": None,
            # pas de 'role' — ancien format
        }
        storage.download = AsyncMock(return_value=json.dumps(token_data).encode())
        import secrets
        raw = "colaig_alice-tchap-fr_" + secrets.token_hex(32)
        tm = TokenManager(storage=storage)
        ctx = await tm.resolve(raw)
        assert ctx is not None
        assert ctx.role == "user"
