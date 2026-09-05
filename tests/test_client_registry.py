"""
Tests — ClientConfig + ClientRegistry + factories multi-client

Couvre :
- ClientConfig.from_dict() (parsing YAML → dataclass)
- ClientRegistry.from_yaml() (chargement fichier)
- ClientRegistry.from_yaml_or_empty() (fallback silencieux)
- create_storage_for_client / create_messaging_for_client / create_llm_for_client
"""

from __future__ import annotations

import textwrap

import pytest

from colaig.client_registry import ClientRegistry
from colaig.models import ClientConfig, ColaigConfig

# =============================================================================
# ClientConfig.from_dict
# =============================================================================

class TestClientConfigFromDict:

    def test_minimal(self):
        cc = ClientConfig.from_dict({"id": "test"})
        assert cc.client_id == "test"
        assert cc.storage_backend == "local"
        assert cc.messaging_backend == "matrix"
        assert cc.llm_backend == ""

    def test_webdav_storage(self):
        data = {
            "id": "acme",
            "storage": {
                "backend": "webdav",
                "url": "https://cloud.acme.com/dav/",
                "username": "colaig",
                "password": "secret",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.storage_backend == "webdav"
        assert cc.storage_webdav_url == "https://cloud.acme.com/dav/"
        assert cc.storage_webdav_username == "colaig"
        assert cc.storage_webdav_password == "secret"

    def test_s3_storage(self):
        data = {
            "id": "s3-client",
            "storage": {
                "backend": "s3",
                "endpoint_url": "https://minio.example.com",
                "access_key": "AK",
                "secret_key": "SK",
                "bucket": "colaig-bucket",
                "prefix": "data/",
                "region": "eu-west-1",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.storage_s3_endpoint == "https://minio.example.com"
        assert cc.storage_s3_access_key == "AK"
        assert cc.storage_s3_bucket == "colaig-bucket"
        assert cc.storage_s3_prefix == "data/"
        assert cc.storage_s3_region == "eu-west-1"

    def test_msgraph_storage(self):
        data = {
            "id": "ms",
            "storage": {
                "backend": "msgraph",
                "tenant_id": "tid",
                "client_id": "cid",
                "client_secret": "csec",
                "drive_user_id": "user@example.com",
                "root_path": "/Colaig",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.storage_msgraph_tenant_id == "tid"
        assert cc.storage_msgraph_client_id == "cid"
        assert cc.storage_msgraph_root_path == "/Colaig"

    def test_matrix_messaging(self):
        data = {
            "id": "m1",
            "messaging": {
                "backend": "matrix",
                "homeserver": "https://matrix.example.com",
                "username": "@bot:example.com",
                "password": "pw",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.messaging_backend == "matrix"
        assert cc.messaging_matrix_homeserver == "https://matrix.example.com"
        assert cc.messaging_matrix_username == "@bot:example.com"

    def test_telegram_messaging(self):
        data = {
            "id": "tg",
            "messaging": {
                "backend": "telegram",
                "bot_token": "123:ABC",
                "webhook_url": "https://hook.example.com",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.messaging_backend == "telegram"
        assert cc.messaging_telegram_token == "123:ABC"
        assert cc.messaging_telegram_webhook == "https://hook.example.com"

    def test_openai_llm_override(self):
        data = {
            "id": "openai-client",
            "llm": {
                "backend": "openai",
                "api_url": "https://api.openai.com",
                "api_key": "sk-xxx",
                "model_chat": "gpt-4o",
                "model_embed": "text-embedding-3-small",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.llm_backend == "openai"
        assert cc.llm_api_key == "sk-xxx"
        assert cc.llm_model_chat == "gpt-4o"

    def test_azure_llm_override(self):
        data = {
            "id": "az",
            "llm": {
                "backend": "azure",
                "api_key": "az-key",
                "azure_resource": "my-resource",
                "azure_deployment_chat": "gpt-4o",
                "azure_api_version": "2024-06-01",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.llm_backend == "azure"
        assert cc.llm_azure_resource == "my-resource"
        assert cc.llm_azure_api_version == "2024-06-01"

    def test_ollama_llm_override(self):
        data = {
            "id": "local",
            "llm": {
                "backend": "ollama",
                "api_url": "http://ollama:11434",
                "model_chat": "llama3.2",
            },
        }
        cc = ClientConfig.from_dict(data)
        assert cc.llm_backend == "ollama"
        assert cc.llm_api_url == "http://ollama:11434"

    def test_no_llm_section(self):
        cc = ClientConfig.from_dict({"id": "no-llm"})
        assert cc.llm_backend == ""   # hérite de ColaigConfig


# =============================================================================
# ClientRegistry
# =============================================================================

class TestClientRegistry:

    def _write_yaml(self, tmp_path, content: str) -> str:
        p = tmp_path / "clients.yml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(p)

    def test_from_yaml_absent_returns_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent.yml")
        reg = ClientRegistry.from_yaml(path)
        assert len(reg) == 0
        assert not reg

    def test_from_yaml_two_clients(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - id: client-a
                storage:
                  backend: local
                  path: /tmp/a
                messaging:
                  backend: matrix
                  homeserver: https://matrix.a.com
                  username: "@bot:a.com"
                  password: pw
              - id: client-b
                storage:
                  backend: local
                  path: /tmp/b
                messaging:
                  backend: matrix
                  homeserver: https://matrix.b.com
                  username: "@bot:b.com"
                  password: pw
        """)
        reg = ClientRegistry.from_yaml(path)
        assert len(reg) == 2
        assert bool(reg)

    def test_get_by_id(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - id: my-client
                storage:
                  backend: local
                messaging:
                  backend: matrix
        """)
        reg = ClientRegistry.from_yaml(path)
        cc = reg.get("my-client")
        assert cc is not None
        assert cc.client_id == "my-client"

    def test_get_unknown_returns_none(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - id: existing
                storage:
                  backend: local
                messaging:
                  backend: matrix
        """)
        reg = ClientRegistry.from_yaml(path)
        assert reg.get("unknown") is None

    def test_iter(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - id: a
                storage:
                  backend: local
                messaging:
                  backend: matrix
              - id: b
                storage:
                  backend: local
                messaging:
                  backend: matrix
        """)
        reg = ClientRegistry.from_yaml(path)
        ids = [cc.client_id for cc in reg]
        assert set(ids) == {"a", "b"}

    def test_from_yaml_or_empty_on_bad_file(self, tmp_path):
        path = self._write_yaml(tmp_path, "clients: not-a-list")
        reg = ClientRegistry.from_yaml_or_empty(path)
        assert len(reg) == 0   # erreur swallowed, log émis

    def test_from_yaml_missing_id_raises(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - storage:
                  backend: local
                messaging:
                  backend: matrix
        """)
        with pytest.raises(ValueError, match="sans 'id'"):
            ClientRegistry.from_yaml(path)

    def test_empty_clients_list(self, tmp_path):
        path = self._write_yaml(tmp_path, "clients: []")
        reg = ClientRegistry.from_yaml(path)
        assert len(reg) == 0

    def test_all(self, tmp_path):
        path = self._write_yaml(tmp_path, """
            clients:
              - id: x
                storage:
                  backend: local
                messaging:
                  backend: matrix
        """)
        reg = ClientRegistry.from_yaml(path)
        assert len(reg.all()) == 1
        assert reg.all()[0].client_id == "x"


# =============================================================================
# create_storage_for_client / create_messaging_for_client
# =============================================================================

class TestCreateStorageForClient:

    def test_local(self):
        from colaig.integrations.storage.local import LocalStorage
        from colaig.main import create_storage_for_client
        cc = ClientConfig(client_id="test", storage_backend="local", storage_local_path="/tmp/test")
        storage = create_storage_for_client(cc)
        assert isinstance(storage, LocalStorage)

    def test_webdav(self):
        from colaig.integrations.storage.webdav import WebDAVStorage
        from colaig.main import create_storage_for_client
        cc = ClientConfig(
            client_id="test", storage_backend="webdav",
            storage_webdav_url="https://x.com/dav/",
            storage_webdav_username="u", storage_webdav_password="p",
        )
        storage = create_storage_for_client(cc)
        assert isinstance(storage, WebDAVStorage)

    def test_unknown_backend_raises(self):
        from colaig.main import create_storage_for_client
        cc = ClientConfig(client_id="test", storage_backend="unknown_xyz")
        with pytest.raises(ValueError, match="storage_backend inconnu"):
            create_storage_for_client(cc)


class TestCreateMessagingForClient:

    def test_matrix(self):
        from colaig.main import create_messaging_for_client
        from colaig.messaging.matrix import MatrixMessaging
        cc = ClientConfig(
            client_id="test", messaging_backend="matrix",
            messaging_matrix_homeserver="https://matrix.example.com",
            messaging_matrix_username="@bot:example.com",
            messaging_matrix_password="pw",
        )
        m = create_messaging_for_client(cc)
        assert isinstance(m, MatrixMessaging)

    def test_unknown_backend_raises(self):
        from colaig.main import create_messaging_for_client
        cc = ClientConfig(client_id="test", messaging_backend="slack")
        with pytest.raises(ValueError, match="messaging_backend inconnu"):
            create_messaging_for_client(cc)


# =============================================================================
# create_llm_for_client
# =============================================================================

class TestCreateLlmForClient:

    def _default_config(self):
        return ColaigConfig(
            llm_backend="albert",
            albert_api_key="albert-key",
            albert_api_url="https://albert.api.etalab.gouv.fr",
        )

    def test_no_override_uses_default(self):
        from colaig.integrations.albert import AlbertClient
        from colaig.main import create_llm_for_client
        cc = ClientConfig(client_id="test", llm_backend="")
        client = create_llm_for_client(cc, self._default_config())
        assert isinstance(client, AlbertClient)

    def test_openai_override(self):
        from colaig.integrations.llm.openai_client import OpenAIClient
        from colaig.main import create_llm_for_client
        cc = ClientConfig(
            client_id="acme",
            llm_backend="openai",
            llm_api_key="sk-xxx",
            llm_api_url="https://api.openai.com",
        )
        client = create_llm_for_client(cc, self._default_config())
        assert isinstance(client, OpenAIClient)
        assert client._api_key == "sk-xxx"
        assert "acme" in client._backend

    def test_azure_override(self):
        from colaig.integrations.llm.azure_client import AzureClient
        from colaig.main import create_llm_for_client
        cc = ClientConfig(
            client_id="gov",
            llm_backend="azure",
            llm_api_key="az-key",
            llm_azure_resource="my-res",
            llm_azure_deployment_chat="gpt-4o",
        )
        client = create_llm_for_client(cc, self._default_config())
        assert isinstance(client, AzureClient)
        assert client._resource == "my-res"

    def test_ollama_override(self):
        from colaig.integrations.llm.ollama_client import OllamaClient
        from colaig.main import create_llm_for_client
        cc = ClientConfig(
            client_id="local",
            llm_backend="ollama",
            llm_api_url="http://ollama:11434",
            llm_model_chat="mistral",
        )
        client = create_llm_for_client(cc, self._default_config())
        assert isinstance(client, OllamaClient)
        assert "11434" in client._base_url
        assert client._model_chat == "mistral"

    def test_albert_override_uses_client_credentials(self):
        from colaig.integrations.albert import AlbertClient
        from colaig.main import create_llm_for_client
        cc = ClientConfig(
            client_id="agri",
            llm_backend="albert",
            llm_api_key="agri-key",
            llm_api_url="https://agri-api.etalab.gouv.fr",
        )
        client = create_llm_for_client(cc, self._default_config())
        assert isinstance(client, AlbertClient)
        assert client._api_key == "agri-key"
        assert "agri-api" in client._base_url

    def test_unknown_llm_backend_raises(self):
        from colaig.main import create_llm_for_client
        cc = ClientConfig(client_id="test", llm_backend="unknown_xyz")
        with pytest.raises(ValueError, match="llm_backend inconnu"):
            create_llm_for_client(cc, self._default_config())
