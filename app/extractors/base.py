"""
Interface de base pour les extractors de contenu web.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient


class BaseExtractor(ABC):
    """Interface abstraite pour tous les extractors."""

    @abstractmethod
    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> WebResult:
        """
        Extrait le contenu d'une URL.

        Args:
            url: URL à extraire
            prompt: Prompt pour guider l'extraction
            llm_client: Client LLM à utiliser (optionnel selon l'extractor)
            options: Options additionnelles pour l'extraction

        Returns:
            WebResult avec le contenu extrait
        """
        pass


class ExtractionError(Exception):
    """Exception levée en cas d'erreur lors de l'extraction."""
    pass
