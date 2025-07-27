"""
Module pour centraliser la logique d'initialisation asynchrone des services.

Ce module fournit des fonctions pour initialiser et fermer proprement
les services asynchrones de l'application.
"""

import asyncio
import logging
import signal
import sys
from typing import Dict, List, Any, Callable, Awaitable, Optional

logger = logging.getLogger(__name__)

# Registre des services à initialiser et fermer
_services_registry: Dict[str, Dict[str, Any]] = {}
_initialization_lock = asyncio.Lock()
_initialized = False

async def initialize_services(config=None):
    """
    Initialise tous les services enregistrés.
    
    Args:
        config: Configuration à utiliser pour l'initialisation
    """
    global _initialized
    
    async with _initialization_lock:
        if _initialized:
            logger.info("Les services sont déjà initialisés")
            return
        
        logger.info("Initialisation des services...")
        
        # Importer dynamiquement la configuration si nécessaire
        if config is None:
            from app.config import env_config
            config = env_config
        
        # Initialiser les services dans l'ordre de priorité
        services_initialized = []
        for service_name in sorted(_services_registry.keys(), 
                                  key=lambda x: _services_registry[x].get('priority', 100)):
            service_info = _services_registry[service_name]
            init_func = service_info.get('init_func')
            
            if init_func and callable(init_func):
                try:
                    logger.info(f"Initialisation du service {service_name}...")
                    service = await init_func(config)
                    
                    # Stocker l'instance du service si elle est retournée
                    if service:
                        service_info['instance'] = service
                        services_initialized.append(service_name)
                        
                    logger.info(f"Service {service_name} initialisé avec succès")
                except Exception as e:
                    logger.error(f"Erreur lors de l'initialisation du service {service_name}: {str(e)}")
                    # Ne pas planter si un service échoue, continuer avec les autres
        
        _initialized = True
        logger.info(f"Services initialisés: {', '.join(services_initialized)}")
        logger.info("Tous les services ont été initialisés")

async def shutdown_services():
    """Ferme proprement tous les services enregistrés."""
    global _initialized
    
    async with _initialization_lock:
        if not _initialized:
            logger.info("Les services ne sont pas initialisés, rien à fermer")
            return
        
        logger.info("Fermeture des services...")
        
        # Fermer les services dans l'ordre inverse de priorité
        for service_name in sorted(_services_registry.keys(), 
                                  key=lambda x: _services_registry[x].get('priority', 100),
                                  reverse=True):
            service_info = _services_registry[service_name]
            shutdown_func = service_info.get('shutdown_func')
            
            if shutdown_func and callable(shutdown_func):
                try:
                    logger.info(f"Fermeture du service {service_name}...")
                    await shutdown_func(service_info.get('instance'))
                    logger.info(f"Service {service_name} fermé avec succès")
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du service {service_name}: {str(e)}")
        
        _initialized = False
        logger.info("Tous les services ont été fermés")

def register_service(name: str, 
                    init_func: Callable[[Any], Awaitable[Any]], 
                    shutdown_func: Optional[Callable[[Any], Awaitable[None]]] = None,
                    priority: int = 100):
    """
    Enregistre un service pour l'initialisation et la fermeture.
    
    Args:
        name: Nom du service
        init_func: Fonction asynchrone pour initialiser le service
        shutdown_func: Fonction asynchrone pour fermer le service (optionnel)
        priority: Priorité d'initialisation (plus petit = plus prioritaire)
    """
    _services_registry[name] = {
        'init_func': init_func,
        'shutdown_func': shutdown_func,
        'priority': priority,
        'instance': None
    }
    logger.info(f"Service {name} enregistré avec priorité {priority}")

def is_service_initialized(name: str) -> bool:
    """
    Vérifie si un service est initialisé.
    
    Args:
        name: Nom du service
        
    Returns:
        True si le service est initialisé, False sinon
    """
    return name in _services_registry and _services_registry[name].get('instance') is not None

def get_service_instance(name: str) -> Any:
    """
    Récupère l'instance d'un service initialisé.
    
    Args:
        name: Nom du service
        
    Returns:
        L'instance du service ou None si non initialisé
    """
    if name in _services_registry:
        return _services_registry[name].get('instance')
    return None

# Enregistrer les services essentiels
def register_essential_services():
    """Enregistre les services essentiels de l'application."""
    
    # WebDAV Context Manager
    async def init_webdav_context_manager(config):
        from app.services.webdav_context_manager import get_webdav_context_manager
        return await get_webdav_context_manager(config)
    
    async def shutdown_webdav_context_manager(instance):
        from app.services.webdav_context_manager import close_webdav_context_manager
        await close_webdav_context_manager()
    
    register_service(
        name="webdav_context_manager",
        init_func=init_webdav_context_manager,
        shutdown_func=shutdown_webdav_context_manager,
        priority=10  # Priorité élevée car d'autres services en dépendent
    )
    
    # Context Manager
    async def init_context_manager(config):
        from app.services.context.instance import get_context_manager
        return await get_context_manager(config)
    
    async def shutdown_context_manager(instance):
        if instance:
            await instance.close()
    
    register_service(
        name="context_manager",
        init_func=init_context_manager,
        shutdown_func=shutdown_context_manager,
        priority=20
    )
    
    # Index Service
    async def init_index_service(config):
        from app.index.types import get_index_service
        return await get_index_service(config)
    
    async def shutdown_index_service(instance):
        if instance:
            await instance.close()
    
    register_service(
        name="index_service",
        init_func=init_index_service,
        shutdown_func=shutdown_index_service,
        priority=30
    )
    
    # Behavior Manager
    async def init_behavior_manager(config):
        """
        Initialise le gestionnaire de comportements avec gestion des erreurs améliorée.
        Utilise le pattern singleton via get_behavior_manager.
        """
        from app.services.behavior_manager import get_behavior_manager
        
        try:
            # Utiliser la fonction singleton avec timeout intégré
            behavior_manager = await asyncio.wait_for(
                get_behavior_manager(config),
                timeout=60.0  # 60 secondes max pour l'initialisation
            )
            logger.info("BehaviorManager initialisé avec succès via pattern singleton")
            return behavior_manager
        except asyncio.TimeoutError:
            logger.error("Timeout lors de l'initialisation du BehaviorManager")
            # Continuer avec un BehaviorManager en mode dégradé
            behavior_manager = await get_behavior_manager(config)
            return behavior_manager
        except Exception as e:
            logger.error(f"Erreur initialisation BehaviorManager: {str(e)}")
            # Essayer quand même de récupérer une instance en mode dégradé
            try:
                behavior_manager = await get_behavior_manager(config)
                return behavior_manager
            except Exception as inner_e:
                logger.critical(f"Échec critique de l'initialisation du BehaviorManager: {str(inner_e)}")
                return None
    
    async def shutdown_behavior_manager(instance):
        """
        Ferme proprement le BehaviorManager via la fonction singleton dédiée
        plutôt que directement via l'instance.
        """
        from app.services.behavior_manager import close_behavior_manager
        
        try:
            # Fermeture via la fonction singleton
            await close_behavior_manager()
            logger.info("BehaviorManager fermé avec succès via pattern singleton")
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du BehaviorManager: {str(e)}")
    
    register_service(
        name="behavior_manager",
        init_func=init_behavior_manager,
        shutdown_func=shutdown_behavior_manager,
        priority=40
    )

# Configuration des gestionnaires de signaux pour fermeture propre
def setup_signal_handlers():
    """Configure les gestionnaires de signaux pour fermer proprement les services."""
    import signal
    
    def signal_handler(sig, frame):
        """Gestionnaire de signal pour fermeture propre."""
        logger.info(f"Signal {sig} reçu, fermeture en cours...")
        
        # Créer une nouvelle boucle d'événements pour la fermeture
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Exécuter la fermeture des services
            loop.run_until_complete(shutdown_services())
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture des services: {e}")
        finally:
            loop.close()
            logger.info("Fermeture terminée, sortie du programme")
            sys.exit(0)
    
    # Enregistrer les gestionnaires de signaux
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        logger.info("Gestionnaires de signaux configurés (SIGINT, SIGTERM)")
    except Exception as e:
        logger.warning(f"Impossible de configurer les gestionnaires de signaux: {e}")

# Initialiser le module
register_essential_services()
setup_signal_handlers()

# Décorateur pour garantir l'initialisation des services
def with_initialized_services(func):
    """
    Décorateur qui garantit que les services sont initialisés avant d'exécuter la fonction.
    
    Args:
        func: Fonction à décorer
        
    Returns:
        Fonction décorée
    """
    async def wrapper(*args, **kwargs):
        # Récupérer la configuration si présente
        config = None
        for arg in args:
            if hasattr(arg, 'config'):
                config = arg.config
                break
        
        if not config and 'config' in kwargs:
            config = kwargs['config']
        
        # Initialiser les services si nécessaire
        await initialize_services(config)
        
        # Exécuter la fonction
        return await func(*args, **kwargs)
    
    return wrapper 

# Rendre cette variable accessible depuis l'extérieur pour vérification
__all__ = ['initialize_services', 'shutdown_services', 'register_service', 
           'is_service_initialized', 'get_service_instance', 'setup_signal_handlers',
           'register_essential_services', '_initialized'] 