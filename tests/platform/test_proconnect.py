# SPDX-License-Identifier: MIT
"""Tests for platform.proconnect - Auth client and mock."""

import pytest

from app.platform.proconnect import (
    MockProConnectClient,
    OIDCConfig,
    OIDCUserInfo,
    ProConnectClient,
    create_auth_client,
)


class TestMockProConnectClient:
    def test_authenticate_success(self):
        client = MockProConnectClient(
            admin_password="test123",
            operator_emails=["admin@gouv.fr"],
        )
        result = client.authenticate("admin@gouv.fr", "test123")
        assert result is not None
        assert isinstance(result, OIDCUserInfo)
        assert result.email == "admin@gouv.fr"
        assert result.idp_id == "mock"

    def test_authenticate_wrong_password(self):
        client = MockProConnectClient(admin_password="test123", operator_emails=[])
        result = client.authenticate("admin@gouv.fr", "wrong")
        assert result is None

    def test_authenticate_empty_password_disabled(self):
        client = MockProConnectClient(admin_password="", operator_emails=[])
        result = client.authenticate("admin@gouv.fr", "anything")
        assert result is None

    def test_is_enabled_returns_false(self):
        client = MockProConnectClient(admin_password="x", operator_emails=[])
        assert client.is_enabled is False

    def test_display_name_from_email(self):
        client = MockProConnectClient(admin_password="test", operator_emails=[])
        result = client.authenticate("jean.dupont@gouv.fr", "test")
        assert result.display_name == "jean.dupont"


class TestProConnectClient:
    def test_is_enabled_with_credentials(self):
        client = ProConnectClient(
            OIDCConfig(
                client_id="cid",
                client_secret="csecret",
                redirect_uri="https://example.com/callback",
                enabled=True,
            )
        )
        assert client.is_enabled is True

    def test_is_enabled_without_credentials(self):
        client = ProConnectClient(OIDCConfig(enabled=True))
        assert client.is_enabled is False

    def test_get_authorization_url(self):
        client = ProConnectClient(
            OIDCConfig(
                client_id="my-client-id",
                client_secret="secret",
                redirect_uri="https://app.gouv.fr/auth/callback",
                issuer="https://auth.proconnect.gouv.fr/api/v2",
                enabled=True,
            )
        )
        url, state, nonce = client.get_authorization_url()

        assert "my-client-id" in url
        assert "auth.proconnect.gouv.fr" in url
        assert "redirect_uri=" in url
        assert len(state) >= 32
        assert len(nonce) >= 32
        assert state != nonce  # Different random values

    def test_get_logout_url(self):
        client = ProConnectClient(
            OIDCConfig(
                issuer="https://auth.proconnect.gouv.fr/api/v2",
                enabled=True,
            )
        )
        url = client.get_logout_url("my-id-token", post_logout_redirect_uri="https://app.gouv.fr")
        assert "session/end" in url
        assert "my-id-token" in url
        assert "post_logout_redirect_uri" in url


class TestCreateAuthClient:
    def test_creates_mock_when_not_enabled(self):
        client = create_auth_client(
            proconnect_enabled=False,
            admin_password="dev123",
            operator_emails=["op@gouv.fr"],
        )
        assert isinstance(client, MockProConnectClient)
        assert client.admin_password == "dev123"

    def test_creates_mock_when_missing_credentials(self):
        client = create_auth_client(
            proconnect_enabled=True,
            client_id="",  # Missing
            client_secret="secret",
            admin_password="fallback",
        )
        assert isinstance(client, MockProConnectClient)

    def test_creates_real_client_with_full_credentials(self):
        client = create_auth_client(
            proconnect_enabled=True,
            client_id="cid",
            client_secret="csecret",
            redirect_uri="https://app.gouv.fr/callback",
        )
        assert isinstance(client, ProConnectClient)
        assert client.is_enabled is True
