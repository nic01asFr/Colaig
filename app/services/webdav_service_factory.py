"""
Factory pour la création et la gestion des instances WebDAV.

Ce module centralise la création et la gestion des instances WebDAV
pour éviter les dépendances circulaires et simplifier l'utilisation
des services WebDAV dans l'application.
"""

import asyncio
from typing import Dict, Optional, Any, Callable
import inspect
from functools import wraps

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.webdav_context_manager import get_webdav_context_manager

# Cache global des instances WebDAV
_webdav_instances: Dict[str, WebDAVService] = {}
_webdav_lock = asyncio.Lock()

class WebDAVServiceFactory:
    """
    Factory pour la création et la gestion des instances WebDAV.
    
    Cette classe fournit des méthodes pour créer et gérer des instances
    WebDAV de manière centralisée, en évitant les dépendances circulaires
    et en simplifiant l'utilisation des services WebDAV dans l'application.
    """
    
    @staticmethod
    async def get_service(config: Config, room_id: Optional[str] = None, 
                         user_id: Optional[str] = None) -> WebDAVService:
        """
        Récupère ou crée une instance WebDAV pour le contexte spécifié.
        
        Args:
            config: Configuration à utiliser
            room_id: ID du salon (optionnel)
            user_id: ID de l'utilisateur (optionnel)
            
        Returns:
            Instance WebDAV pour le contexte spécifié
        """
        # Clé de cache unique pour cette combinaison de contexte
        cache_key = f"{room_id or ''}_{user_id or ''}"
        
        # Vérifier si l'instance existe déjà dans le cache
        async with _webdav_lock:
            if cache_key in _webdav_instances:
                return _webdav_instances[cache_key]
            
            # Si contexte spécifié, utiliser le gestionnaire contextuel
            if room_id or user_id:
                try:
                    # Récupérer le gestionnaire contextuel
                    ctx_manager = await get_webdav_context_manager(config)
                    # Obtenir le service WebDAV pour ce contexte
                    service = await ctx_manager.get_webdav_for_context(room_id, user_id)
                    if service:
                        # Mettre en cache pour les appels futurs
                        _webdav_instances[cache_key] = service
                        return service
                except Exception as e:
                    logger.error(f"Erreur lors de l'obtention du WebDAV contextuel: {str(e)}")
            
            # Fallback: créer une instance par défaut
            try:
                # Vérifier que la configuration est valide
                if not config or not hasattr(config, 'webdav_url') or not config.webdav_url:
                    logger.error("Configuration WebDAV invalide ou incomplète")
                    return None
                
                # Créer une nouvelle instance
                service = WebDAVService(
                    webdav_url=config.webdav_url,
                    webdav_username=config.webdav_username,
                    webdav_password=config.webdav_password,
                    webdav_root=config.webdav_root_path
                )
                await service.init_webdav()
                
                # Mettre en cache pour les appels futurs
                _webdav_instances[cache_key] = service
                return service
            except Exception as e:
                logger.error(f"Erreur lors de la création du service WebDAV: {str(e)}")
                return None
    
    @staticmethod
    async def clear_cache(room_id: Optional[str] = None, user_id: Optional[str] = None):
        """
        Vide le cache des instances WebDAV.
        
        Args:
            room_id: ID du salon pour ne vider que les instances liées (optionnel)
            user_id: ID de l'utilisateur pour ne vider que les instances liées (optionnel)
        """
        async with _webdav_lock:
            if not room_id and not user_id:
                # Vider tout le cache
                for key, service in _webdav_instances.items():
                    try:
                        await service.close()
                    except Exception as e:
                        logger.error(f"Erreur lors de la fermeture du service WebDAV {key}: {str(e)}")
                _webdav_instances.clear()
            else:
                # Vider seulement les instances liées au contexte spécifié
                keys_to_delete = []
                
                if room_id and user_id:
                    # Instance spécifique salon+utilisateur
                    key = f"{room_id}_{user_id}"
                    if key in _webdav_instances:
                        keys_to_delete.append(key)
                elif room_id:
                    # Instances liées au salon
                    keys_to_delete = [k for k in _webdav_instances if k.startswith(f"{room_id}_")]
                elif user_id:
                    # Instances liées à l'utilisateur
                    keys_to_delete = [k for k in _webdav_instances if k.endswith(f"_{user_id}")]
                
                # Fermer et supprimer les instances
                for key in keys_to_delete:
                    try:
                        await _webdav_instances[key].close()
                    except Exception as e:
                        logger.error(f"Erreur lors de la fermeture du service WebDAV {key}: {str(e)}")
                    del _webdav_instances[key]

# Décorateur pour injecter automatiquement le service WebDAV
def with_webdav_service(func: Callable) -> Callable:
    """
    Décorateur qui injecte automatiquement le service WebDAV dans la fonction.
    
    Args:
        func: Fonction à décorer
        
    Returns:
        Fonction décorée
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Récupérer la configuration
        config = None
        for arg in args:
            if isinstance(arg, Config):
                config = arg
                break
        
        if not config and 'config' in kwargs:
            config = kwargs['config']
            
        if not config:
            # Chercher dans les attributs des objets passés
            for arg in args:
                if hasattr(arg, 'config'):
                    config = arg.config
                    break
                    
        if not config:
            # Fallback à la configuration par défaut
            from app.config import env_config
            config = env_config
            
        # Récupérer room_id et user_id s'ils sont présents
        room_id = kwargs.get('room_id')
        user_id = kwargs.get('user_id')
        
        # Récupérer ou créer le service WebDAV
        webdav_service = await WebDAVServiceFactory.get_service(config, room_id, user_id)
        
        # Ajouter le service WebDAV aux arguments
        if 'webdav_service' in inspect.signature(func).parameters:
            kwargs['webdav_service'] = webdav_service
            
        # Exécuter la fonction
        return await func(*args, **kwargs)
    
    return wrapper

# Fonction globale pour récupérer une instance WebDAV
async def get_webdav_service(config: Config, room_id: Optional[str] = None, 
                           user_id: Optional[str] = None) -> WebDAVService:
    """
    Récupère ou crée une instance WebDAV pour le contexte spécifié.
    
    Args:
        config: Configuration à utiliser
        room_id: ID du salon (optionnel)
        user_id: ID de l'utilisateur (optionnel)
        
    Returns:
        Instance WebDAV pour le contexte spécifié
    """
    return await WebDAVServiceFactory.get_service(config, room_id, user_id)

# Fonction globale pour vider le cache des instances WebDAV
async def clear_webdav_cache(room_id: Optional[str] = None, user_id: Optional[str] = None):
    """
    Vide le cache des instances WebDAV.
    
    Args:
        room_id: ID du salon pour ne vider que les instances liées (optionnel)
        user_id: ID de l'utilisateur pour ne vider que les instances liées (optionnel)
    """
    await WebDAVServiceFactory.clear_cache(room_id, user_id) 