"""
Colaig — OllamaClient (Ollama local)

Client LLM pour Ollama autohébergé.
Implémente LLMClientProtocol (alias LLMClientProtocol).

Ollama expose une API OpenAI-compatible sur http://localhost:11434/v1/
Pas d'authentification requise (usage local uniquement).

Variables d'env attendues (via ClientConfig ou ColaigConfig) :
    LLM_API_URL   → URL de base Ollama (défaut: "http://localhost:11434")
    LLM_MODEL_CHAT    → Modèle chat (ex: "llama3.2", "mistral", "phi3")
    LLM_MODEL_EMBED   → Modèle embeddings (ex: "nomic-embed-text", "mxbai-embed-large")
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator

import httpx

from colaig.utils.reponses_llm import extraire_contenu
from colaig.exceptions import LLMError, LLMUnavailableError
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


class OllamaClient:
    """Client LLM pour Ollama autohébergé (API OpenAI-compatible).

    Pas d'authentification — usage local uniquement.
    Pas de retry sur rate limit (Ollama n'émet pas de 429).
    Retry sur timeout/connect uniquement.

    Args:
        base_url: URL de base Ollama (ex: "http://localhost:11434").
        model_chat: Modèle de chat par défaut (ex: "llama3.2").
        model_embed: Modèle d'embeddings par défaut (ex: "nomic-embed-text").
        chat_timeout: Timeout pour les appels chat (secondes).
        embed_timeout: Timeout pour les appels embeddings (secondes).
        max_retries: Nombre maximal de retries sur timeout/connect.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model_chat: str = "llama3.2",
        model_embed: str = "nomic-embed-text",
        chat_timeout: float = 120.0,  # Ollama peut être lent (génération locale)
        embed_timeout: float = 60.0,
        max_retries: int = 2,
        embed_max_concurrent: int = 2,  # Local — plus conservateur pour éviter OOM
        chat_max_concurrent: int = 2,   # Local — une GPU, accès concurrent limité
        bg_chat_max_concurrent: int = 1,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_chat = model_chat
        self._model_embed = model_embed
        self._chat_timeout = chat_timeout
        self._embed_timeout = embed_timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._embed_semaphore = asyncio.Semaphore(embed_max_concurrent)
        self._chat_semaphore = asyncio.Semaphore(chat_max_concurrent)
        self._bg_chat_semaphore = asyncio.Semaphore(bg_chat_max_concurrent)

    def _chat_url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    def _embed_url(self) -> str:
        return f"{self._base_url}/v1/embeddings"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Content-Type": "application/json"},
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
                logger.debug("Ollama POST %s → %s (%dms)", url, response.status_code, duration_ms)

                if response.status_code in (502, 503, 504):
                    if attempt < self._max_retries:
                        delay = _backoff_delay(attempt)
                        logger.warning("Ollama %s, retry %d/%d après %.1fs", response.status_code, attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMUnavailableError(f"Ollama {response.status_code} après {self._max_retries} retries")

                if response.status_code >= 400:
                    raise LLMError(f"Ollama {response.status_code}: {response.text[:200]}")

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    logger.warning("Ollama timeout/connect, retry %d/%d après %.1fs", attempt + 1, self._max_retries, delay)
                    await asyncio.sleep(delay)
                    continue

        raise LLMUnavailableError(f"Ollama indisponible après {self._max_retries} retries: {last_error}")

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> str:
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with sem:
            response = await self._request_with_retry(self._chat_url(), payload, self._chat_timeout)
        try:
            return extraire_contenu(response.json(), "Ollama", max_tokens)
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Réponse Ollama inattendue: {e}") from e

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
            "model": model or self._model_chat,
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
                    raise LLMError(f"Ollama stream {response.status_code}: {body[:200]}")
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
            "model": model or self._model_chat,
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
            raise LLMError(f"Réponse chat_with_tools Ollama inattendue: {e}") from e

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        """Génère les embeddings par batch. Batch size réduit (local) + parallélisme conservateur."""
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
        """Envoie un batch unique à Ollama et retourne les vecteurs."""
        payload = {"model": self._model_embed, "input": texts}
        response = await self._request_with_retry(self._embed_url(), payload, self._embed_timeout)
        try:
            data = response.json()
            return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        except (KeyError, ValueError) as e:
            raise LLMError(f"Réponse embeddings Ollama inattendue: {e}") from e
