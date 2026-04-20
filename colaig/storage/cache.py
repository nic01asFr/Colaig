"""
Colaig — Cache in-memory avec TTL

Cache générique clé/valeur avec expiration automatique.
Async-safe grâce à asyncio.Lock (pas thread-safe, pas besoin :
tout Colaig tourne dans une seule boucle asyncio).
"""

from __future__ import annotations

import asyncio
import time
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Cache in-memory avec Time-To-Live par entrée.

    Args:
        default_ttl: Durée de vie par défaut en secondes. 0 = pas d'expiration.
        max_size: Nombre maximal d'entrées. 0 = illimité.

    Exemple::

        cache: TTLCache[str, list[float]] = TTLCache(default_ttl=300)
        await cache.set("embed:hello", [0.1, 0.2, 0.3])
        result = await cache.get("embed:hello")  # [0.1, 0.2, 0.3]
    """

    def __init__(self, default_ttl: float = 60.0, max_size: int = 0) -> None:
        self._default_ttl = default_ttl
        self._max_size = max_size
        # Stockage : clé → (valeur, timestamp d'expiration)
        self._store: dict[K, tuple[V, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: K) -> V | None:
        """Récupère une valeur. Retourne None si absente ou expirée."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at > 0 and time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: K, value: V, ttl: float | None = None) -> None:
        """Stocke une valeur avec TTL optionnel.

        Args:
            key: Clé de cache.
            value: Valeur à stocker.
            ttl: Durée de vie en secondes. None = default_ttl. 0 = pas d'expiration.
        """
        effective_ttl = self._default_ttl if ttl is None else ttl
        expires_at = (time.monotonic() + effective_ttl) if effective_ttl > 0 else 0.0

        async with self._lock:
            # Éviction si max_size atteint (supprime l'entrée la plus ancienne)
            if self._max_size > 0 and key not in self._store and len(self._store) >= self._max_size:
                self._evict_one()
            self._store[key] = (value, expires_at)

    async def delete(self, key: K) -> bool:
        """Supprime une entrée. Retourne True si elle existait."""
        async with self._lock:
            return self._store.pop(key, None) is not None

    async def clear(self) -> None:
        """Vide complètement le cache."""
        async with self._lock:
            self._store.clear()

    async def has(self, key: K) -> bool:
        """Vérifie si une clé existe et n'est pas expirée."""
        return await self.get(key) is not None

    @property
    def size(self) -> int:
        """Nombre d'entrées (y compris potentiellement expirées)."""
        return len(self._store)

    async def cleanup(self) -> int:
        """Supprime les entrées expirées. Retourne le nombre supprimé."""
        now = time.monotonic()
        async with self._lock:
            expired = [
                k for k, (_, expires_at) in self._store.items()
                if expires_at > 0 and now > expires_at
            ]
            for k in expired:
                del self._store[k]
            return len(expired)

    def _evict_one(self) -> None:
        """Évicte l'entrée expirant le plus tôt (ou la première si aucune n'expire)."""
        if not self._store:
            return
        # Cherche l'entrée avec le plus petit expires_at
        earliest_key = min(
            self._store,
            key=lambda k: self._store[k][1] if self._store[k][1] > 0 else float("inf"),
        )
        del self._store[earliest_key]
