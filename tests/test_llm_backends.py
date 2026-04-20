"""
Tests — colaig/integrations/llm/ (OpenAIClient, AzureClient, OllamaClient)

Pas de réseau réel : httpx mocké via unittest.mock.
"""

from __future__ import annotations

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from colaig.integrations.llm.openai_client import OpenAIClient
from colaig.integrations.llm.azure_client import AzureClient
from colaig.integrations.llm.ollama_client import OllamaClient
from colaig.exceptions import LLMError, LLMRateLimitError, LLMUnavailableError
from colaig.models import ChatCompletionResult


# =============================================================================
# Fixtures — réponses HTTP mock
# =============================================================================

def _make_response(status: int, body: dict | str) -> MagicMock:
    """Crée un mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status
    if isinstance(body, dict):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = body
    return resp


def _chat_body(content: str = "Bonjour !") -> dict:
    return {
        "choices": [{"message": {"content": content, "tool_calls": None}, "finish_reason": "stop"}]
    }


def _embed_body(vectors: list[list[float]]) -> dict:
    return {
        "data": [{"embedding": v, "index": i} for i, v in enumerate(vectors)]
    }


def _tool_body(content: str, tool_name: str, tool_args: dict, tool_id: str = "tc1") -> dict:
    return {
        "choices": [{
            "message": {
                "content": content,
                "tool_calls": [{
                    "id": tool_id,
                    "function": {"name": tool_name, "arguments": json.dumps(tool_args)},
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }


# =============================================================================
# OpenAIClient
# =============================================================================

class TestOpenAIClient:

    def _client(self) -> OpenAIClient:
        return OpenAIClient(
            api_key="sk-test",
            base_url="https://api.openai.com",
            model_chat="gpt-4o",
            model_embed="text-embedding-3-small",
            max_retries=0,
        )

    @pytest.mark.asyncio
    async def test_chat_ok(self):
        client = self._client()
        resp = _make_response(200, _chat_body("Bonjour !"))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result == "Bonjour !"

    @pytest.mark.asyncio
    async def test_chat_uses_model_override(self):
        client = self._client()
        resp = _make_response(200, _chat_body("ok"))
        captured = {}

        async def fake_retry(url, payload, timeout):
            captured["model"] = payload.get("model")
            return resp

        with patch.object(client, "_request_with_retry", new=fake_retry):
            await client.chat([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        assert captured["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_chat_bad_response_raises_llmerror(self):
        client = self._client()
        resp = _make_response(200, {"unexpected": True})
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            with pytest.raises(LLMError):
                await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_embed_batch_ok(self):
        client = self._client()
        vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        resp = _make_response(200, _embed_body(vectors))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.embed_batch(["a", "b"])
        assert result == vectors

    @pytest.mark.asyncio
    async def test_embed_single(self):
        client = self._client()
        vector = [0.1, 0.2, 0.3]
        resp = _make_response(200, _embed_body([vector]))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.embed("hello")
        assert result == vector

    @pytest.mark.asyncio
    async def test_chat_with_tools_ok(self):
        client = self._client()
        resp = _make_response(200, _tool_body("", "search", {"query": "test"}))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat_with_tools(
                messages=[{"role": "user", "content": "cherche"}],
                tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
            )
        assert isinstance(result, ChatCompletionResult)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "search"
        assert result.tool_calls[0].arguments == {"query": "test"}
        assert result.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_retry_on_429(self):
        client = OpenAIClient(api_key="x", max_retries=2)
        mock_client = AsyncMock()
        resp_429 = _make_response(429, {"error": "rate limit"})
        resp_200 = _make_response(200, _chat_body("ok"))
        mock_client.post = AsyncMock(side_effect=[resp_429, resp_200])

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_client)):
            with patch("colaig.integrations.llm.openai_client._backoff_delay", return_value=0.0):
                result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_raises_rate_limit_error_after_max_retries(self):
        client = OpenAIClient(api_key="x", max_retries=1)
        mock_client = AsyncMock()
        resp_429 = _make_response(429, {"error": "rate limit"})
        mock_client.post = AsyncMock(return_value=resp_429)

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_client)):
            with patch("colaig.integrations.llm.openai_client._backoff_delay", return_value=0.0):
                with pytest.raises(LLMRateLimitError):
                    await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_raises_llm_error_on_4xx(self):
        client = OpenAIClient(api_key="x", max_retries=0)
        mock_client = AsyncMock()
        resp = _make_response(400, "bad request")
        mock_client.post = AsyncMock(return_value=resp)

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_client)):
            with pytest.raises(LLMError):
                await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_close(self):
        client = self._client()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        client._client = mock_http
        await client.close()
        mock_http.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch_batching(self):
        """Vérifie que embed_batch découpe bien en lots."""
        client = self._client()
        calls = []

        async def fake_retry(url, payload, timeout):
            calls.append(len(payload["input"]))
            vecs = [[float(i)] for i in range(len(payload["input"]))]
            return _make_response(200, _embed_body(vecs))

        with patch.object(client, "_request_with_retry", new=fake_retry):
            # 5 textes, batch_size=3 → 2 appels (3+2), parallèles → ordre non garanti
            result = await client.embed_batch(["a", "b", "c", "d", "e"], batch_size=3)
        assert sorted(calls) == [2, 3]  # 2 batches de taille 3 et 2, ordre parallèle
        assert len(result) == 5


# =============================================================================
# AzureClient
# =============================================================================

class TestAzureClient:

    def _client(self) -> AzureClient:
        return AzureClient(
            api_key="azure-key",
            resource_name="my-resource",
            deployment_chat="gpt-4o",
            deployment_embed="text-embedding-3-small",
            api_version="2024-02-01",
            max_retries=0,
        )

    def test_chat_url(self):
        client = self._client()
        url = client._chat_url()
        assert "my-resource.openai.azure.com" in url
        assert "gpt-4o" in url
        assert "chat/completions" in url
        assert "api-version=2024-02-01" in url

    def test_embed_url(self):
        client = self._client()
        url = client._embed_url()
        assert "my-resource.openai.azure.com" in url
        assert "text-embedding-3-small" in url
        assert "embeddings" in url

    @pytest.mark.asyncio
    async def test_chat_ok(self):
        client = self._client()
        resp = _make_response(200, _chat_body("Réponse Azure"))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "Réponse Azure"

    @pytest.mark.asyncio
    async def test_chat_model_not_in_payload(self):
        """Azure : le modèle est dans l'URL, pas dans le payload."""
        client = self._client()
        resp = _make_response(200, _chat_body("ok"))
        captured = {}

        async def fake_retry(url, payload, timeout):
            captured["payload"] = payload
            return resp

        with patch.object(client, "_request_with_retry", new=fake_retry):
            await client.chat([{"role": "user", "content": "hi"}])
        assert "model" not in captured["payload"]

    @pytest.mark.asyncio
    async def test_chat_with_tools_ok(self):
        client = self._client()
        resp = _make_response(200, _tool_body("", "tool_a", {"x": 1}))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat_with_tools(
                messages=[{"role": "user", "content": "go"}],
                tools=[{"type": "function", "function": {"name": "tool_a", "parameters": {}}}],
            )
        assert result.tool_calls[0].tool_name == "tool_a"
        assert result.tool_calls[0].arguments == {"x": 1}

    @pytest.mark.asyncio
    async def test_embed_batch_ok(self):
        client = self._client()
        vectors = [[1.0, 2.0], [3.0, 4.0]]
        resp = _make_response(200, _embed_body(vectors))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.embed_batch(["a", "b"])
        assert result == vectors

    @pytest.mark.asyncio
    async def test_auth_header_api_key(self):
        """Azure utilise 'api-key' header, pas 'Authorization: Bearer'."""
        client = self._client()
        http = await client._get_client()
        assert http.headers.get("api-key") == "azure-key"
        assert "Authorization" not in http.headers
        await client.close()

    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        client = AzureClient(api_key="x", resource_name="r", max_retries=2)
        mock_client = AsyncMock()
        resp_503 = _make_response(503, "unavailable")
        resp_200 = _make_response(200, _chat_body("ok"))
        mock_client.post = AsyncMock(side_effect=[resp_503, resp_200])

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_client)):
            with patch("colaig.integrations.llm.azure_client._backoff_delay", return_value=0.0):
                result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_raises_unavailable_after_max_retries(self):
        client = AzureClient(api_key="x", resource_name="r", max_retries=1)
        mock_client = AsyncMock()
        resp_503 = _make_response(503, "unavailable")
        mock_client.post = AsyncMock(return_value=resp_503)

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_client)):
            with patch("colaig.integrations.llm.azure_client._backoff_delay", return_value=0.0):
                with pytest.raises(LLMUnavailableError):
                    await client.chat([{"role": "user", "content": "hi"}])


# =============================================================================
# OllamaClient
# =============================================================================

class TestOllamaClient:

    def _client(self) -> OllamaClient:
        return OllamaClient(
            base_url="http://localhost:11434",
            model_chat="llama3.2",
            model_embed="nomic-embed-text",
            max_retries=0,
        )

    @pytest.mark.asyncio
    async def test_chat_ok(self):
        client = self._client()
        resp = _make_response(200, _chat_body("Salut depuis Ollama !"))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "Salut depuis Ollama !"

    @pytest.mark.asyncio
    async def test_chat_uses_openai_compat_url(self):
        client = self._client()
        assert client._chat_url() == "http://localhost:11434/v1/chat/completions"
        assert client._embed_url() == "http://localhost:11434/v1/embeddings"

    @pytest.mark.asyncio
    async def test_no_auth_header(self):
        """Ollama : pas d'authentification."""
        client = self._client()
        http = await client._get_client()
        assert "Authorization" not in http.headers
        assert "api-key" not in http.headers
        await client.close()

    @pytest.mark.asyncio
    async def test_embed_batch_ok(self):
        client = self._client()
        vectors = [[0.1, 0.2]]
        resp = _make_response(200, _embed_body(vectors))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.embed_batch(["hello"])
        assert result == vectors

    @pytest.mark.asyncio
    async def test_embed_batch_small_default(self):
        """Ollama utilise batch_size=16 par défaut (plus petit qu'OpenAI)."""
        client = self._client()
        calls = []

        async def fake_retry(url, payload, timeout):
            calls.append(len(payload["input"]))
            vecs = [[float(i)] for i in range(len(payload["input"]))]
            return _make_response(200, _embed_body(vecs))

        with patch.object(client, "_request_with_retry", new=fake_retry):
            await client.embed_batch([f"text{i}" for i in range(20)])
        # 20 textes, batch_size=16 → 2 appels (16+4)
        assert calls == [16, 4]

    @pytest.mark.asyncio
    async def test_chat_with_tools_ok(self):
        client = self._client()
        resp = _make_response(200, _tool_body("", "get_weather", {"city": "Paris"}))
        with patch.object(client, "_request_with_retry", new=AsyncMock(return_value=resp)):
            result = await client.chat_with_tools(
                messages=[{"role": "user", "content": "météo Paris"}],
                tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
            )
        assert result.tool_calls[0].tool_name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "Paris"}

    @pytest.mark.asyncio
    async def test_raises_llm_error_on_4xx(self):
        client = OllamaClient(max_retries=0)
        mock_http = AsyncMock()
        resp = _make_response(404, "model not found")
        mock_http.post = AsyncMock(return_value=resp)

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_http)):
            with pytest.raises(LLMError):
                await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self):
        import httpx
        client = OllamaClient(max_retries=2)
        mock_http = AsyncMock()
        resp_200 = _make_response(200, _chat_body("ok"))
        mock_http.post = AsyncMock(side_effect=[
            httpx.ConnectError("Connection refused"),
            resp_200,
        ])

        with patch.object(client, "_get_client", new=AsyncMock(return_value=mock_http)):
            with patch("colaig.integrations.llm.ollama_client._backoff_delay", return_value=0.0):
                result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "ok"


# =============================================================================
# create_llm_client factory (main.py)
# =============================================================================

class TestCreateLlmClient:

    def _config(self, **kwargs):
        from colaig.models import ColaigConfig
        return ColaigConfig(**kwargs)

    def test_albert_default(self):
        from colaig.main import create_llm_client
        from colaig.integrations.albert import AlbertClient
        config = self._config(llm_backend="albert", albert_api_key="key")
        client = create_llm_client(config)
        assert isinstance(client, AlbertClient)

    def test_openai_backend(self):
        from colaig.main import create_llm_client
        config = self._config(llm_backend="openai", llm_api_key="sk-x", llm_api_url="https://api.openai.com")
        client = create_llm_client(config)
        assert isinstance(client, OpenAIClient)
        assert client._api_key == "sk-x"

    def test_azure_backend(self):
        from colaig.main import create_llm_client
        config = self._config(llm_backend="azure", llm_api_key="az-key", llm_azure_resource="my-res")
        client = create_llm_client(config)
        assert isinstance(client, AzureClient)
        assert client._resource == "my-res"
        assert client._api_key == "az-key"

    def test_ollama_backend(self):
        from colaig.main import create_llm_client
        config = self._config(llm_backend="ollama", llm_api_url="http://localhost:11434")
        client = create_llm_client(config)
        assert isinstance(client, OllamaClient)
        assert "11434" in client._base_url

    def test_unknown_backend_raises(self):
        from colaig.main import create_llm_client
        config = self._config(llm_backend="unknown_xyz")
        with pytest.raises(ValueError, match="LLM_BACKEND inconnu"):
            create_llm_client(config)

    def test_albert_by_default_when_backend_empty(self):
        """llm_backend vide/None → albert."""
        from colaig.main import create_llm_client
        from colaig.integrations.albert import AlbertClient
        config = self._config(llm_backend="")
        # llm_backend="" → or "albert" → AlbertClient
        client = create_llm_client(config)
        assert isinstance(client, AlbertClient)

    def test_openai_fallback_to_albert_key(self):
        """Si llm_api_key vide, openai fallback sur albert_api_key."""
        from colaig.main import create_llm_client
        config = self._config(
            llm_backend="openai",
            llm_api_key="",
            albert_api_key="albert-key",
        )
        client = create_llm_client(config)
        assert isinstance(client, OpenAIClient)
        assert client._api_key == "albert-key"
