from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os
import sys
from pathlib import Path
import logging
import traceback

# Ajout du chemin racine au PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent))

from app.config import Config
from app.core_llm import AlbertApiClient
from app.services.index_service import IndexService
from app.services.webdav import WebDAVService
from app.matrix_bot.config import logger
from app.services.initialization import with_initialized_services

@dataclass
class AskAlbertRagAction:
    """Action pour interroger Albert en utilisant le RAG (Retrieval Augmented Generation)"""
    query: str
    config: Config
    index_service: Optional[IndexService] = None
    chunks: List[Dict[str, Any]] = None
    webdav_service: Optional[WebDAVService] = None
    room_id: Optional[str] = None
    
    async def initialize(self) -> None:
        """Initialise le service d'index en mode lecture seule"""
        try:
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
                
            if not self.index_service:
                self.index_service = IndexService(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
                # Initialisation sans reconstruction
                try:
                    await self.index_service.initialize(allow_rebuild=False)
                except Exception as e:
                    logger.error(f"Impossible d'initialiser l'index: {str(e)}")
                    # Ne pas lever l'erreur, laisser l'action continuer avec un index vide
                    pass
                
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du RAG: {str(e)}")
            # Ne pas lever l'erreur, laisser l'action continuer
            pass
    
    async def retrieve_relevant_chunks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Récupère les chunks pertinents pour la requête"""
        try:
            if not self.index_service:
                logger.warning("Service d'index non disponible")
                return []
                
            # Recherche des chunks pertinents sans reconstruction
            # Passer l'ID du salon pour utiliser l'espace documentaire associé si disponible
            relevant_chunks = await self.index_service.search(
                self.query, 
                limit=limit,
                room_id=self.room_id
            )
            
            if not relevant_chunks:
                logger.info("Aucun chunk pertinent trouvé dans l'index existant")
                return []
            
            # Formatage des chunks pour le prompt
            self.chunks = [{
                "content": chunk["content"] if isinstance(chunk, dict) else chunk.content,
                "metadata": {
                    "document_name": os.path.basename(
                        chunk["metadata"]["document_path"] if isinstance(chunk, dict) 
                        else chunk.document_path
                    ),
                    **(chunk["metadata"] if isinstance(chunk, dict) else chunk.metadata)
                }
            } for chunk in relevant_chunks]
            
            return self.chunks
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche des chunks: {str(e)}")
            return []
    
    async def generate_response(self) -> str:
        """Génère une réponse basée sur les chunks trouvés"""
        try:
            if not self.chunks:
                return "Je ne peux pas répondre à votre question car aucune information pertinente n'a été trouvée dans l'index existant."
                
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
            
            # Générer la réponse
            messages = [
                {
                    "role": "system",
                    "content": "Vous êtes Albert, l'assistant de l'État français dédié aux questions légales et administratives. "
                              "Votre rôle est d'aider les agents en fournissant des réponses précises et sourcées, "
                              "basées uniquement sur la documentation officielle fournie."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
            
            response = await albert_client.generate(
                model=self.config.albert_model,
                messages=messages
            )
            
            # Formatage de la réponse avec les sources
            formatted_sources = []
            for chunk in self.chunks:
                metadata = chunk["metadata"]
                source_info = f"📄 **{metadata['document_name']}**"
                if "document_title" in metadata:
                    source_info += f"\n   *{metadata['document_title']}*"
                if "section_title" in metadata:
                    source_info += f"\n   Section: {metadata['section_title']}"
                if "page" in metadata:
                    source_info += f"\n   Page: {metadata['page']}"
                if "similarity_score" in metadata:
                    source_info += f"\n   Score de pertinence: {metadata['similarity_score']:.3f}"
                formatted_sources.append(source_info)
            
            sources_text = "\n\n".join(formatted_sources)
            
            final_response = (
                f"🤖 **Réponse d'Albert** :\n\n"
                f"{response}\n\n"
                f"📚 **Sources consultées** :\n\n"
                f"{sources_text}"
            )
            
            return final_response
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de la réponse: {str(e)}")
            return "Une erreur est survenue lors de la génération de la réponse."
    
    @with_initialized_services
    async def execute(self) -> str:
        """Exécute l'action RAG complète"""
        try:
            # Récupération des chunks pertinents
            chunks = await self.retrieve_relevant_chunks()
            if not chunks:
                return "Je ne peux pas répondre à votre question car l'index n'est pas disponible ou aucune information pertinente n'a été trouvée. Veuillez vérifier que l'index a été construit correctement."
            
            # Génération de la réponse
            return await self.generate_response()
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'action RAG: {str(e)}")
            logger.error(traceback.format_exc())
            return "Une erreur est survenue lors de l'exécution de la recherche."
        finally:
            # Nettoyage des ressources
            if self.webdav_service:
                await self.webdav_service.__aexit__(None, None, None)
            if self.index_service:
                await self.index_service.__aexit__(None, None, None)

class RagAction:
    """
    Classe de base pour les actions RAG (Retrieval Augmented Generation).
    
    Cette classe définit l'interface commune pour toutes les actions RAG
    qui combinent la recherche d'informations et la génération de réponses.
    """
    
    def __init__(self, config: Any):
        """
        Initialise une nouvelle action RAG.
        
        Args:
            config: Configuration de l'application
        """
        self.config = config
        self.name = self.__class__.__name__
    
    @with_initialized_services
    async def execute(self, 
                     query: str, 
                     context: Dict[str, Any] = None, 
                     options: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Exécute l'action RAG sur une requête donnée.
        
        Args:
            query: La requête utilisateur
            context: Contexte additionnel pour l'exécution
            options: Options spécifiques pour l'exécution
            
        Returns:
            Dictionnaire contenant le résultat de l'action
        """
        if context is None:
            context = {}
            
        if options is None:
            options = {}
            
        try:
            # Extraire l'identifiant room_id du contexte si disponible
            room_id = context.get("room_id")
            
            # Ajouter l'identifiant aux options
            if room_id:
                options["room_id"] = room_id
            
            # Exécuter l'action spécifique
            result = await self._execute_action(query, context, options)
            
            # Ajouter des métadonnées standard
            result["action_name"] = self.name
            result["success"] = True
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'action {self.name}: {str(e)}")
            logger.error(traceback.format_exc())
            
            # Retourner une erreur structurée
            return {
                "action_name": self.name,
                "success": False,
                "error": str(e),
                "response": f"Une erreur est survenue lors du traitement de votre demande: {str(e)}"
            }
    
    async def _execute_action(self, 
                             query: str, 
                             context: Dict[str, Any], 
                             options: Dict[str, Any]) -> Dict[str, Any]:
        """
        Méthode abstraite à implémenter par les sous-classes.
        
        Args:
            query: La requête utilisateur
            context: Contexte additionnel pour l'exécution
            options: Options spécifiques pour l'exécution
            
        Returns:
            Dictionnaire contenant le résultat de l'action
        """
        raise NotImplementedError("Cette méthode doit être implémentée par les sous-classes") 