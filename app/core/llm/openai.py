"""
Client LLM pour OpenAI API.
"""

from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import AsyncOpenAI

from .base import BaseLLMClient, LLMClientError


class OpenAILLMClient(BaseLLMClient):
    """
    Client pour l'API OpenAI (GPT-4, GPT-4o, etc.)
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "gpt-4o"
    ):
        """
        Initialise le client OpenAI.

        Args:
            api_key: Clé API OpenAI
            base_url: URL de base personnalisée (pour proxies ou providers compatibles)
            model: Nom du modèle OpenAI à utiliser
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)

        # Client OpenAI asynchrone
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    async def generate(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec OpenAI.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels (temperature, max_tokens, etc.)

        Returns:
            Contenu de la réponse générée
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )

            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content
            else:
                raise LLMClientError("Réponse inattendue de l'API OpenAI")

        except Exception as e:
            raise LLMClientError(f"Erreur lors de la génération OpenAI: {str(e)}")

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        OpenAI est nativement supporté par LangChain via ChatOpenAI.

        Returns:
            ChatOpenAI compatible avec browser-use
        """
        kwargs = {
            "api_key": self.api_key,
            "model": self.model
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatOpenAI(**kwargs)

    async def close(self):
        """Ferme le client OpenAI."""
        await self.client.close()
