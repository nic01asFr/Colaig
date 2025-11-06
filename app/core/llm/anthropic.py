"""
Client LLM pour Anthropic API (Claude).
"""

from typing import List, Dict, Any, Optional
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from anthropic import AsyncAnthropic

from .base import BaseLLMClient, LLMClientError


class AnthropicLLMClient(BaseLLMClient):
    """
    Client pour l'API Anthropic (Claude 3, Claude 3.5, etc.)
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022"
    ):
        """
        Initialise le client Anthropic.

        Args:
            api_key: Clé API Anthropic
            base_url: URL de base personnalisée (optionnel)
            model: Nom du modèle Claude à utiliser
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)

        # Client Anthropic asynchrone
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        self.client = AsyncAnthropic(**kwargs)

    async def generate(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec Claude.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels (temperature, max_tokens, etc.)

        Returns:
            Contenu de la réponse générée
        """
        try:
            # Anthropic nécessite un message système séparé
            system_message = None
            filtered_messages = []

            for msg in messages:
                if msg["role"] == "system":
                    system_message = msg["content"]
                else:
                    filtered_messages.append(msg)

            # Paramètres pour l'API Anthropic
            api_params = {
                "model": self.model,
                "messages": filtered_messages,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs
            }

            if system_message:
                api_params["system"] = system_message

            response = await self.client.messages.create(**api_params)

            if response.content and len(response.content) > 0:
                return response.content[0].text
            else:
                raise LLMClientError("Réponse inattendue de l'API Anthropic")

        except Exception as e:
            raise LLMClientError(f"Erreur lors de la génération Anthropic: {str(e)}")

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        Anthropic est supporté par LangChain via ChatAnthropic.

        Returns:
            ChatAnthropic compatible avec browser-use
        """
        kwargs = {
            "api_key": self.api_key,
            "model": self.model
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatAnthropic(**kwargs)

    async def close(self):
        """Ferme le client Anthropic."""
        await self.client.close()
