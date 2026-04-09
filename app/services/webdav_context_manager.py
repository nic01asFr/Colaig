"""
Service de gestion des contextes WebDAV pour ColaIG.

Ce module permet de gérer différentes connexions WebDAV selon le contexte
de l'utilisateur et du salon. Il maintient un cache de connexions WebDAV
et les sélectionne dynamiquement selon le message reçu.
"""

import asyncio
from typing import Dict, Optional, Tuple, Any, List
import os
from datetime import datetime, timedelta
import json

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService

class WebDAVContextManager:
    """
    Gestionnaire de contextes WebDAV qui permet de sélectionner
    dynamiquement la connexion WebDAV selon le contexte.
    """
    
    # Constante pour limiter le nombre de contextes préchargés
    MAX_PRELOAD_CONTEXTS = 5  # Valeur par défaut, peut être ajustée après l'initialisation
    
    def __init__(self, config: Config):
        """
        Initialise le gestionnaire de contextes WebDAV.
        
        Args:
            config: Configuration de l'application
        """
        self._config = config
        
        # Configuration WebDAV par défaut
        self._default_webdav_config = {
            'webdav_url': config.webdav_url,
            'webdav_username': config.webdav_username,
            'webdav_password': config.webdav_password,
            'webdav_root': config.webdav_root_path
        }
        
        # Configurations spécifiques
        self._room_configs = {}  # Par salon
        self._user_configs = {}  # Par utilisateur
        self._combined_configs = {}  # Par combinaison salon+utilisateur
        
        # Cache des instances WebDAV
        self._webdav_instances = {}
        
        # Service par défaut (lazy loaded)
        self._default_service = None
        
        # Verrou pour la synchronisation
        self._lock = asyncio.Lock()
        
        # État d'initialisation
        self._initialized = False  # Flag pour suivre l'état d'initialisation
        self._initializing = False
        self._initialization_timeout = 10.0  # Timeout en secondes pour l'initialisation
        self._contexts_loaded_event = asyncio.Event()  # Événement signalant que les contextes sont chargés
        self._preloaded_contexts_count = 0  # Compteur de contextes préchargés
        
        # Cache des contextes
        self._contexts = {}
        
    async def select_webdav_config(self, room_id: Optional[str] = None, 
                                  user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Fonction centralisée pour sélectionner la configuration WebDAV appropriée
        selon le contexte.
        
        L'ordre de priorité est le suivant :
        1. Configuration spécifique à la combinaison salon+utilisateur
        2. Configuration spécifique à l'utilisateur
        3. Configuration spécifique au salon
        4. Configuration par défaut
        
        Args:
            room_id: ID du salon Matrix (optionnel)
            user_id: ID de l'utilisateur Matrix (optionnel)
            
        Returns:
            La configuration WebDAV à utiliser pour ce contexte
        """
        # Si aucun contexte spécifié, retourner la configuration par défaut
        if not room_id and not user_id:
            return self._default_webdav_config
            
        # 1. Vérifier la config combinée salon+utilisateur
        if room_id and user_id and f"{room_id}_{user_id}" in self._combined_configs:
            logger.info(f"Utilisation de la configuration WebDAV pour salon+utilisateur: {room_id}_{user_id}")
            return self._combined_configs[f"{room_id}_{user_id}"]
            
        # 2. Vérifier la config utilisateur
        if user_id and user_id in self._user_configs:
            logger.info(f"Utilisation de la configuration WebDAV pour utilisateur: {user_id}")
            return self._user_configs[user_id]
            
        # 3. Vérifier la config salon
        if room_id and room_id in self._room_configs:
            logger.info(f"Utilisation de la configuration WebDAV pour salon: {room_id}")
            return self._room_configs[room_id]
            
        # 4. Utiliser la configuration par défaut
        logger.info(f"Aucune configuration WebDAV spécifique trouvée, utilisation de la configuration par défaut")
        return self._default_webdav_config
        
    async def get_default_service(self) -> Optional[WebDAVService]:
        """
        Récupère le service WebDAV par défaut.
        
        Returns:
            Le service WebDAV par défaut ou None si non disponible
        """
        if not self._initialized:
            await self.initialize()
            
        return self._default_service
        
    async def get_service_for_context(self, context_path: str) -> Optional[WebDAVService]:
        """
        Récupère un service WebDAV pour un contexte spécifique.
        
        Args:
            context_path: Chemin du contexte (dossier WebDAV)
            
        Returns:
            Un service WebDAV configuré pour le contexte spécifié ou None si non disponible
        """
        if not self._initialized:
            await self.initialize()
            
        try:
            # Vérifier si le contexte existe
            if not await self._default_service.exists(context_path):
                logger.warning(f"Contexte WebDAV inexistant: {context_path}")
                return None
                
            # Créer un service WebDAV spécifique pour ce contexte
            service = WebDAVService(
                webdav_url=self._config.webdav_url,
                webdav_username=self._config.webdav_username,
                webdav_password=self._config.webdav_password,
                webdav_root=os.path.join(self._config.webdav_root_path, context_path)
            )
            
            # Initialiser le service
            await service.init_webdav()
            
            # Définir les répertoires spécifiques à ce contexte
            service.CONTEXTS_DIR = ".albert/contexts"
            service.VERSIONS_DIR = ".albert/versions"
            service.INDEX_DIR = ".albert/index"
            service.BEHAVIOR_DIR = ".albert/behavior"
            
            # Ajouter le chemin de l'espace de travail pour permettre à DocumentIndex
            # de déterminer s'il doit utiliser un index spécifique à cet espace
            service.workspace_path = context_path
            logger.info(f"Service WebDAV créé pour l'espace de travail: {context_path}")
            
            return service
        except Exception as e:
            logger.error(f"Erreur lors de la création du service WebDAV pour le contexte {context_path}: {str(e)}")
            return None
        
    async def _resolve_room_workspace_path(self, room_id: str) -> Optional[str]:
        """Résout le chemin workspace WebDAV d'une room.

        Source unique de vérité :
        1. RoomContext.webdav_context si déjà peuplé (via !space link ou auto-mapping)
        2. None sinon (pas de fallback ici, le caller décide)

        Cette méthode lit le ContextManager mais ne le crée pas (zero side-effect).
        """
        try:
            from app.services.context.instance import get_context_manager
            from app.services.context.types import ContextType
            ctx_manager = await get_context_manager(self._config)
            room_ctx = await ctx_manager.get_context(room_id, ContextType.ROOM)
            if room_ctx and getattr(room_ctx, "webdav_context", None):
                return room_ctx.webdav_context
        except Exception as e:
            logger.debug(f"Résolution workspace pour {room_id} échouée: {e}")
        return None

    async def get_webdav_for_context(self, room_id: Optional[str] = None,
                                     user_id: Optional[str] = None) -> WebDAVService:
        """
        Récupère ou crée un service WebDAV pour le contexte spécifié.

        Logique de résolution :
        1. Aucun contexte → service par défaut
        2. room_id avec workspace_path résolu (via room_context.webdav_context) →
           service scopé qui propage workspace_path / BEHAVIOR_DIR / INDEX_DIR.
           C'est ce qui permet l'isolation par workspace effective.
        3. Configuration spécifique room/user (set_room_webdav_context) →
           service avec credentials dédiés
        4. Sinon → service par défaut

        Args:
            room_id: ID du salon Matrix (optionnel)
            user_id: ID de l'utilisateur Matrix (optionnel)

        Returns:
            Le service WebDAV approprié pour ce contexte
        """
        try:
            # Cas 1 : aucun contexte spécifié
            if not room_id and not user_id:
                default_service = await self.get_default_service()
                if not default_service:
                    raise ValueError("Impossible d'initialiser le service WebDAV par défaut")
                return default_service

            combined_key = f"{room_id or ''}_{user_id or ''}"

            async with self._lock:
                if combined_key in self._webdav_instances:
                    return self._webdav_instances[combined_key]

                # Cas 2 : si la room a un webdav_context défini (workspace path),
                # créer un service scopé qui propage workspace_path et les
                # constantes de chemins (.albert/behavior, .albert/index, etc.).
                # C'est essentiel pour l'isolation effective des behaviors et de l'index.
                if room_id:
                    workspace_path = await self._resolve_room_workspace_path(room_id)
                    if workspace_path:
                        scoped_service = await self.get_service_for_context(workspace_path)
                        if scoped_service:
                            self._webdav_instances[combined_key] = scoped_service
                            return scoped_service

                # Cas 3 : configuration spécifique room/user (credentials dédiés)
                config_to_use = await self.select_webdav_config(room_id, user_id)
                if config_to_use == self._default_webdav_config:
                    default_service = await self.get_default_service()
                    if not default_service:
                        raise ValueError("Impossible d'initialiser le service WebDAV par défaut")
                    return default_service

                service = WebDAVService(
                    webdav_url=config_to_use['webdav_url'],
                    webdav_username=config_to_use['webdav_username'],
                    webdav_password=config_to_use['webdav_password'],
                    webdav_root=config_to_use.get('webdav_root', self._config.webdav_root_path)
                )
                await service.init_webdav()

                self._webdav_instances[combined_key] = service
                return service
        except Exception as e:
            # En cas d'erreur, toujours retourner le service par défaut plutôt que None
            logger.error(f"Erreur lors de la récupération du contexte WebDAV: {e}")
            try:
                # Si la création échoue, créer une instance basique avec la config globale
                default_service = WebDAVService(
                    webdav_url=self._config.webdav_url,
                    webdav_username=self._config.webdav_username,
                    webdav_password=self._config.webdav_password,
                    webdav_root=self._config.webdav_root_path
                )
                await default_service.init_webdav()
                return default_service
            except Exception as e2:
                logger.error(f"Erreur lors de la création du service WebDAV par défaut: {e2}")
                # Dernier recours, retourner l'instance par défaut même si elle est None
                return self._default_service

    async def set_webdav_config(self, config: Dict[str, Any], 
                               room_id: Optional[str] = None, 
                               user_id: Optional[str] = None):
        """
        Définit une configuration WebDAV pour un contexte spécifique.
        
        Args:
            config: Configuration WebDAV (url, username, password, root)
            room_id: ID du salon (optionnel)
            user_id: ID de l'utilisateur (optionnel)
        """
        # Vérifier que nous avons au moins l'URL, le nom d'utilisateur et le mot de passe
        if not all(k in config for k in ('webdav_url', 'webdav_username', 'webdav_password')):
            raise ValueError("La configuration WebDAV doit inclure 'webdav_url', 'webdav_username' et 'webdav_password'")
            
        async with self._lock:
            if not room_id and not user_id:
                # Mise à jour de la configuration par défaut
                self._default_webdav_config = config
                self._default_service = None  # Forcer la recréation du service par défaut
                logger.info(f"Configuration WebDAV par défaut mise à jour")
            elif room_id and user_id:
                # Configuration spécifique salon+utilisateur
                self._combined_configs[f"{room_id}_{user_id}"] = config
                # Invalider le cache pour cette combinaison
                if f"{room_id}_{user_id}" in self._webdav_instances:
                    del self._webdav_instances[f"{room_id}_{user_id}"]
                logger.info(f"Configuration WebDAV mise à jour pour salon+utilisateur: {room_id}_{user_id}")
            elif room_id:
                # Configuration spécifique salon
                self._room_configs[room_id] = config
                # Invalider tous les caches liés à ce salon
                keys_to_delete = [k for k in self._webdav_instances if k.startswith(f"{room_id}_")]
                for key in keys_to_delete:
                    del self._webdav_instances[key]
                logger.info(f"Configuration WebDAV mise à jour pour salon: {room_id}")
            elif user_id:
                # Configuration spécifique utilisateur
                self._user_configs[user_id] = config
                # Invalider tous les caches liés à cet utilisateur
                keys_to_delete = [k for k in self._webdav_instances if k.endswith(f"_{user_id}")]
                for key in keys_to_delete:
                    del self._webdav_instances[key]
                logger.info(f"Configuration WebDAV mise à jour pour utilisateur: {user_id}")

    async def remove_webdav_config(self, room_id: Optional[str] = None, user_id: Optional[str] = None):
        """
        Supprime une configuration WebDAV pour un contexte spécifique.
        
        Args:
            room_id: ID du salon (optionnel)
            user_id: ID de l'utilisateur (optionnel)
        """
        async with self._lock:
            if room_id and user_id:
                # Supprimer configuration spécifique salon+utilisateur
                if f"{room_id}_{user_id}" in self._combined_configs:
                    del self._combined_configs[f"{room_id}_{user_id}"]
                if f"{room_id}_{user_id}" in self._webdav_instances:
                    del self._webdav_instances[f"{room_id}_{user_id}"]
                logger.info(f"Configuration WebDAV supprimée pour salon+utilisateur: {room_id}_{user_id}")
            elif room_id:
                # Supprimer configuration spécifique salon
                if room_id in self._room_configs:
                    del self._room_configs[room_id]
                # Supprimer tous les caches liés à ce salon
                keys_to_delete = [k for k in self._webdav_instances if k.startswith(f"{room_id}_")]
                for key in keys_to_delete:
                    del self._webdav_instances[key]
                logger.info(f"Configuration WebDAV supprimée pour salon: {room_id}")
            elif user_id:
                # Supprimer configuration spécifique utilisateur
                if user_id in self._user_configs:
                    del self._user_configs[user_id]
                # Supprimer tous les caches liés à cet utilisateur
                keys_to_delete = [k for k in self._webdav_instances if k.endswith(f"_{user_id}")]
                for key in keys_to_delete:
                    del self._webdav_instances[key]
                logger.info(f"Configuration WebDAV supprimée pour utilisateur: {user_id}")

    async def list_configs(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Liste toutes les configurations WebDAV enregistrées.
        
        Returns:
            Dictionnaire contenant les configurations par type
        """
        return {
            "default": self._default_webdav_config,
            "rooms": [{"room_id": room_id, **config} for room_id, config in self._room_configs.items()],
            "users": [{"user_id": user_id, **config} for user_id, config in self._user_configs.items()],
            "combined": [{"room_id": key.split('_')[0], "user_id": key.split('_')[1], **config} 
                        for key, config in self._combined_configs.items()]
        }

    async def close(self):
        """Ferme toutes les connexions WebDAV."""
        async with self._lock:
            # Fermer le service par défaut
            if self._default_service:
                try:
                    await self._default_service.close()
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du service WebDAV par défaut: {str(e)}")
            
            # Fermer tous les services en cache
            for key, service in self._webdav_instances.items():
                try:
                    await service.close()
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du service WebDAV {key}: {str(e)}")
            
            # Vider les caches
            self._webdav_instances.clear()
            self._default_service = None

    async def get_webdav_service(self) -> WebDAVService:
        """
        Récupère un service WebDAV prêt à l'emploi.
        Cette méthode est utilisée par le BehaviorManager pour obtenir un service WebDAV.
        
        Returns:
            Un service WebDAV initialisé
        """
        try:
            # Essayer d'abord d'obtenir le service par défaut
            service = await self.get_default_service()
            if service:
                return service
                
            # Si le service par défaut n'est pas disponible, créer un nouveau service
            service = WebDAVService(
                webdav_url=self._config.webdav_url,
                webdav_username=self._config.webdav_username,
                webdav_password=self._config.webdav_password,
                webdav_root=self._config.webdav_root_path
            )
            await service.init_webdav()
            return service
        except Exception as e:
            logger.error(f"Erreur lors de l'obtention du service WebDAV: {str(e)}")
            return None

    async def wait_for_contexts_loaded(self) -> None:
        """
        Attend que tous les contextes soient chargés.
        Cette méthode est utilisée par d'autres services qui dépendent des contextes WebDAV.
        """
        # Si l'événement existe déjà et est défini, retourner immédiatement
        if hasattr(self, "_contexts_loaded_event") and self._contexts_loaded_event.is_set():
            logger.info("Contextes déjà chargés, pas d'attente nécessaire")
            return
            
        # Créer l'événement s'il n'existe pas encore
        if not hasattr(self, "_contexts_loaded_event"):
            self._contexts_loaded_event = asyncio.Event()
            
        # Vérifier si les contextes sont déjà chargés
        if hasattr(self, "_preloaded_contexts_count") and self._preloaded_contexts_count >= self.MAX_PRELOAD_CONTEXTS:
            logger.info(f"Tous les contextes sont déjà chargés ({self._preloaded_contexts_count}), pas d'attente nécessaire")
            self._contexts_loaded_event.set()
            return
            
        # Attendre que l'événement soit défini
        logger.info("Attente du chargement des contextes WebDAV...")
        await self._contexts_loaded_event.wait()
        logger.info("Contextes WebDAV chargés, continuation")

    async def initialize(self):
        """Initialise le service WebDAV par défaut avec timeout."""
        if self._initialized:
            return
            
        try:
            # Créer le service WebDAV par défaut
            from app.services.webdav import WebDAVService
            
            self._default_service = WebDAVService(
                webdav_url=self._config.webdav_url,
                webdav_username=self._config.webdav_username,
                webdav_password=self._config.webdav_password,
                webdav_root=self._config.webdav_root_path
            )
            
            # Initialiser avec un timeout pour éviter les blocages
            try:
                await asyncio.wait_for(self._default_service.init_webdav(), timeout=self._initialization_timeout)
                self._initialized = True
                logger.info("Service WebDAV par défaut initialisé avec succès")
                
                # Initialiser un chargement de base des contextes en arrière-plan
                # mais sans attendre sa complétion
                asyncio.create_task(self._background_preload_contexts())
                
            except asyncio.TimeoutError:
                logger.error(f"Timeout lors de l'initialisation du service WebDAV (>{self._initialization_timeout}s)")
                # Ne pas bloquer le démarrage, continuer avec un service partiellement initialisé
                self._initialized = True  # Marquer comme initialisé malgré le timeout
                
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du service WebDAV: {str(e)}")
            raise
            
    async def _background_preload_contexts(self):
        """Charge les contextes en arrière-plan sans bloquer l'application."""
        try:
            # Charger un nombre limité de contextes récents
            await self.preload_contexts(max_contexts=self.MAX_PRELOAD_CONTEXTS)
            logger.info(f"Préchargement initial de {self._preloaded_contexts_count} contextes terminé")
            
            # Une fois que le chargement initial est terminé, planifier un chargement complet différé
            async def delayed_full_load():
                try:
                    # Attendre un peu pour que l'application démarre complètement
                    await asyncio.sleep(10)
                    
                    # Augmenter la limite pour le chargement complet
                    old_limit = self.MAX_PRELOAD_CONTEXTS
                    self.MAX_PRELOAD_CONTEXTS = 50  # Charger plus de contextes après le démarrage
                    
                    # Charger les contextes restants
                    logger.info(f"Démarrage du chargement différé des contextes (limite: {self.MAX_PRELOAD_CONTEXTS})")
                    await self.preload_contexts(max_contexts=self.MAX_PRELOAD_CONTEXTS)
                    logger.info(f"Chargement différé terminé, total: {self._preloaded_contexts_count} contextes")
                    
                    # Restaurer la limite d'origine
                    self.MAX_PRELOAD_CONTEXTS = old_limit
                except Exception as e:
                    logger.error(f"Erreur lors du chargement différé des contextes: {str(e)}")
            
            # Lancer le chargement différé sans bloquer
            asyncio.create_task(delayed_full_load())
            
        except Exception as e:
            logger.error(f"Erreur lors du préchargement des contextes: {str(e)}")
            
    async def preload_contexts(self, recent_contexts=None, max_contexts=None):
        """
        Précharge les contextes récents pour éviter les délais lors de l'accès ultérieur.
        
        Args:
            recent_contexts: Liste de contextes récents à précharger en priorité
            max_contexts: Nombre maximum de contextes à précharger
        """
        try:
            # Créer et initialiser l'événement de chargement des contextes
            if not hasattr(self, "_contexts_loaded_event"):
                self._contexts_loaded_event = asyncio.Event()
            else:
                # Réinitialiser l'événement avant de commencer le chargement
                self._contexts_loaded_event.clear()
                
            # Utiliser la limite de contextes spécifiée ou la constante de classe
            if max_contexts is None:
                max_contexts = self.MAX_PRELOAD_CONTEXTS
                
            # Créer un compteur pour suivre le nombre de contextes chargés
            self._preloaded_contexts_count = 0
            
            if self._default_service is None:
                await self.get_default_service()
                if self._default_service is None:
                    logger.error("Impossible d'obtenir le service WebDAV par défaut")
                    return
            
            # Obtenir la liste des fichiers de contexte
            context_files = await self._list_context_files()
            if not context_files:
                logger.info("Aucun fichier de contexte trouvé")
                self._contexts_loaded_event.set()
                return
                
            # Prioriser les contextes récents s'ils sont fournis
            if recent_contexts:
                # Récupérer d'abord les contextes récents
                for context_id in recent_contexts:
                    if self._preloaded_contexts_count >= max_contexts:
                        logger.info(f"Limite de préchargement atteinte ({max_contexts})")
                        break
                        
                    # Vérifier si le fichier de contexte existe
                    context_filename = f"{context_id}.json"
                    if context_filename in context_files:
                        try:
                            # Charger le contexte
                            await self._load_context(context_id)
                            self._preloaded_contexts_count += 1
                        except Exception as e:
                            logger.error(f"Erreur lors du chargement du contexte {context_id}: {str(e)}")
            
            # Charger les autres contextes jusqu'à la limite
            for context_file in context_files:
                if self._preloaded_contexts_count >= max_contexts:
                    logger.info(f"Limite de préchargement atteinte ({max_contexts})")
                    break
                    
                # Extraire l'ID du contexte du nom de fichier
                context_id = context_file.replace(".json", "")
                
                # Vérifier si le contexte a déjà été chargé
                if context_id in self._combined_configs:
                    continue
                    
                try:
                    # Charger le contexte
                    await self._load_context(context_id)
                    self._preloaded_contexts_count += 1
                except Exception as e:
                    logger.error(f"Erreur lors du chargement du contexte {context_id}: {str(e)}")
                    
            # Signaler que le chargement des contextes est terminé
            self._contexts_loaded_event.set()
            logger.info(f"Préchargement de {self._preloaded_contexts_count}/{min(len(context_files), max_contexts)} contextes récents terminé")
            
        except Exception as e:
            logger.error(f"Erreur lors du préchargement des contextes: {str(e)}")
            # S'assurer que l'événement est défini même en cas d'erreur
            self._contexts_loaded_event.set()

    async def _list_context_files(self):
        """Liste les fichiers de contexte disponibles dans le répertoire WebDAV."""
        try:
            if self._default_service is None:
                await self.get_default_service()
                if self._default_service is None:
                    logger.error("Impossible de lister les fichiers de contexte, aucun service WebDAV par défaut")
                    return []
                    
            # Vérifier que le répertoire des contextes existe
            default_context_path = os.path.join(self._config.webdav_root_path, '.albert/contexts')
            exists = await self._default_service.exists(default_context_path)
            if not exists:
                # Créer le répertoire s'il n'existe pas
                try:
                    await self._default_service.create_directory(default_context_path)
                    logger.info(f"Répertoire de contextes créé: {default_context_path}")
                except Exception as e:
                    logger.error(f"Erreur lors de la création du répertoire des contextes: {str(e)}")
                    return []
                    
            # Lister les fichiers de contexte
            context_files = await self._default_service.list_documents(default_context_path)
            # Filtrer pour ne garder que les fichiers JSON
            context_files = [os.path.basename(f) for f in context_files if f.endswith('.json')]
            return context_files
            
        except Exception as e:
            logger.error(f"Erreur lors du listage des fichiers de contexte: {str(e)}")
            return []
            
    async def _load_context(self, context_id):
        """
        Charge un contexte spécifique sans bloquer l'application.
        
        Args:
            context_id: Identifiant du contexte à charger (room_id ou room_id_user_id)
        """
        try:
            # Analyser l'ID de contexte pour déterminer s'il s'agit d'un contexte de salon ou utilisateur
            parts = context_id.split('_')
            room_id = parts[0]
            user_id = parts[1] if len(parts) > 1 else None
            
            # Utiliser le chemin approprié pour le fichier de contexte
            context_path = os.path.join(
                self._config.webdav_root_path, 
                '.albert/contexts', 
                f"{context_id}.json"
            )
            
            # Vérifier que le service WebDAV par défaut est disponible
            if self._default_service is None:
                await self.get_default_service()
                if self._default_service is None:
                    logger.error(f"Impossible de charger le contexte {context_id}, aucun service WebDAV par défaut")
                    return
                    
            # Vérifier si le fichier existe
            exists = await self._default_service.exists(context_path)
            if not exists:
                logger.warning(f"Le fichier de contexte {context_path} n'existe pas")
                return
                
            # Charger le contenu du fichier en utilisant read_document au lieu de get_document
            content = await self._default_service.read_document(context_path)
            if not content:
                logger.warning(f"Contenu vide pour le fichier de contexte {context_path}")
                return
                
            # Désérialiser le contenu JSON
            try:
                config_data = json.loads(content)
                # Stocker dans le dictionnaire approprié selon qu'il s'agit d'un contexte de salon ou d'utilisateur
                if user_id:
                    # Format room_id_user_id.json -> contexte utilisateur dans un salon
                    key = f"{room_id}_{user_id}"
                    self._combined_configs[key] = config_data
                    logger.debug(f"Contexte utilisateur chargé: {key}")
                else:
                    # Format room_id.json -> contexte de salon
                    self._room_configs[room_id] = config_data
                    logger.debug(f"Contexte de salon chargé: {room_id}")
                    
                return config_data
            except json.JSONDecodeError:
                logger.error(f"Erreur de décodage JSON pour le fichier {context_path}")
                return
                
        except Exception as e:
            logger.error(f"Erreur lors du chargement du contexte {context_id}: {str(e)}")
            return

    async def discover_documentation_spaces(self) -> Dict[str, Dict[str, Any]]:
        """
        Détecte les espaces documentaires (dossiers avec .albert/).

        Cherche à deux niveaux :
        1. La racine du service elle-même (mode mono-workspace : root_path
           pointe déjà vers un workspace comme 'Archivist')
        2. Les sous-dossiers de premier niveau (mode multi-workspace :
           root_path vide, les workspaces sont des dossiers partagés)

        Returns:
            Dict {space_id: {path, id, name, last_indexed}}
        """
        spaces = {}
        try:
            webdav = await self.get_default_service()
            if not webdav:
                logger.error("Service WebDAV non disponible pour la découverte des espaces")
                return spaces

            # 1. Vérifier si la racine elle-même est un workspace
            #    (mode mono-workspace : root_path = "Archivist", qui contient .albert/)
            try:
                if await webdav.exists(".albert"):
                    root_name = getattr(webdav, "root_path", "").strip("/")
                    space_id = root_name.replace("/", "_") if root_name else "root"
                    spaces[space_id] = {
                        "path": "",  # relatif au service = racine
                        "id": space_id,
                        "name": os.path.basename(root_name) if root_name else "Racine",
                        "last_indexed": None,
                    }
                    logger.info(f"Workspace racine détecté: {root_name or '/'}")
            except Exception as e:
                logger.debug(f"Vérification workspace racine échouée: {e}")

            # 2. Scanner les sous-dossiers de premier niveau
            try:
                items = await webdav.list_directory("/")
            except Exception as e:
                logger.debug(f"Listage des sous-dossiers échoué: {e}")
                items = []

            for item in items:
                if item.get("is_dir", False):
                    path = item.get("path", "")
                    try:
                        has_marker = await webdav.exists(f"{path}/.albert")
                        if has_marker:
                            norm_path = path.rstrip("/")
                            space_id = norm_path.strip("/").replace("/", "_")
                            if not space_id:
                                space_id = "root"
                            if space_id not in spaces:
                                spaces[space_id] = {
                                    "path": norm_path,
                                    "id": space_id,
                                    "name": os.path.basename(norm_path) or "Racine",
                                    "last_indexed": None,
                                }
                                logger.info(f"Workspace détecté: {norm_path}")
                    except Exception as e:
                        logger.warning(f"Erreur vérification espace {path}: {e}")

            logger.info(
                f"Découverte terminée: {len(spaces)} workspace(s) "
                f"({', '.join(s['name'] for s in spaces.values())})"
            )
            return spaces
        except Exception as e:
            logger.error(f"Erreur lors de la découverte des espaces documentaires: {str(e)}")
            return spaces
            
    async def set_room_webdav_context(self, room_id: str, space_path: str) -> bool:
        """
        Associe un salon à un espace documentaire.
        
        Args:
            room_id: ID du salon
            space_path: Chemin de l'espace documentaire
            
        Returns:
            True si l'association a réussi, False sinon
        """
        try:
            # Vérifier si le chemin existe
            webdav = await self.get_default_service()
            if not webdav:
                logger.error("Service WebDAV non disponible")
                return False
                
            # Vérifier si le chemin existe et contient .colaig
            if not await webdav.exists(space_path):
                logger.error(f"Chemin d'espace documentaire inexistant: {space_path}")
                return False
                
            if not await webdav.exists(f"{space_path}/.colaig"):
                logger.error(f"Le chemin {space_path} ne contient pas de marqueur .colaig")
                return False
            
            # Obtenir le contexte du salon
            from app.commands import get_room_context
            room_context = await get_room_context(self._config, room_id)
            if not room_context:
                logger.error(f"Contexte du salon {room_id} non trouvé")
                return False
                
            # Mettre à jour le contexte
            room_context.set_documentation_space(space_path)
            
            # Sauvegarder dans le gestionnaire de contexte
            from app.services.context.instance import get_context_manager
            from app.services.context.types import ContextType
            ctx_manager = await get_context_manager(self._config)
            await ctx_manager.update_context(room_id, ContextType.ROOM, room_context.to_dict())
            
            logger.info(f"Association salon-espace créée: {room_id} -> {space_path}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'association salon-espace: {str(e)}")
            return False
            
    async def clear_room_webdav_context(self, room_id: str) -> bool:
        """
        Supprime l'association entre un salon et un espace documentaire.
        
        Args:
            room_id: ID du salon
            
        Returns:
            True si la suppression a réussi, False sinon
        """
        try:
            # Obtenir le contexte du salon
            from app.commands import get_room_context
            room_context = await get_room_context(self._config, room_id)
            if not room_context:
                logger.error(f"Contexte du salon {room_id} non trouvé")
                return False
                
            # Mettre à jour le contexte
            room_context.webdav_context = None
            if "webdav_context" in room_context.shared_context:
                del room_context.shared_context["webdav_context"]
            
            # Sauvegarder dans le gestionnaire de contexte
            from app.services.context.instance import get_context_manager
            from app.services.context.types import ContextType
            ctx_manager = await get_context_manager(self._config)
            await ctx_manager.update_context(room_id, ContextType.ROOM, room_context.to_dict())
            
            logger.info(f"Association salon-espace supprimée pour: {room_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de l'association salon-espace: {str(e)}")
            return False

# Instance unique du gestionnaire de contexte WebDAV
_webdav_context_manager = None

async def get_webdav_context_manager(config: Config) -> WebDAVContextManager:
    """
    Récupère l'instance unique du gestionnaire de contexte WebDAV.
    
    Args:
        config: Configuration à utiliser
        
    Returns:
        L'instance du gestionnaire de contexte WebDAV
    """
    global _webdav_context_manager
    
    if _webdav_context_manager is None:
        _webdav_context_manager = WebDAVContextManager(config)
        # Initialiser le gestionnaire avec un timeout
        try:
            logger.info("Initialisation du gestionnaire de contexte WebDAV")
            await asyncio.wait_for(_webdav_context_manager.initialize(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout lors de l'initialisation du gestionnaire de contexte WebDAV, fonctionnement en mode dégradé")
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du gestionnaire de contexte WebDAV: {str(e)}")
            logger.info("Fonctionnement en mode dégradé, sans WebDAV")
    
    return _webdav_context_manager

async def close_webdav_context_manager():
    """Ferme l'instance unique du gestionnaire de contexte WebDAV."""
    global _webdav_context_manager
    
    if _webdav_context_manager:
        await _webdav_context_manager.close()
        _webdav_context_manager = None