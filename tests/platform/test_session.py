# SPDX-License-Identifier: MIT
"""Tests for platform.session - HMAC-SHA256 signed session management."""

import pytest
import pytest_asyncio

from app.platform.models import PlatformAdmin
from app.platform.registry import PlatformRegistry
from app.platform.session import SessionManager


@pytest_asyncio.fixture
async def registry(tmp_path):
    db_path = str(tmp_path / "test_session.db")
    reg = PlatformRegistry(db_path=db_path)
    await reg.open()
    yield reg
    await reg.close()


@pytest.fixture
def session_mgr(registry):
    return SessionManager(secret="test-secret-at-least-32-characters-long", registry=registry)


class TestSessionManager:
    def test_init_requires_secret(self, registry):
        with pytest.raises(ValueError, match="must not be empty"):
            SessionManager(secret="", registry=registry)

    @pytest.mark.asyncio
    async def test_create_and_validate_session(self, session_mgr):
        cookie = await session_mgr.create_session(
            email="user@gouv.fr",
            extra_data={"role": "admin"},
        )

        assert cookie  # non-empty string
        assert isinstance(cookie, str)

        session = await session_mgr.validate_session(cookie)
        assert session is not None
        assert session["email"] == "user@gouv.fr"

    @pytest.mark.asyncio
    async def test_invalid_cookie_returns_none(self, session_mgr):
        result = await session_mgr.validate_session("garbage-cookie-value")
        assert result is None

    @pytest.mark.asyncio
    async def test_tampered_cookie_returns_none(self, session_mgr):
        cookie = await session_mgr.create_session(email="user@gouv.fr")

        # Tamper with the cookie
        import base64
        raw = base64.urlsafe_b64decode(cookie.encode("ascii")).decode("utf-8")
        parts = raw.split(":")
        parts[2] = "0" * len(parts[2])  # Replace signature
        tampered = base64.urlsafe_b64encode(":".join(parts).encode("utf-8")).decode("ascii")

        result = await session_mgr.validate_session(tampered)
        assert result is None

    @pytest.mark.asyncio
    async def test_destroy_session(self, session_mgr):
        cookie = await session_mgr.create_session(email="user@gouv.fr")

        await session_mgr.destroy_session(cookie)

        result = await session_mgr.validate_session(cookie)
        assert result is None

    @pytest.mark.asyncio
    async def test_different_secrets_reject_cookies(self, registry):
        mgr1 = SessionManager(secret="secret-one-long-enough-for-test!", registry=registry)
        mgr2 = SessionManager(secret="secret-two-long-enough-for-test!", registry=registry)

        cookie = await mgr1.create_session(email="user@gouv.fr")

        # Same DB, but different secret - signature check should fail
        result = await mgr2.validate_session(cookie)
        assert result is None
