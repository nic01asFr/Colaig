"""
Module définissant les commandes web pour Albert Tchap.
Ces commandes permettent d'interagir avec le contenu web et d'extraire des informations.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from app.services.browser_extraction import (
    extract_web_content, 
    extract_structured_web_data,
    BrowserExtractionError,
    is_browser_extraction_available
)
from app.services.models import WebResult
from app.matrix_bot.config import logger

class WebCommandResult:
    """
    Classe pour stocker le résultat d'une commande web.
    Compatible avec l'API existante mais convertit les résultats en WebResult
    et vice-versa.
    """
    def __init__(
        self, 
        success: bool, 
        data: Optional[Dict[str, Any]] = None, 
        error: Optional[str] = None,
        source_url: Optional[str] = None
    ):
        self.success = success
        self.data = data or {}
        self.error = error
        self.source_url = source_url
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convertit le résultat en dictionnaire.
        """
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "source_url": self.source_url
        }
    
    @classmethod
    def error_result(cls, message: str, url: Optional[str] = None) -> 'WebCommandResult':
        """
        Crée un résultat d'erreur.
        """
        return cls(success=False, error=message, source_url=url)
    
    @classmethod
    def success_result(cls, data: Dict[str, Any], url: Optional[str] = None) -> 'WebCommandResult':
        """
        Crée un résultat de succès.
        """
        return cls(success=True, data=data, source_url=url)
    
    @classmethod
    def from_web_result(cls, web_result: WebResult) -> 'WebCommandResult':
        """
        Convertit un WebResult en WebCommandResult.
        
        Args:
            web_result: Objet WebResult à convertir
            
        Returns:
            WebCommandResult équivalent
        """
        if not web_result.success:
            return cls.error_result(web_result.error, web_result.url)
        
        data = {
            "title": web_result.title,
            "content": web_result.content,
            "url": web_result.url,
            "content_type": web_result.content_type,
            "metadata": web_result.metadata,
        }
        
        return cls.success_result(data, web_result.url)
    
    def to_web_result(self) -> WebResult:
        """
        Convertit ce WebCommandResult en WebResult.
        
        Returns:
            WebResult équivalent
        """
        if not self.success:
            return WebResult.from_error(
                url=self.source_url or "",
                error_message=self.error or "Erreur inconnue"
            )
        
        content_type = self.data.get("content_type", "webpage")
        title = self.data.get("title", "")
        content = self.data.get("content", "")
        metadata = self.data.get("metadata", {})
        
        # Si les champs extraction_method et extraction_type existent, les ajouter aux métadonnées
        if "extraction_method" in self.data and "extraction_method" not in metadata:
            metadata["extraction_method"] = self.data["extraction_method"]
        
        if "extraction_type" in self.data and "extraction_type" not in metadata:
            metadata["extraction_type"] = self.data["extraction_type"]
        
        return WebResult.from_success(
            url=self.source_url or self.data.get("url", ""),
            content_type=content_type,
            title=title,
            content=content,
            metadata=metadata
        )

class WebCommands:
    """
    Classe regroupant les commandes web disponibles dans Albert Tchap.
    """
    
    def __init__(self, config):
        """
        Initialise le gestionnaire de commandes web.
        
        Args:
            config: Configuration de l'application
        """
        self.config = config
        self.extraction_available = is_browser_extraction_available()
        logger.info(f"Extraction web avec browser-use disponible: {self.extraction_available}")
    
    async def get_webpage_content(self, url: str) -> WebCommandResult:
        """
        Extrait le contenu d'une page web.
        
        Args:
            url: URL de la page à extraire
            
        Returns:
            Résultat de la commande
        """
        try:
            result = await extract_web_content(url, self.config)
            return WebCommandResult.success_result(result, url)
        except BrowserExtractionError as e:
            logger.error(f"Erreur lors de l'extraction de la page {url}: {str(e)}")
            return WebCommandResult.error_result(f"Impossible d'extraire le contenu de la page: {str(e)}", url)
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'extraction de la page {url}: {str(e)}")
            return WebCommandResult.error_result(f"Erreur lors de l'extraction: {str(e)}", url)
    
    async def get_article_content(self, url: str) -> WebCommandResult:
        """
        Extrait le contenu d'un article.
        
        Args:
            url: URL de l'article à extraire
            
        Returns:
            Résultat de la commande
        """
        try:
            result = await extract_structured_web_data(url, self.config, extraction_type="article")
            result["content_type"] = "article"  # S'assurer que le type de contenu est défini
            return WebCommandResult.success_result(result, url)
        except BrowserExtractionError as e:
            logger.error(f"Erreur lors de l'extraction de l'article {url}: {str(e)}")
            return WebCommandResult.error_result(f"Impossible d'extraire le contenu de l'article: {str(e)}", url)
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'extraction de l'article {url}: {str(e)}")
            return WebCommandResult.error_result(f"Erreur lors de l'extraction: {str(e)}", url)
    
    async def get_product_information(self, url: str) -> WebCommandResult:
        """
        Extrait les informations d'un produit.
        
        Args:
            url: URL du produit à extraire
            
        Returns:
            Résultat de la commande
        """
        try:
            result = await extract_structured_web_data(url, self.config, extraction_type="product")
            result["content_type"] = "product"  # S'assurer que le type de contenu est défini
            return WebCommandResult.success_result(result, url)
        except BrowserExtractionError as e:
            logger.error(f"Erreur lors de l'extraction du produit {url}: {str(e)}")
            return WebCommandResult.error_result(f"Impossible d'extraire les informations du produit: {str(e)}", url)
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'extraction du produit {url}: {str(e)}")
            return WebCommandResult.error_result(f"Erreur lors de l'extraction: {str(e)}", url)
    
    async def get_repository_information(self, url: str) -> WebCommandResult:
        """
        Extrait les informations d'un dépôt de code.
        
        Args:
            url: URL du dépôt à extraire
            
        Returns:
            Résultat de la commande
        """
        try:
            result = await extract_structured_web_data(url, self.config, extraction_type="repository")
            result["content_type"] = "repository"  # S'assurer que le type de contenu est défini
            return WebCommandResult.success_result(result, url)
        except BrowserExtractionError as e:
            logger.error(f"Erreur lors de l'extraction du dépôt {url}: {str(e)}")
            return WebCommandResult.error_result(f"Impossible d'extraire les informations du dépôt: {str(e)}", url)
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'extraction du dépôt {url}: {str(e)}")
            return WebCommandResult.error_result(f"Erreur lors de l'extraction: {str(e)}", url)
    
    async def get_documentation(self, url: str) -> WebCommandResult:
        """
        Extrait la documentation technique.
        
        Args:
            url: URL de la documentation à extraire
            
        Returns:
            Résultat de la commande
        """
        try:
            result = await extract_structured_web_data(url, self.config, extraction_type="documentation")
            result["content_type"] = "documentation"  # S'assurer que le type de contenu est défini
            return WebCommandResult.success_result(result, url)
        except BrowserExtractionError as e:
            logger.error(f"Erreur lors de l'extraction de la documentation {url}: {str(e)}")
            return WebCommandResult.error_result(f"Impossible d'extraire la documentation: {str(e)}", url)
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'extraction de la documentation {url}: {str(e)}")
            return WebCommandResult.error_result(f"Erreur lors de l'extraction: {str(e)}", url)
    
    async def extract_multiple_urls(self, urls: List[str]) -> Dict[str, WebCommandResult]:
        """
        Extrait le contenu de plusieurs URLs en parallèle.
        
        Args:
            urls: Liste d'URLs à extraire
            
        Returns:
            Dictionnaire avec les URLs comme clés et les résultats comme valeurs
        """
        tasks = [self.get_webpage_content(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {
            url: (
                result if isinstance(result, WebCommandResult) 
                else WebCommandResult.error_result(f"Erreur: {str(result)}", url)
            ) 
            for url, result in zip(urls, results)
        } 