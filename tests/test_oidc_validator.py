"""Tests pour OIDCValidator — validation JWT RS256 via JWKS."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from colaig.auth.oidc_validator import OIDCValidationError, OIDCValidator
from colaig.auth.tokens import TokenContext

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_validator(**kwargs) -> OIDCValidator:
    defaults = dict(
        issuer="https://sso.org.fr/realms/org",
        audience="colaig-instance-rh",
        jwks_uri="https://sso.org.fr/realms/org/protocol/openid-connect/certs",
    )
    defaults.update(kwargs)
    return OIDCValidator(**defaults)


def _mock_jwt(payload: dict, kid: str = "key1"):
    """Retourne un mock JWT header + payload pour les tests sans PyJWT."""
    mock_jwt = MagicMock()
    mock_jwt.get_unverified_header.return_value = {"alg": "RS256", "kid": kid}
    mock_jwt.decode.return_value = payload
    mock_jwt.ExpiredSignatureError = Exception
    mock_jwt.InvalidAudienceError = Exception
    mock_jwt.InvalidIssuerError = Exception
    mock_jwt.InvalidTokenError = Exception
    return mock_jwt


def _mock_rsa_algorithm(public_key=MagicMock()):
    mock = MagicMock()
    mock.from_jwk.return_value = public_key
    return mock


# ── Tests OIDCValidator.__init__ ──────────────────────────────────────────────


class TestOIDCValidatorInit:
    def test_stores_params(self):
        v = _make_validator()
        assert v._issuer == "https://sso.org.fr/realms/org"
        assert v._audience == "colaig-instance-rh"
        assert v._jwks_uri == "https://sso.org.fr/realms/org/protocol/openid-connect/certs"

    def test_cache_empty_at_start(self):
        v = _make_validator()
        assert v._jwks_cache == {}
        assert v._cache_fetched_at == 0.0


# ── Tests validate() — cas nominaux ───────────────────────────────────────────


class TestValidateSuccess:
    @pytest.mark.asyncio
    async def test_returns_token_context_with_email(self):
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        payload = {
            "email": "alice@org.fr",
            "sub": "uid-123",
            "name": "Alice Dupont",
        }
        mock_jwt = _mock_jwt(payload)

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            result = await v.validate("fake.jwt.token")

        assert isinstance(result, TokenContext)
        assert result.user_id == "alice@org.fr"
        assert result.name == "Alice Dupont"
        assert result.scope == "*"
        assert result.role == "user"

    @pytest.mark.asyncio
    async def test_falls_back_to_preferred_username(self):
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        payload = {"preferred_username": "alice", "sub": "uid-123"}
        mock_jwt = _mock_jwt(payload)

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            result = await v.validate("fake.jwt.token")

        assert result.user_id == "alice"

    @pytest.mark.asyncio
    async def test_falls_back_to_sub(self):
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        payload = {"sub": "uid-only-123"}
        mock_jwt = _mock_jwt(payload)

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            result = await v.validate("fake.jwt.token")

        assert result.user_id == "uid-only-123"

    @pytest.mark.asyncio
    async def test_role_always_user(self):
        """Le rôle est toujours 'user' — jamais depuis le token client."""
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        # Même si le token contient un champ role=admin, on ignore
        payload = {"email": "hacker@org.fr", "role": "admin"}
        mock_jwt = _mock_jwt(payload)

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            result = await v.validate("fake.jwt.token")

        assert result.role == "user"


# ── Tests validate() — cas d'erreur ───────────────────────────────────────────


class TestValidateErrors:
    @pytest.mark.asyncio
    async def test_unsupported_algorithm(self):
        v = _make_validator()
        mock_jwt = MagicMock()
        mock_jwt.get_unverified_header.return_value = {"alg": "HS256", "kid": "k1"}

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            with pytest.raises(OIDCValidationError, match="Algorithme non supporté"):
                await v.validate("fake.jwt.token")

    @pytest.mark.asyncio
    async def test_malformed_header(self):
        v = _make_validator()
        mock_jwt = MagicMock()
        mock_jwt.get_unverified_header.side_effect = Exception("bad base64")

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            with pytest.raises(OIDCValidationError, match="Header JWT malformé"):
                await v.validate("notajwt")

    @pytest.mark.asyncio
    async def test_expired_token(self):
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        mock_jwt = MagicMock()
        mock_jwt.get_unverified_header.return_value = {"alg": "RS256", "kid": "key1"}

        class ExpiredSignatureError(Exception):
            pass

        mock_jwt.ExpiredSignatureError = ExpiredSignatureError
        mock_jwt.InvalidAudienceError = Exception
        mock_jwt.InvalidIssuerError = Exception
        mock_jwt.InvalidTokenError = Exception
        mock_jwt.decode.side_effect = ExpiredSignatureError("exp")

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            with pytest.raises(OIDCValidationError, match="Token expiré"):
                await v.validate("fake.jwt.token")

    @pytest.mark.asyncio
    async def test_no_user_id_in_payload(self):
        v = _make_validator()
        v._jwks_cache = {"key1": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        payload = {}  # Aucun email/preferred_username/sub
        mock_jwt = _mock_jwt(payload)

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            with pytest.raises(OIDCValidationError, match="Aucun identifiant utilisateur"):
                await v.validate("fake.jwt.token")

    @pytest.mark.asyncio
    async def test_unknown_kid(self):
        v = _make_validator()
        # Cache avec une clé différente
        v._jwks_cache = {"other-key": MagicMock()}
        v._cache_fetched_at = time.monotonic()

        mock_jwt = MagicMock()
        mock_jwt.get_unverified_header.return_value = {"alg": "RS256", "kid": "unknown-kid"}

        # _fetch_jwks ne trouve pas non plus le kid
        async def fake_fetch(alg):
            pass  # cache reste inchangé

        with patch("colaig.auth.oidc_validator._require_pyjwt", return_value=(mock_jwt, MagicMock())):
            with patch.object(v, "_fetch_jwks", fake_fetch):
                with pytest.raises(OIDCValidationError, match="unknown-kid"):
                    await v.validate("fake.jwt.token")


# ── Tests cache JWKS ──────────────────────────────────────────────────────────


class TestJWKSCache:
    @pytest.mark.asyncio
    async def test_cache_hit_no_fetch(self):
        """Si le cache est frais et le kid connu, pas de fetch réseau."""
        v = _make_validator()
        mock_key = MagicMock()
        v._jwks_cache = {"key1": mock_key}
        v._cache_fetched_at = time.monotonic()  # frais

        fetch_called = []

        async def fake_fetch(alg):
            fetch_called.append(1)

        with patch.object(v, "_fetch_jwks", fake_fetch):
            key = await v._get_public_key("key1", MagicMock())

        assert key is mock_key
        assert fetch_called == []

    @pytest.mark.asyncio
    async def test_cache_miss_triggers_fetch(self):
        """Si le kid est inconnu, le JWKS est re-fetché."""
        v = _make_validator()
        new_key = MagicMock()

        async def fake_fetch(alg):
            v._jwks_cache = {"new-kid": new_key}
            v._cache_fetched_at = time.monotonic()

        with patch.object(v, "_fetch_jwks", fake_fetch):
            key = await v._get_public_key("new-kid", MagicMock())

        assert key is new_key

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_fetch(self):
        """Cache expiré (> TTL) → re-fetch même si le kid est connu."""
        v = _make_validator()
        old_key = MagicMock()
        new_key = MagicMock()
        v._jwks_cache = {"key1": old_key}
        v._cache_fetched_at = time.monotonic() - 7200  # expiré (2h)

        async def fake_fetch(alg):
            v._jwks_cache = {"key1": new_key}
            v._cache_fetched_at = time.monotonic()

        with patch.object(v, "_fetch_jwks", fake_fetch):
            key = await v._get_public_key("key1", MagicMock())

        assert key is new_key

    @pytest.mark.asyncio
    async def test_fetch_network_error_preserves_cache(self):
        """Erreur réseau → cache existant conservé (graceful degradation)."""
        import httpx

        v = _make_validator()
        existing_key = MagicMock()
        v._jwks_cache = {"key1": existing_key}
        v._cache_fetched_at = time.monotonic() - 7200  # expiré

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("réseau indisponible")
            mock_client_cls.return_value = mock_client

            RSAAlgorithm = MagicMock()
            await v._fetch_jwks(RSAAlgorithm)

        # Cache existant conservé
        assert v._jwks_cache == {"key1": existing_key}


# ── Tests _require_pyjwt ──────────────────────────────────────────────────────


class TestRequirePyJWT:
    def test_raises_importerror_if_absent(self):
        from colaig.auth.oidc_validator import _require_pyjwt

        with patch.dict("sys.modules", {"jwt": None, "jwt.algorithms": None}):
            import sys
            jwt_backup = sys.modules.pop("jwt", None)
            try:
                with pytest.raises((ImportError, Exception)):
                    _require_pyjwt()
            finally:
                if jwt_backup is not None:
                    sys.modules["jwt"] = jwt_backup


# ── Tests PlatformPolicy + validate_client_against_policy ────────────────────


class TestPlatformPolicy:
    def test_default_policy_no_constraints(self):
        from colaig.models import PlatformPolicy

        policy = PlatformPolicy()
        assert policy.allowed_storage_backends == []
        assert policy.allowed_llm_endpoints == []
        assert policy.allowed_mcp_auth_modes == []
        assert policy.enforce_mcp_auth is False

    def test_load_platform_policy_absent_file(self, tmp_path):
        from colaig.config import load_platform_policy

        policy = load_platform_policy(tmp_path / "nonexistent.yml")
        assert policy.allowed_storage_backends == []

    def test_load_platform_policy_from_yaml(self, tmp_path):
        from colaig.config import load_platform_policy

        yml = tmp_path / "clients.yml"
        yml.write_text(
            "platform_policy:\n"
            "  allowed_storage_backends: [webdav, bigfolder]\n"
            "  allowed_llm_endpoints:\n"
            "    - https://albert.api.etalab.gouv.fr\n"
            "  allowed_mcp_auth_modes: [token, oidc]\n"
            "  enforce_mcp_auth: true\n"
        )
        policy = load_platform_policy(yml)
        assert policy.allowed_storage_backends == ["webdav", "bigfolder"]
        assert policy.allowed_llm_endpoints == ["https://albert.api.etalab.gouv.fr"]
        assert policy.allowed_mcp_auth_modes == ["token", "oidc"]
        assert policy.enforce_mcp_auth is True

    def test_validate_ok(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy(
            allowed_storage_backends=["webdav", "bigfolder"],
            allowed_mcp_auth_modes=["token", "oidc"],
        )
        config = ColaigConfig(storage_backend="webdav", mcp_auth_mode="token")
        # Ne doit pas lever
        validate_client_against_policy("client-1", config, policy)

    def test_validate_storage_violation(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy(allowed_storage_backends=["webdav", "bigfolder"])
        config = ColaigConfig(storage_backend="s3")

        with pytest.raises(ValueError, match="s3"):
            validate_client_against_policy("client-bad", config, policy)

    def test_validate_mcp_auth_mode_violation(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy(allowed_mcp_auth_modes=["oidc"])
        config = ColaigConfig(mcp_auth_mode="token")

        with pytest.raises(ValueError, match="token"):
            validate_client_against_policy("client-bad", config, policy)

    def test_validate_llm_endpoint_violation(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy(
            allowed_llm_endpoints=["https://albert.api.etalab.gouv.fr"]
        )
        config = ColaigConfig(albert_api_url="https://api.openai.com")

        with pytest.raises(ValueError, match="openai"):
            validate_client_against_policy("client-bad", config, policy)

    def test_validate_llm_endpoint_prefix_match(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy(
            allowed_llm_endpoints=["https://albert.api.etalab.gouv.fr"]
        )
        config = ColaigConfig(albert_api_url="https://albert.api.etalab.gouv.fr/v1")
        # Préfixe match → OK
        validate_client_against_policy("client-ok", config, policy)

    def test_validate_empty_policy_allows_all(self):
        from colaig.config import validate_client_against_policy
        from colaig.models import ColaigConfig, PlatformPolicy

        policy = PlatformPolicy()  # aucune contrainte
        config = ColaigConfig(
            storage_backend="s3",
            mcp_auth_mode="token",
            albert_api_url="https://api.openai.com",
        )
        # Ne doit pas lever
        validate_client_against_policy("client-free", config, policy)
