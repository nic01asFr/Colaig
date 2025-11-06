"""
Extractor utilisant browser-use avec agent IA.
Adapté depuis Colaig pour être autonome et multi-LLM.
"""

import re
import asyncio
import logging
from typing import Dict, Any, Optional

from browser_use import Agent

from .base import BaseExtractor, ExtractionError
from .direct_extractor import DirectExtractor
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient
from app.core.browser.playwright_manager import ensure_playwright_installed

logger = logging.getLogger(__name__)


class AgentExtractor(BaseExtractor):
    """
    Extractor utilisant browser-use avec un agent IA.
    Plus puissant que l'extraction directe, capable de gérer du contenu dynamique
    et d'interagir intelligemment avec les pages.
    """

    def __init__(self):
        """Initialise l'extractor avec agent."""
        self.direct_extractor = DirectExtractor()

    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> WebResult:
        """
        Extrait le contenu avec un agent IA (browser-use).

        Args:
            url: URL à extraire
            prompt: Prompt pour guider l'agent
            llm_client: Client LLM à utiliser (requis pour l'agent)
            options: Options d'extraction

        Returns:
            WebResult avec le contenu extrait
        """
        # Vérifier que Playwright est disponible
        if not await ensure_playwright_installed():
            return WebResult.from_error(
                url=url,
                error_message="Playwright n'est pas disponible"
            )

        # Le client LLM est requis pour l'agent
        if not llm_client:
            return WebResult.from_error(
                url=url,
                error_message="Client LLM requis pour l'extraction avec agent"
            )

        # Options par défaut
        opts = options or {}
        timeout = opts.get("timeout", 45)
        use_fallback = opts.get("use_fallback", True)

        try:
            # ÉTAPE 1: Essayer l'extraction directe d'abord (plus rapide)
            logger.info(f"Tentative d'extraction directe pour {url}")
            direct_result = await self.direct_extractor.extract(url, prompt, None, options)

            # Si l'extraction directe a produit un contenu substantiel, l'utiliser
            if direct_result.success and direct_result.content and len(direct_result.content) > 500:
                logger.info(f"Extraction directe réussie: {len(direct_result.content)} caractères")
                direct_result.metadata["extraction_method"] = "direct_playwright_fast_path"
                return direct_result

            # ÉTAPE 2: Utiliser l'agent IA
            logger.info(f"Extraction avec agent IA pour {url}")

            # Obtenir le wrapper LangChain du client LLM
            llm_wrapper = llm_client.get_langchain_wrapper()

            # Créer l'agent browser-use
            agent = Agent(
                task=prompt,
                llm=llm_wrapper
            )

            # Exécuter l'agent avec timeout
            extraction_task = asyncio.create_task(agent.run())

            try:
                result = await asyncio.wait_for(extraction_task, timeout=float(timeout))

                # Extraire le contenu de la réponse de l'agent
                processed_content = await self._extract_from_agent_response(result)

                # Vérifier la qualité du contenu
                if processed_content and len(processed_content) > 100:
                    # Analyser le contenu pour extraire titre et corps
                    title_match = re.search(
                        r'TITRE:\s*(.*?)(?:\n|$)',
                        processed_content,
                        re.IGNORECASE
                    )
                    content_match = re.search(
                        r'CONTENU:\s*(.*)',
                        processed_content,
                        re.IGNORECASE | re.DOTALL
                    )

                    if title_match and content_match:
                        title = title_match.group(1).strip()
                        content = content_match.group(1).strip()
                    else:
                        # Fallback: utiliser le contenu brut
                        title = "Contenu extrait"
                        content = processed_content

                    logger.info(f"Extraction avec agent réussie: {len(content)} caractères")

                    return WebResult.from_success(
                        url=url,
                        content_type="webpage",
                        title=title,
                        content=content,
                        metadata={
                            "extraction_method": "agent_browser_use",
                            "content_length": len(content),
                            "llm_provider": type(llm_client).__name__
                        }
                    )

                else:
                    logger.warning(f"Contenu extrait par agent insuffisant: {len(processed_content) if processed_content else 0} caractères")

                    # Fallback vers extraction directe si configuré
                    if use_fallback and direct_result.success:
                        logger.info("Utilisation du résultat de l'extraction directe en fallback")
                        return direct_result

                    return WebResult.from_error(
                        url=url,
                        error_message="Contenu extrait par agent insuffisant"
                    )

            except asyncio.TimeoutError:
                logger.error(f"Timeout de l'agent après {timeout}s pour {url}")

                # Fallback vers extraction directe
                if use_fallback and direct_result.success:
                    logger.info("Utilisation du résultat de l'extraction directe après timeout agent")
                    return direct_result

                return WebResult.from_error(
                    url=url,
                    error_message=f"Timeout de l'agent après {timeout}s"
                )

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction avec agent de {url}: {e}")

            # Fallback vers extraction directe en cas d'erreur
            if use_fallback:
                try:
                    logger.info("Tentative de fallback vers extraction directe après erreur agent")
                    direct_result = await self.direct_extractor.extract(url, prompt, None, options)
                    if direct_result.success:
                        return direct_result
                except Exception as fallback_error:
                    logger.error(f"Échec du fallback: {fallback_error}")

            return WebResult.from_error(
                url=url,
                error_message=f"Erreur lors de l'extraction avec agent: {str(e)}"
            )

    async def _extract_from_agent_response(self, response: Any) -> str:
        """
        Extrait le contenu textuel de la réponse de l'agent browser-use.

        Args:
            response: Réponse de l'agent

        Returns:
            Contenu extrait sous forme de texte
        """
        try:
            # La réponse peut être de différents types selon la version de browser-use
            if isinstance(response, str):
                return response

            # Si c'est un objet avec attribut text ou content
            if hasattr(response, 'text'):
                return str(response.text)

            if hasattr(response, 'content'):
                return str(response.content)

            # Si c'est une liste de messages
            if isinstance(response, list):
                contents = []
                for item in response:
                    if isinstance(item, dict) and 'content' in item:
                        contents.append(str(item['content']))
                    elif hasattr(item, 'content'):
                        contents.append(str(item.content))
                    elif isinstance(item, str):
                        contents.append(item)

                return '\n'.join(contents)

            # Fallback: conversion en string
            return str(response)

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction du contenu de la réponse: {e}")
            return ""
