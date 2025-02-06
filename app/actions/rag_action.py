from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os
import sys
from pathlib import Path

# Ajout du chemin racine au PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent))

from config import Config
from core_llm import AlbertApiClient
from services.document_index import DocumentIndex
from services.webdav import WebDAVService
from matrix_bot.config import logger

@dataclass
class AskAlbertRagAction:
    """Action pour interroger Albert en utilisant le RAG (Retrieval Augmented Generation)"""
    query: str
    config: Config
    document_index: Optional[DocumentIndex] = None
    chunks: List[Dict[str, Any]] = None
    webdav_service: Optional[WebDAVService] = None
    
    async def initialize(self) -> None:
        """Initialise l'index de documents si nécessaire"""
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
            logger.error(f"Erreur lors de l'initialisation du RAG: {str(e)}")
            raise
    
    async def retrieve_relevant_chunks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Récupère les chunks pertinents pour la requête"""
        try:
            # Recherche des chunks pertinents
            relevant_chunks = await self.document_index.search(self.query, limit=limit)
            
            # Formatage des chunks pour le prompt
            self.chunks = [{
                "id": chunk.id,
                "content": chunk.content,
                "metadata": {
                    "document_name": os.path.basename(chunk.document_path),
                    **chunk.metadata
                }
            } for chunk in relevant_chunks]
            
            return self.chunks
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche des chunks: {str(e)}")
            return []
    
    async def generate_response(self) -> str:
        """Génère une réponse basée sur les chunks trouvés"""
        try:
            # Initialisation du client Albert
            albert_client = AlbertApiClient(
                base_url=self.config.albert_api_url,
                api_key=self.config.albert_api_token
            )
            
            # Formatage du prompt avec le contexte
            prompt = albert_client.format_albert_template(
                query=self.query,
                chunks=self.chunks
            )
            
            # Génération de la réponse
            response = await albert_client.generate(
                model=self.config.albert_model,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Formatage de la réponse avec les sources
            sources = set(chunk["metadata"]["document_name"] for chunk in self.chunks)
            sources_text = "\n".join(f"- {source}" for source in sources)
            
            return f"{response}\n\nSources consultées:\n{sources_text}"
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de la réponse: {str(e)}")
            raise
    
    async def execute(self) -> str:
        """Exécute l'action RAG complète"""
        try:
            # Initialisation
            await self.initialize()
            
            # Récupération des chunks pertinents
            chunks = await self.retrieve_relevant_chunks()
            if not chunks:
                return "Aucune information pertinente n'a été trouvée dans la documentation pour votre question."
            
            # Génération de la réponse
            return await self.generate_response()
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'action RAG: {str(e)}")
            raise
        finally:
            # Nettoyage des ressources
            if self.webdav_service:
                await self.webdav_service.__aexit__(None, None, None) 