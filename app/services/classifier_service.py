from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import asyncio

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.document_index import DocumentIndex
from services.embedding_service import EmbeddingService

@dataclass
class ClassificationResult:
    """Résultat de la classification d'un fichier"""
    suggested_path: str
    confidence: float
    alternative_paths: List[Tuple[str, float]]
    categories: List[Tuple[str, float]]
    metadata: Dict[str, Any]

class ClassifierService:
    """Service de classification intelligente des fichiers"""
    
    # Catégories prédéfinies
    CATEGORIES = {
        "finance": ["budget", "facture", "comptabilité", "dépense", "recette"],
        "rh": ["cv", "contrat", "formation", "évaluation", "congé"],
        "technique": ["rapport", "documentation", "spécification", "architecture"],
        "administratif": ["procédure", "règlement", "note", "circulaire"],
        "communication": ["présentation", "communiqué", "newsletter"],
        "projet": ["planning", "suivi", "réunion", "compte-rendu"],
        "juridique": ["contrat", "convention", "accord", "règlement"]
    }
    
    def __init__(self, config: Config):
        self.config = config
        self.webdav_service = None
        self.embedding_service = None
        self.document_index = None
        
    async def initialize(self) -> None:
        """Initialise les services nécessaires"""
        if not self.webdav_service:
            self.webdav_service = WebDAVService(self.config)
        if not self.embedding_service:
            self.embedding_service = EmbeddingService(self.config)
        if not self.document_index:
            self.document_index = DocumentIndex(
                config=self.config,
                webdav_service=self.webdav_service
            )
        await self.document_index.initialize()
        
    async def classify_file(self, file_path: str, content: str) -> ClassificationResult:
        """Analyse et classifie un fichier"""
        try:
            # Extraction des caractéristiques
            file_name = Path(file_path).name
            file_ext = Path(file_path).suffix.lower()
            
            # Analyse sémantique du contenu
            content_embedding = await self.embedding_service.get_embedding(content)
            
            # Calcul des scores pour chaque catégorie
            category_scores = []
            for category, keywords in self.CATEGORIES.items():
                # Combiner les mots-clés en une seule chaîne pour l'embedding
                category_text = " ".join(keywords)
                category_embedding = await self.embedding_service.get_embedding(category_text)
                
                # Calculer la similarité cosinus
                similarity = self._cosine_similarity(content_embedding, category_embedding)
                category_scores.append((category, float(similarity)))
            
            # Trier les catégories par score
            category_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Déterminer le meilleur chemin
            best_category = category_scores[0][0]
            best_score = category_scores[0][1]
            
            # Construire le chemin suggéré
            year = datetime.now().year
            month = datetime.now().strftime("%m")
            suggested_path = f"{best_category}/{year}/{month}/{file_name}"
            
            # Générer des chemins alternatifs
            alternative_paths = []
            for category, score in category_scores[1:3]:  # Top 2 alternatives
                alt_path = f"{category}/{year}/{month}/{file_name}"
                alternative_paths.append((alt_path, float(score)))
            
            # Collecter les métadonnées
            metadata = {
                "original_name": file_name,
                "extension": file_ext,
                "size": len(content),
                "classified_at": datetime.now().isoformat(),
                "mime_type": self._guess_mime_type(file_ext)
            }
            
            return ClassificationResult(
                suggested_path=suggested_path,
                confidence=best_score,
                alternative_paths=alternative_paths,
                categories=category_scores[:3],  # Top 3 catégories
                metadata=metadata
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la classification: {str(e)}")
            raise
            
    def _cosine_similarity(self, embedding1, embedding2) -> float:
        """Calcule la similarité cosinus entre deux embeddings"""
        import numpy as np
        return np.dot(embedding1, embedding2) / (
            np.linalg.norm(embedding1) * np.linalg.norm(embedding2)
        )
        
    def _guess_mime_type(self, extension: str) -> str:
        """Devine le type MIME basé sur l'extension"""
        mime_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xml': 'application/xml',
            '.html': 'text/html'
        }
        return mime_types.get(extension.lower(), 'application/octet-stream')
        
    async def move_file(self, source_path: str, target_path: str) -> None:
        """Déplace un fichier vers son nouvel emplacement"""
        lock_service = None
        try:
            # Validation des chemins
            if not self.webdav_service._validate_path(source_path):
                raise ValueError(f"Chemin source invalide: {source_path}")
            if not self.webdav_service._validate_path(target_path):
                raise ValueError(f"Chemin cible invalide: {target_path}")
            
            # Initialisation du service de verrouillage
            from services.lock_service import LockService
            lock_service = LockService(self.config, self.webdav_service)
            await lock_service.initialize()
            
            # Tentative d'acquisition des verrous avec retry
            max_retries = 3
            retry_delay = 1
            
            for attempt in range(max_retries):
                # Tentative d'acquisition du verrou source
                if not await lock_service.acquire_lock(source_path, "classifier", "move_source"):
                    if attempt == max_retries - 1:
                        raise ValueError(f"Impossible de verrouiller le fichier source après {max_retries} tentatives: {source_path}")
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} d'acquisition du verrou source")
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
                
                try:
                    # Tentative d'acquisition du verrou cible
                    if not await lock_service.acquire_lock(target_path, "classifier", "move_target"):
                        if attempt == max_retries - 1:
                            raise ValueError(f"Impossible de verrouiller le fichier cible après {max_retries} tentatives: {target_path}")
                        logger.warning(f"Tentative {attempt + 1}/{max_retries} d'acquisition du verrou cible")
                        await lock_service.release_lock(source_path, "classifier")
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    
                    try:
                        # Lecture du fichier source
                        content = await self.webdav_service.read_document(source_path)
                        
                        # Création des dossiers nécessaires
                        path_parts = Path(target_path).parts
                        current_path = ""
                        for part in path_parts[:-1]:
                            current_path = os.path.join(current_path, part)
                            await self.webdav_service.create_directory(current_path)
                        
                        # Écriture dans le nouvel emplacement
                        await self.webdav_service.write_file(target_path, content)
                        
                        # Vérification que le fichier a bien été écrit
                        try:
                            await self.webdav_service.read_document(target_path)
                            # Si la lecture réussit, on peut supprimer l'original
                            url = self.webdav_service._get_url(source_path)
                            response = self.webdav_service.session.delete(url)
                            response.raise_for_status()
                            logger.info(f"Fichier source supprimé: {source_path}")
                            return  # Succès !
                        except Exception as e:
                            logger.error(f"Erreur lors de la vérification/suppression: {str(e)}")
                            # Tentative de nettoyage du fichier cible en cas d'erreur
                            try:
                                url = self.webdav_service._get_url(target_path)
                                self.webdav_service.session.delete(url)
                            except Exception as cleanup_error:
                                logger.error(f"Erreur lors du nettoyage du fichier cible: {str(cleanup_error)}")
                            raise ValueError(f"Le fichier a été copié mais n'a pas pu être supprimé: {str(e)}")
                    
                    finally:
                        # Libération du verrou cible
                        await lock_service.release_lock(target_path, "classifier")
                
                finally:
                    # Libération du verrou source
                    await lock_service.release_lock(source_path, "classifier")
                
            raise ValueError("Échec du déplacement du fichier après plusieurs tentatives")
            
        except Exception as e:
            logger.error(f"Erreur lors du déplacement du fichier: {str(e)}")
            raise
            
        finally:
            if lock_service:
                await lock_service.__aexit__(None, None, None)
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.document_index:
            await self.document_index.__aexit__(exc_type, exc_val, exc_tb)

    def _validate_metadata(self, metadata: Dict[str, Any]) -> bool:
        """Valide les métadonnées d'un document"""
        try:
            # Vérification des champs requis
            required_fields = ['title', 'mime_type', 'size']
            if not all(field in metadata for field in required_fields):
                logger.warning("Champs requis manquants dans les métadonnées")
                return False
            
            # Validation du type MIME
            if not self.webdav_service._is_supported_mime_type(metadata['mime_type']):
                logger.warning(f"Type MIME non supporté: {metadata['mime_type']}")
                return False
            
            # Validation de la taille
            max_size = 100 * 1024 * 1024  # 100 MB
            if not isinstance(metadata['size'], (int, float)) or metadata['size'] <= 0 or metadata['size'] > max_size:
                logger.warning(f"Taille de fichier invalide: {metadata['size']}")
                return False
            
            # Validation du titre
            if not isinstance(metadata['title'], str) or len(metadata['title']) > 255:
                logger.warning("Titre invalide")
                return False
            
            # Validation des dates si présentes
            date_fields = ['created_at', 'modified_at']
            for field in date_fields:
                if field in metadata:
                    try:
                        datetime.fromisoformat(metadata[field])
                    except (ValueError, TypeError):
                        logger.warning(f"Format de date invalide pour {field}")
                        return False
            
            # Validation des tags si présents
            if 'tags' in metadata:
                if not isinstance(metadata['tags'], list):
                    logger.warning("Format de tags invalide")
                    return False
                if any(not isinstance(tag, str) for tag in metadata['tags']):
                    logger.warning("Tags invalides")
                    return False
                if len(metadata['tags']) > 50:  # Limite arbitraire
                    logger.warning("Trop de tags")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de la validation des métadonnées: {str(e)}")
            return False 