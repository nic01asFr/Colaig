"""Tests pour colaig/integrations/albert.py — Client Albert API."""

import json
from unittest.mock import MagicMock, patch

import pytest

from colaig.exceptions import AlbertError
from colaig.integrations.albert import AlbertClient
from colaig.models import ChatCompletionResult, ColaigConfig


@pytest.fixture
def albert_config() -> ColaigConfig:
    return ColaigConfig(
        albert_api_url="https://albert-api.test.local",
        albert_api_key="test-key-123",
        albert_model_chat="test-chat-model",
        albert_model_embed="test-embed-model",
    )


@pytest.fixture
def client(albert_config) -> AlbertClient:
    return AlbertClient(albert_config, max_retries=0)


def _mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


class TestAlbertChat:
    """Tests du chat completions."""

    async def test_chat_success(self, client):
        response_data = {
            "choices": [{"message": {"content": "Voici la réponse."}}]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat([{"role": "user", "content": "Bonjour"}])
        assert result == "Voici la réponse."

    async def test_chat_with_custom_model(self, client):
        response_data = {
            "choices": [{"message": {"content": "ok"}}]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.chat(
                [{"role": "user", "content": "test"}],
                model="custom-model",
                temperature=0.5,
                max_tokens=1024,
            )
            payload = mock.call_args[0][1]
            assert payload["model"] == "custom-model"
            assert payload["temperature"] == 0.5
            assert payload["max_tokens"] == 1024

    async def test_chat_bad_response_format(self, client):
        response_data = {"unexpected": "format"}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            with pytest.raises(AlbertError, match="inattendue"):
                await client.chat([{"role": "user", "content": "test"}])


class TestAlbertEmbed:
    """Tests du service embeddings."""

    async def test_embed_single(self, client):
        response_data = {
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.embed("texte à vectoriser")
        assert result == [0.1, 0.2, 0.3]

    async def test_embed_batch(self, client):
        response_data = {
            "data": [
                {"embedding": [0.1, 0.2], "index": 0},
                {"embedding": [0.3, 0.4], "index": 1},
            ]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.embed_batch(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]

    async def test_embed_batch_respects_order(self, client):
        """Les résultats sont triés par index même si le serveur les renvoie dans le désordre."""
        response_data = {
            "data": [
                {"embedding": [0.3, 0.4], "index": 1},
                {"embedding": [0.1, 0.2], "index": 0},
            ]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.embed_batch(["first", "second"])
        assert result[0] == [0.1, 0.2]  # index 0
        assert result[1] == [0.3, 0.4]  # index 1

    async def test_embed_batch_splits_large(self, client):
        """Les gros batches sont découpés."""
        response_data = {
            "data": [{"embedding": [0.1], "index": i} for i in range(3)]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            texts = [f"text{i}" for i in range(6)]
            result = await client.embed_batch(texts, batch_size=3)
        assert mock.call_count == 2  # 2 batches de 3
        assert len(result) == 6


class TestAlbertEndpoints:
    """Tests des URLs et headers."""

    async def test_chat_url(self, client):
        response_data = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.chat([{"role": "user", "content": "test"}])
            url = mock.call_args[0][0]
            assert url == "https://albert-api.test.local/v1/chat/completions"

    async def test_embed_url(self, client):
        response_data = {"data": [{"embedding": [0.1], "index": 0}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.embed("test")
            url = mock.call_args[0][0]
            assert url == "https://albert-api.test.local/v1/embeddings"

    async def test_default_model_used(self, client):
        response_data = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.chat([{"role": "user", "content": "test"}])
            payload = mock.call_args[0][1]
            assert payload["model"] == "test-chat-model"


class TestChatWithTools:
    """Tests pour AlbertClient.chat_with_tools — tool calling OpenAI-compatible."""

    @pytest.fixture
    def client(self, albert_config):
        return AlbertClient(albert_config)

    @pytest.fixture
    def simple_tools(self):
        return [{
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "Recherche des documents",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }]

    @pytest.mark.asyncio
    async def test_text_response(self, client, simple_tools):
        """LLM retourne une réponse texte (pas de tool call)."""
        response_data = {
            "choices": [{"message": {"content": "Voici la réponse.", "tool_calls": None}, "finish_reason": "stop"}]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat_with_tools([{"role": "user", "content": "test"}], simple_tools)
        assert isinstance(result, ChatCompletionResult)
        assert result.content == "Voici la réponse."
        assert not result.has_tool_calls
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_tool_call_response(self, client, simple_tools):
        """LLM retourne un tool_call."""
        response_data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc123",
                        "type": "function",
                        "function": {"name": "search_documents", "arguments": '{"query": "procédure"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat_with_tools([{"role": "user", "content": "test"}], simple_tools)
        assert result.has_tool_calls
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc.tool_name == "search_documents"
        assert tc.arguments == {"query": "procédure"}
        assert tc.call_id == "allabc123"  # normalize_tool_call_id("call_abc123") → 9 alphanum
        assert result.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, client, simple_tools):
        """LLM retourne plusieurs tool_calls."""
        response_data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_documents", "arguments": '{"query": "A"}'},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "fetch_document", "arguments": '{"path": "guide.pdf"}'},
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat_with_tools([{"role": "user", "content": "test"}], simple_tools)
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].tool_name == "search_documents"
        assert result.tool_calls[1].tool_name == "fetch_document"

    @pytest.mark.asyncio
    async def test_tools_in_payload(self, client, simple_tools):
        """Vérifie que tools et tool_choice sont envoyés dans le payload."""
        response_data = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.chat_with_tools(
                [{"role": "user", "content": "test"}], simple_tools, tool_choice="auto"
            )
        payload = mock.call_args[0][1]
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"
        assert payload["tools"] == simple_tools

    @pytest.mark.asyncio
    async def test_tool_choice_none(self, client, simple_tools):
        """tool_choice=none force une réponse texte."""
        response_data = {"choices": [{"message": {"content": "réponse finale"}, "finish_reason": "stop"}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            result = await client.chat_with_tools(
                [{"role": "user", "content": "test"}], simple_tools, tool_choice="none"
            )
        payload = mock.call_args[0][1]
        assert payload["tool_choice"] == "none"
        assert not result.has_tool_calls

    @pytest.mark.asyncio
    async def test_malformed_arguments_graceful(self, client, simple_tools):
        """Arguments mal formés → dict vide (pas d'exception)."""
        response_data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "search_documents", "arguments": "NOT_JSON"},
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat_with_tools([{"role": "user", "content": "test"}], simple_tools)
        assert result.has_tool_calls
        assert result.tool_calls[0].arguments == {}

    @pytest.mark.asyncio
    async def test_empty_tools_list(self, client):
        """Sans tools, payload sans clé tools."""
        response_data = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)) as mock:
            await client.chat_with_tools([{"role": "user", "content": "test"}], tools=[])
        payload = mock.call_args[0][1]
        assert "tools" not in payload

    @pytest.mark.asyncio
    async def test_chat_still_works(self, client):
        """Régression : chat() inchangé après ajout de chat_with_tools."""
        response_data = {"choices": [{"message": {"content": "réponse normale"}}]}
        with patch.object(client, "_request_with_retry", return_value=_mock_response(200, response_data)):
            result = await client.chat([{"role": "user", "content": "test"}])
        assert result == "réponse normale"
