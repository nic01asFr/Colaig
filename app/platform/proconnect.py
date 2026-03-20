# SPDX-License-Identifier: MIT
"""
ProConnect OIDC client for Colaig platform.

Implements the Authorization Code flow for ProConnect (formerly AgentConnect).
When ProConnect credentials are not configured, falls back to a simple
password-based authentication for dev/test.

ProConnect technical reference:
- Discovery: {issuer}/.well-known/openid-configuration
- Auth endpoint: /api/v2/authorize
- Token endpoint: /api/v2/token
- Userinfo endpoint: /api/v2/userinfo
- Logout endpoint: /api/v2/session/end
- Authorization code TTL: 30 seconds
- Access token TTL: 60 seconds
- Session duration: 12 hours
- State & nonce: minimum 32 characters
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OIDCConfig:
    """ProConnect OIDC configuration."""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    issuer: str = "https://auth.proconnect.gouv.fr/api/v2"
    scopes: list[str] = field(default_factory=lambda: ["openid"])
    enabled: bool = False
    # Fallback for dev/test
    admin_password: str = ""


@dataclass
class OIDCUserInfo:
    """User information returned by ProConnect."""
    sub: str  # Unique identifier
    email: str = ""
    given_name: str = ""
    usual_name: str = ""
    siret: str = ""
    uid: str = ""
    idp_id: str = ""

    @property
    def display_name(self) -> str:
        parts = [self.given_name, self.usual_name]
        return " ".join(p for p in parts if p) or self.email


class ProConnectClient:
    """
    ProConnect OIDC client.

    Usage:
        client = ProConnectClient(oidc_config)

        # 1. Generate login URL
        url, state, nonce = client.get_authorization_url()
        # -> redirect user to url, store state+nonce in session

        # 2. Handle callback
        user_info = await client.handle_callback(code, stored_state, stored_nonce)

        # 3. Logout
        logout_url = client.get_logout_url(id_token)
    """

    def __init__(self, config: OIDCConfig):
        self.config = config
        self._discovery: Optional[dict] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def is_enabled(self) -> bool:
        return (
            self.config.enabled
            and bool(self.config.client_id)
            and bool(self.config.client_secret)
        )

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def discover(self) -> dict:
        """Fetch OpenID Connect discovery document."""
        if self._discovery:
            return self._discovery

        discovery_url = f"{self.config.issuer}/.well-known/openid-configuration"
        client = await self._get_http_client()
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        self._discovery = resp.json()
        logger.info(f"ProConnect discovery loaded from {discovery_url}")
        return self._discovery

    def get_authorization_url(self) -> tuple[str, str, str]:
        """
        Build the authorization URL for ProConnect.

        Returns:
            (authorization_url, state, nonce) - store state and nonce in session
        """
        state = secrets.token_urlsafe(48)  # >= 32 chars required
        nonce = secrets.token_urlsafe(48)

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "nonce": nonce,
        }

        # Use discovery if available, otherwise construct URL
        auth_endpoint = f"{self.config.issuer}/authorize"
        url = f"{auth_endpoint}?{urlencode(params)}"

        return url, state, nonce

    async def exchange_code(self, code: str) -> dict:
        """
        Exchange authorization code for tokens.
        Code expires in 30s, must be called immediately.

        Returns:
            Token response dict with access_token, id_token, etc.
        """
        token_endpoint = f"{self.config.issuer}/token"

        client = await self._get_http_client()
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_userinfo(self, access_token: str) -> OIDCUserInfo:
        """
        Fetch user information from ProConnect userinfo endpoint.
        Access token expires in 60s, must be called immediately after exchange.
        """
        userinfo_endpoint = f"{self.config.issuer}/userinfo"

        client = await self._get_http_client()
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()

        # ProConnect returns a JWT, but for now we handle JSON response
        data = resp.json()
        return OIDCUserInfo(
            sub=data.get("sub", ""),
            email=data.get("email", ""),
            given_name=data.get("given_name", ""),
            usual_name=data.get("usual_name", ""),
            siret=data.get("siret", ""),
            uid=data.get("uid", ""),
            idp_id=data.get("idp_id", ""),
        )

    async def handle_callback(
        self, code: str, expected_state: str, received_state: str, stored_nonce: str
    ) -> OIDCUserInfo:
        """
        Complete the OIDC callback flow:
        1. Verify state (CSRF protection)
        2. Exchange code for tokens
        3. Fetch userinfo

        Raises ValueError on state mismatch or auth failure.
        """
        # Verify state
        if not secrets.compare_digest(expected_state, received_state):
            raise ValueError("State mismatch - possible CSRF attack")

        # Exchange code for tokens (must be done within 30s)
        tokens = await self.exchange_code(code)

        access_token = tokens.get("access_token")
        if not access_token:
            raise ValueError("No access_token in token response")

        # Fetch user info (must be done within 60s)
        user_info = await self.get_userinfo(access_token)

        # Store id_token for logout
        user_info._id_token = tokens.get("id_token", "")

        return user_info

    def get_logout_url(self, id_token: str, post_logout_redirect_uri: str = "") -> str:
        """Build the ProConnect logout URL."""
        params = {"id_token_hint": id_token}
        if post_logout_redirect_uri:
            state = secrets.token_urlsafe(32)
            params["post_logout_redirect_uri"] = post_logout_redirect_uri
            params["state"] = state

        logout_endpoint = f"{self.config.issuer}/session/end"
        return f"{logout_endpoint}?{urlencode(params)}"


# === Mock / Fallback for dev/test ===


class MockProConnectClient:
    """
    Fallback auth client for dev/test when ProConnect is not configured.
    Authenticates via a simple admin password.
    """

    def __init__(self, admin_password: str, operator_emails: list[str]):
        self.admin_password = admin_password
        self.operator_emails = operator_emails

    @property
    def is_enabled(self) -> bool:
        return False  # ProConnect is not enabled, this is the fallback

    def authenticate(self, email: str, password: str) -> Optional[OIDCUserInfo]:
        """
        Simple password authentication for dev/test.
        Returns OIDCUserInfo if valid, None otherwise.
        """
        if not self.admin_password:
            logger.warning("No admin password configured, authentication disabled")
            return None

        if not secrets.compare_digest(password, self.admin_password):
            logger.warning(f"Failed password login attempt for {email}")
            return None

        return OIDCUserInfo(
            sub=hashlib.sha256(email.encode()).hexdigest()[:16],
            email=email,
            given_name=email.split("@")[0],
            usual_name="",
            siret="",
            uid="",
            idp_id="mock",
        )

    async def close(self) -> None:
        pass  # Nothing to close


def create_auth_client(
    *,
    proconnect_enabled: bool = False,
    client_id: str = "",
    client_secret: str = "",
    redirect_uri: str = "",
    issuer: str = "https://auth.proconnect.gouv.fr/api/v2",
    admin_password: str = "",
    operator_emails: Optional[list[str]] = None,
) -> ProConnectClient | MockProConnectClient:
    """
    Factory: create the appropriate auth client based on configuration.

    If ProConnect is enabled and credentials are present, returns ProConnectClient.
    Otherwise returns MockProConnectClient for dev/test.
    """
    if proconnect_enabled and client_id and client_secret:
        logger.info("ProConnect OIDC authentication enabled")
        return ProConnectClient(
            OIDCConfig(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                issuer=issuer,
                enabled=True,
            )
        )

    logger.info("ProConnect not configured, using password fallback for dev/test")
    return MockProConnectClient(
        admin_password=admin_password,
        operator_emails=operator_emails or [],
    )
