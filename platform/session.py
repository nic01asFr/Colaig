# SPDX-License-Identifier: MIT
"""
Web session management with HMAC-SHA256 signed cookies.

Sessions are stored in the platform registry (SQLite).
Cookie value = base64(session_id:timestamp:hmac_signature).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .registry import PlatformRegistry

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "colaig_session"
SESSION_DURATION_HOURS = 8


class SessionManager:
    """Manages web sessions with HMAC-SHA256 signed cookies."""

    def __init__(self, secret: str, registry: PlatformRegistry):
        if not secret:
            raise ValueError("Session secret must not be empty")
        self._secret = secret.encode("utf-8")
        self._registry = registry

    def _sign(self, payload: str) -> str:
        """Create HMAC-SHA256 signature for a payload."""
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _make_cookie_value(self, session_id: str) -> str:
        """Create a signed cookie value: base64(session_id:timestamp:signature)."""
        ts = str(int(time.time()))
        payload = f"{session_id}:{ts}"
        sig = self._sign(payload)
        raw = f"{payload}:{sig}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    def _verify_cookie_value(self, cookie_value: str) -> Optional[str]:
        """Verify and extract session_id from a signed cookie. Returns None if invalid."""
        try:
            raw = base64.urlsafe_b64decode(cookie_value.encode("ascii")).decode("utf-8")
            parts = raw.split(":")
            if len(parts) != 3:
                return None
            session_id, ts, sig = parts
            expected_sig = self._sign(f"{session_id}:{ts}")
            if not hmac.compare_digest(sig, expected_sig):
                logger.warning("Invalid session signature")
                return None
            return session_id
        except Exception:
            logger.warning("Failed to decode session cookie")
            return None

    async def create_session(self, email: str, extra_data: Optional[dict] = None) -> str:
        """Create a new session, store it, return the signed cookie value."""
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=SESSION_DURATION_HOURS)

        await self._registry.save_session(
            session_id=session_id,
            email=email,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            data=extra_data or {},
        )

        return self._make_cookie_value(session_id)

    async def validate_session(self, cookie_value: str) -> Optional[dict]:
        """
        Validate a session cookie.
        Returns session data dict (with 'email') or None if invalid/expired.
        """
        session_id = self._verify_cookie_value(cookie_value)
        if not session_id:
            return None

        session = await self._registry.get_session(session_id)
        if not session:
            return None

        # Check expiration
        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            await self._registry.delete_session(session_id)
            logger.info(f"Session expired for {session['email']}")
            return None

        return session

    async def destroy_session(self, cookie_value: str) -> None:
        """Invalidate a session."""
        session_id = self._verify_cookie_value(cookie_value)
        if session_id:
            await self._registry.delete_session(session_id)

    async def cleanup_expired(self) -> int:
        """Remove all expired sessions from the DB."""
        now_iso = datetime.now(timezone.utc).isoformat()
        return await self._registry.cleanup_expired_sessions(now_iso)
