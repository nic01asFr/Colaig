from typing import List, Dict, Optional
import numpy as np
from pathlib import Path
import json
import asyncio
from datetime import datetime, timedelta
import os
import httpx
import time
import hashlib

from matrix_bot.config import logger
from config import Config

class EmbeddingCache:
    """Gère le cache des embeddings avec une taille maximale et une politique LRU"""
    
    def __init__(self, max_size: int = 10000):
        self.cache: Dict[str, List[float]] = {}
        self.last_accessed: Dict[str, datetime] = {}
        self.max_size = max_size
        self._lock = asyncio.Lock()
        
    def _generate_key(self, text: str) -> str:
        """Génère une clé de cache unique pour un texte"""
        return hashlib.sha256(text.encode()).hexdigest()
        
    async def get(self, text: str) -> Optional[List[float]]:
        """Récupère un embedding du cache avec mise à jour du timestamp"""
        key = self._generate_key(text)
        async with self._lock:
            if key in self.cache:
                self.last_accessed[key] = datetime.now()
                return self.cache[key]
            return None
        
    async def set(self, text: str, embedding: List[float]):
        """Ajoute un embedding au cache avec politique LRU"""
        key = self._generate_key(text)
        async with self._lock:
            if len(self.cache) >= self.max_size:
                # Supprimer l'élément le moins récemment utilisé
                oldest_key = min(self.last_accessed.items(), key=lambda x: x[1])[0]
                del self.cache[oldest_key]
                del self.last_accessed[oldest_key]
            
            self.cache[key] = embedding
            self.last_accessed[key] = datetime.now()
        
    async def clear_old_entries(self, max_age: timedelta):
        """Supprime les entrées plus anciennes que max_age"""
        now = datetime.now()
        async with self._lock:
            old_keys = [k for k, v in self.last_accessed.items() if (now - v) > max_age]
            for k in old_keys:
                del self.cache[k]
                del self.last_accessed[k]
            
    def save(self, path: str):
        """Sauvegarde le cache sur disque"""
        data = {
            "cache": self.cache,
            "last_accessed": {k: v.isoformat() for k, v in self.last_accessed.items()},
            "max_size": self.max_size
        }
        with open(path, 'w') as f:
            json.dump(data, f)
            
    def load(self, path: str):
        """Charge le cache depuis le disque"""
        if not os.path.exists(path):
            return
            
        with open(path, 'r') as f:
            data = json.load(f)
            
        self.cache = data["cache"]
        self.last_accessed = {
            k: datetime.fromisoformat(v) 
            for k, v in data["last_accessed"].items()
        }
        self.max_size = data.get("max_size", 10000)

class RateLimit:
    """Gestion du rate limiting pour l'API"""
    
    def __init__(self, calls_per_minute: int = 60):
        self.rate = calls_per_minute
        self.window = 60  # 1 minute
        self.calls = []
        self._lock = asyncio.Lock()
        
    async def wait(self):
        """Attend si nécessaire pour respecter le rate limit"""
        async with self._lock:
            now = time.time()
            # Nettoyer les appels trop anciens
            self.calls = [t for t in self.calls if now - t < self.window]
            
            if len(self.calls) >= self.rate:
                # Attendre jusqu'à ce qu'un créneau se libère
                wait_time = self.window - (now - self.calls[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                self.calls = self.calls[1:]
            
            self.calls.append(now)

class EmbeddingService:
    """Service de gestion des embeddings avec Albert API"""
    
    def __init__(self, config: Config):
        self.config = config
        self.cache = EmbeddingCache(max_size=config.embedding_cache_size)
        self._embedding_dimension = config.embedding_dimension
        self.rate_limiter = RateLimit(calls_per_minute=60)
        self.http_client = None
        self._batch_lock = asyncio.Lock()
        
        # Validation de la dimension
        if self._embedding_dimension <= 0 or self._embedding_dimension > 1024:
            raise ValueError(f"Dimension d'embedding invalide: {self._embedding_dimension}")
            
    async def _init_http_client(self):
        """Initialise le client HTTP si nécessaire"""
        if not self.http_client:
            headers = {
                "Authorization": f"Bearer {self.config.albert_api_token}",
                "Content-Type": "application/json"
            }
            self.http_client = httpx.AsyncClient(
                timeout=120.0,  # Augmenté pour les batchs
                headers=headers,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                http2=True
            )
            
    @property
    def embedding_dimension(self) -> int:
        """Retourne la dimension des embeddings"""
        return self._embedding_dimension
        
    def resize_embedding(self, embedding: np.ndarray, target_dim: int) -> np.ndarray:
        """Redimensionne un embedding à la dimension cible de manière robuste
        
        Args:
            embedding: Embedding source
            target_dim: Dimension cible
            
        Returns:
            Embedding redimensionné
        """
        current_dim = len(embedding)
        if current_dim == target_dim:
            return embedding
            
        # Normaliser l'embedding source
        normalized = embedding / np.linalg.norm(embedding)
        
        if current_dim > target_dim:
            # Réduction par PCA simplifiée
            logger.info(f"Réduction dimension par PCA: {current_dim} -> {target_dim}")
            covariance = np.outer(normalized, normalized)
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            indices = np.argsort(eigenvalues)[::-1][:target_dim]
            selected_vectors = eigenvectors[:, indices]
            return np.dot(normalized, selected_vectors)
        else:
            # Augmentation avec préservation des propriétés
            logger.info(f"Augmentation dimension: {current_dim} -> {target_dim}")
            result = np.zeros(target_dim)
            result[:current_dim] = normalized
            # Ajouter du bruit gaussien normalisé pour les dimensions supplémentaires
            if target_dim > current_dim:
                noise = np.random.normal(0, 0.01, target_dim - current_dim)
                noise = noise / np.linalg.norm(noise) * 0.1  # Réduire l'impact
                result[current_dim:] = noise
            return result / np.linalg.norm(result)  # Renormaliser
            
    async def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Récupère l'embedding d'un texte via Albert API"""
        if not text or not text.strip():
            return None
            
        # Vérifier le cache
        cached = await self.cache.get(text)
        if cached is not None:
            embedding = np.array(cached)
            if len(embedding) != self.embedding_dimension:
                embedding = self.resize_embedding(embedding, self.embedding_dimension)
            return embedding
            
        # Attendre le rate limiting
        await self.rate_limiter.wait()
        
        try:
            await self._init_http_client()
            base_url = self.config.albert_api_url.rstrip('/')
            if '/v1' in base_url:
                base_url = base_url[:base_url.index('/v1')]
            
            response = await self.http_client.post(
                f"{base_url}/v1/embeddings",
                json={
                    "input": [text],
                    "model": self.config.albert_model_embedding,
                    "dimensions": self.embedding_dimension,
                    "encoding_format": "float"
                }
            )
            
            response.raise_for_status()
            embedding_data = response.json()
            
            # Vérifier que la réponse contient les données attendues
            if "data" not in embedding_data or not embedding_data["data"]:
                raise ValueError("Réponse API invalide : pas de données d'embedding")
                
            embedding = np.array(embedding_data["data"][0]["embedding"])
            
            # Vérifier la dimension retournée
            if len(embedding) != self.embedding_dimension:
                logger.warning(
                    f"Dimension inattendue de l'API : {len(embedding)} != {self.embedding_dimension}"
                )
                embedding = self.resize_embedding(embedding, self.embedding_dimension)
            
            # Mettre en cache
            await self.cache.set(text, embedding.tolist())
            return embedding
            
        except Exception as e:
            logger.error(f"Erreur génération embedding: {str(e)}")
            raise
            
    async def get_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """Récupère les embeddings pour une liste de textes en mode batch"""
        if not texts:
            return []
            
        results = []
        batch = []
        batch_indices = []
        
        for i, text in enumerate(texts):
            if not text.strip():
                results.append((i, np.zeros(self.embedding_dimension)))
                continue
                
            cached = await self.cache.get(text)
            if cached is not None:
                embedding = np.array(cached)
                if len(embedding) != self.embedding_dimension:
                    embedding = self.resize_embedding(embedding, self.embedding_dimension)
                results.append((i, embedding))
                continue
                
            batch.append(text)
            batch_indices.append(i)
            
            if len(batch) >= self.config.embedding_batch_size:
                await self._process_batch(batch, batch_indices, results)
                batch = []
                batch_indices = []
                
        if batch:
            await self._process_batch(batch, batch_indices, results)
            
        # Trier les résultats par index original
        results.sort(key=lambda x: x[0])
        return [emb for _, emb in results]
        
    async def _process_batch(
        self,
        batch: List[str],
        indices: List[int],
        results: List[tuple]
    ):
        """Traite un lot de textes"""
        async with self._batch_lock:
            try:
                # Attendre le rate limiting
                await self.rate_limiter.wait()
                
                await self._init_http_client()
                base_url = self.config.albert_api_url.rstrip('/')
                if '/v1' in base_url:
                    base_url = base_url[:base_url.index('/v1')]
                
                response = await self.http_client.post(
                    f"{base_url}/v1/embeddings",
                    json={
                        "input": batch,
                        "model": self.config.albert_model_embedding,
                        "dimensions": self.embedding_dimension,
                        "encoding_format": "float"
                    }
                )
                
                response.raise_for_status()
                embedding_data = response.json()
                
                # Vérifier la validité de la réponse
                if "data" not in embedding_data:
                    raise ValueError("Réponse API invalide : pas de données d'embedding")
                    
                if len(embedding_data["data"]) != len(batch):
                    raise ValueError(
                        f"Nombre d'embeddings reçus ({len(embedding_data['data'])}) "
                        f"!= nombre de textes envoyés ({len(batch)})"
                    )
                
                # Traiter chaque embedding
                for i, data in enumerate(embedding_data["data"]):
                    try:
                        if "embedding" not in data:
                            logger.error(f"Pas d'embedding pour le texte {i}")
                            results.append((indices[i], np.zeros(self.embedding_dimension)))
                            continue
                            
                        embedding = np.array(data["embedding"])
                        if len(embedding) != self.embedding_dimension:
                            embedding = self.resize_embedding(embedding, self.embedding_dimension)
                        
                        # Mettre en cache
                        await self.cache.set(batch[i], embedding.tolist())
                        results.append((indices[i], embedding))
                        
                    except Exception as e:
                        logger.error(f"Erreur traitement embedding {i}: {str(e)}")
                        results.append((indices[i], np.zeros(self.embedding_dimension)))
                    
            except Exception as e:
                logger.error(f"Erreur traitement batch: {str(e)}")
                # En cas d'erreur, traiter un par un avec retries
                for i, text in enumerate(batch):
                    for retry in range(3):  # 3 tentatives maximum
                        try:
                            embedding = await self.get_embedding(text)
                            if embedding is not None:
                                results.append((indices[i], embedding))
                                break
                        except Exception as retry_error:
                            if retry == 2:  # Dernière tentative
                                logger.error(f"Échec final pour le texte {i}: {str(retry_error)}")
                                results.append((indices[i], np.zeros(self.embedding_dimension)))
                            else:
                                await asyncio.sleep(1)  # Attendre avant retry
                        
    async def clear_cache(self):
        """Vide le cache des embeddings"""
        await self.cache.clear_old_entries(timedelta(hours=0))
        
    def save_cache(self, path: str):
        """Sauvegarde le cache"""
        self.cache.save(path)
        
    def load_cache(self, path: str):
        """Charge le cache"""
        self.cache.load(path)
        
    async def close(self):
        """Ferme les connexions"""
        if self.http_client:
            await self.http_client.aclose() 