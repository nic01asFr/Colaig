"""
Module pour centraliser les imports dynamiques et éviter les dépendances circulaires.

Ce module fournit des fonctions pour importer dynamiquement des classes et des fonctions
au moment de l'exécution, évitant ainsi les problèmes de dépendances circulaires.
"""

import importlib
from typing import Any, Dict, Optional, Type, Callable, List
import inspect
import logging

logger = logging.getLogger(__name__)

# Cache des modules importés dynamiquement
_module_cache: Dict[str, Any] = {}

def dynamic_import(module_path: str, attribute_name: Optional[str] = None) -> Any:
    """
    Importe dynamiquement un module ou un attribut d'un module.
    
    Args:
        module_path: Chemin du module à importer
        attribute_name: Nom de l'attribut à extraire du module (optionnel)
        
    Returns:
        Le module ou l'attribut importé
    """
    try:
        # Vérifier si le module est déjà dans le cache
        cache_key = f"{module_path}.{attribute_name}" if attribute_name else module_path
        if cache_key in _module_cache:
            return _module_cache[cache_key]
        
        # Importer le module
        module = importlib.import_module(module_path)
        
        # Si un attribut est spécifié, le retourner
        if attribute_name:
            if hasattr(module, attribute_name):
                attr = getattr(module, attribute_name)
                _module_cache[cache_key] = attr
                return attr
            else:
                raise AttributeError(f"Le module {module_path} n'a pas d'attribut {attribute_name}")
        
        # Sinon, retourner le module entier
        _module_cache[cache_key] = module
        return module
    except (ImportError, AttributeError) as e:
        logger.error(f"Erreur lors de l'import dynamique de {module_path}.{attribute_name}: {str(e)}")
        raise

def get_class(module_path: str, class_name: str) -> Type:
    """
    Récupère une classe depuis un module.
    
    Args:
        module_path: Chemin du module contenant la classe
        class_name: Nom de la classe à récupérer
        
    Returns:
        La classe demandée
    """
    return dynamic_import(module_path, class_name)

def get_function(module_path: str, function_name: str) -> Callable:
    """
    Récupère une fonction depuis un module.
    
    Args:
        module_path: Chemin du module contenant la fonction
        function_name: Nom de la fonction à récupérer
        
    Returns:
        La fonction demandée
    """
    return dynamic_import(module_path, function_name)

def lazy_import(module_path: str, attribute_name: Optional[str] = None):
    """
    Crée un proxy pour un import qui ne sera réalisé qu'au moment de l'utilisation.
    
    Args:
        module_path: Chemin du module à importer
        attribute_name: Nom de l'attribut à extraire du module (optionnel)
        
    Returns:
        Un proxy qui importera le module ou l'attribut à la demande
    """
    class LazyImportProxy:
        def __init__(self, module_path, attribute_name):
            self._module_path = module_path
            self._attribute_name = attribute_name
            self._target = None
        
        def _ensure_imported(self):
            if self._target is None:
                self._target = dynamic_import(self._module_path, self._attribute_name)
            return self._target
        
        def __call__(self, *args, **kwargs):
            target = self._ensure_imported()
            if callable(target):
                return target(*args, **kwargs)
            raise TypeError(f"{self._module_path}.{self._attribute_name} n'est pas callable")
        
        def __getattr__(self, name):
            target = self._ensure_imported()
            return getattr(target, name)
        
        def __repr__(self):
            return f"<LazyImport {self._module_path}.{self._attribute_name}>"
    
    return LazyImportProxy(module_path, attribute_name)

# Fonctions spécifiques pour les services courants
def get_webdav_service():
    """
    Récupère la fonction get_webdav_service du module webdav_service_factory.
    
    Returns:
        La fonction get_webdav_service
    """
    return get_function('app.services.webdav_service_factory', 'get_webdav_service')

def get_context_manager():
    """
    Récupère la fonction get_context_manager du module context.instance.
    
    Returns:
        La fonction get_context_manager
    """
    return get_function('app.services.context.instance', 'get_context_manager')

def get_index_service():
    """
    Récupère la fonction get_index_service du module index.types.
    
    Returns:
        La fonction get_index_service
    """
    return get_function('app.index.types', 'get_index_service')

# Classe utilitaire pour l'injection de dépendances
class ServiceProvider:
    """
    Fournisseur de services avec injection de dépendances.
    
    Cette classe permet de gérer les dépendances entre services
    et de les injecter automatiquement dans les fonctions qui en ont besoin.
    """
    
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable] = {}
    
    def register_service(self, name: str, service: Any):
        """
        Enregistre un service.
        
        Args:
            name: Nom du service
            service: Instance du service
        """
        self._services[name] = service
    
    def register_factory(self, name: str, factory: Callable):
        """
        Enregistre une factory pour un service.
        
        Args:
            name: Nom du service
            factory: Fonction qui crée le service
        """
        self._factories[name] = factory
    
    async def get_service(self, name: str) -> Any:
        """
        Récupère un service par son nom.
        
        Args:
            name: Nom du service
            
        Returns:
            Le service demandé
        """
        # Si le service existe déjà, le retourner
        if name in self._services:
            return self._services[name]
        
        # Si une factory existe, l'utiliser pour créer le service
        if name in self._factories:
            factory = self._factories[name]
            service = await factory() if inspect.iscoroutinefunction(factory) else factory()
            self._services[name] = service
            return service
        
        raise KeyError(f"Service {name} non trouvé")
    
    def inject(self, **service_names):
        """
        Décorateur pour injecter des services dans une fonction.
        
        Args:
            **service_names: Noms des services à injecter
            
        Returns:
            Décorateur qui injecte les services
        """
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # Injecter les services demandés
                for param_name, service_name in service_names.items():
                    if param_name not in kwargs:
                        kwargs[param_name] = await self.get_service(service_name)
                
                # Appeler la fonction avec les services injectés
                return await func(*args, **kwargs) if inspect.iscoroutinefunction(func) else func(*args, **kwargs)
            
            return wrapper
        
        return decorator

# Instance globale du fournisseur de services
service_provider = ServiceProvider()

# Initialiser les services courants
def init_service_provider():
    """Initialise le fournisseur de services avec les services courants."""
    # Enregistrer les factories pour les services courants
    service_provider.register_factory('webdav_service', 
                                     lambda: dynamic_import('app.services.webdav_service_factory', 'get_webdav_service'))
    service_provider.register_factory('context_manager', 
                                     lambda: dynamic_import('app.services.context.instance', 'get_context_manager'))
    service_provider.register_factory('index_service', 
                                     lambda: dynamic_import('app.index.types', 'get_index_service'))

# Initialiser le fournisseur de services
init_service_provider()

# Décorateur pour injecter des services
def inject(**service_names):
    """
    Décorateur pour injecter des services dans une fonction.
    
    Args:
        **service_names: Noms des services à injecter
        
    Returns:
        Décorateur qui injecte les services
    """
    return service_provider.inject(**service_names) 