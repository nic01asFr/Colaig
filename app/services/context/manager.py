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
        return f"{self._webdav.CONTEXTS_DIR}/{context_id}.json"

    async def get_context(
        self,
        context_id: str,
        context_type: ContextType
    ) -> Optional[BaseContext]:
        """Récupère un contexte par son ID et son type
        
        Args:
            context_id: ID du contexte
            context_type: Type de contexte
            
        Returns:
            Le contexte demandé ou None s'il n'existe pas
        """
        if not self._initialized:
            try:
                await self.initialize()
            except Exception as e:
                logger.warning(f"Problème d'initialisation lors de get_context: {str(e)}")
                # Continue pour permettre de récupérer du cache malgré l'erreur
        
        if not context_id:
            return None
        
        # Vérifier le cache d'abord
        cache_key = f"{context_type.value}:{context_id}"
        cached_context = await self._cache.get(cache_key)
        if cached_context:
            return cached_context
        
        # Si c'est un contexte de salon, vérifier le dictionnaire local
        if context_type == ContextType.ROOM and context_id in self._room_contexts:
            # Mettre en cache pour les prochains accès
            await self._cache.set(cache_key, self._room_contexts[context_id])
            return self._room_contexts[context_id]
        
        try:
            # Vérifier si le WebDAV est disponible
            if not self._webdav or not hasattr(self._webdav, '_initialized') or not self._webdav._initialized:
                logger.warning("WebDAV non disponible lors de get_context")
                return None
            
            # Construire le chemin WebDAV
            context_path = self._get_context_path(context_id)
            
            # Récupérer le contexte de manière sécurisée
            context_data = await self._webdav.get_context_safely(context_id)
            
            if not context_data:
                return None
            
            # Vérifier si le type corresponds
            if context_data.get("context_type", "") != context_type.value:
                logger.warning(f"Type de contexte incorrect: {context_data.get('context_type')} != {context_type.value}")
                return None
            
            # Créer l'instance avec la classe appropriée
            context_class = self.CONTEXT_CLASSES.get(context_type)
            if not context_class:
                logger.error(f"Classe de contexte non trouvée pour {context_type}")
                return None
            
            try:
                # Utiliser la méthode from_dict spécifique si elle existe
                if hasattr(context_class, 'from_dict'):
                    context = context_class.from_dict(context_data)
                else:
                    # Sinon, utiliser l'initialisation standard
                    context = context_class(**context_data)
                
                # Mettre en cache pour les prochains accès
                await self._cache.set(cache_key, context)
                
                return context
            except Exception as e:
                logger.error(f"Erreur lors de la création du contexte: {str(e)}")
                return None
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du contexte {context_id}: {str(e)}")
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
        """Met à jour un contexte existant
        
        Args:
            context_id: ID du contexte
            context_type: Type de contexte
            data: Données à mettre à jour
            immediate_save: Si True, sauvegarde immédiatement, sinon ajoute à la file d'attente
        """
        if not self._initialized:
            try:
                await self.initialize()
            except Exception as e:
                logger.warning(f"Problème d'initialisation lors de update_context: {str(e)}")
                # Continue pour essayer malgré l'erreur
            
        if not context_id:
            logger.warning("Tentative de mise à jour d'un contexte avec ID vide")
            return
        
        try:
            # Récupérer le contexte existant ou en créer un nouveau
            cache_key = f"{context_type.value}:{context_id}"
            context = await self._cache.get(cache_key)
            
            if not context:
                # Tenter de récupérer depuis WebDAV
                context = await self.get_context(context_id, context_type)
                
            if not context:
                # Créer un nouveau contexte
                logger.warning(f"Contexte {context_id} non trouvé, création d'un nouveau")
                context_class = self.CONTEXT_CLASSES.get(context_type)
                if context_class:
                    # Créer une copie des données sans le champ context_type
                    clean_data = {k: v for k, v in data.items() if k != 'context_type'}
                    
                    try:
                        if hasattr(context_class, 'from_dict'):
                            context = context_class.from_dict(clean_data)
                        else:
                            context = context_class(**clean_data)
                    except Exception as create_err:
                        logger.error(f"Erreur lors de la création du contexte: {str(create_err)}")
                        return
                else:
                    logger.error(f"Classe de contexte non trouvée pour {context_type}")
                    return
            
            # Mettre à jour le contexte avec les nouvelles données
            for key, value in data.items():
                if key != 'context_type' and hasattr(context, key):
                    setattr(context, key, value)
                
            # Si c'est un contexte de salon, mettre à jour le dictionnaire local
            if context_type == ContextType.ROOM:
                self._room_contexts[context_id] = context
            
            # Mettre à jour le cache
            await self._cache.set(cache_key, context)
            
            # Sauvegarder immédiatement si demandé
            if immediate_save:
                try:
                    await self._save_context(context_id, context)
                except Exception as save_err:
                    logger.error(f"Erreur lors de la sauvegarde immédiate du contexte {context_id}: {str(save_err)}")
                    # Ajouter à la file d'attente malgré l'erreur
                    async with self._save_lock:
                        self._pending_saves[context_id] = context
            else:
                # Ajouter à la file d'attente de sauvegarde
                async with self._save_lock:
                    self._pending_saves[context_id] = context
                
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du contexte {context_id}: {str(e)}")
            # Essayer d'enregistrer malgré l'erreur
            if context:
                async with self._save_lock:
                    self._pending_saves[context_id] = context

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
                
                # S'assurer que les dates sont des objets datetime
                if 'updated_at' in data and isinstance(data['updated_at'], str):
                    try:
                        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
                    except ValueError:
                        logger.warning(f"Format de date invalide pour updated_at: {data['updated_at']}")
                        data['updated_at'] = datetime.now()
                        
                if 'created_at' in data and isinstance(data['created_at'], str):
                    try:
                        data['created_at'] = datetime.fromisoformat(data['created_at'])
                    except ValueError:
                        logger.warning(f"Format de date invalide pour created_at: {data['created_at']}")
                        data['created_at'] = datetime.now()
                
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