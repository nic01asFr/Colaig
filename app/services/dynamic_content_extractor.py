"""
Module d'extraction de contenu dynamique pour les sites web modernes.
Complément au système d'extraction existant pour gérer les sites avec contenu généré dynamiquement.
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from app.matrix_bot.config import logger


class DynamicContentDetector:
    """
    Détecteur de contenu dynamique pour sites web modernes.
    Identifie les sites qui nécessitent une extraction spécialisée.
    """
    
    # Patterns d'URL indiquant du contenu dynamique
    DYNAMIC_URL_PATTERNS = [
        r'/#/',  # Hash routing (SPA)
        r'/app/',  # Applications web
        r'/dashboard/',  # Tableaux de bord
        r'/admin/',  # Interfaces d'administration
        r'\.html#',  # Hash fragments
    ]
    
    # Domaines connus pour utiliser beaucoup de JavaScript
    DYNAMIC_DOMAINS = [
        'notion.so',
        'github.com',
        'gitlab.com',
        'trello.com',
        'asana.com',
        'monday.com',
        'clickup.com',
        'figma.com',
        'miro.com',
        'canva.com',
        'discord.com',
        'slack.com',
        'teams.microsoft.com',
        'app.',  # Sous-domaines "app"
        'dashboard.',  # Sous-domaines "dashboard"
    ]
    
    # Frameworks JavaScript/SPA détectables dans le DOM
    JS_FRAMEWORK_INDICATORS = [
        'data-react-root',
        'ng-app',
        'v-app',
        '__nuxt',
        '__next',
        'gatsby-focus-wrapper',
        'svelte-app',
        'ember-application',
    ]
    
    @classmethod
    def is_likely_dynamic(cls, url: str) -> bool:
        """
        Détermine si l'URL indique probablement du contenu dynamique.
        
        Args:
            url: URL à analyser
            
        Returns:
            bool: True si le site est probablement dynamique
        """
        url_lower = url.lower()
        
        # Vérifier les patterns d'URL
        for pattern in cls.DYNAMIC_URL_PATTERNS:
            if re.search(pattern, url_lower):
                logger.info(f"Pattern dynamique détecté dans URL {url}: {pattern}")
                return True
        
        # Vérifier les domaines connus
        for domain in cls.DYNAMIC_DOMAINS:
            if domain in url_lower:
                logger.info(f"Domaine dynamique détecté: {domain} dans {url}")
                return True
        
        return False
    
    @classmethod
    async def detect_framework_in_page(cls, page) -> Optional[str]:
        """
        Détecte le framework JavaScript utilisé dans la page.
        
        Args:
            page: Instance Playwright page
            
        Returns:
            str: Nom du framework détecté ou None
        """
        try:
            # Vérifier les indicateurs dans le DOM
            for indicator in cls.JS_FRAMEWORK_INDICATORS:
                try:
                    elements = await page.query_selector_all(f'[{indicator}]')
                    if elements:
                        framework = indicator.replace('data-', '').replace('-root', '').replace('__', '').replace('-app', '')
                        logger.info(f"Framework détecté: {framework} via {indicator}")
                        return framework
                except:
                    continue
            
            # Vérifier les variables globales JavaScript
            framework_check = await page.evaluate("""() => {
                const frameworks = [];
                
                // React
                if (typeof React !== 'undefined' || document.querySelector('[data-reactroot]')) {
                    frameworks.push('react');
                }
                
                // Vue.js
                if (typeof Vue !== 'undefined' || document.querySelector('[data-v-]')) {
                    frameworks.push('vue');
                }
                
                // Angular
                if (typeof angular !== 'undefined' || document.querySelector('[ng-app]') || window.ng) {
                    frameworks.push('angular');
                }
                
                // Next.js
                if (typeof __NEXT_DATA__ !== 'undefined' || document.getElementById('__next')) {
                    frameworks.push('nextjs');
                }
                
                // Nuxt.js
                if (typeof __NUXT__ !== 'undefined' || document.getElementById('__nuxt')) {
                    frameworks.push('nuxtjs');
                }
                
                // Svelte
                if (document.querySelector('.svelte-') || window.__SVELTE__) {
                    frameworks.push('svelte');
                }
                
                return frameworks;
            }""")
            
            if framework_check and len(framework_check) > 0:
                detected_framework = framework_check[0]
                logger.info(f"Framework détecté via JavaScript: {detected_framework}")
                return detected_framework
        
        except Exception as e:
            logger.debug(f"Erreur lors de la détection de framework: {str(e)}")
        
        return None


class DynamicContentExtractor:
    """
    Extracteur spécialisé pour le contenu dynamique.
    Utilise des stratégies adaptées aux sites web modernes.
    """
    
    @classmethod
    async def extract_dynamic_content(cls, url: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Extrait le contenu d'un site web dynamique avec stratégies avancées.
        
        Args:
            url: URL à extraire
            timeout: Timeout en secondes
            
        Returns:
            Dict: Contenu extrait avec métadonnées
        """
        try:
            async with async_playwright() as p:
                # Configuration avancée pour les sites dynamiques
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-gpu',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-web-security',  # Pour certains CORS
                        '--disable-features=VizDisplayCompositor',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},  # Plus grande résolution
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    ignore_https_errors=True,  # Ignorer les erreurs SSL
                    java_script_enabled=True,  # S'assurer que JS est activé
                )
                
                page = await context.new_page()
                
                # Étape 1: Navigation initiale
                logger.info(f"Navigation vers {url} avec stratégie dynamique")
                
                try:
                    # Navigation avec attente du réseau
                    await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                except Exception as nav_error:
                    logger.warning(f"Première navigation échouée, retry: {str(nav_error)}")
                    try:
                        # Retry avec moins de contraintes
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except:
                        logger.warning("Navigation avec domcontentloaded seulement")
                
                # Étape 2: Détection du framework
                framework = await DynamicContentDetector.detect_framework_in_page(page)
                
                # Étape 3: Attentes spécifiques selon le framework
                await cls._wait_for_dynamic_content(page, framework)
                
                # Étape 4: Extraction du titre
                title = await page.title()
                
                # Étape 5: Extraction du contenu avec stratégies multiples
                content = await cls._extract_content_with_strategies(page, framework)
                
                # Étape 6: Détection d'éléments interactifs nécessitant des actions
                interactive_content = await cls._extract_interactive_content(page)
                
                await browser.close()
                
                # Combinaison du contenu
                final_content = content
                if interactive_content:
                    final_content += "\n\n" + interactive_content
                
                if final_content and len(final_content.strip()) > 100:
                    return {
                        "title": title,
                        "content": final_content.strip(),
                        "url": url,
                        "status": 200,
                        "extraction_method": "dynamic-content-extractor",
                        "framework_detected": framework,
                        "content_length": len(final_content),
                    }
                else:
                    logger.warning(f"Contenu dynamique insuffisant pour {url}")
                    return None
                    
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction dynamique: {str(e)}")
            return None
    
    @classmethod
    async def _wait_for_dynamic_content(cls, page, framework: Optional[str]):
        """
        Attendre que le contenu dynamique soit chargé selon le framework.
        
        Args:
            page: Instance Playwright page
            framework: Framework détecté
        """
        try:
            if framework == 'react' or framework == 'nextjs':
                # Attendre les composants React
                await page.wait_for_function(
                    "document.querySelector('[data-reactroot], [data-react-root], #__next')?.children?.length > 0",
                    timeout=10000
                )
                logger.info("Contenu React/Next.js chargé")
                
            elif framework == 'vue' or framework == 'nuxtjs':
                # Attendre les composants Vue.js
                await page.wait_for_function(
                    "document.querySelector('[data-v-], #__nuxt')?.children?.length > 0",
                    timeout=10000
                )
                logger.info("Contenu Vue.js/Nuxt.js chargé")
                
            elif framework == 'angular':
                # Attendre les composants Angular
                await page.wait_for_function(
                    "document.querySelector('[ng-app]')?.children?.length > 0 || document.body.classList.contains('ng-scope')",
                    timeout=10000
                )
                logger.info("Contenu Angular chargé")
                
            else:
                # Attente générique pour le contenu dynamique
                await page.wait_for_function(
                    "document.readyState === 'complete' && document.body.children.length > 0",
                    timeout=8000
                )
                
            # Attente supplémentaire pour les requêtes AJAX/fetch
            await asyncio.sleep(3)
            
        except Exception as e:
            logger.debug(f"Timeout dans l'attente du contenu dynamique: {str(e)}")
            # Continuer même si l'attente échoue
            await asyncio.sleep(2)
    
    @classmethod
    async def _extract_content_with_strategies(cls, page, framework: Optional[str]) -> str:
        """
        Extrait le contenu avec différentes stratégies selon le framework.
        
        Args:
            page: Instance Playwright page
            framework: Framework détecté
            
        Returns:
            str: Contenu extrait
        """
        content = ""
        
        # Stratégie 1: Sélecteurs spécifiques au framework
        if framework:
            content = await cls._extract_framework_specific_content(page, framework)
        
        # Stratégie 2: Sélecteurs génériques pour SPA
        if not content or len(content.strip()) < 200:
            content = await cls._extract_spa_content(page)
        
        # Stratégie 3: Extraction JavaScript avancée
        if not content or len(content.strip()) < 200:
            content = await cls._extract_with_advanced_js(page)
        
        # Stratégie 4: Fallback extraction simple
        if not content or len(content.strip()) < 200:
            content = await cls._extract_fallback_content(page)
        
        return content
    
    @classmethod
    async def _extract_framework_specific_content(cls, page, framework: str) -> str:
        """Extraction spécifique au framework détecté."""
        try:
            if framework in ['react', 'nextjs']:
                # Sélecteurs React/Next.js
                selectors = [
                    '[data-reactroot] main',
                    '[data-react-root] main', 
                    '#__next main',
                    '[data-reactroot] article',
                    '#__next article',
                    '[data-reactroot] .content',
                    '#__next .content'
                ]
                
            elif framework in ['vue', 'nuxtjs']:
                # Sélecteurs Vue.js/Nuxt.js
                selectors = [
                    '#__nuxt main',
                    '[data-v-] main',
                    '#__nuxt article',
                    '[data-v-] article',
                    '#__nuxt .content',
                    '.nuxt-content'
                ]
                
            elif framework == 'angular':
                # Sélecteurs Angular
                selectors = [
                    '[ng-app] main',
                    'app-root main',
                    '[ng-app] article',
                    'app-root article',
                    '.ng-scope main'
                ]
                
            else:
                return ""
            
            # Tenter chaque sélecteur
            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    if text and len(text.strip()) > 200:
                        logger.info(f"Contenu extrait avec sélecteur {framework}: {selector}")
                        return text.strip()
                        
        except Exception as e:
            logger.debug(f"Erreur extraction spécifique {framework}: {str(e)}")
        
        return ""
    
    @classmethod
    async def _extract_spa_content(cls, page) -> str:
        """Extraction pour Single Page Applications génériques."""
        try:
            # Sélecteurs courants pour les SPA
            spa_selectors = [
                'main',
                'article', 
                '.app-content',
                '.main-content',
                '.page-content',
                '.content-wrapper',
                '[role="main"]',
                '.container main',
                '.app main',
                '#app main',
                '#root main'
            ]
            
            for selector in spa_selectors:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    if text and len(text.strip()) > 200:
                        logger.info(f"Contenu SPA extrait avec: {selector}")
                        return text.strip()
                        
        except Exception as e:
            logger.debug(f"Erreur extraction SPA: {str(e)}")
        
        return ""
    
    @classmethod
    async def _extract_with_advanced_js(cls, page) -> str:
        """Extraction JavaScript avancée pour contenu généré dynamiquement."""
        try:
            content = await page.evaluate("""() => {
                // Supprimer les éléments non pertinents
                const elementsToRemove = [
                    'nav', 'header', 'footer', 'aside', 'script', 'style', 'noscript',
                    '[role="navigation"]', '[role="banner"]', '[role="complementary"]',
                    '.navigation', '.nav', '.sidebar', '.ads', '.advertisement',
                    '.social-share', '.cookie-banner', '.popup', '.modal'
                ];
                
                elementsToRemove.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => {
                        if (el && el.parentNode) {
                            el.parentNode.removeChild(el);
                        }
                    });
                });
                
                // Chercher le contenu principal avec plusieurs stratégies
                const contentSelectors = [
                    'main', 'article', '.content', '.main-content', '.post-content',
                    '.entry-content', '.page-content', '[role="main"]', '.container',
                    '.app-content', '.body', '.text', '.description'
                ];
                
                let bestContent = '';
                let bestLength = 0;
                
                for (const selector of contentSelectors) {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => {
                        const text = el.innerText?.trim() || '';
                        if (text.length > bestLength && text.length > 100) {
                            bestContent = text;
                            bestLength = text.length;
                        }
                    });
                }
                
                // Si aucun sélecteur n'a donné de résultat satisfaisant, 
                // prendre le body mais en filtrant le bruit
                if (bestLength < 200) {
                    const bodyText = document.body.innerText || '';
                    // Filtrer les lignes très courtes (menus, etc.)
                    const lines = bodyText.split('\\n')
                        .filter(line => line.trim().length > 20)
                        .join('\\n');
                    
                    if (lines.length > bestLength) {
                        bestContent = lines;
                    }
                }
                
                return bestContent;
            }""")
            
            if content and len(content.strip()) > 200:
                logger.info("Contenu extrait avec JavaScript avancé")
                return content.strip()
                
        except Exception as e:
            logger.debug(f"Erreur extraction JavaScript avancée: {str(e)}")
        
        return ""
    
    @classmethod
    async def _extract_fallback_content(cls, page) -> str:
        """Extraction de fallback simple."""
        try:
            content = await page.evaluate("() => document.body.innerText")
            if content:
                logger.info("Contenu extrait avec fallback simple")
                return content.strip()
        except Exception as e:
            logger.debug(f"Erreur extraction fallback: {str(e)}")
        
        return ""
    
    @classmethod
    async def _extract_interactive_content(cls, page) -> str:
        """
        Extrait le contenu d'éléments interactifs (accordéons, onglets, etc.).
        
        Args:
            page: Instance Playwright page
            
        Returns:
            str: Contenu interactif extrait
        """
        interactive_content = []
        
        try:
            # Rechercher et activer les accordéons
            accordions = await page.query_selector_all('[aria-expanded="false"], .accordion-item, .collapse')
            for accordion in accordions[:5]:  # Limiter à 5 éléments
                try:
                    await accordion.click()
                    await asyncio.sleep(0.5)  # Attendre l'animation
                    text = await accordion.inner_text()
                    if text and len(text.strip()) > 50:
                        interactive_content.append(text.strip())
                except:
                    continue
            
            # Rechercher et activer les onglets
            tabs = await page.query_selector_all('[role="tab"], .tab-button, .nav-tab')
            for tab in tabs[:3]:  # Limiter à 3 onglets
                try:
                    await tab.click()
                    await asyncio.sleep(1)
                    # Extraire le contenu du panneau d'onglet actif
                    panel = await page.query_selector('[role="tabpanel"]:not([hidden]), .tab-content.active')
                    if panel:
                        text = await panel.inner_text()
                        if text and len(text.strip()) > 50:
                            interactive_content.append(text.strip())
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Erreur extraction contenu interactif: {str(e)}")
        
        return "\n\n".join(interactive_content) if interactive_content else ""


async def extract_dynamic_web_content(url: str, timeout: int = 60) -> Optional[Dict[str, Any]]:
    """
    Point d'entrée principal pour l'extraction de contenu dynamique.
    
    Args:
        url: URL à extraire
        timeout: Timeout en secondes
        
    Returns:
        Dict: Contenu extrait ou None si échec
    """
    # Vérifier si l'URL nécessite une extraction dynamique
    if not DynamicContentDetector.is_likely_dynamic(url):
        logger.debug(f"URL {url} ne semble pas nécessiter d'extraction dynamique")
        return None
    
    logger.info(f"Extraction dynamique démarrée pour {url}")
    result = await DynamicContentExtractor.extract_dynamic_content(url, timeout)
    
    if result:
        logger.info(f"Extraction dynamique réussie: {result['content_length']} caractères")
    else:
        logger.warning(f"Extraction dynamique échouée pour {url}")
    
    return result 