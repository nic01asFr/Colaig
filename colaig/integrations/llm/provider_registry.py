"""
ProviderRegistry — Registre des providers LLM.
Lit depuis os.environ : {PROVIDER}_API_URL, {PROVIDER}_API_KEY.
Providers connus : albert, mistral, openai, groq, together, ollama.
"""
from __future__ import annotations

import logging
import os

from colaig.integrations.llm.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

_KNOWN_PROVIDERS: dict[str, str] = {
    "albert":   "https://albert-api.etalab.gouv.fr",
    "mistral":  "https://api.mistral.ai",
    "openai":   "https://api.openai.com",
    "groq":     "https://api.groq.com/openai",
    "together": "https://api.together.xyz",
    "ollama":   "http://localhost:11434",
}
_KEYLESS_PROVIDERS: frozenset[str] = frozenset({"ollama"})


class ProviderRegistry:
    def __init__(self, providers: dict[str, tuple[str, str]]) -> None:
        self._providers = providers  # {name: (url, key)}

    @classmethod
    def from_env(cls) -> ProviderRegistry:
        providers: dict[str, tuple[str, str]] = {}
        for name, default_url in _KNOWN_PROVIDERS.items():
            key = os.environ.get(f"{name.upper()}_API_KEY", "")
            url = os.environ.get(f"{name.upper()}_API_URL", "") or default_url
            if key or name in _KEYLESS_PROVIDERS:
                providers[name] = (url, key)
                logger.debug("ProviderRegistry: %s disponible (%s)", name, url)
        if not providers:
            logger.warning("ProviderRegistry: aucun provider LLM configuré")
        return cls(providers)

    def has(self, name: str) -> bool:
        return name in self._providers

    def get_client(
        self,
        name: str,
        model_chat: str = "",
        model_embed: str = "",
        chat_max_concurrent: int = 4,
        bg_chat_max_concurrent: int = 3,
        embed_max_concurrent: int = 4,
        max_retries: int = 3,
    ) -> OpenAIClient | None:
        if name not in self._providers:
            logger.warning("ProviderRegistry: provider '%s' non disponible", name)
            return None
        url, key = self._providers[name]
        return OpenAIClient(
            api_key=key,
            base_url=url,
            model_chat=model_chat,
            model_embed=model_embed,
            backend_name=name,
            chat_max_concurrent=chat_max_concurrent,
            bg_chat_max_concurrent=bg_chat_max_concurrent,
            embed_max_concurrent=embed_max_concurrent,
            max_retries=max_retries,
        )

    def available(self) -> list[str]:
        return list(self._providers.keys())
