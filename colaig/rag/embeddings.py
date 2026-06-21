"""
Colaig — Service d'embeddings

Implémente EmbeddingServiceProtocol.
Utilise AlbertClientProtocol en priorité, cache en mémoire,
normalisation L2 systématique.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import numpy as np

from colaig.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service d'embeddings avec cache et normalisation.

    Args:
        albert_client: Client Albert API (AlbertClientProtocol).
        dimension: Dimension des vecteurs (doit correspondre au modèle).
        cache_max_size: Taille max du cache de hashes. 0 = illimité.
        cache_namespace: Préfixe opaque incorporé dans chaque clé de cache.
            Garantit que les données d'un client ne servent jamais une requête
            d'un autre client, même si le texte source est identique.
            En pratique : hash(api_key + model_embed) passé depuis main.py.
    """

    def __init__(
        self,
        albert_client,  # AlbertClientProtocol
        dimension: int = 1024,
        cache_max_size: int = 10000,
        cache_namespace: str = "",
        local_fallback: bool = False,
        local_model: str = "BAAI/bge-m3",
    ) -> None:
        self._albert = albert_client
        self._dimension = dimension
        self._cache_max_size = cache_max_size
        # Préfixe opaque pour isoler les données de ce client dans le cache
        self._ns = cache_namespace
        # Cache en mémoire : hash(namespace + text) → embedding normalisé
        self._cache: dict[str, list[float]] = {}
        # Statistiques de cache
        self._cache_hits = 0
        self._cache_misses = 0
        # Fallback embeddings local (SentenceTransformer) si le provider échoue
        self._local_fallback = local_fallback
        self._local_model_name = local_model
        self._st_model = None  # lazy init

    def _load_local_model(self):
        """Charge (paresseusement) le modèle SentenceTransformer de fallback."""
        if self._st_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover - dépend de l'install
                raise EmbeddingError(
                    "sentence-transformers requis pour le fallback embeddings local "
                    "(pip install sentence-transformers)"
                ) from e
            logger.warning(
                "embeddings: chargement du modèle local '%s' (fallback)",
                self._local_model_name,
            )
            self._st_model = SentenceTransformer(self._local_model_name)
        return self._st_model

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Embeddings via le modèle local (hors thread event-loop)."""
        model = self._load_local_model()
        vectors = await asyncio.to_thread(
            model.encode, texts, normalize_embeddings=False
        )
        return [list(map(float, v)) for v in vectors]

    @property
    def dimension(self) -> int:
        """Dimension des vecteurs produits."""
        return self._dimension

    def _cache_key(self, text: str) -> str:
        """Clé de cache isolée par namespace client + contenu."""
        return _text_hash(self._ns + "\x00" + text) if self._ns else _text_hash(text)

    async def embed_text(self, text: str) -> list[float]:
        """Embedding d'un texte unique. Retourne vecteur normalisé L2."""
        cache_key = self._cache_key(text)

        if cache_key in self._cache:
            self._cache_hits += 1
            return self._cache[cache_key]

        self._cache_misses += 1
        try:
            raw = await self._albert.embed(text)
            normalized = _normalize_l2(raw)
        except Exception as e:
            if not self._local_fallback:
                raise EmbeddingError(f"Erreur embedding: {e}") from e
            logger.warning("embeddings: provider échoué (%s) → fallback local", e)
            raw = (await self._embed_local([text]))[0]
            normalized = _normalize_l2(raw)

        if self._cache_max_size == 0 or len(self._cache) < self._cache_max_size:
            self._cache[cache_key] = normalized

        return normalized

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embedding batch. Retourne vecteurs normalisés L2."""
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cache_key = self._cache_key(text)
            if cache_key in self._cache:
                self._cache_hits += 1
                results[i] = self._cache[cache_key]
            else:
                self._cache_misses += 1
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            try:
                raw_embeddings = await self._albert.embed_batch(uncached_texts)
            except Exception as e:
                if not self._local_fallback:
                    raise EmbeddingError(f"Erreur batch embedding: {e}") from e
                logger.warning("embeddings: provider échoué (%s) → fallback local", e)
                raw_embeddings = await self._embed_local(uncached_texts)

            for idx, raw in zip(uncached_indices, raw_embeddings):
                normalized = _normalize_l2(raw)
                results[idx] = normalized
                cache_key = self._cache_key(texts[idx])
                if self._cache_max_size == 0 or len(self._cache) < self._cache_max_size:
                    self._cache[cache_key] = normalized

        return [r for r in results if r is not None]

    def cache_stats(self) -> dict:
        """Retourne les statistiques du cache (hits, misses, hit_rate, size)."""
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        stats = {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": round(hit_rate, 3),
            "size": len(self._cache),
        }
        logger.debug("embedding cache stats: hits=%d misses=%d hit_rate=%.1f%% size=%d",
                     stats["hits"], stats["misses"], hit_rate * 100, stats["size"])
        return stats

    def clear_cache(self) -> None:
        """Vide le cache d'embeddings."""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0


def _text_hash(text: str) -> str:
    """Hash court d'un texte pour clé de cache."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _normalize_l2(vector: list[float]) -> list[float]:
    """Normalise un vecteur L2 (pour similarité cosinus via inner product)."""
    arr = np.array(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()
