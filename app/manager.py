"""
Manager principal pour orchestrer les extractions web.
"""

import logging
from typing import Optional, Dict, Any

from app.api.models import WebResult, ExtractionOptions
from app.core.config import settings
from app.core.llm.base import BaseLLMClient
from app.core.llm.factory import LLMFactory
from app.extractors.direct_extractor import DirectExtractor
from app.extractors.agent_extractor import AgentExtractor
from app.utils.content_detector import ContentDetector
from app.utils.prompts import PromptTemplates

logger = logging.getLogger(__name__)


class ExtractorManager:
    """
    Manager principal qui coordonne les différents extractors.
    """

    def __init__(self):
        """Initialise le manager avec les extractors disponibles."""
        self.direct_extractor = DirectExtractor()
        self.agent_extractor = AgentExtractor()
        self.content_detector = ContentDetector()

    async def extract(
        self,
        url: str,
        prompt: Optional[str] = None,
        extraction_type: str = "general",
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[ExtractionOptions] = None
    ) -> WebResult:
        """
        Point d'entrée principal pour l'extraction de contenu web.

        Args:
            url: URL à extraire
            prompt: Prompt personnalisé (optionnel)
            extraction_type: Type d'extraction ("general", "article", "product", etc.)
            llm_client: Client LLM à utiliser (optionnel, utilise config par défaut si non fourni)
            options: Options d'extraction

        Returns:
            WebResult avec le contenu extrait
        """
        logger.info(f"Début extraction de {url} (type: {extraction_type})")

        # Détection automatique du type si "general"
        if extraction_type == "general":
            detected_type = self.content_detector.detect(url)
            logger.info(f"Type détecté automatiquement: {detected_type}")
            extraction_type = detected_type

        # Générer le prompt approprié
        final_prompt = PromptTemplates.get_prompt(
            content_type=extraction_type,
            url=url,
            custom_prompt=prompt
        )

        logger.debug(f"Prompt utilisé: {final_prompt[:200]}...")

        # Options par défaut
        opts = options or ExtractionOptions()
        extraction_options = {
            "timeout": opts.timeout,
            "headless": opts.headless,
            "use_fallback": True
        }

        # Créer le client LLM par défaut si non fourni
        if not llm_client and opts.use_agent:
            try:
                llm_client = await self._create_default_llm_client()
            except Exception as e:
                logger.error(f"Impossible de créer le client LLM par défaut: {e}")
                # Fallback vers extraction directe
                opts.use_agent = False

        # Sélectionner l'extractor approprié
        if opts.use_agent and llm_client:
            logger.info("Utilisation de l'agent extractor")
            result = await self.agent_extractor.extract(
                url=url,
                prompt=final_prompt,
                llm_client=llm_client,
                options=extraction_options
            )
        else:
            logger.info("Utilisation de l'extractor direct")
            result = await self.direct_extractor.extract(
                url=url,
                prompt=final_prompt,
                llm_client=None,
                options=extraction_options
            )

        # Ajouter le type de contenu au résultat
        if result.success and not result.content_type:
            result.content_type = extraction_type

        logger.info(
            f"Extraction terminée: success={result.success}, "
            f"content_length={len(result.content) if result.content else 0}"
        )

        return result

    async def _create_default_llm_client(self) -> BaseLLMClient:
        """
        Crée un client LLM avec la configuration par défaut.

        Returns:
            Instance de BaseLLMClient

        Raises:
            Exception: Si la configuration est invalide
        """
        provider = settings.default_llm_provider
        api_key = ""
        base_url = settings.default_llm_base_url

        # Récupérer la clé API selon le provider
        if provider == "openai":
            api_key = settings.openai_api_key or settings.default_llm_api_key
        elif provider == "anthropic":
            api_key = settings.anthropic_api_key or settings.default_llm_api_key
        elif provider == "albert":
            api_key = settings.albert_api_key or settings.default_llm_api_key
            base_url = settings.albert_api_url
        else:
            api_key = settings.default_llm_api_key

        if not api_key:
            raise ValueError(
                f"Clé API manquante pour le provider {provider}. "
                f"Configurez la variable d'environnement appropriée."
            )

        return LLMFactory.create(
            provider=provider,
            api_key=api_key,
            model=settings.default_llm_model,
            base_url=base_url
        )
