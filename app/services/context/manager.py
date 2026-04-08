from typing import Dict, Any, Optional, Type, List
from datetime import datetime, timedelta
import asyncio
import json
import os
import urllib.parse

from app.matrix_bot.config import logger
from app.config import Config
from .types import ContextType
from .models import (
    BaseContext,
    UserContext,
    SessionContext,
    RequestContext,
    ResponseContext,
    IntentContext,
    WorkflowContext,
    ExecutionContext,
    RoomContext,
    get_synchronized_time
)
# Importation conditionnelle pour éviter l'importation circulaire
from .cache import ContextCache

class DateTimeEncoder(json.JSONEncoder):
    """Encodeur JSON personnalisé pour gérer les objets datetime"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class ContextManager:
    """Gestionnaire de contexte avec persistance WebDAV"""
    
    CONTEXT_CLASSES = {
        ContextType.ROOM: RoomContext,
        ContextType.USER: UserContext,
        ContextType.SESSION: SessionContext,
        ContextType.REQUEST: RequestContext,
        ContextType.INTENT: IntentContext,
        ContextType.WORKFLOW: WorkflowContext,
        ContextType.EXECUTION: ExecutionContext,
        ContextType.RESPONSE: ResponseContext
    }

    def __init__(self, config: Config):
        self.config = config
        self._webdav = None
        self._cache = ContextCache(
            max_size=config.context_cache_size,
            default_ttl=config.context_cache_ttl,
            cleanup_interval=config.context_cleanup_interval
        )
        self._pending_saves: Dict[str, BaseContext] = {}
        self._save_lock = asyncio.Lock()
        self._cleanup_task = None
        self._save_task = None
        self._initialized = False
        self._room_contexts: Dict[str, RoomContext] = {}
        # Ajout pour le mode dégradé
        self._local_cache: Dict[str, Dict[str, Any]] = {}
        self._is_degraded_mode = False
        self._last_save_attempt = datetime.now() - timedelta(hours=1)  # Forcer une première tentative

    async def initialize(self):
        """Initialise le gestionnaire de contexte"""
        if self._initialized:
            return
            
        try:
            # Import conditionnel à l'intérieur de la méthode pour éviter les importations circulaires
            from ..webdav import WebDAVService
            
            # Initialiser WebDAV
            self._webdav = WebDAVService(self.config)
            try:
                await asyncio.wait_for(self._webdav.initialize(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout lors de l'initialisation WebDAV - passage en mode dégradé")
                self._is_degraded_mode = True
            except Exception as webdav_err:
                logger.warning(f"Erreur initialisation WebDAV: {str(webdav_err)} - passage en mode dégradé")
                self._is_degraded_mode = True
            
            # Démarrer le cache
            await self._cache.start()
            
            # Démarrer la tâche de nettoyage périodique
            if self.config.context_auto_cleanup:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                
            # Démarrer la tâche de sauvegarde périodique
            self._save_task = asyncio.create_task(self._periodic_save_loop())
                
            self._initialized = True
            
            # Précharger les contextes de session les plus récents si possible
            if not self._is_degraded_mode:
                try:
                    await self._preload_recent_contexts()
                except Exception as e:
                    logger.warning(f"Erreur préchargement contextes: {str(e)}")
            
            logger.info(f"Gestionnaire de contexte initialisé (mode dégradé: {self._is_degraded_mode})")
            
        except Exception as e:
            logger.error(f"Erreur initialisation gestionnaire de contexte: {str(e)}")
            if self._webdav:
                await self._webdav.close()
            self._webdav = None
            self._is_degraded_mode = True
            self._initialized = True  # Marqué comme initialisé pour permettre le fonctionnement dégradé
            logger.warning("Gestionnaire de contexte initialisé en mode dégradé")

    async def _preload_recent_contexts(self):
        """Précharge les contextes de session les plus récents"""
        try:
            if not self._webdav or self._is_degraded_mode:
                return
                
            # Lister les fichiers de contexte de session
            context_files = await self._webdav.list_documents(
                f"{self._webdav.CONTEXTS_DIR}",
                pattern="*_*_*"  # Motif pour les sessions (room_id_user_id_*)
            )
            
            # Ne charger que les 50 plus récents pour éviter de surcharger la mémoire
            if len(context_files) > 50:
                # Trier par date de modification (si disponible)
                context_files = context_files[-50:]
                
            # Précharger les contextes
            loaded_count = 0
            for file_path in context_files:
                try:
                    context_id = os.path.basename(file_path).replace(".json", "")
                    context_type_str = context_id.split("_")[-1] if "_" in context_id else "session"
                    context_type = ContextType(context_type_str) if context_type_str in ContextType.__members__ else ContextType.SESSION
                    
                    # Charger le contexte
                    await self.get_context(context_id, context_type)
                    loaded_count += 1
                except Exception as e:
                    logger.debug(f"Erreur préchargement contexte {file_path}: {str(e)}")
                    continue
                    
            logger.info(f"Préchargement de {loaded_count}/{len(context_files)} contextes récents terminé")
            
        except Exception as e:
            logger.warning(f"Erreur générale préchargement contextes: {str(e)}")

    async def _periodic_save_loop(self):
        """Tâche périodique pour sauvegarder les contextes en attente"""
        save_interval = self.config.context_save_interval
        retry_interval = 60  # Réessayer après 1 minute en cas d'échec
        
        while True:
            try:
                # Attendre l'intervalle configuré
                await asyncio.sleep(save_interval)
                
                # Tentative de reconnexion WebDAV si en mode dégradé
                if self._is_degraded_mode:
                    # Ne tenter de reconnecter que toutes les 10 minutes
                    time_since_last_attempt = (datetime.now() - self._last_save_attempt).total_seconds()
                    if time_since_last_attempt > 600:  # 10 minutes
                        self._last_save_attempt = datetime.now()
                        logger.info("Tentative de reconnexion WebDAV...")
                        try:
                            if not self._webdav:
                                self._webdav = WebDAVService(self.config)
                            await asyncio.wait_for(self._webdav.initialize(), timeout=15.0)
                            self._is_degraded_mode = False
                            logger.info("Reconnexion WebDAV réussie, sortie du mode dégradé")
                        except Exception as e:
                            logger.warning(f"Échec reconnexion WebDAV: {str(e)}")
                            await asyncio.sleep(retry_interval)
                            continue
                
                # Sauvegarder les contextes en attente
                if not self._is_degraded_mode:
                    await self.flush_pending_saves()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle de sauvegarde périodique: {str(e)}")
                await asyncio.sleep(retry_interval)

    async def close(self):
        """Ferme proprement le gestionnaire"""
        if not self._initialized:
            return
            
        try:
            # Sauvegarder les contextes en attente
            await self.flush_pending_saves()
            
            # Arrêter le cache
            await self._cache.stop()
            
            # Arrêter les tâches périodiques
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
                    
            if self._save_task:
                self._save_task.cancel()
                try:
                    await self._save_task
                except asyncio.CancelledError:
                    pass
                
            # Fermer la connexion WebDAV
            if self._webdav and not self._is_degraded_mode:
                await self._webdav.close()
                
            self._initialized = False
            logger.info("Gestionnaire de contexte fermé")
            
        except Exception as e:
            logger.error(f"Erreur fermeture gestionnaire de contexte: {str(e)}")
            raise

    async def get_or_create_room_context(self, room_id: str, room_name: str, is_direct: bool) -> RoomContext:
        """Récupère ou crée le contexte d'un salon.

        Garantit que `webdav_context` (chemin du workspace WebDAV) est peuplé,
        soit avec la valeur déjà persistée (via !space link ou usage antérieur),
        soit via la convention de mapping `Config.workspace_path_template`.

        Cette auto-population est essentielle pour l'isolation par workspace :
        sans elle, tous les rooms tombent sur le BehaviorManager global.
        """
        if room_id in self._room_contexts:
            room_context = self._room_contexts[room_id]
        else:
            # Essayer de charger depuis le stockage
            room_context = await self.get_context(room_id, ContextType.ROOM)
            if not room_context:
                # Créer un nouveau contexte de salon
                room_context = await self.create_context(
                    room_id,
                    ContextType.ROOM,
                    {
                        "room_id": room_id,
                        "name": room_name,
                        "is_direct": is_direct
                    }
                )
            self._room_contexts[room_id] = room_context

        # Auto-population du webdav_context si absent et template configuré.
        # Ne touche pas aux rooms déjà associées à un workspace via !space link.
        if not getattr(room_context, "webdav_context", None):
            template = getattr(self.config, "workspace_path_template", "")
            if template:
                workspace_path = template.format(room_id=room_id)
                room_context.set_documentation_space(workspace_path)
                logger.info(
                    f"[ROOM] Auto-mapping workspace pour {room_id}: {workspace_path}"
                )
                # Persister immédiatement pour que les prochains messages le voient
                try:
                    await self.update_context(
                        room_id, ContextType.ROOM,
                        room_context.to_dict(), immediate_save=True,
                    )
                except Exception as e:
                    logger.warning(f"[ROOM] Impossible de persister webdav_context: {e}")

        return room_context

    async def get_room_participants(self, room_id: str) -> List[str]:
        """Récupère la liste des participants d'un salon"""
        room_context = await self.get_context(room_id, ContextType.ROOM)
        if room_context:
            return list(room_context.participants.keys())
        return []

    async def add_room_participant(self, room_id: str, user_id: str, role: str = "member") -> None:
        """Ajoute un participant à un salon"""
        room_context = await self.get_context(room_id, ContextType.ROOM)
        if room_context:
            room_context.add_participant(user_id, role)
            await self.update_context(room_id, ContextType.ROOM, room_context.to_dict(), immediate_save=True)

    async def update_room_activity(self, room_id: str, user_id: str) -> None:
        """Met à jour l'activité dans un salon"""
        try:
            room_context = await self.get_context(room_id, ContextType.ROOM)
            if room_context:
                # Vérifier que l'utilisateur est dans la liste des participants
                if user_id not in room_context.participants:
                    # Ajouter l'utilisateur s'il n'est pas déjà présent
                    room_context.add_participant(user_id)
                    
                # Mettre à jour l'activité
                room_context.update_participant_activity(user_id)
                
                # Sauvegarder les modifications
                try:
                    await self.update_context(room_id, ContextType.ROOM, room_context.to_dict(), immediate_save=True)
                except Exception as e:
                    logger.error(f"Erreur lors de la sauvegarde immédiate du contexte {room_id}: {str(e)}")
            else:
                logger.warning(f"Impossible de mettre à jour l'activité: contexte de salon {room_id} non trouvé")
        except Exception as e:
            logger.warning(f"Erreur lors de la mise à jour de l'activité dans le salon {room_id}: {str(e)}")

    async def update_shared_context(self, room_id: str, key: str, value: Any) -> None:
        """Met à jour le contexte partagé d'un salon"""
        room_context = await self.get_context(room_id, ContextType.ROOM)
        if room_context:
            room_context.update_shared_context(key, value)
            await self.update_context(room_id, ContextType.ROOM, room_context.to_dict(), immediate_save=True)

    async def get_user_sessions(self, room_id: str, user_id: str) -> List[SessionContext]:
        """Récupère toutes les sessions d'un utilisateur dans un salon"""
        sessions = []
        session_pattern = f"{room_id}_{user_id}_*"
        
        try:
            # Lister les fichiers de session correspondants
            session_files = await self._webdav.list_documents(
                f"{self._webdav.CONTEXTS_DIR}",
                pattern=session_pattern
            )
            
            for session_file in session_files:
                session_id = os.path.basename(session_file).replace(".json", "")
                session = await self.get_context(session_id, ContextType.SESSION)
                if session:
                    sessions.append(session)
                    
        except Exception as e:
            logger.error(f"Erreur récupération sessions utilisateur: {str(e)}")
            
        return sessions

    def _get_context_path(self, context_id: str) -> str:
        """Génère le chemin WebDAV pour un contexte"""
        # Utiliser directement les caractères spéciaux sans encodage
        # C'est la même implémentation que _get_context_path_legacy
        return f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"

    def _get_context_path_legacy(self, context_id: str) -> str:
        """Génère le chemin WebDAV pour un contexte avec les caractères spéciaux préservés (format legacy)"""
        # Utiliser directement l'ID sans encodage pour les anciens fichiers
        return f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"

    async def get_context(self, context_id: str, context_type: ContextType) -> Optional[BaseContext]:
        """Récupère un contexte par son ID et son type.
        
        Args:
            context_id: Identifiant du contexte
            context_type: Type de contexte
            
        Returns:
            Le contexte chargé ou None si non trouvé
        """
        # Vérifier si le contexte est dans le cache local
        if context_id in self._local_cache:
            # Créer l'instance à partir des données en cache
            context_class = self.CONTEXT_CLASSES.get(context_type)
            if context_class:
                try:
                    return context_class.from_dict(self._local_cache[context_id])
                except Exception as e:
                    logger.error(f"Erreur création contexte depuis cache local: {str(e)}")
        
        # Vérifier d'abord dans le cache mémoire
        try:
            # Essayer de récupérer depuis le cache
            cached_data = await self._cache.get(f"{context_id}_{context_type.value}")
            if cached_data:
                return cached_data
        except Exception as cache_err:
            logger.error(f"Erreur accès cache pour {context_id}: {str(cache_err)}")
        
        # Si en mode dégradé, impossible de charger depuis WebDAV
        if self._is_degraded_mode:
            return None
            
        # Si pas dans le cache, charger depuis WebDAV
        try:
            if not self._webdav:
                logger.warning("Tentative d'accès WebDAV sans connexion initialisée")
                return None
                
            # Construire le chemin du fichier de contexte
            context_file = f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"
            
            # Vérifier si le fichier existe
            if not await self._webdav.exists(context_file):
                return None
                
            # Lire le contenu du fichier
            content = await self._webdav.read_document(context_file)
            if not content:
                return None
                
            # Désérialiser le contenu JSON
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                logger.error(f"Fichier de contexte {context_id} corrompu")
                return None
                
            # Stocker dans le cache local pour accès futurs
            self._local_cache[context_id] = data
                
            # Créer l'instance de contexte appropriée
            context_class = self.CONTEXT_CLASSES.get(context_type)
            if not context_class:
                logger.error(f"Type de contexte {context_type} non supporté")
                return None
                
            # Créer l'instance
            context = context_class.from_dict(data)
            
            # Mettre en cache
            await self._cache.set(f"{context_id}_{context_type.value}", context)
            
            return context
            
        except Exception as e:
            logger.error(f"Erreur chargement contexte {context_id}: {str(e)}")
            return None

    async def create_context(self, context_id: str, context_type: ContextType, data: Dict[str, Any]) -> BaseContext:
        """Crée un nouveau contexte.
        
        Args:
            context_id: Identifiant du contexte
            context_type: Type de contexte
            data: Données du contexte
            
        Returns:
            Le contexte créé
        """
        # Vérifier si la classe de contexte est supportée
        context_class = self.CONTEXT_CLASSES.get(context_type)
        if not context_class:
            raise ValueError(f"Type de contexte {context_type} non supporté")
            
        # Créer l'instance
        context = context_class.from_dict(data)
        
        # Mettre à jour le timestamp de création
        context.created_at = get_synchronized_time()
        context.updated_at = get_synchronized_time()
        
        # Mettre en cache
        await self._cache.set(f"{context_id}_{context_type.value}", context)
        
        # Stocker dans le cache local
        self._local_cache[context_id] = context.to_dict()
        
        # Ajouter à la liste des sauvegardes en attente
        self._pending_saves[context_id] = context
        
        # Si pas en mode dégradé, tenter une sauvegarde immédiate
        if not self._is_degraded_mode:
            try:
                # Sauvegarder sur WebDAV de manière asynchrone
                asyncio.create_task(self._save_context(context_id, context))
            except Exception as e:
                logger.error(f"Erreur sauvegarde contexte {context_id}: {str(e)}")
        
        return context

    async def update_context(self, context_id: str, context_type: ContextType, 
                            data: Dict[str, Any], immediate_save: bool = False) -> None:
        """Met à jour un contexte existant.
        
        Args:
            context_id: Identifiant du contexte
            context_type: Type de contexte
            data: Nouvelles données
            immediate_save: Si True, force une sauvegarde immédiate
        """
        # Vérifier si la classe de contexte est supportée
        context_class = self.CONTEXT_CLASSES.get(context_type)
        if not context_class:
            raise ValueError(f"Type de contexte {context_type} non supporté")
            
        # Créer l'instance à partir des données
        context = context_class.from_dict(data)
        
        # Mettre à jour le timestamp
        context.updated_at = get_synchronized_time()
        
        # Mettre en cache
        await self._cache.set(f"{context_id}_{context_type.value}", context)
        
        # Mettre à jour le cache local
        self._local_cache[context_id] = context.to_dict()
        
        # Ajouter à la liste des sauvegardes en attente
        self._pending_saves[context_id] = context
        
        # Sauvegarder immédiatement si demandé et pas en mode dégradé
        if immediate_save and not self._is_degraded_mode:
            try:
                await self._save_context(context_id, context)
            except Exception as e:
                logger.error(f"Erreur sauvegarde immédiate contexte {context_id}: {str(e)}")

    async def _save_context(self, context_id: str, context: BaseContext) -> None:
        """Sauvegarde un contexte sur WebDAV.
        
        Args:
            context_id: Identifiant du contexte
            context: Instance du contexte
        """
        if self._is_degraded_mode or not self._webdav:
            return
            
        try:
            # Sérialiser le contexte
            context_data = context.to_dict()
            context_json = json.dumps(context_data, cls=DateTimeEncoder)
            
            # Construire le chemin du fichier
            context_file = f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"
            
            # Écrire le fichier
            await self._webdav.write_file(context_file, context_json)
            
            # Retirer de la liste des sauvegardes en attente
            if context_id in self._pending_saves:
                del self._pending_saves[context_id]
                
        except Exception as e:
            logger.error(f"Erreur sauvegarde contexte {context_id}: {str(e)}")
            # Garder dans la liste des sauvegardes en attente pour réessayer plus tard

    async def flush_pending_saves(self) -> None:
        """Sauvegarde tous les contextes en attente."""
        if self._is_degraded_mode or not self._webdav:
            return
            
        async with self._save_lock:
            pending_ids = list(self._pending_saves.keys())
            saved_count = 0
            
            for context_id in pending_ids:
                try:
                    context = self._pending_saves[context_id]
                    await self._save_context(context_id, context)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Erreur sauvegarde contexte {context_id}: {str(e)}")
                    
            if saved_count > 0:
                logger.info(f"Sauvegarde de {saved_count}/{len(pending_ids)} contextes en attente terminée")

    async def cleanup_old_contexts(self, max_age_days: int = 30) -> None:
        """Nettoie les vieux contextes"""
        try:
            # Calculer la date limite
            limit_date = datetime.now() - timedelta(days=max_age_days)
            
            # Lister les contextes avec list_directory pour avoir plus d'informations
            context_dir = f"{self._webdav.CONTEXTS_DIR}"
            files_info = await self._webdav.list_directory(context_dir)
            
            # Filtrer pour ne garder que les fichiers JSON
            context_files = [item['path'] for item in files_info if item['type'] == 'file' and item['name'].endswith('.json')]
            
            for file_path in context_files:
                try:
                    # Charger le contexte
                    try:
                        content = await self._webdav.read_document(file_path)
                    except FileNotFoundError:
                        logger.debug(f"Fichier non trouvé lors du nettoyage: {file_path}")
                        continue
                    
                    # Parser le contenu JSON
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError as json_err:
                        logger.warning(f"Erreur décodage JSON pour {file_path}: {str(json_err)}")
                        continue
                    
                    # Vérifier la date de dernière mise à jour
                    last_activity = data.get('last_activity', None)
                    if not last_activity:
                        logger.debug(f"Date de dernière activité manquante pour {file_path}, ignoré")
                        continue
                        
                    try:
                        activity_date = datetime.fromisoformat(last_activity)
                        if activity_date < limit_date:
                            # Supprimer le contexte
                            await self._webdav.delete_file(file_path)
                            logger.info(f"Contexte nettoyé: {file_path}")
                    except ValueError:
                        logger.debug(f"Format de date invalide pour {file_path}: {last_activity}, ignoré")
                        continue
                        
                except Exception as e:
                    logger.warning(f"Erreur nettoyage contexte {file_path}: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Erreur nettoyage contextes: {str(e)}")
            raise

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage périodique"""
        while True:
            try:
                await asyncio.sleep(self.config.context_cleanup_interval)
                if self.config.context_auto_cleanup:
                    await self.cleanup_old_contexts(
                        max_age_days=self.config.context_max_age_days
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur boucle nettoyage: {str(e)}")
                await asyncio.sleep(60)  # Attente plus courte en cas d'erreur 

    async def get_or_create_session_context(self, room_id: str, user_id: str) -> SessionContext:
        """Récupère ou crée un contexte de session pour un utilisateur dans un salon."""
        # Construire l'ID de session unique : room_id + user_id
        session_id = f"{room_id}_{user_id}"
        
        # Essayer de récupérer un contexte existant
        context = await self.get_context(session_id, ContextType.SESSION)
        
        # Si le contexte n'existe pas, le créer
        if context is None:
            try:
                # S'assurer que tous les champs obligatoires sont fournis
                context_data = {
                    "session_id": session_id,
                    "room_id": room_id,
                    "user_id": user_id,
                    "history": [],
                    "conversation_state": {}
                }
                context = await self.create_context(session_id, ContextType.SESSION, context_data)
                logger.debug(f"Nouveau contexte de session créé pour {user_id} dans {room_id}")
            except Exception as e:
                logger.error(f"Erreur création contexte session: {str(e)}")
                # En cas d'erreur, créer un contexte en mémoire pour ne pas bloquer
                context = SessionContext(
                    session_id=session_id,
                    room_id=room_id,
                    user_id=user_id
                )
        
        return context 