"""
CapabilityChain — Chaîne de fallback pour une capacité LLM.

Format spec : "provider1:model1,provider2:model2"
Sur LLMRateLimitError ou LLMUnavailableError → passe au provider suivant.
Les LLMError de base (erreurs fonctionnelles) ne déclenchent PAS le fallback.

Circuit breaker intégré : quand un provider échoue (429/indisponible), il est
mis en cooldown (défaut 60s). Les appels suivants le sautent directement sans
attendre les retries internes. Le cooldown se lève automatiquement à expiration,
ou est effacé dès que le provider répond avec succès.

Exemple .env :
    COLAIG_CAP_CHAT=albert:openai/gpt-oss-120b,mistral:mistral-large-latest
    COLAIG_CAP_OCR=albert:mistralai/Mistral-Small-3.2-24B-Instruct-2506,mistral:mistral-small-latest
    COLAIG_CAP_EMBED=albert:BAAI/bge-m3,mistral:mistral-embed
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from colaig.exceptions import LLMError, LLMRateLimitError, LLMUnavailableError
from colaig.models import ChatCompletionResult

if TYPE_CHECKING:
    from colaig.integrations.llm.openai_client import OpenAIClient
    from colaig.integrations.llm.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_S = 60.0


class CapabilityChain:
    def __init__(
        self,
        entries: list[tuple[OpenAIClient, str]],
        capability: str = "",
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
    ) -> None:
        self._entries = entries  # [(client, model_name), ...]
        self._cap = capability
        self._cooldown_s = cooldown_s
        # Circuit breaker : id(client) → timestamp d'expiration du cooldown
        self._cooldowns: dict[int, float] = {}

    # ── Circuit breaker ───────────────────────────────────────────────

    def _is_on_cooldown(self, client: OpenAIClient) -> bool:
        return time.monotonic() < self._cooldowns.get(id(client), 0.0)

    def _set_cooldown(self, client: OpenAIClient, retry_after: float | None = None) -> None:
        # Utilise le Retry-After de l'API si présent, sinon le cooldown par défaut.
        # On prend max des deux pour ne jamais être en dessous du minimum configuré.
        cooldown = max(retry_after or 0.0, self._cooldown_s)
        self._cooldowns[id(client)] = time.monotonic() + cooldown
        extra = f", Retry-After={retry_after:.0f}s" if retry_after else ""
        logger.info(
            "Cap %s: %s en cooldown %.0fs (circuit ouvert%s)",
            self._cap, client._backend, cooldown, extra,
        )

    def _clear_cooldown(self, client: OpenAIClient) -> None:
        self._cooldowns.pop(id(client), None)

    def _active_entries(self) -> list[tuple[OpenAIClient, str]]:
        """Entrées non en cooldown. Si toutes sont en cooldown → liste vide (pas d'appel HTTP).

        Retourner [] permet aux cooldowns de s'écouler naturellement sans être réinitialisés
        par de nouveaux appels qui échoueraient et relanceraient le timer.
        """
        active = [(c, m) for c, m in self._entries if not self._is_on_cooldown(c)]
        if not active and self._entries:
            remaining = min(
                self._cooldowns.get(id(c), 0.0) - time.monotonic()
                for c, _ in self._entries
            )
            logger.warning(
                "Cap %s: tous providers en cooldown (%.0fs restants), skip sans appel HTTP",
                self._cap, max(0.0, remaining),
            )
        return active

    # ── Parse ─────────────────────────────────────────────────────────

    @classmethod
    def parse(
        cls,
        spec: str,
        registry: ProviderRegistry,
        capability: str = "",
        default_model: str = "",
        chat_max_concurrent: int = 4,
        bg_chat_max_concurrent: int = 3,
        embed_max_concurrent: int = 4,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
    ) -> CapabilityChain:
        if not spec.strip():
            return cls([], capability, cooldown_s)
        # Parser d'abord toutes les parties pour connaître le nombre total
        parts = [p.strip() for p in spec.split(",") if p.strip()]
        entries = []
        for i, part in enumerate(parts):
            if ":" in part:
                provider_name, model = part.split(":", 1)
            else:
                provider_name, model = part, ""
            provider_name = provider_name.strip()
            model = model.strip() or default_model
            # Fail-fast sur les providers non-finaux : 0 retry, la chain gère le fallback.
            # Le dernier provider conserve 3 retries (pas de fallback derrière lui).
            is_last = (i == len(parts) - 1)
            client = registry.get_client(
                provider_name,
                model_chat=model,
                model_embed=model,
                chat_max_concurrent=chat_max_concurrent,
                bg_chat_max_concurrent=bg_chat_max_concurrent,
                embed_max_concurrent=embed_max_concurrent,
                max_retries=3 if is_last else 0,
            )
            if client is None:
                logger.warning("Cap %s: provider '%s' ignoré (non configuré)", capability, provider_name)
                continue
            entries.append((client, model))
            retries_label = "3 retries" if is_last else "fail-fast"
            logger.info("Cap %s: provider %s → modèle %s [%s]", capability, provider_name, model or "(défaut)", retries_label)
        if not entries:
            logger.warning("Cap %s: aucun provider valide dans '%s'", capability, spec)
        return cls(entries, capability, cooldown_s)

    @property
    def is_empty(self) -> bool:
        return not self._entries

    # ── Chat ──────────────────────────────────────────────────────────

    async def chat(self, messages, model=None, temperature=0.3, max_tokens=2048, priority="user") -> str:
        last_err: Exception | None = None
        for client, default_model in self._active_entries():
            try:
                # default_model (par provider, issu du spec COLAIG_CAP_*) a priorité sur
                # le model appelant. Cela garantit que chaque provider reçoit son propre
                # modèle même quand le code appelant passe un modèle explicite.
                result = await client.chat(
                    messages,
                    model=default_model or model or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    priority=priority,
                )
                self._clear_cooldown(client)
                return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                last_err = e
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s chat: %s → fallback (%s)", self._cap, client._backend, e)
        if last_err:
            raise last_err
        raise LLMError(f"Cap {self._cap}: aucun provider disponible")

    async def chat_stream(self, messages, model=None, temperature=0.3, max_tokens=2048, priority="user") -> AsyncIterator[str]:
        last_err: Exception | None = None
        for client, default_model in self._active_entries():
            try:
                async for chunk in client.chat_stream(
                    messages,
                    model=default_model or model or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    priority=priority,
                ):
                    yield chunk
                self._clear_cooldown(client)
                return
            except (LLMRateLimitError, LLMUnavailableError) as e:
                last_err = e
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s stream: %s → fallback (%s)", self._cap, client._backend, e)
        if last_err:
            raise last_err
        raise LLMError(f"Cap {self._cap}: aucun provider disponible")

    async def chat_with_tools(self, messages, tools, model=None, temperature=0.3, max_tokens=2048, tool_choice="auto", priority="user") -> ChatCompletionResult:
        last_err: Exception | None = None
        for client, default_model in self._active_entries():
            try:
                result = await client.chat_with_tools(
                    messages,
                    tools=tools,
                    model=default_model or model or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tool_choice=tool_choice,
                    priority=priority,
                )
                self._clear_cooldown(client)
                return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                last_err = e
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s tools: %s → fallback (%s)", self._cap, client._backend, e)
        if last_err:
            raise last_err
        raise LLMError(f"Cap {self._cap}: aucun provider disponible")

    # ── Embeddings ────────────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        last_err: Exception | None = None
        for client, _model in self._active_entries():
            try:
                result = await client.embed(text)
                self._clear_cooldown(client)
                return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                last_err = e
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s embed: %s → fallback (%s)", self._cap, client._backend, e)
        if last_err:
            raise last_err
        raise LLMError(f"Cap {self._cap}: aucun provider disponible")

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        last_err: Exception | None = None
        for client, _model in self._active_entries():
            try:
                result = await client.embed_batch(texts, batch_size=batch_size)
                self._clear_cooldown(client)
                return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                last_err = e
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s embed_batch: %s → fallback (%s)", self._cap, client._backend, e)
        if last_err:
            raise last_err
        raise LLMError(f"Cap {self._cap}: aucun provider disponible")

    # ── Reranking ─────────────────────────────────────────────────────

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        model: str | None = None,
    ) -> list[tuple[int, float]]:
        """Reranke via la chain (fallback si provider retourne liste vide)."""
        for client, default_model in self._active_entries():
            try:
                result = await client.rerank(query, documents, top_n=top_n, model=default_model or model or None)
                if result:  # liste vide = provider ne supporte pas → fallback
                    self._clear_cooldown(client)
                    return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s rerank: %s → fallback (%s)", self._cap, client._backend, e)
            except Exception:
                pass  # LLMError (provider ne supporte pas) → essayer suivant
        return []  # aucun provider disponible → l'appelant utilise MMR seul

    # ── Audio ─────────────────────────────────────────────────────────

    async def transcribe(
        self,
        content: bytes,
        filename: str,
        model: str | None = None,
    ) -> str:
        """Transcription audio via la chain avec fallback."""
        for client, default_model in self._active_entries():
            try:
                result = await client.transcribe(content, filename, model=default_model or model or None)
                if result != "":  # chaîne vide = provider ne supporte pas → fallback
                    self._clear_cooldown(client)
                    return result
            except (LLMRateLimitError, LLMUnavailableError) as e:
                self._set_cooldown(client, retry_after=getattr(e, "retry_after", None))
                logger.warning("Cap %s transcribe: %s → fallback (%s)", self._cap, client._backend, e)
            except Exception:
                pass
        return ""
