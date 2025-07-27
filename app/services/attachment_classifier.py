from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import os
from pathlib import Path
import asyncio

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.embedding_service import EmbeddingService

@dataclass
class AttachmentClassificationResult:
    """Résultat de la classification d'une pièce jointe"""
    suggested_path: str
    confidence: float
    alternative_paths: List[Tuple[str, float]]
    categories: List[Tuple[str, float]]
    metadata: Dict[str, Any]

class AttachmentClassifier:
    """Service de classification légère des pièces jointes"""
    
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

    # Taille maximale de chunk pour l'API d'embeddings (en caractères)
    MAX_CHUNK_SIZE = 8000
    
    def __init__(self, config: Config):
        self.config = config
        self.webdav_service = None
        self.embedding_service = None
        
    async def initialize(self) -> None:
        """Initialise les services nécessaires"""
        if not self.webdav_service:
            self.webdav_service = WebDAVService(self.config)
        if not self.embedding_service:
            self.embedding_service = EmbeddingService(self.config)
            
    async def classify_file(self, file_name: str, file_content: bytes) -> AttachmentClassificationResult:
        """Classifie un fichier sans utiliser l'index FAISS"""
        try:
            # Extraire le texte du fichier
            text = self._extract_text(file_content)
            
            # Découper en chunks plus petits pour éviter l'erreur 413
            chunks = self._split_text(text)
            
            # Obtenir les embeddings des chunks
            chunk_embeddings = []
            for chunk in chunks[:5]:  # Limiter à 5 chunks pour la rapidité
                try:
                    embedding = await self.embedding_service.get_embedding(chunk)
                    if embedding is not None:
                        chunk_embeddings.append(embedding)
                except Exception as e:
                    logger.warning(f"Erreur d'embedding pour un chunk: {str(e)}")
                    continue
            
            if not chunk_embeddings:
                raise ValueError("Impossible d'obtenir des embeddings valides")
            
            # Analyser le contenu pour déterminer les catégories
            categories = self._analyze_categories(text)
            
            # Déterminer le meilleur chemin
            suggested_path = self._determine_path(file_name, categories)
            
            # Générer des alternatives
            alternatives = self._generate_alternatives(file_name, categories)
            
            return AttachmentClassificationResult(
                suggested_path=suggested_path,
                confidence=0.85,  # Valeur par défaut
                alternative_paths=alternatives,
                categories=categories,
                metadata={
                    "file_name": file_name,
                    "size": len(file_content)
                }
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la classification: {str(e)}")
            raise
            
    def _extract_text(self, content: bytes) -> str:
        """Extrait le texte du contenu binaire"""
        # Pour l'instant, on suppose que c'est du texte
        try:
            return content.decode('utf-8')
        except:
            return ""
            
    def _split_text(self, text: str) -> List[str]:
        """Découpe le texte en chunks de taille acceptable"""
        chunks = []
        current_chunk = ""
        
        for line in text.split('\n'):
            if len(current_chunk) + len(line) > self.MAX_CHUNK_SIZE:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += "\n" + line if current_chunk else line
                
        if current_chunk:
            chunks.append(current_chunk)
            
        return chunks
            
    def _analyze_categories(self, text: str) -> List[Tuple[str, float]]:
        """Analyse le texte pour déterminer les catégories"""
        scores = {}
        text_lower = text.lower()
        
        for category, keywords in self.CATEGORIES.items():
            score = 0
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    score += 1
            if score > 0:
                scores[category] = score / len(keywords)
                
        # Trier par score décroissant
        sorted_categories = sorted(
            scores.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        
        return sorted_categories[:3]  # Retourner les 3 meilleures catégories
            
    def _determine_path(self, file_name: str, categories: List[Tuple[str, float]]) -> str:
        """Détermine le meilleur chemin pour le fichier"""
        if not categories:
            return os.path.join("non-classe", file_name)
            
        best_category = categories[0][0]
        year = "2024"  # À améliorer avec la date du fichier
        
        return os.path.join(best_category, year, file_name)
            
    def _generate_alternatives(
        self, 
        file_name: str, 
        categories: List[Tuple[str, float]]
    ) -> List[Tuple[str, float]]:
        """Génère des chemins alternatifs"""
        alternatives = []
        
        # Utiliser les autres catégories
        for category, score in categories[1:]:
            path = os.path.join(category, "2024", file_name)
            alternatives.append((path, score))
            
        # Ajouter un chemin générique
        alternatives.append((
            os.path.join("documents", file_name),
            0.5
        ))
        
        return alternatives
            
    async def move_file(self, source_path: str, target_path: str) -> None:
        """Déplace un fichier vers son nouvel emplacement"""
        try:
            # Créer les dossiers nécessaires
            path_parts = Path(target_path).parts
            current_path = ""
            for part in path_parts[:-1]:
                current_path = os.path.join(current_path, part)
                await self.webdav_service.create_directory(current_path)
            
            # Lire le fichier source
            content = await self.webdav_service.read_document(source_path)
            
            # Écrire dans le nouvel emplacement
            await self.webdav_service.write_file(target_path, content)
            
            # Vérifier que le fichier a bien été écrit
            try:
                await self.webdav_service.read_document(target_path)
                # Si la lecture réussit, on peut supprimer l'original
                url = self.webdav_service._get_url(source_path)
                response = self.webdav_service.session.delete(url)
                response.raise_for_status()
                logger.info(f"Fichier source supprimé: {source_path}")
            except Exception as e:
                logger.error(f"Erreur lors de la vérification/suppression: {str(e)}")
                raise
                
        except Exception as e:
            logger.error(f"Erreur lors du déplacement du fichier: {str(e)}")
            raise
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.webdav_service:
            await self.webdav_service.close() 