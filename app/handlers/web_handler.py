"""
Module de gestion des requêtes web pour Albert Tchap.
"""

import asyncio
import json
from typing import Dict, Any, List, Optional

from app.matrix_bot.config import logger
from app.services.web_manager import WebManager
from app.services.models import WebResult

class WebHandler:
    """
    Gestionnaire pour traiter les commandes web et router vers les services appropriés.
    """
    
    def __init__(self, config, albert_agent=None):
        """
        Initialise le gestionnaire de requêtes web.
        
        Args:
            config: Configuration de l'application
            albert_agent: Instance de l'agent Albert (optionnel)
        """
        self.config = config
        self.albert_agent = albert_agent
        self.web_manager = WebManager(config)
        
        # Dictionnaire des commandes disponibles
        self.commands = {
            "extract": self._handle_extract,
            "browse": self._handle_browse,
            "search": self._handle_search,
            "multi_extract": self._handle_multi_extract
        }
    
    async def handle_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande web.
        
        Args:
            command: Nom de la commande à exécuter
            params: Paramètres de la commande
            
        Returns:
            Résultat de la commande
        """
        logger.info(f"Traitement de la commande web: {command} avec paramètres: {params}")
        
        if command not in self.commands:
            return {
                "success": False,
                "error": f"Commande '{command}' inconnue. Commandes disponibles: {', '.join(self.commands.keys())}"
            }
        
        try:
            # Exécuter la commande
            result = await self.commands[command](params)
            return result
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la commande '{command}': {str(e)}")
            return {
                "success": False,
                "error": f"Erreur lors du traitement de la commande: {str(e)}"
            }
    
    async def _handle_extract(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande d'extraction de contenu web.
        
        Args:
            params: Paramètres de la commande
                - url: URL à extraire
                
        Returns:
            Résultat de l'extraction
        """
        url = params.get("url")
        if not url:
            return {"success": False, "error": "URL manquante pour l'extraction"}
        
        # Utiliser la nouvelle méthode qui retourne directement un WebResult
        result = await self.web_manager.extract_as_web_result(url)
        return result.to_dict()
    
    async def _handle_multi_extract(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande d'extraction de contenu depuis plusieurs URLs.
        
        Args:
            params: Paramètres de la commande
                - urls: Liste d'URLs à extraire
                
        Returns:
            Résultat des extractions
        """
        urls = params.get("urls", [])
        if not urls or not isinstance(urls, list):
            return {"success": False, "error": "Liste d'URLs manquante ou invalide"}
        
        # Utiliser la nouvelle méthode qui retourne directement des WebResults
        results = await self.web_manager.extract_urls_as_web_results(urls)
        
        # Convertir les résultats en dictionnaires
        result_dict = {url: result.to_dict() for url, result in results.items()}
        
        return {
            "success": True,
            "data": {
                "results": result_dict,
                "count": len(result_dict)
            }
        }
    
    async def _handle_browse(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande de navigation interactive.
        
        Args:
            params: Paramètres de la commande
                - url: URL initiale
                - actions: Liste d'actions à exécuter (optionnel)
                
        Returns:
            Résultat de la navigation
        """
        # URL requise
        url = params.get("url")
        if not url:
            return {"success": False, "error": "URL manquante pour la navigation"}
            
        # Actions optionnelles
        actions = params.get("actions", [])
            
        # À implémenter dans une future version
        # Pour l'instant, on simule la navigation interactive par une extraction simple
        result = await self.web_manager.extract_as_web_result(url)
        
        # Ajouter des métadonnées spécifiques à la navigation
        metadata = result.metadata.copy() if result.metadata else {}
        metadata["browse_actions"] = actions
        metadata["is_browsable"] = True
        
        # Créer un nouveau WebResult avec les métadonnées mises à jour
        browse_result = WebResult(
            success=result.success,
            url=result.url,
            content_type=result.content_type,
            title=result.title,
            content=result.content,
            metadata=metadata,
            error=result.error
        )
        
        return browse_result.to_dict()
    
    async def _handle_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande de recherche web.
        
        Args:
            params: Paramètres de la commande
                - query: Requête de recherche
                - engine: Moteur de recherche à utiliser (optionnel)
                
        Returns:
            Résultat de la recherche
        """
        query = params.get("query")
        if not query:
            return {"success": False, "error": "Requête de recherche manquante"}
            
        engine = params.get("engine", "default")
        
        # À implémenter dans une future version
        # Pour l'instant, on retourne une erreur explicite
        return {
            "success": False,
            "error": f"La recherche web '{query}' avec le moteur {engine} n'est pas encore implémentée",
            "search_params": {
                "query": query,
                "engine": engine
            }
        } 