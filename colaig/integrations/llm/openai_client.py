"""
Colaig — OpenAIClient

Client LLM générique pour tout endpoint OpenAI-compatible.
Implémente LLMClientProtocol (alias AlbertClientProtocol).

Compatible avec :
    OpenAI           → base_url="https://api.openai.com"
    Mistral AI       → base_url="https://api.mistral.ai"
    Groq             → base_url="https://api.groq.com/openai"
    Together AI      → base_url="https://api.together.xyz"
    Albert API       → base_url="https://albert.api.etalab.gouv.fr"
    Tout endpoint OpenAI-compat (LM Studio, vLLM...)

Dépendance : httpx (déjà dans le projet)
"""

from __future__ import annotations

import asyncio
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
    """Backoff exponentiel avec jitter : base 1s, max 60s."""
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


class OpenAIClient:
    """Client LLM générique pour tout endpoint OpenAI-compatible.

    Paramétré explicitement (pas de ColaigConfig) pour être utilisable
    dans un contexte multi-client (ClientConfig par tenant).

    Args:
        api_key: Clé API (Bearer token).
        base_url: URL de base du endpoint (sans trailing slash).
        model_chat: Modèle de chat par défaut.
        model_embed: Modèle d'embeddings par défaut.
        chat_timeout: Timeout pour les appels chat (secondes).
        embed_timeout: Timeout pour les appels embeddings (secondes).
        max_retries: Nombre maximal de retries sur 429/503/timeout.
        backend_name: Nom du backend pour les logs/erreurs.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com",
        model_chat: str = "gpt-4o",
        model_embed: str = "text-embedding-3-small",
        chat_timeout: float = 60.0,
        embed_timeout: float = 30.0,
        max_retries: int = 3,
        backend_name: str = "OpenAI",
        embed_max_concurrent: int = 4,
        chat_max_concurrent: int = 4,
        bg_chat_max_concurrent: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model_chat = model_chat
        self._model_embed = model_embed
        self._chat_timeout = chat_timeout
        self._embed_timeout = embed_timeout
        self._max_retries = max_retries
        self._backend = backend_name
        self._client: httpx.AsyncClient | None = None
        self._embed_semaphore = asyncio.Semaphore(embed_max_concurrent)
        self._chat_semaphore = asyncio.Semaphore(chat_max_concurrent)
        self._bg_chat_semaphore = asyncio.Semaphore(bg_chat_max_concurrent)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Ferme le client HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request_with_retry(self, url: str, payload: dict, timeout: float) -> httpx.Response:
        """POST avec retry et backoff exponentiel."""
        client = await self._get_client()
        last_error: Exception | None = None
        last_retry_after: float | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.monotonic()
            try:
                response = await client.post(url, json=payload, timeout=httpx.Timeout(timeout))
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.debug("%s POST %s → %s (%dms)", self._backend, url, response.status_code, duration_ms)

                if response.status_code == 429:
                    delay = _retry_after_delay(response, attempt)
                    last_retry_after = delay
                    if attempt < self._max_retries:
                        logger.warning("%s rate limit, retry %d/%d après %.1fs", self._backend, attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMRateLimitError(
                        f"{self._backend} 429 après {self._max_retries} retries",
                        retry_after=last_retry_after,
                    )

                if response.status_code in (502, 503, 504):
                    if attempt < self._max_retries:
                        delay = _backoff_delay(attempt)
                        logger.warning("%s %s, retry %d/%d après %.1fs", self._backend, response.status_code, attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMUnavailableError(f"{self._backend} {response.status_code} après {self._max_retries} retries")

                if response.status_code >= 400:
                    raise LLMError(f"{self._backend} {response.status_code}: {response.text[:200]}")

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    logger.warning("%s timeout/connect, retry %d/%d après %.1fs", self._backend, attempt + 1, self._max_retries, delay)
                    await asyncio.sleep(delay)
                    continue

        raise LLMUnavailableError(f"{self._backend} indisponible après {self._max_retries} retries: {last_error}")

    # ── Chat ──────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> str:
        """Appel chat completions. Retourne le texte de la réponse."""
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with sem:
            response = await self._request_with_retry(url, payload, self._chat_timeout)
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Réponse {self._backend} inattendue: {e}") from e

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> AsyncIterator[str]:
        """Appel chat completions en streaming (SSE)."""
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        client = await self._get_client()
        async with sem:
            async with client.stream("POST", url, json=payload, timeout=httpx.Timeout(self._chat_timeout)) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMError(f"{self._backend} stream {response.status_code}: {body[:200]}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        import json
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content")
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
        """Chat completions avec tool calling (format OpenAI). Retourne ChatCompletionResult."""
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with sem:
            response = await self._request_with_retry(url, payload, self._chat_timeout)
        try:
            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            tool_calls: list[ToolCall] = []
            for tc in msg.get("tool_calls") or []:
                import json as _json
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
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
            raise LLMError(f"Réponse chat_with_tools {self._backend} inattendue: {e}") from e

    # ── Embeddings ────────────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """Génère l'embedding d'un texte unique."""
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
        """Envoie un batch unique à l'API embeddings et retourne les vecteurs."""
        payload = {"model": self._model_embed, "input": texts}
        response = await self._request_with_retry(
            f"{self._base_url}/v1/embeddings", payload, self._embed_timeout
        )
        try:
            data = response.json()
            return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        except (KeyError, ValueError) as e:
            raise LLMError(f"Réponse embeddings {self._backend} inattendue: {e}") from e

    # ── Reranking (OpenAI-compatible /v1/rerank) ─────────────────────

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        model: str | None = None,
    ) -> list[tuple[int, float]]:
        """Reranke des documents (endpoint /v1/rerank, compatible Albert/Cohere).

        Returns:
            Liste de (index_original, score) triée par score décroissant.
            Retourne [] si le provider ne supporte pas l'endpoint (404/405).
        """
        payload: dict = {
            "model": model or self._model_chat,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        url = self._base_url + "/v1/rerank"
        try:
            client = await self._get_client()
            async with self._chat_semaphore:
                resp = await client.post(url, json=payload, timeout=30.0)
            if resp.status_code in (404, 405):
                return []
            if resp.status_code >= 400:
                raise LLMError(f"{self._backend} rerank {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            results = data.get("results", [])
            pairs = [(r["index"], float(r["relevance_score"])) for r in results]
            return sorted(pairs, key=lambda x: x[1], reverse=True)
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"rerank {self._backend}: {e}") from e

    # ── Audio transcription (OpenAI-compatible /v1/audio/transcriptions) ─────

    async def transcribe(
        self,
        content: bytes,
        filename: str,
        model: str | None = None,
    ) -> str:
        """Transcription audio via /v1/audio/transcriptions (standard OpenAI).

        Returns:
            Texte transcrit. Chaîne vide si erreur ou provider ne supporte pas.
        """
        url = self._base_url + "/v1/audio/transcriptions"
        try:
            files = {"file": (filename, content, "audio/wav")}
            data = {"model": model or self._model_chat}
            # Requête multipart sans Content-Type JSON — utilise un client httpx dédié
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._api_key}"},
                follow_redirects=True,
            ) as mp_client:
                async with self._chat_semaphore:
                    resp = await mp_client.post(url, files=files, data=data, timeout=60.0)
            if resp.status_code in (404, 405):
                return ""
            resp.raise_for_status()
            return resp.json().get("text", "")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"transcribe {self._backend}: {e}") from e
