from typing import Dict, Any, Optional, Type, List
from datetime import datetime, timedelta
import asyncio
import json
import os

from matrix_bot.config import logger
from config import Config
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
from ..webdav import WebDAVService
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
        self._initialized = False
        self._room_contexts: Dict[str, RoomContext] = {}

    async def initialize(self):
        """Initialise le gestionnaire de contexte"""
        if self._initialized:
            return
            
        try:
            # Initialiser WebDAV
            self._webdav = WebDAVService(self.config)
            await self._webdav.initialize()
            
            # Démarrer le cache
            await self._cache.start()
            
            # Démarrer la tâche de nettoyage périodique
            if self.config.context_auto_cleanup:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                
            self._initialized = True
            logger.info("Gestionnaire de contexte initialisé")
            
        except Exception as e:
            logger.error(f"Erreur initialisation gestionnaire de contexte: {str(e)}")
            if self._webdav:
                await self._webdav.close()
            self._webdav = None
            raise

    async def close(self):
        """Ferme proprement le gestionnaire"""
        if not self._initialized:
            return
            
        try:
            # Sauvegarder les contextes en attente
            await self.flush_pending_saves()
            
            # Arrêter le cache
            await self._cache.stop()
            
            # Arrêter la tâche de nettoyage
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
                
            # Fermer la connexion WebDAV
            if self._webdav:
                await self._webdav.close()
                
            self._initialized = False
            logger.info("Gestionnaire de contexte fermé")
            
        except Exception as e:
            logger.error(f"Erreur fermeture gestionnaire de contexte: {str(e)}")
            raise

    async def get_or_create_room_context(self, room_id: str, room_name: str, is_direct: bool) -> RoomContext:
        """Récupère ou crée le contexte d'un salon"""
        if room_id in self._room_contexts:
            return self._room_contexts[room_id]
            
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
        room_context = await self.get_context(room_id, ContextType.ROOM)
        if room_context:
            room_context.update_participant_activity(user_id)
            await self.update_context(room_id, ContextType.ROOM, room_context.to_dict(), immediate_save=True)

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
        return f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"

    async def get_context(
        self,
        context_id: str,
        context_type: ContextType
    ) -> Optional[BaseContext]:
        """Récupère un contexte existant"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire de contexte n'est pas initialisé")
            
        try:
            # Vérifier le cache
            cached_context = await self._cache.get(context_id)
            if cached_context:
                if isinstance(cached_context, self.CONTEXT_CLASSES[context_type]):
                    return cached_context
                else:
                    logger.warning(f"Type de contexte incorrect dans le cache pour {context_id}")
                    await self._cache.delete(context_id)
            
            # Charger depuis WebDAV
            context_path = self._get_context_path(context_id)
            try:
                content = await self._webdav.read_document(context_path)
                data = json.loads(content)
                
                # Vérifier le type
                stored_type = data.get('_type')
                if not stored_type or ContextType(stored_type) != context_type:
                    logger.warning(f"Type de contexte incorrect pour {context_id}")
                    return None
                
                # Supprimer le type avant la création de l'instance
                data.pop('_type', None)
                
                # Créer l'instance
                context_class = self.CONTEXT_CLASSES[context_type]
                context = context_class.from_dict(data)
                
                # Mettre en cache
                await self._cache.set(context_id, context)
                
                return context
                
            except Exception as e:
                if "404" in str(e):
                    logger.debug(f"Contexte {context_id} non trouvé")
                    return None
                raise
                
        except Exception as e:
            logger.error(f"Erreur récupération contexte {context_id}: {str(e)}")
            return None

    async def create_context(
        self,
        context_id: str,
        context_type: ContextType,
        data: Dict[str, Any]
    ) -> BaseContext:
        """Crée un nouveau contexte"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire de contexte n'est pas initialisé")
            
        try:
            # Récupérer la classe appropriée
            context_class = self.CONTEXT_CLASSES[context_type]
            
            # Nettoyer les données d'entrée
            clean_data = {
                k: v for k, v in data.items() 
                if k not in ['_created_at', '_last_activity', '_meta']
            }
            
            # Créer l'instance
            context = context_class(**clean_data)
            
            # Sauvegarder dans le cache
            await self._cache.set(context_id, context)
            
            # Marquer pour sauvegarde
            self._pending_saves[context_id] = context
            
            # Sauvegarder immédiatement
            await self._save_context(context_id, context)
            
            return context
            
        except Exception as e:
            logger.error(f"Erreur création contexte {context_id}: {str(e)}")
            raise

    async def update_context(
        self,
        context_id: str,
        context_type: ContextType,
        data: Dict[str, Any],
        immediate_save: bool = False
    ) -> None:
        """Met à jour un contexte existant"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire de contexte n'est pas initialisé")
            
        try:
            context = await self.get_context(context_id, context_type)
            if not context:
                context = await self.create_context(context_id, context_type, data)
            else:
                # Mettre à jour les champs
                for key, value in data.items():
                    if hasattr(context, key) and key not in ["created_at", "_creation_time", "_meta"]:
                        setattr(context, key, value)
                
                # Mettre à jour le timestamp
                context.last_activity = get_synchronized_time()
                
                # Mettre à jour le cache
                await self._cache.set(context_id, context)
                
                # Marquer pour sauvegarde
                self._pending_saves[context_id] = context
                
                if immediate_save:
                    await self._save_context(context_id, context)
                    
        except Exception as e:
            logger.error(f"Erreur mise à jour contexte {context_id}: {str(e)}")
            raise

    async def delete_context(self, context_id: str) -> None:
        """Supprime un contexte"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire de contexte n'est pas initialisé")
            
        try:
            # Supprimer du cache
            await self._cache.delete(context_id)
            
            # Supprimer des sauvegardes en attente
            self._pending_saves.pop(context_id, None)
            
            # Supprimer le fichier WebDAV
            context_path = self._get_context_path(context_id)
            try:
                await self._webdav.delete_file(context_path)
            except Exception as e:
                if "404" not in str(e):
                    raise
                    
        except Exception as e:
            logger.error(f"Erreur suppression contexte {context_id}: {str(e)}")
            raise

    async def _save_context(self, context_id: str, context: BaseContext) -> None:
        """Sauvegarde un contexte sur WebDAV"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire de contexte n'est pas initialisé")
            
        try:
            async with self._save_lock:
                # Préparer les données
                data = context.to_dict()
                # Ajouter le type de contexte
                context_type = next(
                    (t for t, c in self.CONTEXT_CLASSES.items() 
                     if isinstance(context, c)),
                    None
                )
                if not context_type:
                    raise ValueError(f"Type de contexte inconnu pour {context.__class__.__name__}")
                    
                data['_type'] = context_type.value
                
                # Sauvegarder sur WebDAV
                context_path = self._get_context_path(context_id)
                await self._webdav.write_file(
                    context_path,
                    json.dumps(data, indent=2, cls=DateTimeEncoder)
                )
                
                # Retirer des sauvegardes en attente
                self._pending_saves.pop(context_id, None)
                
        except Exception as e:
            logger.error(f"Erreur sauvegarde contexte {context_id}: {str(e)}")
            raise

    async def flush_pending_saves(self) -> None:
        """Sauvegarde tous les contextes en attente"""
        try:
            async with self._save_lock:
                for context_id, context in self._pending_saves.items():
                    await self._save_context(context_id, context)
                    
        except Exception as e:
            logger.error(f"Erreur sauvegarde contextes en attente: {str(e)}")
            raise

    async def cleanup_old_contexts(self, max_age_days: int = 30) -> None:
        """Nettoie les vieux contextes"""
        try:
            # Calculer la date limite
            limit_date = datetime.now() - timedelta(days=max_age_days)
            
            # Lister les contextes
            context_dir = f"{self._webdav.CONTEXTS_DIR}"
            files = await self._webdav.list_documents(context_dir)
            
            for file in files:
                try:
                    # Charger le contexte
                    content = await self._webdav.read_document(file)
                    data = json.loads(content)
                    
                    # Vérifier la date de dernière mise à jour
                    updated_at_str = data.get('updated_at', '')
                    if not updated_at_str:
                        logger.debug(f"Date de mise à jour manquante pour {file}, ignoré")
                        continue
                        
                    try:
                        updated_at = datetime.fromisoformat(updated_at_str)
                        if updated_at < limit_date:
                            # Supprimer le contexte
                            await self._webdav.delete_file(file)
                            logger.info(f"Contexte nettoyé: {file}")
                    except ValueError:
                        logger.debug(f"Format de date invalide pour {file}: {updated_at_str}, ignoré")
                        continue
                        
                except Exception as e:
                    logger.warning(f"Erreur nettoyage contexte {file}: {str(e)}")
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