"""
Service de cache pour les résultats de recherche web.
Stocke les résultats de recherche fréquentes pour réduire les appels API.
"""

import json
import os
from typing import Dict, List, Optional, Any
import time
import hashlib

from app.config import Config
from app.matrix_bot.config import logger

class WebSearchCache:
    """
    Cache pour les résultats de recherche web.
    Stocke les résultats de recherche fréquentes pour réduire les appels API.
    """
    
    # Chemin relatif pour le fichier de cache
    CACHE_PATH = ".albert/web_cache/search_cache.json"
    
    # Durée de vie du cache en secondes (12 heures par défaut)
    DEFAULT_TTL = 12 * 60 * 60
    
    # Taille maximale du cache (nombre d'entrées)
    MAX_CACHE_SIZE = 1000
    
    def __init__(self, config: Config, webdav_service = None):
        self.config = config
        self.webdav_service = webdav_service
        self.cache = {}
        self.loaded = False
        
    async def ensure_initialized(self):
        """Assure que le cache est initialisé."""
        if not self.loaded:
            await self.load_cache()
    
    async def load_cache(self):
        """Charge le cache depuis WebDAV."""
        if not self.webdav_service:
            from app.services.webdav_instance import get_webdav_service
            self.webdav_service = await get_webdav_service(self.config)
        
        # Créer le dossier de cache s'il n'existe pas
        cache_dir = os.path.dirname(os.path.join(self.config.webdav_root_path, self.CACHE_PATH))
        if not await self.webdav_service.exists(cache_dir):
            await self.webdav_service.create_directory(cache_dir)
        
        # Charger le cache s'il existe
        cache_path = os.path.join(self.config.webdav_root_path, self.CACHE_PATH)
        if await self.webdav_service.exists(cache_path):
            try:
                content = await self.webdav_service.read_document(cache_path)
                self.cache = json.loads(content)
                logger.info(f"Cache de recherche web chargé: {len(self.cache)} entrées")
                
                # Nettoyer les entrées expirées
                await self._clean_expired_entries()
            except Exception as e:
                logger.error(f"Erreur lors du chargement du cache: {str(e)}")
                self.cache = {}
        else:
            self.cache = {}
            await self.save_cache()
        
        self.loaded = True
            
    async def save_cache(self):
        """Sauvegarde le cache sur WebDAV."""
        if not self.webdav_service:
            from app.services.webdav_instance import get_webdav_service
            self.webdav_service = await get_webdav_service(self.config)
        
        cache_path = os.path.join(self.config.webdav_root_path, self.CACHE_PATH)
        content = json.dumps(self.cache, ensure_ascii=False, indent=2)
        
        try:
            await self.webdav_service.write_file(cache_path, content)
            logger.info("Cache de recherche web sauvegardé avec succès")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde du cache: {str(e)}")
            return False
    
    async def _clean_expired_entries(self):
        """Nettoie les entrées expirées du cache."""
        current_time = time.time()
        keys_to_delete = []
        
        for key, entry in self.cache.items():
            if entry.get("expiry", 0) < current_time:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self.cache[key]
            
        if keys_to_delete:
            logger.info(f"Nettoyage du cache: {len(keys_to_delete)} entrées expirées supprimées")
    
    async def _enforce_cache_size_limit(self):
        """Maintient la taille du cache sous la limite maximale en supprimant les entrées les moins utilisées."""
        if len(self.cache) <= self.MAX_CACHE_SIZE:
            return
        
        # Trier les entrées par utilisation (usage_count) et date de création
        sorted_entries = sorted(
            [(k, v.get("usage_count", 0), v.get("created_at", 0)) for k, v in self.cache.items()],
            key=lambda x: (x[1], x[2])  # Trier par usage_count, puis par date
        )
        
        # Supprimer les entrées les moins utilisées
        entries_to_remove = len(self.cache) - self.MAX_CACHE_SIZE
        for i in range(entries_to_remove):
            if i < len(sorted_entries):
                key_to_delete = sorted_entries[i][0]
                del self.cache[key_to_delete]
        
        logger.info(f"Cache limité: {entries_to_remove} entrées supprimées pour respecter la limite de taille")
    
    def _generate_cache_key(self, query: str, collection: str = "internet") -> str:
        """Génère une clé de cache unique pour une requête."""
        key_str = f"{query.lower().strip()}:{collection}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def get(self, query: str, collection: str = "internet") -> Optional[Dict]:
        """
        Récupère un résultat depuis le cache.
        Retourne None si le résultat n'est pas en cache ou s'il est expiré.
        """
        await self.ensure_initialized()
        
        key = self._generate_cache_key(query, collection)
        entry = self.cache.get(key)
        
        if not entry:
            return None
        
        # Vérifier si l'entrée est expirée
        if entry.get("expiry", 0) < time.time():
            del self.cache[key]
            await self.save_cache()
            return None
        
        # Incrémenter le compteur d'utilisation
        entry["usage_count"] = entry.get("usage_count", 0) + 1
        await self.save_cache()
        
        return entry.get("data")
    
    async def set(self, query: str, data: Dict, collection: str = "internet", ttl: int = None):
        """
        Stocke un résultat dans le cache.
        
        Args:
            query: La requête de recherche
            data: Les données à mettre en cache
            collection: La collection utilisée pour la recherche
            ttl: Durée de vie en secondes (utilise DEFAULT_TTL si non spécifié)
        """
        await self.ensure_initialized()
        
        if ttl is None:
            ttl = self.DEFAULT_TTL
        
        key = self._generate_cache_key(query, collection)
        
        self.cache[key] = {
            "query": query,
            "collection": collection,
            "data": data,
            "created_at": time.time(),
            "expiry": time.time() + ttl,
            "usage_count": 0
        }
        
        # Maintenir la taille du cache sous la limite
        await self._enforce_cache_size_limit()
        
        # Sauvegarder le cache
        await self.save_cache()
        return True

# Instance unique pour tout le programme
_web_search_cache = None

async def get_web_search_cache(config: Config) -> WebSearchCache:
    """Récupère l'instance unique du cache de recherche web."""
    global _web_search_cache
    
    if _web_search_cache is None:
        from app.services.webdav_instance import get_webdav_service
        webdav_service = await get_webdav_service(config)
        _web_search_cache = WebSearchCache(config, webdav_service)
        await _web_search_cache.ensure_initialized()
    
    return _web_search_cache 