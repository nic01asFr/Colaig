"""
Colaig — AzureClient (Azure OpenAI Service)

Implémente LLMClientProtocol pour Azure OpenAI.
Implémente LLMClientProtocol (alias LLMClientProtocol).

Différences vs OpenAI standard :
    - Endpoint : https://{resource}.openai.azure.com/openai/deployments/{deployment}/
    - Auth     : header "api-key" (pas "Authorization: Bearer")
    - Version  : paramètre ?api-version=2024-02-01 obligatoire
    - Modèle   : spécifié via le nom du déploiement (pas dans le payload)

Variables d'env attendues (via ClientConfig ou ColaigConfig) :
    LLM_AZURE_RESOURCE      → nom de la ressource Azure (ex: "colaig-prod")
    LLM_AZURE_DEPLOYMENT    → nom du déploiement de modèle (ex: "gpt-4o")
    LLM_AZURE_EMBED_DEPLOYMENT → déploiement embeddings (ex: "text-embedding-3-small")
    LLM_API_KEY             → clé API Azure OpenAI
    LLM_AZURE_API_VERSION   → version API (défaut: "2024-02-01")
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator

import httpx

from colaig.exceptions import LLMError, LLMRateLimitError, LLMUnavailableError
from colaig.integrations.llm.utils import normalize_tool_call_id as _normalize_id
from colaig.models import ChatCompletionResult, ToolCall

logger = logging.getLogger(__name__)


def _backoff_delay(attempt: int) -> float:
    return min(60.0, (2 ** attempt) + random.uniform(0, 1))


def _retry_after_delay(response: httpx.Response, attempt: int) -> float:
    """Délai avant retry : lit Retry-After si présent, sinon backoff exponentiel."""
    retry_after = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return _backoff_delay(attempt)


class AzureClient:
    """Client LLM pour Azure OpenAI Service.

    Implémente LLMClientProtocol.
    L'endpoint est construit depuis resource_name + deployment_name.

    Args:
        api_key: Clé API Azure OpenAI (header "api-key").
        resource_name: Nom de la ressource Azure (ex: "colaig-prod").
        deployment_chat: Nom du déploiement chat (ex: "gpt-4o").
        deployment_embed: Nom du déploiement embeddings.
        api_version: Version de l'API Azure OpenAI.
        chat_timeout: Timeout pour les appels chat (secondes).
        embed_timeout: Timeout pour les appels embeddings (secondes).
        max_retries: Nombre maximal de retries.
    """

    _API_BASE = "https://{resource}.openai.azure.com/openai/deployments/{deployment}"

    def __init__(
        self,
        api_key: str,
        resource_name: str,
        deployment_chat: str = "gpt-4o",
        deployment_embed: str = "text-embedding-3-small",
        api_version: str = "2024-02-01",
        chat_timeout: float = 60.0,
        embed_timeout: float = 30.0,
        max_retries: int = 3,
        embed_max_concurrent: int = 4,
        chat_max_concurrent: int = 4,
        bg_chat_max_concurrent: int = 3,
    ) -> None:
        self._api_key = api_key
        self._resource = resource_name
        self._deployment_chat = deployment_chat
        self._deployment_embed = deployment_embed
        self._api_version = api_version
        self._chat_timeout = chat_timeout
        self._embed_timeout = embed_timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._embed_semaphore = asyncio.Semaphore(embed_max_concurrent)
        self._chat_semaphore = asyncio.Semaphore(chat_max_concurrent)
        self._bg_chat_semaphore = asyncio.Semaphore(bg_chat_max_concurrent)

    def _chat_url(self) -> str:
        base = self._API_BASE.format(resource=self._resource, deployment=self._deployment_chat)
        return f"{base}/chat/completions?api-version={self._api_version}"

    def _embed_url(self) -> str:
        base = self._API_BASE.format(resource=self._resource, deployment=self._deployment_embed)
        return f"{base}/embeddings?api-version={self._api_version}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "api-key": self._api_key,          # Auth Azure (pas Bearer)
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request_with_retry(self, url: str, payload: dict, timeout: float) -> httpx.Response:
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.monotonic()
            try:
                response = await client.post(url, json=payload, timeout=httpx.Timeout(timeout))
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.debug("Azure POST %s → %s (%dms)", url, response.status_code, duration_ms)

                if response.status_code == 429:
                    if attempt < self._max_retries:
                        delay = _retry_after_delay(response, attempt)
                        logger.warning("Azure rate limit, retry %d/%d après %.1fs", attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMRateLimitError(f"Azure 429 après {self._max_retries} retries")

                if response.status_code in (502, 503, 504):
                    if attempt < self._max_retries:
                        delay = _backoff_delay(attempt)
                        logger.warning("Azure %s, retry %d/%d après %.1fs", response.status_code, attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMUnavailableError(f"Azure {response.status_code} après {self._max_retries} retries")

                if response.status_code >= 400:
                    raise LLMError(f"Azure {response.status_code}: {response.text[:200]}")

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    logger.warning("Azure timeout/connect, retry %d/%d après %.1fs", attempt + 1, self._max_retries, delay)
                    await asyncio.sleep(delay)
                    continue

        raise LLMUnavailableError(f"Azure indisponible après {self._max_retries} retries: {last_error}")

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> str:
        # Azure : le modèle est dans l'URL (deployment), pas dans le payload
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with sem:
            response = await self._request_with_retry(self._chat_url(), payload, self._chat_timeout)
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Réponse Azure inattendue: {e}") from e

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> AsyncIterator[str]:
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        client = await self._get_client()
        async with sem:
            async with client.stream("POST", self._chat_url(), json=payload, timeout=httpx.Timeout(self._chat_timeout)) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMError(f"Azure stream {response.status_code}: {body[:200]}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        content = chunk["choices"][0].get("delta", {}).get("content")
                        if content:
                            yield content
                    except (KeyError, ValueError):
                        continue

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        tool_choice: str = "auto",
        priority: str = "user",
    ) -> ChatCompletionResult:
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload = {
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with sem:
            response = await self._request_with_retry(self._chat_url(), payload, self._chat_timeout)
        try:
            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            tool_calls: list[ToolCall] = []
            for tc in msg.get("tool_calls") or []:
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                tool_calls.append(ToolCall(
                    tool_name=tc["function"]["name"],
                    arguments=args,
                    call_id=_normalize_id(tc.get("id", "")),
                ))

            return ChatCompletionResult(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Réponse chat_with_tools Azure inattendue: {e}") from e

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Génère les embeddings d'une liste de textes par batch (batches parallèles)."""
        if not texts:
            return []

        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

        if len(batches) == 1:
            return await self._embed_batch_single(batches[0])

        async def _run(batch: list[str]) -> list[list[float]]:
            async with self._embed_semaphore:
                return await self._embed_batch_single(batch)

        results = await asyncio.gather(*[_run(b) for b in batches])
        return [emb for batch_embs in results for emb in batch_embs]

    async def _embed_batch_single(self, texts: list[str]) -> list[list[float]]:
        """Envoie un batch unique à l'API embeddings Azure et retourne les vecteurs."""
        payload = {"input": texts}
        response = await self._request_with_retry(self._embed_url(), payload, self._embed_timeout)
        try:
            data = response.json()
            return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        except (KeyError, ValueError) as e:
            raise LLMError(f"Réponse embeddings Azure inattendue: {e}") from e
