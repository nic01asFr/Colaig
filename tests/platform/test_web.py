# SPDX-License-Identifier: MIT
"""Tests for platform.web - Starlette API endpoints."""

import json

import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from app.platform.models import AdminRole, BotInstanceConfig, PlatformAdmin
from app.platform.proconnect import MockProConnectClient
from app.platform.registry import PlatformRegistry
from app.platform.session import SessionManager
from app.platform.web import create_app


@pytest_asyncio.fixture
async def registry(tmp_path):
    db_path = str(tmp_path / "test_web.db")
    reg = PlatformRegistry(db_path=db_path)
    await reg.open()
    yield reg
    await reg.close()


@pytest.fixture
def app(registry):
    """Create a test Starlette app with mock auth."""
    session_mgr = SessionManager(
        secret="test-secret-long-enough-for-hmac-signing",
        registry=registry,
    )
    auth_client = MockProConnectClient(
        admin_password="testpass",
        operator_emails=["operator@gouv.fr"],
    )

    # Dummy runner
    class DummyRunner:
        running_instances = {}

        async def start_instance(self, instance):
            pass

        async def stop_instance(self, instance_id):
            pass

    return create_app(
        registry=registry,
        session_manager=session_mgr,
        auth_client=auth_client,
        runner=DummyRunner(),
    )


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "running_instances" in data


class TestPasswordLogin:
    def test_successful_login(self, client):
        resp = client.post("/login", json={
            "email": "operator@gouv.fr",
            "password": "testpass",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["email"] == "operator@gouv.fr"
        assert data["role"] == AdminRole.OPERATOR
        assert "colaig_session" in resp.cookies

    def test_wrong_password(self, client):
        resp = client.post("/login", json={
            "email": "user@gouv.fr",
            "password": "wrong",
        })
        assert resp.status_code == 401

    def test_missing_fields(self, client):
        resp = client.post("/login", json={"email": "user@gouv.fr"})
        assert resp.status_code == 400

    def test_invalid_json(self, client):
        resp = client.post("/login", content=b"not json", headers={"content-type": "application/json"})
        assert resp.status_code == 400


class TestAuthRequired:
    def test_me_without_auth(self, client):
        resp = client.get("/api/me")
        assert resp.status_code == 401

    def test_instances_without_auth(self, client):
        resp = client.get("/api/instances")
        assert resp.status_code == 401


def _login(client, email="operator@gouv.fr", password="testpass"):
    """Helper to login and return cookies."""
    resp = client.post("/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.cookies


class TestMeEndpoint:
    def test_me_with_auth(self, client):
        cookies = _login(client)
        resp = client.get("/api/me", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "operator@gouv.fr"
        assert data["role"] == AdminRole.OPERATOR


class TestInstancesCRUD:
    def test_create_instance(self, client):
        cookies = _login(client)
        resp = client.post("/api/instances", json={
            "name": "Bot Test",
            "matrix_homeserver": "https://matrix.tchap.gouv.fr",
            "matrix_username": "bot",
            "matrix_password": "pass",
        }, cookies=cookies)
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["instance_id"]

    def test_list_instances(self, client):
        cookies = _login(client)
        # Create one
        client.post("/api/instances", json={"name": "Bot 1"}, cookies=cookies)
        client.post("/api/instances", json={"name": "Bot 2"}, cookies=cookies)

        resp = client.get("/api/instances", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["instances"]) == 2

    def test_get_instance_hides_secrets(self, client):
        cookies = _login(client)
        create_resp = client.post("/api/instances", json={
            "name": "Secret Bot",
            "matrix_password": "super-secret",
            "albert_api_token": "my-token",
        }, cookies=cookies)
        iid = create_resp.json()["instance_id"]

        resp = client.get(f"/api/instances/{iid}", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert "matrix_password" not in data
        assert "albert_api_token" not in data

    def test_update_instance(self, client):
        cookies = _login(client)
        create_resp = client.post("/api/instances", json={"name": "Old Name"}, cookies=cookies)
        iid = create_resp.json()["instance_id"]

        resp = client.put(f"/api/instances/{iid}", json={"name": "New Name"}, cookies=cookies)
        assert resp.status_code == 200

        get_resp = client.get(f"/api/instances/{iid}", cookies=cookies)
        assert get_resp.json()["name"] == "New Name"

    def test_delete_instance(self, client):
        cookies = _login(client)
        create_resp = client.post("/api/instances", json={"name": "To Delete"}, cookies=cookies)
        iid = create_resp.json()["instance_id"]

        resp = client.delete(f"/api/instances/{iid}", cookies=cookies)
        assert resp.status_code == 200

        get_resp = client.get(f"/api/instances/{iid}", cookies=cookies)
        assert get_resp.status_code == 404

    def test_get_nonexistent_instance(self, client):
        cookies = _login(client)
        resp = client.get("/api/instances/nonexistent", cookies=cookies)
        assert resp.status_code == 404


class TestLogout:
    def test_logout_destroys_session(self, client):
        cookies = _login(client)

        # Verify session works
        resp = client.get("/api/me", cookies=cookies)
        assert resp.status_code == 200

        # Logout
        resp = client.get("/auth/logout", cookies=cookies)
        assert resp.status_code == 200

        # Session should be invalid now
        resp = client.get("/api/me", cookies=cookies)
        assert resp.status_code == 401
