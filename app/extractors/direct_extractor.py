"""
Extractor direct utilisant Playwright sans agent IA.
Rapide mais limité pour le contenu dynamique.
"""

import re
import logging
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base import BaseExtractor, ExtractionError
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient
from app.core.browser.playwright_manager import ensure_playwright_installed

logger = logging.getLogger(__name__)


class DirectExtractor(BaseExtractor):
    """
    Extractor utilisant Playwright directement sans agent IA.
    Bon pour les sites statiques ou peu dynamiques.
    """

    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> WebResult:
        """
        Extrait le contenu avec Playwright direct.

        Args:
            url: URL à extraire
            prompt: Prompt (ignoré pour l'extraction directe)
            llm_client: Client LLM (non utilisé pour l'extraction directe)
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

        # Options par défaut
        opts = options or {}
        timeout = opts.get("timeout", 45) * 1000  # Convertir en millisecondes
        headless = opts.get("headless", True)

        try:
            async with async_playwright() as p:
                # Lancer le navigateur
                browser = await p.chromium.launch(
                    headless=headless,
                    args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
                )

                # Créer un contexte
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )

                page = await context.new_page()

                try:
                    # Naviguer vers l'URL
                    logger.info(f"Navigation vers {url}")
                    await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

                    # Attendre un peu pour le contenu dynamique
                    await page.wait_for_timeout(2000)

                    # Extraire le titre
                    title = await page.title()

                    # Extraire le contenu principal
                    # Essayer plusieurs sélecteurs communs pour le contenu principal
                    content = ""

                    # Sélecteurs à essayer (par priorité)
                    selectors = [
                        "article",
                        "main",
                        '[role="main"]',
                        ".main-content",
                        "#main-content",
                        ".content",
                        "#content",
                        "body"
                    ]

                    for selector in selectors:
                        try:
                            element = await page.query_selector(selector)
                            if element:
                                text = await element.inner_text()
                                if text and len(text) > len(content):
                                    content = text
                        except Exception:
                            continue

                    # Nettoyer le contenu
                    content = self._clean_content(content)

                    # Vérifier si on a du contenu
                    if not content or len(content) < 100:
                        return WebResult.from_error(
                            url=url,
                            error_message="Contenu extrait insuffisant (< 100 caractères)"
                        )

                    logger.info(f"Extraction directe réussie: {len(content)} caractères")

                    return WebResult.from_success(
                        url=url,
                        content_type="webpage",
                        title=title,
                        content=content,
                        metadata={
                            "extraction_method": "direct_playwright",
                            "content_length": len(content)
                        }
                    )

                finally:
                    await page.close()
                    await context.close()
                    await browser.close()

        except PlaywrightTimeout:
            logger.error(f"Timeout lors de l'extraction de {url}")
            return WebResult.from_error(
                url=url,
                error_message=f"Timeout lors de l'accès à l'URL (> {timeout}ms)"
            )

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction directe de {url}: {e}")
            return WebResult.from_error(
                url=url,
                error_message=f"Erreur lors de l'extraction: {str(e)}"
            )

    def _clean_content(self, content: str) -> str:
        """
        Nettoie le contenu extrait.

        Args:
            content: Contenu brut

        Returns:
            Contenu nettoyé
        """
        if not content:
            return ""

        # Supprimer les lignes vides multiples
        content = re.sub(r'\n\s*\n', '\n\n', content)

        # Supprimer les espaces multiples
        content = re.sub(r' +', ' ', content)

        # Nettoyer les lignes
        lines = content.split('\n')
        cleaned_lines = [line.strip() for line in lines if line.strip()]

        return '\n'.join(cleaned_lines)
