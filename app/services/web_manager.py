"""
Module pour la gestion des opérations web dans Albert Tchap.
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional
import re
from urllib.parse import urlparse

from app.services.web_commands import WebCommands, WebCommandResult
from app.services.browser_extraction import ensure_playwright_installed
from app.services.models import WebResult
from app.matrix_bot.config import logger

class WebManager:
    """
    Gestionnaire des opérations web pour Albert Tchap.
    Cette classe coordonne les différentes commandes web et gère les extractions.
    """
    
    def __init__(self, config):
        """
        Initialise le gestionnaire web.
        
        Args:
            config: Configuration de l'application
        """
        self.config = config
        self.commands = None
        self.initialized = False
    
    async def initialize(self):
        """
        Initialise le gestionnaire web, vérifie les dépendances et prépare les commandes.
        """
        if self.initialized:
            return
        
        try:
            # Vérifier si Playwright est installé
            await ensure_playwright_installed()
            logger.info("Playwright est correctement installé.")
        except Exception as e:
            logger.warning(f"Impossible d'installer Playwright: {str(e)}. L'extraction avancée sera limitée.")
        
        # Initialiser les commandes web
        self.commands = WebCommands(self.config)
        self.initialized = True
        logger.info("WebManager initialisé.")
    
    async def extract_content(self, url: str) -> WebCommandResult:
        """
        Extrait le contenu d'une URL en détectant automatiquement le type de contenu.
        
        Args:
            url: URL à extraire
            
        Returns:
            Résultat de l'extraction
        """
        if not self.initialized:
            await self.initialize()
        
        # Analyser l'URL pour déterminer le type de contenu
        content_type = self._detect_content_type(url)
        
        # Sélectionner la méthode d'extraction appropriée
        if content_type == "article":
            return await self.commands.get_article_content(url)
        elif content_type == "product":
            return await self.commands.get_product_information(url)
        elif content_type == "repository":
            return await self.commands.get_repository_information(url)
        elif content_type == "documentation":
            return await self.commands.get_documentation(url)
        else:
            # Par défaut, extraire le contenu général
            return await self.commands.get_webpage_content(url)
    
    async def extract_as_web_result(self, url: str) -> WebResult:
        """
        Extrait le contenu d'une URL et retourne le résultat sous forme de WebResult.
        
        Args:
            url: URL à extraire
            
        Returns:
            WebResult contenant les données extraites
        """
        result = await self.extract_content(url)
        return result.to_web_result()
    
    async def extract_urls(self, urls: List[str]) -> Dict[str, WebCommandResult]:
        """
        Extrait le contenu de plusieurs URLs en parallèle.
        
        Args:
            urls: Liste d'URLs à extraire
            
        Returns:
            Dictionnaire avec les URLs comme clés et les résultats comme valeurs
        """
        if not self.initialized:
            await self.initialize()
        
        # Extraire les URLs en parallèle
        results = {}
        for url in urls:
            results[url] = await self.extract_content(url)
        
        return results
    
    async def extract_urls_as_web_results(self, urls: List[str]) -> Dict[str, WebResult]:
        """
        Extrait le contenu de plusieurs URLs en parallèle et retourne les résultats sous forme de WebResult.
        
        Args:
            urls: Liste d'URLs à extraire
            
        Returns:
            Dictionnaire avec les URLs comme clés et les WebResult comme valeurs
        """
        command_results = await self.extract_urls(urls)
        
        # Convertir les résultats en WebResult
        return {
            url: result.to_web_result() 
            for url, result in command_results.items()
        }
    
    def _detect_content_type(self, url: str) -> str:
        """
        Détecte le type de contenu à partir de l'URL.
        
        Args:
            url: URL à analyser
            
        Returns:
            Type de contenu détecté (article, product, repository, documentation, webpage)
        """
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        path = parsed_url.path.lower()
        
        # Détection de dépôts de code
        if any(repo_domain in domain for repo_domain in ["github.com", "gitlab.com", "bitbucket.org"]):
            return "repository"
        
        # Détection de documentation technique
        if any(docs_pattern in domain or docs_pattern in path for docs_pattern in 
               ["docs.", "documentation", "developer", "api", "reference", "manual"]):
            return "documentation"
        
        # Détection de sites e-commerce
        if any(shop_domain in domain for shop_domain in 
               ["amazon", "ebay", "walmart", "alibaba", "shopping", "store", "shop"]):
            return "product"
        
        # Détection d'articles
        if any(news_domain in domain for news_domain in 
               ["news", "blog", "article", "post", "medium.com"]):
            return "article"
        
        # Par défaut
        return "webpage" 