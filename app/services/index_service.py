from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.document_index import DocumentIndex, DocumentChunk, FAISSIndex

@dataclass
class IndexService:
    """Service dédié à la gestion de l'index"""
    
    def __init__(self, config: Config, webdav_service: Optional[WebDAVService] = None):
        self.config = config
        self.webdav_service = webdav_service
        self.document_index = None
        self._initialized = False
        self._current_dimension = None
        
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
            if not self.document_index:
                self.document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
            
            try:
                # Tentative de chargement de l'index existant
                await self.document_index.load_index()
                
                # Vérifier la dimension
                current_dim = self.document_index.faiss_index.dimension
                target_dim = self.document_index.embedding_service.embedding_dimension
                
                if current_dim != target_dim:
                    if allow_rebuild:
                        await self._handle_dimension_mismatch(current_dim, target_dim)
                    else:
                        raise ValueError(
                            f"Dimension incorrecte ({current_dim} != {target_dim}) "
                            "et reconstruction non autorisée"
                        )
                
                self._current_dimension = target_dim
                logger.info("Index chargé avec succès")
                self._initialized = True
                
            except FileNotFoundError:
                if allow_rebuild:
                    logger.warning("Index non trouvé, création d'un nouvel index...")
                    await self.document_index.build_index()
                    await self.document_index.save_index()
                    self._current_dimension = self.document_index.embedding_service.embedding_dimension
                    logger.info("Index créé avec succès")
                    self._initialized = True
                else:
                    logger.error("Index non trouvé et reconstruction non autorisée")
                    raise
            except Exception as e:
                if allow_rebuild:
                    logger.warning(f"Erreur de chargement: {str(e)}, reconstruction...")
                    await self.document_index.build_index()
                    await self.document_index.save_index()
                    self._current_dimension = self.document_index.embedding_service.embedding_dimension
                    logger.info("Index reconstruit avec succès")
                    self._initialized = True
                else:
                    logger.error(f"Impossible de charger l'index et reconstruction non autorisée: {str(e)}")
                    raise
                    
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du service d'indexation: {str(e)}")
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
                self.document_index.embedding_service.cache.clear_old_entries(
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
            self.document_index.embedding_service.cache.clear_old_entries(
                timedelta(hours=0)  # Tout nettoyer
            )
            
            await self.document_index.save_index()
            logger.info("Index et cache nettoyés avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage de l'index: {str(e)}")
            raise
    
    async def search(self, query: str, limit: int = 5) -> List[DocumentChunk]:
        """Effectue une recherche dans l'index existant uniquement
        
        Args:
            query: Requête de recherche
            limit: Nombre maximum de résultats
            
        Returns:
            Liste des chunks les plus pertinents ou liste vide si l'index n'existe pas
        """
        try:
            # Vérifier si l'index existe sans initialisation
            if not self._initialized:
                try:
                    if not self.webdav_service:
                        self.webdav_service = WebDAVService(self.config)
                    if not self.document_index:
                        self.document_index = DocumentIndex(
                            config=self.config,
                            webdav_service=self.webdav_service
                        )
                    
                    # Tenter de charger l'index sans reconstruction
                    await self.document_index.load_index()
                    self._initialized = True
                except FileNotFoundError:
                    logger.warning("Index non disponible pour la recherche")
                    return []
                except Exception as e:
                    logger.error(f"Erreur lors du chargement de l'index: {str(e)}")
                    return []
            
            # Vérifier que l'index est initialisé et non vide
            if not self._initialized or self.document_index.faiss_index.index.ntotal == 0:
                logger.warning("Index non initialisé ou vide")
                return []
                
            # Effectuer la recherche
            try:
                return await self.document_index.search(query, limit)
            except Exception as e:
                logger.error(f"Erreur lors de la recherche: {str(e)}")
                return []
                
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la recherche: {str(e)}")
            return []
    
    async def __aenter__(self):
        # Si nous sommes en mode rebuild, ne pas initialiser
        if not self._initialized and hasattr(self, '_is_rebuilding') and self._is_rebuilding:
            return self
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.document_index and self.document_index.embedding_service:
            await self.document_index.embedding_service.close() 