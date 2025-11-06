"""
Client LLM pour Albert API.
Adapté depuis Colaig pour être autonome et réutilisable.
"""

import httpx
from typing import List, Dict, Any, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from .base import BaseLLMClient, LLMClientError


class AlbertLLMClient(BaseLLMClient):
    """
    Client pour l'API Albert.
    Albert est le LLM gouvernemental français développé par Etalab.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://albert.api.etalab.gouv.fr",
        model: str = "AgentPublic/llama3-instruct-8b"
    ):
        """
        Initialise le client Albert.

        Args:
            api_key: Clé API Albert
            base_url: URL de base de l'API Albert
            model: Nom du modèle Albert à utiliser
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)

        # Normaliser l'URL de base
        self.base_url = base_url.rstrip('/').replace('/v1', '')

        # Client HTTP pour les requêtes
        self.http_client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            http2=True
        )

    async def generate(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec Albert.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels

        Returns:
            Contenu de la réponse générée
        """
        url = f"{self.base_url}/v1/chat/completions"

        data = {
            "model": self.model,
            "messages": messages,
            **kwargs
        }

        try:
            response = await self.http_client.post(url, json=data)
            response.raise_for_status()
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                raise LLMClientError("Réponse inattendue de l'API Albert")

        except httpx.HTTPStatusError as e:
            raise LLMClientError(
                f"Erreur HTTP {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            raise LLMClientError(f"Erreur lors de la génération: {str(e)}")

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        Returns:
            AlbertAgentWrapper compatible avec browser-use
        """
        return AlbertAgentWrapper(
            api_key=self.api_key,
            base_url=self.base_url,
            model_name=self.model,
            albert_client=self
        )

    async def close(self):
        """Ferme le client HTTP."""
        await self.http_client.aclose()


class AlbertAgentWrapper(BaseChatModel):
    """
    Wrapper pour intégrer AlbertLLMClient avec browser-use.
    Adapte l'interface d'Albert pour fonctionner comme un LLM compatible LangChain.
    """

    # Attributs Pydantic
    api_key: str = Field(description="Clé API pour Albert")
    base_url: str = Field(description="URL de base pour l'API Albert")
    model_name: str = Field(description="Nom du modèle à utiliser")

    # Attributs privés
    _albert_client: Any = PrivateAttr()

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        albert_client: Optional[AlbertLLMClient] = None,
        **kwargs
    ):
        """Initialise le wrapper avec les informations d'API Albert."""
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            **kwargs
        )

        # Utiliser le client existant ou en créer un nouveau
        if albert_client:
            self._albert_client = albert_client
        else:
            self._albert_client = AlbertLLMClient(
                api_key=api_key,
                base_url=base_url,
                model=model_name
            )

    @property
    def _llm_type(self) -> str:
        """Retourne le type de LLM pour la compatibilité LangChain."""
        return "albert_api"

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs):
        """
        Méthode synchrone requise par BaseChatModel.
        Non implémentée car nous utilisons uniquement l'interface asynchrone.
        """
        raise NotImplementedError(
            "Cette méthode n'est pas implémentée. Utilisez _agenerate à la place."
        )

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs
    ) -> ChatResult:
        """
        Génère une réponse de façon asynchrone à partir d'une liste de messages.

        Args:
            messages: Liste de messages au format LangChain
            stop: Liste de chaînes de caractères pour arrêter la génération
            run_manager: Gestionnaire de callbacks

        Returns:
            Réponse LangChain formatée
        """
        # Convertir les messages LangChain en format Albert
        albert_messages = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                albert_messages.append({
                    "role": "user",
                    "content": msg.content
                })
            elif isinstance(msg, AIMessage):
                albert_messages.append({
                    "role": "assistant",
                    "content": msg.content
                })
            else:
                # Traiter d'autres types de messages
                albert_messages.append({
                    "role": "system" if msg.type == "system" else "user",
                    "content": msg.content
                })

        # Générer la réponse avec Albert
        response_text = await self._albert_client.generate(
            messages=albert_messages,
            **kwargs
        )

        # Créer un message IA pour la réponse
        message = AIMessage(content=response_text)

        # Retourner le résultat au format LangChain
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])
