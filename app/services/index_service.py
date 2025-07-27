from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
from functools import lru_cache

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.document_index import DocumentIndex, DocumentChunk, FAISSIndex
from app.services.behavior_index import BehaviorIndex, BehaviorChunk
from app.services.behavior_manager import BehaviorManager

@dataclass
class IndexService:
    """Service dédié à la gestion de l'index"""
    
    def __init__(self, config: Config, webdav_service: Optional[WebDAVService] = None):
        self.config = config
        self.webdav_service = webdav_service
        self.document_index = None
        self.behavior_manager = None
        self._initialized = False
        self._current_dimension = None
        self._index_cache = {}
        self._cache_lock = asyncio.Lock()
        self._cache_cleanup_task = None
        self._last_cache_cleanup = datetime.now()
        self._cache_ttl = timedelta(hours=1)  # TTL par défaut de 1 heure
        
    async def _handle_dimension_mismatch(self, current_dim: int, target_dim: int) -> None:
        """Gère une différence de dimension entre l'index et le modèle d'embedding
        
        Args:
            current_dim: Dimension actuelle de l'index
            target_dim: Dimension cible du modèle
        """
        if current_dim == target_dim:
            return
            
        if target_dim <= 0 or target_dim > 1024:
            raise ValueError(f"Dimension cible invalide: {target_dim}")
            
        logger.warning(
            f"Différence de dimension détectée - Index: {current_dim}, "
            f"Modèle: {target_dim}"
        )
        
        try:
            # Récupérer les documents existants
            old_documents = self.document_index.faiss_index.resize_index(target_dim)
            
            # Recalculer les embeddings avec la nouvelle dimension
            for chunk in old_documents:
                try:
                    embedding = await self.document_index.embedding_service.get_embedding(chunk.content)
                    if embedding is not None:
                        self.document_index.faiss_index.add_document(chunk, embedding)
                    else:
                        logger.warning(f"Impossible de recalculer l'embedding pour {chunk.id}")
                except Exception as e:
                    logger.error(f"Erreur lors du recalcul de l'embedding pour {chunk.id}: {str(e)}")
                    continue
                    
            await self.document_index.save_index()
            self._current_dimension = target_dim
            logger.info(f"Index redimensionné avec succès: {target_dim}")
            
        except Exception as e:
            logger.error(f"Erreur lors du redimensionnement: {str(e)}")
            raise
            
    async def _cleanup_cache(self) -> None:
        """Nettoie les entrées expirées du cache"""
        async with self._cache_lock:
            now = datetime.now()
            expired_keys = [
                key for key, (timestamp, _) in self._index_cache.items()
                if now - timestamp > self._cache_ttl
            ]
            for key in expired_keys:
                del self._index_cache[key]
            self._last_cache_cleanup = now
            
    async def _start_cache_cleanup(self) -> None:
        """Démarre la tâche de nettoyage périodique du cache"""
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(3600)  # Nettoyage toutes les heures
                    await self._cleanup_cache()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Erreur nettoyage cache: {str(e)}")
                    
        if not self._cache_cleanup_task:
            self._cache_cleanup_task = asyncio.create_task(cleanup_loop())
            
    async def initialize(self, init_document_index: bool = False, init_behavior_manager: bool = True) -> None:
        """Initialise les services nécessaires"""
        if self._initialized:
            return
            
        try:
            # 1. Initialiser le service WebDAV en premier
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
            
            if not hasattr(self.webdav_service, '_initialized') or not self.webdav_service._initialized:
                try:
                    await self.webdav_service.initialize()
                except Exception as webdav_err:
                    logger.warning(f"Erreur d'initialisation WebDAV, mais on continue: {str(webdav_err)}")
            
            # 2. Démarrer le nettoyage du cache
            await self._start_cache_cleanup()
                
            # 3. Initialiser le gestionnaire de comportements si demandé
            if init_behavior_manager:
                if not self.behavior_manager:
                    self.behavior_manager = BehaviorManager(self.config)
                    self.behavior_manager._webdav = self.webdav_service
                
                try:
                    await self.behavior_manager.initialize()
                    logger.info("Gestionnaire de comportements initialisé")
                except Exception as e:
                    logger.warning(f"Erreur initialisation comportements, mais on continue: {str(e)}")
                    
            # 4. Initialiser l'index de documents si demandé
            if init_document_index:
                if not self.document_index:
                    self.document_index = DocumentIndex(
                        config=self.config,
                        webdav_service=self.webdav_service
                    )
                
                try:
                    await self.document_index.initialize()
                    logger.info("Index de documents initialisé")
                except Exception as e:
                    logger.warning(f"Erreur initialisation documents, mais on continue: {str(e)}")
                    
            self._initialized = True
            logger.info("Service d'index initialisé avec succès")
                
        except Exception as e:
            logger.error(f"Erreur initialisation services: {str(e)}")
            await self._cleanup_resources()
            # Marquer comme initialisé quand même pour permettre l'exécution partielle
            self._initialized = True
            logger.warning("Service d'index initialisé en mode dégradé")
            
    async def _cleanup_resources(self) -> None:
        """Nettoie les ressources en cas d'erreur"""
        try:
            if self._cache_cleanup_task:
                self._cache_cleanup_task.cancel()
                
            # Ne pas fermer automatiquement le webdav_service car il peut être réutilisé
            # Réinitialiser le client à la place si nécessaire
            if self.webdav_service and hasattr(self.webdav_service, 'http_client') and hasattr(self.webdav_service.http_client, 'is_closed') and self.webdav_service.http_client.is_closed:
                await self.webdav_service.reinitialize_client()
                
            if self.behavior_manager:
                await self.behavior_manager.close()
                
            self._initialized = False
            
        except Exception as e:
            logger.error(f"Erreur nettoyage ressources: {str(e)}")
            
    async def _ensure_initialized(self, init_document_index: bool = False, init_behavior_manager: bool = True) -> None:
        """S'assure que le service est initialisé"""
        if not self._initialized:
            try:
                # Vérifier et initialiser le WebDAV service si nécessaire
                if not self.webdav_service:
                    self.webdav_service = WebDAVService(self.config)
                    await self.webdav_service.initialize()
                elif not hasattr(self.webdav_service, '_initialized') or not self.webdav_service._initialized:
                    await self.webdav_service.initialize()
                
                # Initialiser les services demandés
                await self.initialize(init_document_index, init_behavior_manager)
                
            except Exception as e:
                logger.error(f"Erreur lors de l'initialisation des services: {str(e)}")
                raise RuntimeError(f"Échec de l'initialisation des services: {str(e)}")
                
        # Vérifier l'état des services requis
        if init_behavior_manager and (not self.behavior_manager or not self.behavior_manager._initialized):
            await self.initialize(init_document_index=False, init_behavior_manager=True)
            
        if init_document_index and (not self.document_index or not hasattr(self.document_index, '_initialized')):
            await self.initialize(init_document_index=True, init_behavior_manager=False)
    
    async def get_status(self) -> Dict[str, Any]:
        """Retourne l'état actuel de l'index"""
        try:
            await self._ensure_initialized()
            
            total_docs = len(set(chunk.document_path 
                for chunk in self.document_index.faiss_index.document_map.values()))
            total_chunks = self.document_index.faiss_index.index.ntotal
            
            # Obtenir les statistiques du cache d'embeddings
            cache_stats = {
                "size": len(self.document_index.embedding_service.cache.cache),
                "max_size": self.document_index.embedding_service.cache.max_size
            }
            
            return {
                "total_documents": total_docs,
                "total_chunks": total_chunks,
                "embedding_dimension": self.document_index.embedding_service.embedding_dimension,
                "embedding_model": self.config.albert_model_embedding,
                "embedding_cache": cache_stats,
                "is_fresh": await self.document_index.verify_index_freshness(),
                "last_update": max((chunk.last_updated for chunk in self.document_index.faiss_index.document_map.values()), default=None)
            }
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
            raise
    
    async def rebuild(self) -> None:
        """Reconstruit l'index complètement"""
        try:
            logger.info("Début de la reconstruction de l'index...")
            
            # Forcer la réinitialisation
            self._initialized = False
            self.document_index = None
            
            # Initialiser les services
            if not self.webdav_service:
                logger.info("Initialisation du service WebDAV...")
                self.webdav_service = WebDAVService(self.config)
                await self.webdav_service.initialize()
                
            if not self.document_index:
                logger.info("Initialisation de DocumentIndex...")
                self.document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
            
            # Nettoyer le cache des embeddings
            if self.document_index.embedding_service:
                logger.info("Nettoyage du cache des embeddings...")
                await self.document_index.embedding_service.cache.clear_old_entries(
                    timedelta(hours=0)  # Tout nettoyer
                )
            
            # Forcer la dimension correcte pour le nouvel index
            logger.info("Création d'un nouvel index FAISS...")
            self.document_index.faiss_index = FAISSIndex(
                dimension=self.document_index.embedding_service.embedding_dimension
            )
            
            # Construire et sauvegarder l'index
            logger.info("Construction de l'index...")
            await self.document_index.build_index()
            
            logger.info("Sauvegarde de l'index...")
            await self.document_index.save_index()
            
            self._initialized = True
            self._current_dimension = self.document_index.embedding_service.embedding_dimension
            logger.info("Index reconstruit avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de la reconstruction de l'index: {str(e)}")
            self._initialized = False
            raise IndexError(f"Échec de la reconstruction de l'index: {str(e)}")
    
    async def verify(self) -> bool:
        """Vérifie la fraîcheur de l'index"""
        try:
            await self._ensure_initialized()
            is_fresh, _, _ = await self.document_index.verify_index_freshness()
            return is_fresh
        except Exception as e:
            logger.error(f"Erreur lors de la vérification de l'index: {str(e)}")
            return False
    
    async def update(self) -> None:
        """Met à jour l'index en ajoutant uniquement les documents manquants"""
        try:
            await self._ensure_initialized()
            is_fresh, missing_docs, extra_docs = await self.document_index.verify_index_freshness()
            if is_fresh:
                logger.info("Index déjà à jour")
                return
                
            await self.document_index.update_index(missing_docs, extra_docs)
            await self.document_index.save_index()
            logger.info("Index mis à jour avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour de l'index: {str(e)}")
            raise
    
    async def clean(self) -> None:
        """Nettoie l'index et le cache"""
        try:
            await self._ensure_initialized()
            
            # Réinitialiser l'index FAISS
            self.document_index.faiss_index = FAISSIndex(
                dimension=self.document_index.embedding_service.embedding_dimension
            )
            
            # Nettoyer le cache des embeddings
            await self.document_index.embedding_service.cache.clear_old_entries(
                timedelta(hours=0)  # Tout nettoyer
            )
            
            await self.document_index.save_index()
            logger.info("Index et cache nettoyés avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage de l'index: {str(e)}")
            raise
    
    async def _load_index_to_cache(self, index_type: str) -> None:
        """Charge un index en cache"""
        async with self._cache_lock:
            if index_type not in self._index_cache:
                logger.info(f"Chargement de l'index {index_type} en cache")
                if index_type == "behavior":
                    self._index_cache[index_type] = {
                        "data": await self.behavior_manager.index.load_index(),
                        "last_access": datetime.now(),
                        "access_count": 0
                    }
                elif index_type == "document":
                    self._index_cache[index_type] = {
                        "data": await self.document_index.load_index(),
                        "last_access": datetime.now(),
                        "access_count": 0
                    }
                    
    async def _get_cached_index(self, index_type: str) -> Any:
        """Récupère un index depuis le cache avec gestion de la durée de vie"""
        async with self._cache_lock:
            if index_type in self._index_cache:
                cache_entry = self._index_cache[index_type]
                # Mettre à jour les statistiques d'accès
                cache_entry["last_access"] = datetime.now()
                cache_entry["access_count"] += 1
                return cache_entry["data"]
            return None
            
    def _make_cache_key(self, query: str, index_type: str, limit: int) -> str:
        """Crée une clé de cache basée sur les paramètres de recherche"""
        return f"{query}_{index_type}_{limit}"
            
    async def _get_optimized_search_results(
        self,
        query: str,
        index_type: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Cache les résultats de recherche fréquents"""
        # Créer une clé de cache basée sur les paramètres
        cache_key = self._make_cache_key(query, index_type, limit)
        
        # Vérifier si les résultats sont dans le cache
        result = await self.get_cached_index(cache_key)
        if result is not None:
            return result
            
        # Si pas dans le cache, récupérer les résultats
        if index_type == "behavior":
            result = await self.behavior_manager.index.search(query, limit=limit)
        elif index_type == "document":
            result = await self.document_index.search(query, limit=limit)
        else:
            result = []
            
        # Mettre en cache le résultat
        await self.set_cached_index(cache_key, result)
        return result
    
    async def search(self, query: str, limit: int = 10, filters: Optional[Dict[str, Any]] = None,
                room_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Recherche des documents dans l'index.
        Si room_id est fourni, tente d'utiliser l'espace associé à ce salon (si disponible).
        
        Args:
            query: Requête de recherche
            limit: Nombre maximum de résultats
            filters: Filtres à appliquer sur les résultats
            room_id: ID du salon (optionnel)
            user_id: ID de l'utilisateur (optionnel, non utilisé dans cette implémentation)
            
        Returns:
            Liste des résultats de recherche
        """
        try:
            if not self.document_index:
                await self.initialize(init_document_index=True)
            
            # Si on a un salon, vérifier s'il est associé à un espace documentaire
            if room_id:
                try:
                    # Import ici pour éviter les dépendances circulaires
                    from app.commands import get_room_context
                    
                    # Obtenir le contexte du salon
                    room_context = await get_room_context(self.config, room_id)
                    
                    # Si le salon est associé à un espace documentaire, utiliser search_in_space
                    if room_context and hasattr(room_context, 'webdav_context') and room_context.webdav_context:
                        logger.debug(f"Utilisation de l'espace documentaire associé au salon: {room_context.webdav_context}")
                        return await self.search_in_space(query, room_context.webdav_context, limit, filters)
                except Exception as e:
                    logger.error(f"Erreur lors de la récupération du contexte du salon: {str(e)}")
                    # Continuer avec la recherche globale en cas d'erreur
            
            # Effectuer la recherche standard avec les filtres appliqués
            results = await self.document_index.search(query, limit=limit, filters=filters)
            return results
        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {str(e)}")
            return []
    
    async def search_in_space(self, query: str, space_path: str, limit: int = 10, 
                           filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Recherche des documents dans un espace documentaire spécifique.
        
        Args:
            query: Requête de recherche
            space_path: Chemin de l'espace documentaire
            limit: Nombre maximum de résultats
            filters: Filtres à appliquer sur les résultats
            
        Returns:
            Liste des résultats de recherche
        """
        try:
            if not self.document_index:
                await self.initialize(init_document_index=True)
                
            # Créer un service WebDAV spécifique à cet espace de travail
            # pour pouvoir charger l'index spécifique à cet espace
            from app.services.webdav_context_manager import WebDAVContextManager
            webdav_context_manager = WebDAVContextManager(self.config)
            await webdav_context_manager.initialize()
            
            # Obtenir un service WebDAV spécifique à cet espace
            space_webdav_service = await webdav_context_manager.get_service_for_context(space_path)
            
            if space_webdav_service:
                logger.info(f"Recherche dans l'index spécifique à l'espace: {space_path}")
                # Créer un DocumentIndex spécifique à cet espace
                space_document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=space_webdav_service
                )
                
                # Initialiser l'index spécifique à l'espace
                try:
                    await space_document_index.initialize()
                    # Effectuer la recherche dans l'index spécifique
                    results = await space_document_index.search(query, limit=limit, filters=filters)
                    return results
                except Exception as e:
                    logger.warning(f"Erreur lors de l'initialisation de l'index spécifique à l'espace {space_path}: {str(e)}")
                    # En cas d'échec, utiliser la méthode de recherche avec filtres
                    logger.info(f"Fallback sur la recherche avec filtres dans l'index global")
            else:
                logger.warning(f"Impossible d'obtenir un service WebDAV pour l'espace {space_path}, utilisation de filtres")
            
            # Ajouter un filtre sur le chemin des documents
            if not filters:
                filters = {}
                
            # S'assurer que le chemin se termine par un /
            normalized_path = space_path
            if not normalized_path.endswith('/'):
                normalized_path += '/'
                
            # Au lieu d'ajouter à une liste existante de path_filter, remplacer pour garantir
            # que seuls les documents de cet espace sont recherchés
            filters["path_filter"] = [normalized_path]
            
            # Effectuer la recherche avec filtres
            logger.debug(f"Recherche dans l'espace {space_path} avec filtres: {filters}")
            results = await self.document_index.search(query, limit=limit, filters=filters)
            return results
        except Exception as e:
            logger.error(f"Erreur lors de la recherche dans l'espace {space_path}: {str(e)}")
            return []
            
    async def rebuild_index(self, space_path: Optional[str] = None):
        """
        Reconstruit l'index pour un espace documentaire spécifique ou global.
        
        Args:
            space_path: Chemin de l'espace documentaire (si None, indexation globale)
        """
        try:
            logger.info(f"Reconstruction de l'index{' pour '+space_path if space_path else ' global'}")
            
            if not self.document_index:
                await self.initialize(init_document_index=True)
                
            if space_path:
                # Indexer uniquement l'espace spécifié
                # Normaliser le chemin
                if not space_path.endswith('/'):
                    space_path += '/'
                    
                # Filtrer pour n'indexer que les documents de cet espace
                result = await self.document_index.rebuild(path_prefix=space_path)
            else:
                # Indexation globale
                result = await self.document_index.rebuild()
                
            return result
        except Exception as e:
            logger.error(f"Erreur lors de la reconstruction de l'index: {str(e)}")
            return False
    
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._cache_cleanup_task:
                self._cache_cleanup_task.cancel()
                try:
                    await self._cache_cleanup_task
                except asyncio.CancelledError:
                    pass
            
            # Ne pas fermer automatiquement le webdav_service car il peut être réutilisé
            # Réinitialiser le client à la place si nécessaire
            if self.webdav_service and hasattr(self.webdav_service, 'http_client') and hasattr(self.webdav_service.http_client, 'is_closed') and self.webdav_service.http_client.is_closed:
                await self.webdav_service.reinitialize_client()
                
            if self.behavior_manager:
                await self.behavior_manager.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture des services: {str(e)}")
            raise

    async def close(self) -> None:
        """Ferme proprement le service"""
        if self._cache_cleanup_task:
            self._cache_cleanup_task.cancel()
            try:
                await self._cache_cleanup_task
            except asyncio.CancelledError:
                pass
            
        # Ne pas fermer le WebDAV service automatiquement
        # pour permettre sa réutilisation
        
    async def get_cached_index(self, key: str) -> Optional[Any]:
        """Récupère un index depuis le cache"""
        async with self._cache_lock:
            if key in self._index_cache:
                timestamp, value = self._index_cache[key]
                if datetime.now() - timestamp <= self._cache_ttl:
                    return value
                del self._index_cache[key]
        return None
        
    async def set_cached_index(self, key: str, value: Any) -> None:
        """Stocke un index dans le cache"""
        async with self._cache_lock:
            self._index_cache[key] = (datetime.now(), value)
            # Déclencher un nettoyage si nécessaire
            if datetime.now() - self._last_cache_cleanup > timedelta(hours=1):
                await self._cleanup_cache() 

# Singleton pour IndexService
_index_service_instance = None
_init_lock = asyncio.Lock()

async def get_index_service(config, force_reload=False):
    """
    Récupère l'instance unique de IndexService, la créant si elle n'existe pas.
    
    Args:
        config: Configuration de l'application
        force_reload: Si True, force la réinitialisation de l'instance
        
    Returns:
        L'instance unique de IndexService
    """
    global _index_service_instance, _init_lock
    
    async with _init_lock:
        if _index_service_instance is None or force_reload:
            logger.info("Création d'une nouvelle instance de IndexService")
            _index_service_instance = IndexService(config)
            try:
                await _index_service_instance.initialize(init_document_index=True)
                logger.info("Service d'index initialisé globalement")
            except Exception as e:
                logger.error(f"Erreur initialisation service d'index global: {str(e)}")
                _index_service_instance = None
                raise
    
    return _index_service_instance

async def close_index_service():
    """Ferme proprement l'instance du service d'index global."""
    global _index_service_instance
    
    if _index_service_instance is not None:
        logger.info("Fermeture du service d'index global")
        try:
            await _index_service_instance.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du service d'index global: {str(e)}")
        finally:
            _index_service_instance = None

# Exporter la fonction get_index_service
__all__ = ['IndexService', 'get_index_service', 'close_index_service'] 