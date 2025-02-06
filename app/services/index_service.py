from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.document_index import DocumentIndex, DocumentChunk, FAISSIndex

@dataclass
class IndexService:
    """Service dédié à la gestion de l'index"""
    config: Config
    webdav_service: Optional[WebDAVService] = None
    document_index: Optional[DocumentIndex] = None
    
    async def initialize(self) -> None:
        """Initialise les services nécessaires"""
        try:
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
            if not self.document_index:
                self.document_index = DocumentIndex(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
            await self.document_index.initialize()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du service d'indexation: {str(e)}")
            raise
    
    async def get_status(self) -> Dict[str, Any]:
        """Retourne l'état actuel de l'index"""
        try:
            await self.initialize()
            
            total_docs = len(set(chunk.document_path 
                for chunk in self.document_index.faiss_index.document_map.values()))
            total_chunks = self.document_index.faiss_index.index.ntotal
            
            return {
                "total_documents": total_docs,
                "total_chunks": total_chunks,
                "embedding_dimension": self.document_index.embedding_service.embedding_dimension,
                "is_fresh": await self.document_index.verify_index_freshness(),
                "last_update": max((chunk.last_updated for chunk in self.document_index.faiss_index.document_map.values()), default=None)
            }
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
            raise
    
    async def rebuild(self) -> None:
        """Reconstruit l'index complètement"""
        try:
            await self.initialize()
            await self.document_index.build_index()
            await self.document_index.save_index()
        except Exception as e:
            logger.error(f"Erreur lors de la reconstruction de l'index: {str(e)}")
            raise
    
    async def verify(self) -> bool:
        """Vérifie la fraîcheur de l'index"""
        try:
            await self.initialize()
            is_fresh, _, _ = await self.document_index.verify_index_freshness()
            return is_fresh
        except Exception as e:
            logger.error(f"Erreur lors de la vérification de l'index: {str(e)}")
            return False
    
    async def update(self) -> None:
        """Met à jour l'index en ajoutant uniquement les documents manquants"""
        try:
            await self.initialize()
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
        """Nettoie l'index"""
        try:
            await self.initialize()
            self.document_index.faiss_index = FAISSIndex(
                dimension=self.document_index.embedding_service.embedding_dimension
            )
            await self.document_index.save_index()
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage de l'index: {str(e)}")
            raise
    
    async def search(self, query: str, limit: int = 5) -> List[DocumentChunk]:
        """Effectue une recherche dans l'index"""
        try:
            await self.initialize()
            return await self.document_index.search(query, limit)
        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {str(e)}")
            return []
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.document_index:
            await self.document_index.__aexit__(exc_type, exc_val, exc_tb) 