from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
from functools import lru_cache

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.document_index import DocumentIndex, DocumentChunk, FAISSIndex
from services.behavior_index import BehaviorIndex, BehaviorChunk

@dataclass
class IndexService:
    """Service dédié à la gestion de l'index"""
    
    def __init__(self, config: Config, webdav_service: Optional[WebDAVService] = None):
        self.config = config
        self.webdav_service = webdav_service
        self.document_index = None
        self.behavior_index = None
        self._initialized = False
        self._current_dimension = None
        self._index_cache = {}
        self._cache_lock = asyncio.Lock()
        
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
            
    async def initialize(self, allow_rebuild: bool = False) -> None:
        """Initialise les services nécessaires"""
        if self._initialized:
            return
            
        try:
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
                
            # Initialiser l'index de documents
            if not self.document_index:
                self.document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
                try:
                    await self.document_index.initialize(allow_rebuild=allow_rebuild)
                except Exception as e:
                    logger.error(f"Erreur initialisation index documents: {str(e)}")
                    
            # Initialiser l'index comportemental
            if not self.behavior_index:
                self.behavior_index = BehaviorIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
                try:
                    await self.behavior_index.initialize()
                except Exception as e:
                    logger.error(f"Erreur initialisation index comportemental: {str(e)}")
                    
            self._initialized = True
                
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des index: {str(e)}")
            raise
            
    async def _ensure_initialized(self, allow_rebuild: bool = False) -> None:
        """S'assure que le service est initialisé"""
        if not self._initialized:
            await self.initialize(allow_rebuild)
    
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
            # Forcer la réinitialisation
            self._initialized = False
            self.document_index = None
            
            # Initialiser les services
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
            if not self.document_index:
                self.document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
            
            # Nettoyer le cache des embeddings
            if self.document_index.embedding_service:
                await self.document_index.embedding_service.cache.clear_old_entries(
                    timedelta(hours=0)  # Tout nettoyer
                )
            
            # Forcer la dimension correcte pour le nouvel index
            self.document_index.faiss_index = FAISSIndex(
                dimension=self.document_index.embedding_service.embedding_dimension
            )
            
            # Construire et sauvegarder l'index
            await self.document_index.build_index()
            await self.document_index.save_index()
            self._initialized = True
            self._current_dimension = self.document_index.embedding_service.embedding_dimension
            logger.info("Index reconstruit avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de la reconstruction de l'index: {str(e)}")
            raise
    
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
                        "data": await self.behavior_index.load_index(),
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
            
    async def _cleanup_cache(self) -> None:
        """Nettoie les entrées du cache selon les règles définies"""
        async with self._cache_lock:
            now = datetime.now()
            to_remove = []
            
            for index_type, cache_entry in self._index_cache.items():
                # Supprimer les entrées non utilisées depuis plus d'une heure
                if (now - cache_entry["last_access"]) > timedelta(hours=1):
                    to_remove.append(index_type)
                    
            for index_type in to_remove:
                del self._index_cache[index_type]
                
    @lru_cache(maxsize=100)
    async def _get_optimized_search_results(
        self,
        query: str,
        index_type: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Cache les résultats de recherche fréquents"""
        if index_type == "behavior":
            return await self.behavior_index.search(query, limit=limit)
        elif index_type == "document":
            return await self.document_index.search(query, limit=limit)
        return []
    
    async def search(
        self,
        query: str,
        behavior_type: Optional[str] = None,
        include_behavior: bool = True,
        include_documents: bool = True,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Recherche optimisée dans les index avec cache"""
        try:
            results = []
            
            # Recherche dans l'index comportemental si demandé
            if include_behavior and self.behavior_index:
                # Vérifier le cache
                cached_index = await self._get_cached_index("behavior")
                if not cached_index:
                    await self._load_index_to_cache("behavior")
                
                # Recherche avec cache des résultats
                behavior_chunks = await self._get_optimized_search_results(
                    query,
                    "behavior",
                    limit
                )
                
                for chunk in behavior_chunks:
                    if isinstance(chunk, dict):
                        chunk["source_type"] = "behavior"
                        results.append(chunk)
                    else:
                        results.append({
                            "content": chunk.content,
                            "metadata": chunk.metadata,
                            "behavior_type": chunk.behavior_type,
                            "priority": chunk.priority,
                            "source_type": "behavior"
                        })
            
            # Recherche dans l'index de documents si demandé
            if include_documents and self.document_index:
                # Vérifier le cache
                cached_index = await self._get_cached_index("document")
                if not cached_index:
                    await self._load_index_to_cache("document")
                
                # Recherche avec cache des résultats
                doc_chunks = await self._get_optimized_search_results(
                    query,
                    "document",
                    limit
                )
                
                for chunk in doc_chunks:
                    if isinstance(chunk, dict):
                        chunk["source_type"] = "document"
                        results.append(chunk)
                    else:
                        results.append({
                            "content": chunk.content,
                            "metadata": chunk.metadata,
                            "source_type": "document"
                        })
            
            # Trier les résultats combinés
            results.sort(
                key=lambda x: (
                    x.get("priority", 0.0) if x.get("source_type") == "behavior" else 0.0,
                    x.get("metadata", {}).get("score", 0.0)
                ),
                reverse=True
            )
            
            # Nettoyer le cache périodiquement
            asyncio.create_task(self._cleanup_cache())
            
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {str(e)}")
            return []
    
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.webdav_service:
                await self.webdav_service.__aexit__(None, None, None)
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture des services: {str(e)}")
            raise 