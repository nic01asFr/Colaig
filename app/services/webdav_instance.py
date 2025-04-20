"""
Service d'instance unique de WebDAV pour Albert.
Fournit une instance unique du service WebDAV pour toute l'application.
"""

import asyncio
from typing import Optional, TYPE_CHECKING

from app.config import Config
from app.matrix_bot.config import logger

# Importation conditionnelle pour éviter les importations circulaires
if TYPE_CHECKING:
    from app.services.webdav import WebDAVService

# Instance unique pour tout le programme
_webdav_service: Optional["WebDAVService"] = None
_init_lock = asyncio.Lock()

async def get_webdav_service(config: Config) -> "WebDAVService":
    """
    Récupère l'instance unique du service WebDAV, la créant si elle n'existe pas.
    
    Args:
        config: Configuration de l'application
        
    Returns:
        L'instance unique de WebDAVService
    """
    global _webdav_service, _init_lock
    
    async with _init_lock:
        if _webdav_service is None:
            logger.info("Création d'une nouvelle instance de WebDAVService")
            # Import à l'intérieur de la fonction pour éviter les importations circulaires
            from app.services.webdav import WebDAVService
            _webdav_service = WebDAVService(config)
            try:
                await _webdav_service.initialize()
                logger.info("Service WebDAV initialisé globalement")
            except Exception as e:
                logger.error(f"Erreur initialisation service WebDAV global: {str(e)}")
                # Garder l'instance en mode dégradé mais la marquer comme initialisée
                logger.warning("Service WebDAV initialisé en mode dégradé")
    
    return _webdav_service

async def close_webdav_service():
    """Ferme proprement l'instance du service WebDAV global."""
    global _webdav_service
    
    if _webdav_service is not None:
        logger.info("Fermeture du service WebDAV global")
        try:
            await _webdav_service.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du service WebDAV global: {str(e)}")
        finally:
            _webdav_service = None

# Exporter les fonctions 
__all__ = ['get_webdav_service', 'close_webdav_service'] 