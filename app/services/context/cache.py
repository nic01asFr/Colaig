from datetime import datetime, timedelta
from typing import Dict, Any, Optional, TypeVar, Generic
from dataclasses import dataclass, field
import asyncio
from matrix_bot.config import logger

T = TypeVar('T')

@dataclass
class CacheEntry(Generic[T]):
    """Entrée de cache avec expiration"""
    value: T
    expiry: datetime
    last_access: datetime = field(default_factory=datetime.now)
    lock_token: Optional[str] = field(default=None)

    @property
    def is_expired(self) -> bool:
        """Vérifie si l'entrée est expirée"""
        return datetime.now() > self.expiry

    def access(self) -> None:
        """Met à jour la date de dernier accès"""
        self.last_access = datetime.now()

    def is_locked(self) -> bool:
        """Vérifie si l'entrée est verrouillée"""
        return self.lock_token is not None

class ContextCache:
    """Cache optimisé pour les contextes"""
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: int = 3600,
        cleanup_interval: int = 300
    ):
        self._cache: Dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cleanup_interval = cleanup_interval
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "locks": 0
        }

    async def start(self):
        """Démarre la tâche de nettoyage périodique"""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Arrête la tâche de nettoyage"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def get(self, key: str) -> Optional[Any]:
        """Récupère une valeur du cache"""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if not entry.is_expired:
                    entry.access()
                    self._stats["hits"] += 1
                    return entry.value
                del self._cache[key]
                self._stats["evictions"] += 1
            self._stats["misses"] += 1
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        lock_token: Optional[str] = None
    ) -> None:
        """Ajoute ou met à jour une valeur dans le cache"""
        async with self._lock:
            if len(self._cache) >= self._max_size:
                await self._cleanup(force=True)
                if len(self._cache) >= self._max_size:
                    # Si toujours plein, supprime les entrées les moins utilisées
                    await self._evict_lru()

            expiry = datetime.now() + timedelta(
                seconds=ttl if ttl is not None else self._default_ttl
            )
            self._cache[key] = CacheEntry(
                value=value,
                expiry=expiry,
                lock_token=lock_token
            )
            if lock_token:
                self._stats["locks"] += 1

    async def delete(self, key: str) -> None:
        """Supprime une entrée du cache"""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if entry.is_locked():
                    self._stats["locks"] -= 1
                del self._cache[key]
                self._stats["evictions"] += 1

    async def clear(self) -> None:
        """Vide le cache"""
        async with self._lock:
            self._cache.clear()
            self._stats = {
                "hits": 0,
                "misses": 0,
                "evictions": 0,
                "locks": 0
            }

    async def lock(self, key: str, token: str) -> bool:
        """Verrouille une entrée du cache"""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if not entry.is_locked():
                    entry.lock_token = token
                    self._stats["locks"] += 1
                    return True
            return False

    async def unlock(self, key: str, token: str) -> bool:
        """Déverrouille une entrée du cache"""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if entry.lock_token == token:
                    entry.lock_token = None
                    self._stats["locks"] -= 1
                    return True
            return False

    async def _cleanup(self, force: bool = False) -> None:
        """Nettoie les entrées expirées"""
        now = datetime.now()
        expired = [
            k for k, v in self._cache.items()
            if v.is_expired or (
                force and 
                not v.is_locked() and
                (now - v.last_access).total_seconds() > self._default_ttl
            )
        ]
        for key in expired:
            entry = self._cache[key]
            if entry.is_locked():
                self._stats["locks"] -= 1
            del self._cache[key]
            self._stats["evictions"] += 1

    async def _evict_lru(self) -> None:
        """Supprime les entrées les moins récemment utilisées"""
        if not self._cache:
            return

        # Ne considère que les entrées non verrouillées
        unlocked_entries = [
            (k, v) for k, v in self._cache.items()
            if not v.is_locked()
        ]

        if not unlocked_entries:
            return

        # Trie par date de dernier accès
        sorted_entries = sorted(
            unlocked_entries,
            key=lambda x: x[1].last_access
        )

        # Supprime 10% des entrées les plus anciennes
        num_to_remove = max(1, len(sorted_entries) // 10)
        for key, _ in sorted_entries[:num_to_remove]:
            del self._cache[key]
            self._stats["evictions"] += 1

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage périodique"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle de nettoyage: {str(e)}")
                await asyncio.sleep(60)  # Attente plus courte en cas d'erreur

    @property
    def stats(self) -> Dict[str, int]:
        """Retourne les statistiques du cache"""
        return self._stats.copy()

    @property
    def size(self) -> int:
        """Retourne la taille actuelle du cache"""
        return len(self._cache)

    @property
    def locked_entries(self) -> int:
        """Retourne le nombre d'entrées verrouillées"""
        return self._stats["locks"] 