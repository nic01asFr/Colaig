from typing import List, Dict, Optional
import numpy as np
from pathlib import Path
import json
import asyncio
from datetime import datetime, timedelta
import os
from mistralai import Mistral
import time

from matrix_bot.config import logger
from config import Config

class EmbeddingCache:
    """Gère le cache des embeddings"""
    
    def __init__(self):
        self.cache: Dict[str, List[float]] = {}
        self.last_updated: Dict[str, datetime] = {}
        
    def get(self, text: str) -> List[float]:
        """Récupère un embedding du cache"""
        return self.cache.get(text)
        
    def set(self, text: str, embedding: List[float]):
        """Ajoute un embedding au cache"""
        self.cache[text] = embedding
        self.last_updated[text] = datetime.now()
        
    def save(self, path: str):
        """Sauvegarde le cache"""
        data = {
            "cache": self.cache,
            "last_updated": {k: v.isoformat() for k, v in self.last_updated.items()}
        }
        with open(path, 'w') as f:
            json.dump(data, f)
            
    def load(self, path: str):
        """Charge le cache"""
        if not os.path.exists(path):
            return
            
        with open(path, 'r') as f:
            data = json.load(f)
            
        self.cache = data["cache"]
        self.last_updated = {
            k: datetime.fromisoformat(v) 
            for k, v in data["last_updated"].items()
        }

class EmbeddingService:
    """Service de gestion des embeddings via Mistral"""
    
    def __init__(self, config: Config):
        self.config = config
        self.cache = EmbeddingCache()
        self.client = Mistral(
            api_key=config.mistral_api_token
        )
        # La dimension est maintenant configurable
        self._embedding_dimension = None
        
    @property
    def embedding_dimension(self) -> int:
        """Retourne la dimension des embeddings"""
        if self._embedding_dimension is None:
            # Par défaut, utiliser la dimension de Mistral
            self._embedding_dimension = 1024
        return self._embedding_dimension
        
    @embedding_dimension.setter
    def embedding_dimension(self, value: int):
        """Définit la dimension des embeddings"""
        if value <= 0:
            raise ValueError("La dimension doit être positive")
        self._embedding_dimension = value
        logger.info(f"Dimension des embeddings mise à jour: {value}")
        
    def resize_embedding(self, embedding: np.ndarray, target_dim: int) -> np.ndarray:
        """Redimensionne un embedding à la dimension cible"""
        current_dim = len(embedding)
        if current_dim == target_dim:
            return embedding
            
        if current_dim > target_dim:
            # Réduire la dimension en prenant les premiers composants
            logger.info(f"Réduction de la dimension de l'embedding: {current_dim} -> {target_dim}")
            return embedding[:target_dim]
        else:
            # Augmenter la dimension en ajoutant des zéros
            logger.info(f"Augmentation de la dimension de l'embedding: {current_dim} -> {target_dim}")
            padded = np.zeros(target_dim)
            padded[:current_dim] = embedding
            return padded
            
    async def get_embedding(self, text: str) -> np.ndarray:
        """Récupère l'embedding d'un texte via Mistral"""
        # Vérifier le cache
        cached = self.cache.get(text)
        if cached is not None:
            embedding = np.array(cached)
            # Redimensionner si nécessaire
            if len(embedding) != self.embedding_dimension:
                embedding = self.resize_embedding(embedding, self.embedding_dimension)
            return embedding
            
        # Obtenir depuis l'API Mistral
        max_retries = 7
        base_delay = 3
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = base_delay * (2 ** attempt)
                    logger.info(f"Attente de {delay} secondes avant la tentative {attempt + 1}/{max_retries}")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(base_delay)
                    
                response = self.client.embeddings.create(
                    model="mistral-embed",
                    inputs=[text]
                )
                embedding = np.array(response.data[0].embedding)
                
                # Redimensionner si nécessaire
                if len(embedding) != self.embedding_dimension:
                    embedding = self.resize_embedding(embedding, self.embedding_dimension)
                
                # Mettre en cache
                self.cache.set(text, embedding.tolist())
                logger.info("Embedding généré et mis en cache avec succès")
                
                return embedding
                
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Limite de taux atteinte (tentative {attempt + 1}/{max_retries})")
                    continue
                logger.error(f"Erreur lors de la génération de l'embedding Mistral: {str(e)}")
                raise
    
    async def get_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """Récupère les embeddings pour une liste de textes via Mistral"""
        results = []
        for i, text in enumerate(texts):
            # Ajouter un délai entre les requêtes batch pour éviter les limites de taux
            if i > 0:
                await asyncio.sleep(2)  # 2 secondes de délai entre chaque requête batch
            embedding = await self.get_embedding(text)
            results.append(embedding)
        return results
    
    def save_cache(self, path: str):
        """Sauvegarde le cache"""
        self.cache.save(path)
        
    def load_cache(self, path: str):
        """Charge le cache"""
        self.cache.load(path)
        
    def close(self):
        """Ferme les connexions"""
        pass  # Pas nécessaire pour le client Mistral 