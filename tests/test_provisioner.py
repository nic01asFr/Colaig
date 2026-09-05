"""Tests pour ClientProvisioner et les routes /api/platform/provision."""

from __future__ import annotations

import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from colaig.platform.provisioner import ClientProvisioner, _build_env_file
from colaig.web.routes import create_app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_clients_yml(tmp_path):
    return tmp_path / "clients.yml"


@pytest.fixture
def provisioner(tmp_clients_yml):
    return ClientProvisioner(tmp_clients_yml)


_STORAGE = {"backend": "webdav", "url": "https://cloud.example.fr/", "username": "colaig", "password": "secret"}
_MESSAGING = {"backend": "matrix", "homeserver": "https://matrix.example.fr", "username": "@bot:example.fr", "password": "secret"}


# ── Tests ClientProvisioner — CRUD ─────────────────────────────────────────────


class TestClientProvisioner:
    def test_load_absent_file(self, provisioner):
        data = provisioner.load()
        assert data == {"clients": []}

    def test_upsert_creates(self, provisioner):
        action = provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        assert action == "created"
        assert provisioner._path.exists()
        data = provisioner.load()
        assert len(data["clients"]) == 1
        assert data["clients"][0]["id"] == "org-rh"

    def test_upsert_updates(self, provisioner):
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        new_storage = {**_STORAGE, "url": "https://new.example.fr/"}
        action = provisioner.upsert("org-rh", new_storage, _MESSAGING)
        assert action == "updated"
        data = provisioner.load()
        assert len(data["clients"]) == 1
        assert data["clients"][0]["storage"]["url"] == "https://new.example.fr/"

    def test_upsert_multiple_clients(self, provisioner):
        provisioner.upsert("org-a", _STORAGE, _MESSAGING)
        provisioner.upsert("org-b", _STORAGE, _MESSAGING)
        assert provisioner.list_clients() == ["org-a", "org-b"]

    def test_get_client_found(self, provisioner):
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        client = provisioner.get_client("org-rh")
        assert client is not None
        assert client["id"] == "org-rh"

    def test_get_client_not_found(self, provisioner):
        assert provisioner.get_client("nonexistent") is None

    def test_delete_existing(self, provisioner):
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        deleted = provisioner.delete("org-rh")
        assert deleted is True
        assert provisioner.list_clients() == []

    def test_delete_nonexistent(self, provisioner):
        assert provisioner.delete("ghost") is False

    def test_upsert_with_mcp_auth(self, provisioner):
        mcp_auth = {"mode": "oidc", "oidc_issuer": "https://sso.example.fr", "oidc_audience": "colaig", "oidc_jwks_uri": "https://sso.example.fr/certs"}
        provisioner.upsert("org-sso", _STORAGE, _MESSAGING, mcp_auth=mcp_auth)
        client = provisioner.get_client("org-sso")
        assert client["mcp_auth"]["mode"] == "oidc"
        assert client["mcp_auth"]["oidc_issuer"] == "https://sso.example.fr"

    def test_upsert_with_llm_override(self, provisioner):
        llm = {"backend": "openai", "api_url": "https://api.openai.com", "api_key": "sk-xxx", "model_chat": "gpt-4o"}
        provisioner.upsert("org-openai", _STORAGE, _MESSAGING, llm=llm)
        client = provisioner.get_client("org-openai")
        assert client["llm"]["backend"] == "openai"

    def test_list_clients_empty(self, provisioner):
        assert provisioner.list_clients() == []

    def test_preserves_platform_policy(self, provisioner, tmp_clients_yml):
        # Si clients.yml contient déjà une platform_policy, elle doit être préservée
        existing = {
            "platform_policy": {"allowed_storage_backends": ["webdav"]},
            "clients": [],
        }
        tmp_clients_yml.write_text(yaml.safe_dump(existing))
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        data = provisioner.load()
        assert "platform_policy" in data
        assert data["platform_policy"]["allowed_storage_backends"] == ["webdav"]


# ── Tests _build_env_file ──────────────────────────────────────────────────────


class TestBuildEnvFile:
    def _client(self, **kwargs):
        c = {"id": "test", "storage": _STORAGE, "messaging": _MESSAGING}
        c.update(kwargs)
        return c

    def test_contains_storage_vars(self):
        env = _build_env_file(self._client(), base_url="https://colaig.example.fr",
                              albert_api_url="https://albert.fr", albert_api_key="key",
                              albert_model_chat="chat", albert_model_embed="embed")
        assert "STORAGE_BACKEND=webdav" in env
        assert "WEBDAV_URL=https://cloud.example.fr/" in env
        assert "WEBDAV_USERNAME=colaig" in env

    def test_contains_messaging_vars(self):
        env = _build_env_file(self._client(), base_url="", albert_api_url="", albert_api_key="",
                              albert_model_chat="", albert_model_embed="")
        assert "MESSAGING_BACKEND=matrix" in env
        assert "MATRIX_HOMESERVER=https://matrix.example.fr" in env

    def test_contains_albert_vars_when_no_llm_override(self):
        env = _build_env_file(self._client(), base_url="", albert_api_url="https://albert.fr",
                              albert_api_key="sk-xyz", albert_model_chat="gpt-oss", albert_model_embed="bge")
        assert "ALBERT_API_URL=https://albert.fr" in env
        assert "ALBERT_API_KEY=sk-xyz" in env

    def test_llm_override_replaces_albert(self):
        llm = {"backend": "openai", "api_url": "https://api.openai.com", "api_key": "sk-xxx", "model_chat": "gpt-4o", "model_embed": "ada"}
        env = _build_env_file(self._client(llm=llm), base_url="", albert_api_url="https://albert.fr",
                              albert_api_key="key", albert_model_chat="chat", albert_model_embed="embed")
        assert "LLM_BACKEND=openai" in env
        assert "ALBERT_API_URL" not in env

    def test_oidc_vars_included(self):
        mcp_auth = {"mode": "oidc", "oidc_issuer": "https://sso.fr", "oidc_audience": "colaig", "oidc_jwks_uri": "https://sso.fr/certs"}
        env = _build_env_file(self._client(mcp_auth=mcp_auth), base_url="", albert_api_url="",
                              albert_api_key="", albert_model_chat="", albert_model_embed="")
        assert "COLAIG_MCP_AUTH_MODE=oidc" in env
        assert "COLAIG_OIDC_ISSUER=https://sso.fr" in env


# ── Tests build_selfhosted_package ────────────────────────────────────────────


class TestBuildSelfhostedPackage:
    def test_returns_valid_zip(self, provisioner):
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        zip_bytes = provisioner.build_selfhosted_package("org-rh", base_url="https://colaig.example.fr")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert "docker-compose.yml" in names
        assert ".env" in names

    def test_docker_compose_contains_client_id(self, provisioner):
        provisioner.upsert("my-org", _STORAGE, _MESSAGING)
        zip_bytes = provisioner.build_selfhosted_package("my-org")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            content = zf.read("docker-compose.yml").decode()
        assert "my-org" in content

    def test_raises_on_unknown_client(self, provisioner):
        with pytest.raises(KeyError):
            provisioner.build_selfhosted_package("ghost")

    def test_env_file_contains_storage(self, provisioner):
        provisioner.upsert("org-rh", _STORAGE, _MESSAGING)
        zip_bytes = provisioner.build_selfhosted_package("org-rh")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            env_content = zf.read(".env").decode()
        assert "STORAGE_BACKEND=webdav" in env_content


# ── Tests routes HTTP /api/platform/provision ─────────────────────────────────


@pytest.fixture
def test_client(tmp_clients_yml, monkeypatch):
    monkeypatch.setenv("COLAIG_PLATFORM_API_KEY", "test-secret")
    app = create_app(clients_yml_path=str(tmp_clients_yml))
    return TestClient(app)


_AUTH = {"Authorization": "Bearer test-secret"}
_PROVISION_BODY = {
    "client_id": "org-rh",
    "storage": {"backend": "webdav", "url": "https://cloud.fr/", "username": "colaig", "password": "secret"},
    "messaging": {"backend": "matrix", "homeserver": "https://matrix.fr", "username": "@bot:matrix.fr", "password": "pass"},
    "test_backends": False,
}


class TestProvisionRoutes:
    def test_provision_no_auth_returns_401(self, test_client):
        resp = test_client.post("/api/platform/provision", json=_PROVISION_BODY)
        assert resp.status_code == 401

    def test_provision_wrong_auth_returns_403(self, test_client):
        resp = test_client.post("/api/platform/provision", json=_PROVISION_BODY,
                                headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403

    def test_provision_creates_client(self, test_client):
        resp = test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["client_id"] == "org-rh"
        assert data["action"] == "created"

    def test_provision_updates_client(self, test_client):
        test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        resp = test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        assert resp.json()["action"] == "updated"

    def test_list_clients(self, test_client):
        test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        resp = test_client.get("/api/platform/provision", headers=_AUTH)
        assert resp.status_code == 200
        assert "org-rh" in resp.json()["clients"]

    def test_delete_client(self, test_client):
        test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        resp = test_client.delete("/api/platform/provision/org-rh", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["action"] == "deleted"

    def test_delete_nonexistent_returns_404(self, test_client):
        resp = test_client.delete("/api/platform/provision/ghost", headers=_AUTH)
        assert resp.status_code == 404

    def test_download_package(self, test_client):
        test_client.post("/api/platform/provision", json=_PROVISION_BODY, headers=_AUTH)
        resp = test_client.get("/api/platform/provision/org-rh/package", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            assert "docker-compose.yml" in zf.namelist()
            assert ".env" in zf.namelist()

    def test_download_package_unknown_returns_404(self, test_client):
        resp = test_client.get("/api/platform/provision/ghost/package", headers=_AUTH)
        assert resp.status_code == 404

    def test_provision_with_backends_test_failure(self, test_client):
        """Si test_backends=True et storage invalide, retourne 422."""
        body = {**_PROVISION_BODY, "test_backends": True}
        with patch("colaig.mcp.server._test_storage", new=AsyncMock(return_value=(False, "connexion refusée", 50))):
            resp = test_client.post("/api/platform/provision", json=body, headers=_AUTH)
        assert resp.status_code == 422
        data = resp.json()
        assert data["step"] == "storage"
        assert "connexion refusée" in data["error"]

    def test_provision_no_api_key_configured_allows_all(self, tmp_clients_yml, monkeypatch):
        """Si COLAIG_PLATFORM_API_KEY absent, accès ouvert (mode self-hosted dev)."""
        monkeypatch.delenv("COLAIG_PLATFORM_API_KEY", raising=False)
        app = create_app(clients_yml_path=str(tmp_clients_yml))
        client = TestClient(app)
        resp = client.post("/api/platform/provision", json=_PROVISION_BODY)
        assert resp.status_code == 200

    def test_provision_routes_absent_without_clients_yml(self):
        """Si clients_yml_path non fourni, les routes /api/platform ne sont pas enregistrées."""
        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/platform/provision", json=_PROVISION_BODY)
        assert resp.status_code == 404
