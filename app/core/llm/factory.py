"""
Factory pour créer des clients LLM selon le provider.
"""

from typing import Optional
from .base import BaseLLMClient, LLMClientError
from .albert import AlbertLLMClient
from .openai import OpenAILLMClient
from .anthropic import AnthropicLLMClient


class LLMFactory:
    """Factory pour créer des instances de clients LLM."""

    @staticmethod
    def create(
        provider: str,
        api_key: str,
        model: str,
        base_url: Optional[str] = None
    ) -> BaseLLMClient:
        """
        Crée un client LLM selon le provider spécifié.

        Args:
            provider: Nom du provider ("openai", "anthropic", "albert")
            api_key: Clé API pour le provider
            model: Nom du modèle à utiliser
            base_url: URL de base optionnelle

        Returns:
            Instance de BaseLLMClient pour le provider

        Raises:
            LLMClientError: Si le provider n'est pas supporté
        """
        provider = provider.lower()

        if provider == "openai":
            return OpenAILLMClient(
                api_key=api_key,
                model=model,
                base_url=base_url
            )
        elif provider == "anthropic":
            return AnthropicLLMClient(
                api_key=api_key,
                model=model,
                base_url=base_url
            )
        elif provider == "albert":
            # Albert nécessite une base_url
            if not base_url:
                base_url = "https://albert.api.etalab.gouv.fr"

            return AlbertLLMClient(
                api_key=api_key,
                model=model,
                base_url=base_url
            )
        else:
            raise LLMClientError(
                f"Provider '{provider}' non supporté. "
                f"Providers supportés: openai, anthropic, albert"
            )
