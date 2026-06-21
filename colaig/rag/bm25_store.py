"""
Colaig — Index BM25 pour recherche lexicale

Complément au FAISS vectoriel : capture les correspondances exactes de termes
que les embeddings peuvent manquer (acronymes, codes, noms propres techniques).

Compatible avec le pipeline hybride : BM25Store + FaissStore → RRF.
"""

from __future__ import annotations

import logging
import pickle

from colaig.models import DocumentChunk

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    _BM25_AVAILABLE = True
except ImportError:
    _BM25_AVAILABLE = False
    logger.warning("rank-bm25 non installé — BM25Store désactivé (pip install rank-bm25)")


def _tokenize(text: str) -> list[str]:
    """Tokenisation simple : lowercase + split sur espaces/ponctuation."""
    import re
    text = text.lower()
    return re.findall(r'\w+', text)


class BM25Store:
    """Index BM25 pour la recherche lexicale sur les chunks documentaires.

    Conçu pour fonctionner en parallèle du FaissStore (hybrid search + RRF).

    Notes:
        - Index entièrement en mémoire (comme FAISS)
        - Suppression lazy + rebuild (même pattern que FaissStore)
        - Sérialisable via pickle pour persistance sur storage
        - Requiert le paquet `rank-bm25` (pip install rank-bm25)
    """

    def __init__(self) -> None:
        self._chunks: list[DocumentChunk] = []  # corpus indexé (position → chunk)
        self._deleted: set[int] = set()          # positions marquées supprimées
        self._bm25: object | None = None      # BM25Okapi | None (lazy rebuild)
        self._dirty: bool = False                # True si rebuild nécessaire

    # ── API publique ────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Nombre de chunks actifs."""
        return len(self._chunks) - len(self._deleted)

    def add(self, chunks: list[DocumentChunk]) -> None:
        """Ajoute des chunks dans l'index.

        Args:
            chunks: Chunks à indexer.
        """
        if not chunks:
            return
        if not _BM25_AVAILABLE:
            return
        self._chunks.extend(chunks)
        self._dirty = True
        logger.debug("bm25 ajouté %d chunks (total actifs: %d)", len(chunks), self.count)

    def delete_by_source(self, source_path: str) -> int:
        """Suppression lazy de tous les chunks d'un document source.

        Returns:
            Nombre de chunks marqués supprimés.
        """
        count = 0
        for idx, chunk in enumerate(self._chunks):
            if chunk.source_path == source_path and idx not in self._deleted:
                self._deleted.add(idx)
                count += 1
        if count:
            self._dirty = True
            logger.debug("bm25 supprimé %d chunks de %s", count, source_path)
        return count

    def rebuild(self) -> None:
        """Compacte l'index en supprimant physiquement les entrées marquées."""
        active = [c for i, c in enumerate(self._chunks) if i not in self._deleted]
        self._chunks = active
        self._deleted.clear()
        self._bm25 = None
        self._dirty = bool(active)  # forcer rebuild BM25 au prochain search
        logger.debug("bm25 rebuilt: %d chunks actifs", len(active))

    def reset(self) -> None:
        """Réinitialise l'index à zéro (vide complet)."""
        self._chunks = []
        self._deleted.clear()
        self._bm25 = None
        self._dirty = False
        logger.info("bm25 store réinitialisé")

    def has_deletions(self) -> bool:
        """True si des suppressions lazy sont en attente."""
        return bool(self._deleted)

    def search(self, query: str, k: int = 10) -> list[tuple[DocumentChunk, float]]:
        """Recherche BM25.

        Args:
            query: Texte de la requête.
            k: Nombre de résultats.

        Returns:
            Liste de (chunk, score_bm25) triés par score décroissant.
            Retourne [] si rank-bm25 non disponible ou index vide.
        """
        if not _BM25_AVAILABLE or not self._chunks:
            return []

        # Construire/reconstruire l'index BM25 si nécessaire
        if self._dirty or self._bm25 is None:
            self._build_index()

        if self._bm25 is None:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        # Associer scores aux chunks actifs (même ordre que _build_index)
        active_indices = [i for i in range(len(self._chunks)) if i not in self._deleted]
        if len(scores) != len(active_indices):
            logger.warning("bm25 mismatch scores/chunks (%d vs %d)", len(scores), len(active_indices))
            return []

        # Trier par score décroissant
        # Note : scores BM25 peuvent être négatifs (IDF~0 si terme dans tous les docs)
        # → garder tous les résultats ; le RRF ou l'appelant filtre selon son contexte
        scored = [(self._chunks[active_indices[i]], float(scores[i]))
                  for i in range(len(active_indices))]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:k]

    def get_all_active_chunks(self) -> list[DocumentChunk]:
        """Retourne tous les chunks actifs."""
        return [c for i, c in enumerate(self._chunks) if i not in self._deleted]

    # ── Persistance ─────────────────────────────────────────────────────────

    def serialize(self) -> bytes:
        """Sérialise l'index en bytes (stockage sur storage).

        Returns:
            bytes du pickle contenant chunks actifs et deleted set.
        """
        # Sauvegarder uniquement les chunks actifs (index compacté)
        active = self.get_all_active_chunks()
        return pickle.dumps({"chunks": active})

    def deserialize(self, data: bytes) -> None:
        """Désérialise depuis bytes.

        Args:
            data: bytes produits par serialize().
        """
        obj = pickle.loads(data)
        self._chunks = obj.get("chunks", [])
        self._deleted = set()
        self._bm25 = None
        self._dirty = bool(self._chunks)
        logger.debug("bm25 désérialisé: %d chunks", len(self._chunks))

    # ── Interne ──────────────────────────────────────────────────────────────

    def _build_index(self) -> None:
        """(Re)construit l'index BM25Okapi sur les chunks actifs."""
        if not _BM25_AVAILABLE:
            return
        active = [self._chunks[i] for i in range(len(self._chunks)) if i not in self._deleted]
        if not active:
            self._bm25 = None
            self._dirty = False
            return
        corpus = [_tokenize(c.text) for c in active]
        self._bm25 = BM25Okapi(corpus)
        self._dirty = False
        logger.debug("bm25 index construit sur %d chunks", len(active))
