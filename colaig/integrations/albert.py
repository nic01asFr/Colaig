"""
Colaig — Client Albert API (LLM souverain Etalab/DINUM)

Implémente AlbertClientProtocol.
Format API OpenAI-compatible : /v1/chat/completions, /v1/embeddings.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import random
import time
from typing import AsyncIterator, Optional

import httpx

from colaig.exceptions import AlbertError, AlbertRateLimitError, AlbertUnavailableError
from colaig.models import ChatCompletionResult, ColaigConfig, ToolCall

logger = logging.getLogger(__name__)


def _normalize_id(raw_id: str) -> str:
    """Normalise un tool call ID en 9 caractères alphanumériques (contrainte Mistral)."""
    import re
    alnum = re.sub(r"[^a-zA-Z0-9]", "", raw_id)
    if not alnum:
        import hashlib
        alnum = hashlib.sha256(raw_id.encode()).hexdigest()
    return alnum[-9:] if len(alnum) >= 9 else alnum.ljust(9, "a")


class AlbertClient:
    """Client async pour Albert API.

    Args:
        config: Configuration globale Colaig.
        chat_timeout: Timeout pour les appels chat (secondes).
        embed_timeout: Timeout pour les appels embeddings (secondes).
        max_retries: Nombre maximal de retries sur 429/503/timeout.
    """

    def __init__(
        self,
        config: ColaigConfig,
        chat_timeout: float = 60.0,
        embed_timeout: float = 30.0,
        max_retries: int = 3,
        embed_max_concurrent: int = 4,
        chat_max_concurrent: int = 4,
        bg_chat_max_concurrent: int = 3,
        ocr_page_delay: float = 3.0,
    ) -> None:
        self._base_url = config.albert_api_url.rstrip("/")
        self._api_key = config.albert_api_key
        self._model_chat = config.albert_model_chat
        self._model_embed = config.albert_model_embed
        self._model_rerank = config.albert_model_rerank
        self._model_ocr = config.albert_model_ocr
        self._model_audio = config.albert_model_audio
        self._chat_timeout = chat_timeout
        self._embed_timeout = embed_timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._multipart_client: httpx.AsyncClient | None = None
        # Sémaphore embedding : limite le parallélisme des requêtes d'embedding
        self._embed_semaphore = asyncio.Semaphore(embed_max_concurrent)
        # Sémaphores chat : user a accès à N slots, background (OCR/indexation) à N-1
        # Garantit qu'un appel utilisateur trouve toujours au moins 1 slot disponible
        self._chat_semaphore = asyncio.Semaphore(chat_max_concurrent)
        self._bg_chat_semaphore = asyncio.Semaphore(bg_chat_max_concurrent)
        # Sémaphore OCR : un seul appel OCR à la fois (toutes pages/fichiers confondus)
        # Évite de saturer le quota quand plusieurs PDFs sont indexés en parallèle
        self._ocr_semaphore = asyncio.Semaphore(1)
        # Délai entre pages OCR (rate limiting background) — évite de saturer Albert
        self._ocr_page_delay = ocr_page_delay
        # Capability chains — routing par provider avec fallback
        # Construites depuis les vars COLAIG_CAP_* et les credentials provider
        from colaig.integrations.llm.provider_registry import ProviderRegistry
        from colaig.integrations.llm.capability_chain import CapabilityChain
        _registry = ProviderRegistry.from_env()
        self._chat_chain = CapabilityChain.parse(
            config.cap_chat, _registry, "chat",
            default_model=config.albert_model_chat,
            chat_max_concurrent=chat_max_concurrent,
            bg_chat_max_concurrent=bg_chat_max_concurrent,
        )
        self._embed_chain = CapabilityChain.parse(
            config.cap_embed, _registry, "embed",
            default_model=config.albert_model_embed,
            embed_max_concurrent=embed_max_concurrent,
        )
        self._ocr_chain = CapabilityChain.parse(
            config.cap_ocr, _registry, "ocr",
            default_model=config.albert_model_ocr,
            chat_max_concurrent=1, bg_chat_max_concurrent=1,  # contrôlé par _ocr_semaphore
        )
        self._rerank_chain = CapabilityChain.parse(
            config.cap_rerank, _registry, "rerank",
            default_model=config.albert_model_rerank,
        )
        self._audio_chain = CapabilityChain.parse(
            config.cap_audio, _registry, "audio",
            default_model=config.albert_model_audio,
        )

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

    async def _get_multipart_client(self) -> httpx.AsyncClient:
        """Client HTTP pour les requêtes multipart/form-data (sans Content-Type JSON)."""
        if self._multipart_client is None or self._multipart_client.is_closed:
            self._multipart_client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._api_key}"},
                follow_redirects=True,
            )
        return self._multipart_client

    async def close(self) -> None:
        """Ferme les clients HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self._multipart_client and not self._multipart_client.is_closed:
            await self._multipart_client.aclose()

    async def _request_with_retry(
        self,
        url: str,
        payload: dict,
        timeout: float,
    ) -> httpx.Response:
        """POST avec retry et backoff exponentiel."""
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.monotonic()
            try:
                response = await client.post(
                    url,
                    json=payload,
                    timeout=httpx.Timeout(timeout),
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.debug("albert POST %s → %s (%dms)", url, response.status_code, duration_ms)

                if response.status_code == 429:
                    if attempt < self._max_retries:
                        delay = _retry_after_delay(response, attempt)
                        logger.warning("albert rate limit, retry %d/%d après %.1fs", attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise AlbertRateLimitError(f"Albert 429 après {self._max_retries} retries")

                if response.status_code in (502, 503, 504):
                    if attempt < self._max_retries:
                        delay = _backoff_delay(attempt)
                        logger.warning("albert %s, retry %d/%d après %.1fs", response.status_code, attempt + 1, self._max_retries, delay)
                        await asyncio.sleep(delay)
                        continue
                    raise AlbertUnavailableError(f"Albert {response.status_code} après {self._max_retries} retries")

                if response.status_code >= 400:
                    raise AlbertError(f"Albert {response.status_code}: {response.text[:200]}")

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    logger.warning("albert timeout/connect, retry %d/%d après %.1fs", attempt + 1, self._max_retries, delay)
                    await asyncio.sleep(delay)
                    continue

        raise AlbertUnavailableError(f"Albert indisponible après {self._max_retries} retries: {last_error}")

    # ── Chat ─────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> str:
        """Appel chat completions. Retourne le texte de la réponse.

        Args:
            priority: "user" (défaut) ou "background". Les appels "background"
                (OCR, indexation) acquièrent un sémaphore limité (N-1 slots) pour
                toujours laisser au moins 1 slot libre aux requêtes utilisateur.
        """
        if not self._chat_chain.is_empty:
            return await self._chat_chain.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, priority=priority)
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with sem:
            response = await self._request_with_retry(
                f"{self._base_url}/v1/chat/completions",
                payload,
                self._chat_timeout,
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise AlbertError(f"Réponse Albert inattendue: {e}") from e

    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> AsyncIterator[str]:
        """Appel chat completions en streaming SSE.

        Args:
            priority: "user" (défaut) ou "background". Voir chat() pour le détail.
        """
        if not self._chat_chain.is_empty:
            async for chunk in self._chat_chain.chat_stream(messages, model=model, temperature=temperature, max_tokens=max_tokens, priority=priority):
                yield chunk
            return
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
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(self._chat_timeout),
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise AlbertError(f"Albert stream {response.status_code}: {body[:200]}")

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        tool_choice: str = "auto",
        priority: str = "user",
    ) -> ChatCompletionResult:
        """Chat completions avec tool calling natif (format OpenAI).

        Albert API (gpt-oss-120b, modèles Mistral) supporte nativement le paramètre
        `tools` via l'interface OpenAI-compatible.

        Args:
            messages: Historique de conversation OpenAI format.
            tools: Schémas JSON des outils disponibles (format OpenAI function calling).
            model: Modèle à utiliser (défaut : model_chat configuré).
            temperature: Température de génération.
            max_tokens: Nombre maximum de tokens dans la réponse.
            tool_choice: "auto" | "none" | "required". "none" force une réponse texte.
            priority: "user" (défaut) ou "background". Voir chat() pour le détail.

        Returns:
            ChatCompletionResult avec soit content (texte), soit tool_calls.
        """
        if not self._chat_chain.is_empty:
            return await self._chat_chain.chat_with_tools(messages, tools=tools, model=model, temperature=temperature, max_tokens=max_tokens, tool_choice=tool_choice, priority=priority)
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        payload: dict = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        async with sem:
            response = await self._request_with_retry(
                f"{self._base_url}/v1/chat/completions",
                payload,
                self._chat_timeout,
            )

        data = response.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")

            raw_tool_calls = message.get("tool_calls") or []
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    try:
                        arguments = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_calls.append(ToolCall(
                        tool_name=fn.get("name", ""),
                        arguments=arguments,
                        call_id=_normalize_id(tc.get("id", "")),
                    ))
                return ChatCompletionResult(
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                )

            content = message.get("content") or ""
            return ChatCompletionResult(content=content, finish_reason=finish_reason)

        except (KeyError, IndexError) as e:
            raise AlbertError(f"Réponse chat_with_tools inattendue: {e}") from e

    # ── Embeddings ───────────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """Génère l'embedding d'un texte unique."""
        if not self._embed_chain.is_empty:
            return await self._embed_chain.embed(text)
        payload = {
            "model": self._model_embed,
            "input": text,
        }

        response = await self._request_with_retry(
            f"{self._base_url}/v1/embeddings",
            payload,
            self._embed_timeout,
        )

        data = response.json()
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError) as e:
            raise AlbertError(f"Réponse embeddings inattendue: {e}") from e

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Génère les embeddings d'une liste de textes par batch.

        Les batches sont envoyés en parallèle (jusqu'à embed_max_concurrent requêtes
        simultanées) pour maximiser le débit, quelle que soit la taille du corpus.
        """
        if not self._embed_chain.is_empty:
            return await self._embed_chain.embed_batch(texts, batch_size=batch_size)
        if not texts:
            return []

        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

        if len(batches) == 1:
            # Fast path — pas d'overhead asyncio.gather pour le cas commun
            return await self._embed_batch_single(batches[0])

        async def _run(batch: list[str]) -> list[list[float]]:
            async with self._embed_semaphore:
                return await self._embed_batch_single(batch)

        results = await asyncio.gather(*[_run(b) for b in batches])
        return [emb for batch_embs in results for emb in batch_embs]

    async def _embed_batch_single(self, texts: list[str]) -> list[list[float]]:
        """Envoie un batch unique à l'API embeddings et retourne les vecteurs."""
        payload = {
            "model": self._model_embed,
            "input": texts,
        }
        response = await self._request_with_retry(
            f"{self._base_url}/v1/embeddings",
            payload,
            self._embed_timeout,
        )
        data = response.json()
        try:
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]
        except (KeyError, IndexError) as e:
            raise AlbertError(f"Réponse batch embeddings inattendue: {e}") from e

    # ── Reranking ─────────────────────────────────────────────────────

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: Optional[int] = None,
        model: Optional[str] = None,
    ) -> list[tuple[int, float]]:
        """Reranke des documents selon leur pertinence pour une query (cross-encoder).

        Args:
            query: La question/requête de référence.
            documents: Liste de textes à reranker.
            top_n: Nombre de résultats à retourner (None = tous).
            model: Modèle de reranking (défaut: albert_model_rerank).

        Returns:
            Liste de (index_original, score) triée par score décroissant.
        """
        # Déléguer à la chain si configurée
        if not self._rerank_chain.is_empty():
            chain_result = await self._rerank_chain.rerank(query, documents, top_n=top_n, model=model)
            if chain_result:
                return chain_result

        payload: dict = {
            "model": model or self._model_rerank,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        response = await self._request_with_retry(
            f"{self._base_url}/v1/rerank",
            payload,
            self._embed_timeout,
        )

        data = response.json()
        try:
            results = data["results"]
            return [(r["index"], r["relevance_score"]) for r in results]
        except (KeyError, IndexError) as e:
            raise AlbertError(f"Réponse rerank inattendue: {e}") from e

    # ── OCR ───────────────────────────────────────────────────────────

    async def ocr(
        self,
        content: bytes,
        filename: str,
        model: Optional[str] = None,
        dpi: int = 150,
        prompt: str = "",
    ) -> str:
        """OCR d'un document (PDF ou image) via Albert API.

        Pour les PDFs : vision multimodal page-par-page via /v1/chat/completions
        (contourne le timeout 504 de /v1/ocr-beta sur les gros fichiers).
        Nécessite pymupdf pour la conversion PDF→PNG.
        Fallback sur /v1/ocr-beta si vision échoue ou pymupdf absent.

        Pour les images : /v1/ocr-beta directement.

        Args:
            content: Contenu binaire du fichier.
            filename: Nom du fichier (pour le MIME type et la détection du format).
            model: Modèle OCR (défaut: albert_model_ocr).
            dpi: Résolution de numérisation (défaut: 150).
            prompt: Instruction optionnelle pour le modèle multimodal.

        Returns:
            Texte extrait en Markdown (toutes pages concaténées).
        """
        # PDFs : vision multimodal page-par-page (pas de timeout 504)
        if filename.lower().endswith(".pdf"):
            try:
                text = await self.ocr_vision(content, filename, model=model, dpi=dpi, ocr_prompt=prompt)
                if text.strip():
                    return text
                logger.debug("OCR vision vide pour %s, fallback ocr-beta", filename)
            except Exception as e:
                logger.warning("OCR vision échoué pour %s, fallback ocr-beta: %s", filename, e)

        # Fallback : /v1/ocr-beta (multipart)
        return await self._ocr_beta(content, filename, model=model, dpi=dpi, prompt=prompt)

    async def ocr_vision(
        self,
        content: bytes,
        filename: str,
        model: Optional[str] = None,
        dpi: int = 150,
        ocr_prompt: str = "",
    ) -> str:
        """OCR d'un PDF via /v1/chat/completions + image_url (vision multimodal).

        Convertit chaque page PDF en PNG et envoie une requête chat par page.
        Contourne le timeout 504 de /v1/ocr-beta (requêtes courtes de ~10-30s/page).
        Nécessite pymupdf (pip install pymupdf).

        Args:
            content: Contenu binaire du PDF.
            filename: Nom du fichier (pour le logging).
            model: Modèle vision (défaut: albert_model_ocr).
            dpi: Résolution de rendu des pages (défaut: 150).
            ocr_prompt: Instruction optionnelle pour guider l'extraction.

        Returns:
            Texte extrait en Markdown (toutes pages concaténées).

        Raises:
            AlbertUnavailableError: Si la conversion PDF→PNG est impossible.
        """
        page_images = _pdf_pages_to_png(content, dpi=dpi)
        if not page_images:
            raise AlbertUnavailableError(
                "OCR vision: impossible de convertir le PDF en images (pymupdf absent?)"
            )

        model_vision = model or self._model_ocr
        text_prompt = ocr_prompt or (
            "Extrait tout le texte de cette page de document en Markdown. "
            "Préserve la structure (titres, listes, tableaux). "
            "Ne génère rien d'autre que le texte extrait."
        )

        pages_text: list[str] = []
        for i, png_bytes in enumerate(page_images):
            b64 = base64.b64encode(png_bytes).decode("ascii")
            data_url = f"data:image/png;base64,{b64}"

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ]

            async with self._ocr_semaphore:
                try:
                    if not self._ocr_chain.is_empty:
                        # Ne pas forcer model= : chaque entrée de la chain utilise
                        # son default_model propre (ex: albert→HF name, mistral→mistral name)
                        page_text = await self._ocr_chain.chat(
                            messages, temperature=0.1, max_tokens=4000,
                            priority="background",
                        )
                    else:
                        page_text = await self.chat(
                            messages, model=model_vision, temperature=0.1, max_tokens=4000,
                            priority="background",
                        )
                    if page_text.strip():
                        pages_text.append(page_text.strip())
                    logger.debug(
                        "OCR vision %s page %d/%d : %d chars",
                        filename, i + 1, len(page_images), len(page_text),
                    )
                except Exception as e:
                    logger.warning(
                        "OCR vision page %d/%d échouée (%s) : %s",
                        i + 1, len(page_images), filename, e,
                    )

                # Rate limiting : pause entre pages (globale, sous le sémaphore)
                # Garantit le délai même entre pages de fichiers différents traités en parallèle
                if self._ocr_page_delay > 0 and i < len(page_images) - 1:
                    await asyncio.sleep(self._ocr_page_delay)

        return "\n\n".join(pages_text)

    async def _ocr_json(
        self,
        content: bytes,
        filename: str,
        model: Optional[str] = None,
    ) -> str:
        """OCR via POST /v1/ocr (JSON body, document_url base64)."""
        import base64
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = "application/octet-stream"

        b64 = base64.b64encode(content).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64}"

        payload: dict = {
            "document": {
                "type": "document_url",
                "document_url": data_url,
                "document_name": filename,
            },
        }
        # model est optionnel sur /v1/ocr.
        # Si un modèle explicite est passé (par l'appelant), on l'utilise.
        # Sinon on laisse Albert choisir son modèle OCR par défaut (évite 422 Wrong model type).
        if model:
            payload["model"] = model

        response = await self._request_with_retry(
            f"{self._base_url}/v1/ocr",
            payload,
            timeout=120.0,
        )

        result = response.json()
        pages = result.get("pages", [])
        texts = [p.get("markdown", "") for p in pages if p.get("markdown")]
        return "\n\n".join(texts)

    async def _ocr_beta(
        self,
        content: bytes,
        filename: str,
        model: Optional[str] = None,
        dpi: int = 150,
        prompt: str = "",
    ) -> str:
        """OCR via POST /v1/ocr-beta (multipart form-data)."""
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = "application/octet-stream"

        data_fields: dict = {
            "model": model or self._model_ocr,
            "dpi": str(dpi),
        }
        if prompt:
            data_fields["prompt"] = prompt

        client = await self._get_multipart_client()
        try:
            response = await client.post(
                f"{self._base_url}/v1/ocr-beta",
                files={"file": (filename, content, mime_type)},
                data=data_fields,
                timeout=httpx.Timeout(300.0),
            )
            if response.status_code >= 400:
                raise AlbertError(f"Albert OCR-beta {response.status_code}: {response.text[:200]}")

            result = response.json()
            pages = result.get("data", [])
            texts = [p.get("content", "") for p in pages if p.get("content")]
            return "\n\n".join(texts)

        except AlbertError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise AlbertUnavailableError(f"Albert OCR indisponible: {e}") from e

    # ── Audio ─────────────────────────────────────────────────────────

    async def transcribe(
        self,
        content: bytes,
        filename: str,
        model: Optional[str] = None,
    ) -> str:
        """Transcription audio via Albert API (whisper-large-v3).

        Endpoint OpenAI-compatible : POST /v1/audio/transcriptions.
        Supporte les formats Tchap/Matrix (ogg/opus) et Telegram (ogg, mp3, wav).

        Args:
            content: Contenu binaire du fichier audio.
            filename: Nom du fichier (ogg, mp3, wav, m4a, webm…).
            model: Modèle audio (défaut: albert_model_audio).

        Returns:
            Texte transcrit. Chaîne vide si erreur.
        """
        # Albert n'accepte que mp3 et wav — convertir si nécessaire via ffmpeg
        content, filename = await _convert_audio_to_wav(content, filename)

        # Déléguer à la chain si configurée
        if not self._audio_chain.is_empty():
            chain_result = await self._audio_chain.transcribe(content, filename, model=model)
            if chain_result:
                return chain_result

        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = "audio/wav"

        client = await self._get_multipart_client()
        try:
            response = await client.post(
                f"{self._base_url}/v1/audio/transcriptions",
                files={"file": (filename, content, mime_type)},
                data={"model": model or self._model_audio},
                timeout=httpx.Timeout(120.0),
            )
            if response.status_code >= 400:
                raise AlbertError(f"Albert transcribe {response.status_code}: {response.text[:200]}")

            result = response.json()
            return result.get("text", "")

        except AlbertError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise AlbertUnavailableError(f"Albert audio indisponible: {e}") from e


# ── Helpers ──────────────────────────────────────────────────────────


async def _convert_audio_to_wav(content: bytes, filename: str) -> tuple[bytes, str]:
    """Convertit un fichier audio en WAV 16kHz mono via ffmpeg.

    Albert n'accepte que mp3 et wav. Tchap envoie du ogg/opus (codec Opus).
    ffmpeg gère tous les formats audio sans restriction de codec.
    Retourne (content, filename) éventuellement convertis.
    """
    import os
    import tempfile

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp3", "wav"):
        return content, filename

    tmp_in: str | None = None
    tmp_out: str | None = None
    try:
        # Écrire le fichier source dans un fichier temporaire
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(content)
            tmp_in = f.name

        tmp_out = tmp_in + "_out.wav"

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", tmp_in,
            "-ar", "16000",   # 16kHz
            "-ac", "1",        # mono
            "-f", "wav",
            tmp_out,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[-300:]
            logging.getLogger(__name__).warning("conversion audio ffmpeg échouée (rc=%d): %s, envoi brut", proc.returncode, err)
            return content, filename

        with open(tmp_out, "rb") as f:
            wav_bytes = f.read()

        new_filename = (filename.rsplit(".", 1)[0] if "." in filename else filename) + ".wav"
        return wav_bytes, new_filename

    except FileNotFoundError:
        logging.getLogger(__name__).warning("ffmpeg introuvable, envoi audio brut")
        return content, filename
    except Exception as exc:
        logging.getLogger(__name__).warning("conversion audio ffmpeg échouée (%s), envoi brut", exc)
        return content, filename
    finally:
        for path in (tmp_in, tmp_out):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


@contextlib.contextmanager
def _suppress_mupdf_output():
    """Redirige temporairement fd 1 (stdout) et fd 2 (stderr) vers /dev/null.

    Pymupdf >= 1.18 redirige les avertissements MuPDF vers stdout via print().
    Ce context manager supprime les deux flux pour étouffer les messages parasites
    (ex: "format error: No common ancestor in structure tree").
    Sûr en contexte asyncio single-threaded.
    """
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    try:
        yield
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(null)
        os.close(saved_out)
        os.close(saved_err)


def _pdf_pages_to_png(content: bytes, dpi: int = 150) -> list[bytes]:
    """Convertit les pages d'un PDF en images PNG via pymupdf (fitz).

    Args:
        content: Contenu binaire du PDF.
        dpi: Résolution de rendu (défaut: 150). 150 DPI = bon équilibre qualité/taille.

    Returns:
        Liste d'images PNG (bytes), une par page. Vide si pymupdf absent ou erreur.
    """
    try:
        import fitz  # pymupdf

        with _suppress_mupdf_output():
            doc = fitz.open(stream=content, filetype="pdf")
            images: list[bytes] = []
            scale = dpi / 72  # 72 DPI = résolution native PDF
            mat = fitz.Matrix(scale, scale)
            for page in doc:
                pix = page.get_pixmap(matrix=mat)
                images.append(pix.tobytes("png"))
            doc.close()
        return images
    except ImportError:
        logging.getLogger(__name__).warning(
            "pymupdf non installé — OCR vision PDF impossible (pip install pymupdf)"
        )
        return []
    except Exception as e:
        logging.getLogger(__name__).warning("conversion PDF→PNG échouée: %s", e)
        return []


def _backoff_delay(attempt: int, base: float = 1.0, maximum: float = 60.0) -> float:
    """Calcule un délai de backoff exponentiel avec jitter."""
    delay = min(base * (2 ** attempt), maximum)
    return delay + random.uniform(0, delay * 0.1)


def _retry_after_delay(response: "httpx.Response", attempt: int, maximum: float = 60.0) -> float:
    """Délai avant retry : lit Retry-After si présent, sinon backoff exponentiel.

    Retry-After (RFC 7231 §7.1.3) peut être :
    - Entier (secondes) : "Retry-After: 30"
    - Date HTTP : "Retry-After: Wed, 21 Oct 2015 07:28:00 GMT" (ignoré, fallback backoff)

    Compatible avec tous les providers OpenAI-compat (OpenAI, Azure, Mistral, Albert...).
    Albert API n'envoie pas Retry-After → fallback backoff exponentiel.
    """
    retry_after = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if retry_after:
        try:
            seconds = float(retry_after)
            # Plafonner à maximum pour éviter d'attendre indéfiniment
            return min(seconds, maximum)
        except ValueError:
            pass  # Format date HTTP — fallback backoff
    return _backoff_delay(attempt)
