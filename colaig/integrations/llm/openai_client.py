"""
Colaig — OpenAIClient

Client LLM générique pour tout endpoint OpenAI-compatible.
Implémente LLMClientProtocol (alias LLMClientProtocol).

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
import base64
import logging
import random
import time
from collections.abc import AsyncIterator

import httpx

from colaig.exceptions import LLMError, LLMRateLimitError, LLMUnavailableError
# Le convertisseur PDF vers PNG est deja ecrit et eprouve dans `albert.py`. Une
# seconde copie divergerait au premier correctif — ce depot a paye cinq fois la
# copie d'un motif.
from colaig.integrations.albert import _pdf_pages_to_png
from colaig.integrations.llm.utils import normalize_tool_call_id as _normalize_id
from colaig.metrics.quota import enregistrer_usage, verifier_quota
from colaig.models import ChatCompletionResult, ToolCall
from colaig.utils.reponses_llm import extraire_contenu

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
        usage_tracker=None,   # UsageTracker | None — quota et comptage par tenant (L2.6)
        client_id: str = "",  # tenant, pour le quota
        enable_thinking: bool = False,  # jetons de raisonnement — voir _kwargs_modele
        model_ocr: str = "",            # vide = capacité `ocr` honnêtement absente
    ) -> None:
        self._api_key = api_key
        self._enable_thinking = enable_thinking
        self._model_ocr = model_ocr

        # SANS MODELE, LA CAPACITE EST HONNETEMENT ABSENTE.
        #
        # `supporte(client, "ocr")` teste `callable(getattr(client, "ocr", None))`.
        # Laisser la methode en place sans modele annoncerait donc une capacite qui
        # echouerait au premier appel — et l'indexeur, qui interroge `supporte()` AVANT
        # d'appeler, cesserait de sauter proprement le document.
        #
        # Ce serait la treizieme « capacite declaree qui ne fait rien » de ce depot, et
        # la premiere introduite en croyant en corriger une. `self.ocr = None` le dit a
        # `supporte()` dans son propre langage.
        if not model_ocr:
            self.ocr = None
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
        self._usage_tracker = usage_tracker
        self._client_id = client_id

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

    async def ping(self, timeout: float = 5.0) -> bool:
        """Sonde de disponibilité : l'endpoint répond-il ? Sans consommer de jetons.

        POURQUOI CETTE MÉTHODE MANQUAIT, ET CE QUE CELA COÛTAIT
        ---------------------------------------------------------
        `/ready` interroge le client ainsi :

            ping = getattr(llm_client, "ping", None)
            checks["llm"] = "ok" if (ping and await ping()) else "unavailable"

        Un client SANS `ping` tombe dans la branche `else` — indistinguable d'un
        endpoint en panne. Or `ping()` n'existait que sur `AlbertClient`, alors que la
        cible de production est un endpoint OpenAI-compatible (`CLAUDE.md` §3).

        Mesuré le 29/08/2026 sur un déploiement réel : `/ready` rendait 503 avec
        `llm: unavailable`, tandis que le même pod recevait **HTTP 200** en interrogeant
        l'endpoint directement. Le pod ne devenait jamais prêt, et Kubernetes ne lui
        envoyait aucun trafic — indéfiniment.

        Le défaut était invisible tant que le chart sondait `/health`, qui rend 200 sans
        rien vérifier. Une sonde qui ne peut pas échouer ne cache pas que les pannes :
        elle cache aussi ses propres trous.

        TOUT STATUT < 500 VAUT DISPONIBLE. Un 401 prouve qu'un serveur est là et répond
        — c'est la joignabilité qu'on mesure, pas l'autorisation. Sortir le pod du
        service pour une clé expirée traiterait par le redémarrage un problème que le
        redémarrage ne répare pas.

        NE LÈVE JAMAIS : une sonde qui lève transforme une dépendance lente en pod
        redémarré en boucle.
        """
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/v1/models", timeout=timeout)
            return resp.status_code < 500
        except Exception:  # noqa: BLE001 — une sonde ne doit jamais lever
            return False

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

    def _kwargs_modele(self) -> dict:
        """Le paramètre qui décide si le modèle réfléchit à voix haute.

        UN MODELE A RAISONNEMENT PEUT CONSOMMER TOUT LE BUDGET AVANT DE REPONDRE. Le
        serveur rend alors `content` vide, et l'appel echoue :

            réponse vide, budget de tokens épuisé (max_tokens=2048)

        Releve le 30/08/2026, a la premiere question posee a un vrai corpus : cinq
        passages de contexte ont suffi a epuiser le budget de `qwen3-6-35b-moe`.

        LE DEPOT CONNAISSAIT DEJA CE PIEGE — mais seulement dans son harnais de mesure.
        Tous les scripts de `_chantier/scripts/` passent ce parametre, l'un d'eux avec
        ce commentaire : « SANS CECI, LA MESURE EST VIDE ». Il n'apparaissait nulle part
        dans `colaig/` : les mesures portaient donc sur une configuration que le produit
        n'avait pas.

        Le defaut est DESACTIVE : un modele qui reflechit mieux mais ne repond pas vaut
        moins qu'un modele qui repond. `COLAIG_LLM_THINKING=true` rouvre le reglage pour
        une instance dont le budget le permet — et l'on n'envoie alors RIEN, laissant le
        defaut du fournisseur decider.
        """
        if self._enable_thinking:
            return {}
        return {"chat_template_kwargs": {"enable_thinking": False}}

    # ── OCR ───────────────────────────────────────────────────────────

    async def ocr(
        self,
        content: bytes,
        filename: str,
        model: str | None = None,
        dpi: int = 150,
        prompt: str = "",
    ) -> str:
        """Extrait le texte d'un PDF scanné ou d'une image, par vision multimodale.

        POURQUOI CETTE METHODE EXISTE. `AlbertClient` savait faire l'OCR ; ce client,
        non — et c'est lui qui tourne en production. Sur les 59 documents du corpus
        depose le 30/08/2026, **sept restaient invisibles**, avec ce message a chaque
        indexation :

            document non indexe (document sans texte natif —
            le backend LLM (OpenAIClient) ne fournit pas la capacite « ocr »)

        Le message etait juste, et le catalogue de SSPCloud contient `chandra-ocr-2`.
        La capacite existait des deux cotes ; rien ne les reliait.

        UNE REQUETE PAR PAGE. Un PDF entier en un seul appel expire : c'est ce qu'Albert
        avait deja constate (504 sur `/v1/ocr-beta`), et la page-par-page est ce qui l'a
        resolu. On reprend sa methode plutot que d'en inventer une seconde.

        Args:
            content: contenu binaire du document.
            filename: nom du fichier — decide du traitement (PDF ou image).
            model: modele de vision ; par defaut celui de la configuration.
            dpi: resolution de rendu des pages PDF.
            prompt: consigne d'extraction ; une consigne par defaut sinon.

        Returns:
            Le texte extrait, en Markdown, pages concatenees.

        Raises:
            LLMUnavailableError: si un PDF ne peut pas etre converti en images.
        """
        modele = model or self._model_ocr
        consigne = prompt or (
            "Extrais tout le texte de cette page de document en Markdown. "
            "Préserve la structure (titres, listes, tableaux). "
            "Ne génère rien d'autre que le texte extrait."
        )

        if filename.lower().endswith(".pdf"):
            pages = _pdf_pages_to_png(content, dpi=dpi)
            if not pages:
                # RENDRE UNE CHAINE VIDE SERAIT PIRE QUE D'ECHOUER : le document serait
                # indexe sans contenu, occuperait une place, et repondrait du vide a une
                # question. L'indexeur sait traiter une erreur ; il ne sait pas deviner
                # qu'un texte vide n'est pas un texte.
                raise LLMUnavailableError(
                    f"OCR impossible pour {filename} : conversion PDF→image en échec "
                    f"(pymupdf absent ?)"
                )
        else:
            pages = [content]

        textes: list[str] = []
        for page in pages:
            b64 = base64.b64encode(page).decode("ascii")
            texte = await self.chat(
                [{"role": "user", "content": [
                    {"type": "text", "text": consigne},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
                model=modele,
                temperature=0.0,
                max_tokens=4096,
                priority="background",
            )
            if texte and texte.strip():
                textes.append(texte.strip())

        return "\n\n".join(textes)

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
        # Quota du tenant — point de passage unique (L2.6). Il n'existait que
        # dans albert.py, donc PAS sur le fournisseur de production.
        verifier_quota(self._usage_tracker, self._client_id)
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._kwargs_modele(),
        }
        async with sem:
            response = await self._request_with_retry(url, payload, self._chat_timeout)
            _donnees = response.json()
            enregistrer_usage(self._usage_tracker, self._client_id, _donnees)
            return extraire_contenu(_donnees, self._backend, max_tokens)

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> AsyncIterator[str]:
        """Appel chat completions en streaming (SSE)."""
        # Quota du tenant — point de passage unique (L2.6). Il n'existait que
        # dans albert.py, donc PAS sur le fournisseur de production.
        verifier_quota(self._usage_tracker, self._client_id)
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **self._kwargs_modele(),
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
        # Quota du tenant — point de passage unique (L2.6). Il n'existait que
        # dans albert.py, donc PAS sur le fournisseur de production.
        verifier_quota(self._usage_tracker, self._client_id)
        sem = self._chat_semaphore if priority == "user" else self._bg_chat_semaphore
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": model or self._model_chat,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._kwargs_modele(),
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
