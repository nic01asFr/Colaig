"""
Colaig — FaissIndexRegistry

Registry centralisé des index FAISS en mémoire.

Principes :
- Un seul chargement par index par process (lazy, LRU-evictable)
- Lectures concurrentes : asyncio.to_thread (FAISS release le GIL)
- Écritures exclusives : asyncio.Lock par index
- search_multi() : asyncio.gather sur N indexes en parallèle
- Clé : chaîne libre, convention "{workspace_path}::{index_name}"

Usage :
    registry = FaissIndexRegistry()

    # Charger (ou obtenir depuis cache)
    store = await registry.get_or_load("ws1::documents", loader_coroutine)

    # Recherche concurrente sur 3 indexes
    results = await registry.search_multi({
        "ws1::documents": (query_vec, 5),
        "ws1::behaviors": (query_vec, 3),
        "user::alice::memory": (query_vec, 5),
    })
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from colaig.models import SearchResult

logger = logging.getLogger(__name__)


class FaissIndexRegistry:
    """Registry en mémoire des FaissStore — lecture concurrente, écriture exclusive.

    Une instance partagée par process (injectée via main.py → handlers → agents).
    Thread-safe pour asyncio (pas de threading.Lock — tout passe par asyncio.Lock).
    """

    def __init__(self) -> None:
        # key → FaissStore
        self._stores: dict[str, Any] = {}
        # key → asyncio.Lock (pour les opérations de chargement/mise à jour)
        self._load_locks: dict[str, asyncio.Lock] = {}

    # ──────────────────────────────────────────────────────────────────
    # Accès aux stores

    def get(self, key: str) -> Any | None:
        """Retourne le store si présent en mémoire, sinon None."""
        return self._stores.get(key)

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Coroutine[Any, Any, Any | None]],
    ) -> Any | None:
        """Retourne le store (charge depuis le loader si absent du cache).

        Le loader est une coroutine qui retourne un FaissStore ou None.
        Le chargement est sérialisé par clé (pas de double-chargement concurrent).

        Args:
            key: Clé unique de l'index.
            loader: Coroutine async () → FaissStore | None.

        Returns:
            FaissStore chargé, ou None si le loader retourne None.
        """
        if key in self._stores:
            return self._stores[key]

        lock = self._load_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Double-check après acquisition du lock
            if key in self._stores:
                return self._stores[key]
            try:
                store = await loader()
                if store is not None:
                    self._stores[key] = store
                    logger.debug("registry: index chargé '%s' (%d vecteurs)", key, store.count)
                return store
            except Exception as e:
                logger.warning("registry: échec chargement '%s': %s", key, e)
                return None

    def set(self, key: str, store: Any) -> None:
        """Enregistre un store directement (index créé en mémoire)."""
        self._stores[key] = store
        logger.debug("registry: index enregistré '%s'", key)

    def evict(self, key: str) -> None:
        """Supprime un store du registry (libère la mémoire)."""
        if key in self._stores:
            del self._stores[key]
            logger.debug("registry: index évincé '%s'", key)

    def evict_prefix(self, prefix: str) -> int:
        """Évince tous les stores dont la clé commence par `prefix`."""
        keys = [k for k in self._stores if k.startswith(prefix)]
        for k in keys:
            del self._stores[k]
        if keys:
            logger.debug("registry: %d index évincés (prefix='%s')", len(keys), prefix)
        return len(keys)

    def keys(self) -> list[str]:
        """Liste des clés actuellement en mémoire."""
        return list(self._stores.keys())

    def count(self) -> int:
        """Nombre d'index en mémoire."""
        return len(self._stores)

    # ──────────────────────────────────────────────────────────────────
    # Recherche

    async def search(self, key: str, query_embedding: list[float], k: int = 5) -> list[SearchResult]:
        """Recherche dans un index (non-bloquant via asyncio.to_thread).

        Returns [] si l'index n'est pas en mémoire.
        """
        store = self._stores.get(key)
        if store is None:
            return []
        return await store.search_async(query_embedding, k)

    async def search_multi(
        self,
        queries: dict[str, tuple[list[float], int]],
    ) -> dict[str, list[SearchResult]]:
        """Recherche parallèle sur plusieurs indexes en un seul appel.

        Tous les indexes présents en mémoire sont interrogés en parallèle
        via asyncio.gather (chacun dans asyncio.to_thread → GIL libéré).

        Les indexes absents du registry retournent [] sans erreur.

        Args:
            queries: {key: (query_vec, k)} — un dict par index à interroger.

        Returns:
            {key: [SearchResult]} — même structure, avec résultats (ou []).

        Example:
            results = await registry.search_multi({
                "ws1::documents": (vec, 5),
                "ws1::behaviors": (vec, 3),
                "user::alice@tchap.fr::memory": (vec, 5),
            })
        """
        if not queries:
            return {}

        keys = list(queries.keys())
        tasks = [self.search(key, queries[key][0], queries[key][1]) for key in keys]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, list[SearchResult]] = {}
        for key, result in zip(keys, raw):
            if isinstance(result, Exception):
                logger.warning("registry.search_multi: erreur index '%s': %s", key, result)
                results[key] = []
            else:
                results[key] = result

        return results

    # ──────────────────────────────────────────────────────────────────
    # Persistance

    async def save(self, key: str, storage, faiss_path: str, meta_path: str) -> None:
        """Sérialise et upload un index via StorageProtocol.

        Args:
            key: Clé du store à persister.
            storage: Instance StorageProtocol.
            faiss_path: Chemin de destination pour le .faiss.
            meta_path: Chemin de destination pour le .pkl.
        """
        store = self._stores.get(key)
        if store is None:
            logger.warning("registry.save: index '%s' absent du registry", key)
            return
        try:
            index_bytes, meta_bytes = await asyncio.to_thread(store.serialize)
            await storage.upload(faiss_path, index_bytes)
            await storage.upload(meta_path, meta_bytes)
            logger.debug("registry: index '%s' persisté → %s", key, faiss_path)
        except Exception as e:
            logger.warning("registry.save: échec persistance '%s': %s", key, e)
