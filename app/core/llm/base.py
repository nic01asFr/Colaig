"""
Interface de base pour les clients LLM.
Permet l'abstraction des différents providers (Albert, OpenAI, Anthropic, etc.)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from langchain_core.language_models import BaseChatModel


class BaseLLMClient(ABC):
    """
    Interface abstraite pour tous les clients LLM.
    Chaque provider (Albert, OpenAI, Anthropic) doit implémenter cette interface.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = ""):
        """
        Initialise le client LLM.

        Args:
            api_key: Clé API pour le provider
            base_url: URL de base pour l'API (optionnel)
            model: Nom du modèle à utiliser
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse à partir d'une liste de messages.

        Args:
            messages: Liste de messages au format {"role": "user/assistant/system", "content": "..."}
            **kwargs: Paramètres additionnels spécifiques au provider

        Returns:
            Contenu de la réponse générée
        """
        pass

    @abstractmethod
    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper compatible LangChain pour browser-use.

        Browser-use nécessite un modèle compatible LangChain (BaseChatModel).
        Cette méthode retourne un wrapper qui adapte le client LLM actuel
        pour être utilisé avec browser-use.

        Returns:
            Instance de BaseChatModel compatible avec browser-use
        """
        pass

    @abstractmethod
    async def close(self):
        """Ferme proprement les connexions du client."""
        pass


class LLMClientError(Exception):
    """Exception levée en cas d'erreur avec un client LLM."""
    pass
