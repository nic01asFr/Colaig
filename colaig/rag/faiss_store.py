"""
Colaig — Index vectoriel FAISS

Implémente VectorStoreProtocol.
IndexFlatIP (Inner Product) avec vecteurs normalisés L2 = similarité cosinus.
Persistance : .faiss + .pkl (métadonnées DocumentChunk).
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle

import faiss
import numpy as np

from colaig.exceptions import FaissIndexError, IndexNotFoundError
from colaig.models import DocumentChunk, SearchResult

logger = logging.getLogger(__name__)


class FaissStore:
    """Gestion d'index vectoriel FAISS.

    Args:
        dimension: Dimension des vecteurs.
    """

    def __init__(self, dimension: int = 1024) -> None:
        self._dimension = dimension
        self._index: faiss.IndexFlatIP = faiss.IndexFlatIP(dimension)
        # Métadonnées : position FAISS → DocumentChunk
        self._metadata: dict[int, DocumentChunk] = {}
        # Positions marquées comme supprimées (lazy delete)
        self._deleted: set[int] = set()
        # Verrou asyncio pour les écritures exclusives (reads concurrents OK)
        self._write_lock: asyncio.Lock = asyncio.Lock()

    @property
    def count(self) -> int:
        """Nombre de vecteurs actifs dans l'index."""
        return self._index.ntotal - len(self._deleted)

    @property
    def dimension(self) -> int:
        return self._dimension

    def add(self, embeddings: list[list[float]], metadata: list[DocumentChunk]) -> None:
        """Ajoute des vecteurs avec leurs métadonnées.

        Args:
            embeddings: Vecteurs normalisés L2.
            metadata: DocumentChunks correspondants (même longueur).
        """
        if len(embeddings) != len(metadata):
            raise FaissIndexError(
                f"Mismatch: {len(embeddings)} embeddings vs {len(metadata)} metadata"
            )
        if not embeddings:
            return

        vectors = np.array(embeddings, dtype=np.float32)
        # Normaliser par sécurité
        faiss.normalize_L2(vectors)

        start_idx = self._index.ntotal
        self._index.add(vectors)

        for i, chunk in enumerate(metadata):
            self._metadata[start_idx + i] = chunk

        logger.debug("faiss ajouté %d vecteurs (total: %d)", len(embeddings), self._index.ntotal)

    def search(self, query_embedding: list[float], k: int = 5) -> list[SearchResult]:
        """Recherche les k vecteurs les plus similaires.

        Args:
            query_embedding: Vecteur de requête normalisé L2.
            k: Nombre de résultats.

        Returns:
            Liste de SearchResult triés par score décroissant.
        """
        if self._index.ntotal == 0:
            return []

        query = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query)

        # Chercher plus que k pour compenser les supprimés
        search_k = min(k + len(self._deleted), self._index.ntotal)
        scores, indices = self._index.search(query, search_k)

        results: list[SearchResult] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            if idx in self._deleted:
                continue
            chunk = self._metadata.get(idx)
            if chunk is None:
                continue
            # Reconstruire le vecteur stocké — utilisé par MMR pour la vraie cosine similarity
            try:
                vec = self._index.reconstruct(int(idx)).tolist()
            except Exception:
                vec = []
            results.append(SearchResult(chunk=chunk, score=float(score), rank=len(results), embedding=vec))
            if len(results) >= k:
                break

        return results

    async def search_async(self, query_embedding: list[float], k: int = 5) -> list[SearchResult]:
        """Recherche async — libère l'event loop pendant le compute FAISS (GIL released).

        Équivalent à search() mais non-bloquant pour l'event loop asyncio.
        Utiliser cette méthode dans les pipelines async pour permettre la concurrence.
        """
        return await asyncio.to_thread(self.search, query_embedding, k)

    def search_batch(self, query_embeddings: list[list[float]], k: int = 5) -> list[list[SearchResult]]:
        """Recherche N requêtes en un seul appel FAISS (batch search).

        Plus efficace que N appels search() indépendants — FAISS traite le batch
        vectoriellement en C++.

        Args:
            query_embeddings: Liste de N vecteurs de requête.
            k: Nombre de résultats par requête.

        Returns:
            Liste de N listes de SearchResult.
        """
        if not query_embeddings or self._index.ntotal == 0:
            return [[] for _ in query_embeddings]

        queries = np.array(query_embeddings, dtype=np.float32)
        faiss.normalize_L2(queries)

        search_k = min(k + len(self._deleted), self._index.ntotal)
        scores, indices = self._index.search(queries, search_k)

        all_results: list[list[SearchResult]] = []
        for q_idx in range(len(query_embeddings)):
            results: list[SearchResult] = []
            for score, idx in zip(scores[q_idx], indices[q_idx]):
                if idx == -1 or idx in self._deleted:
                    continue
                chunk = self._metadata.get(idx)
                if chunk is None:
                    continue
                results.append(SearchResult(chunk=chunk, score=float(score), rank=len(results)))
                if len(results) >= k:
                    break
            all_results.append(results)

        return all_results

    async def search_batch_async(
        self, query_embeddings: list[list[float]], k: int = 5
    ) -> list[list[SearchResult]]:
        """Batch search async — libère l'event loop pendant le compute FAISS."""
        return await asyncio.to_thread(self.search_batch, query_embeddings, k)

    def get_all_vectors(self) -> np.ndarray:
        """Retourne tous les vecteurs actifs sous forme de matrice numpy.

        Returns:
            np.ndarray de shape (n_actifs, dimension), dtype float32.
            Tableau vide de shape (0, dimension) si aucun vecteur actif.
        """
        if self.count == 0:
            return np.zeros((0, self._dimension), dtype=np.float32)
        active_indices = sorted(set(range(self._index.ntotal)) - self._deleted)
        all_vectors = self._index.reconstruct_n(0, self._index.ntotal)
        return all_vectors[np.array(active_indices, dtype=np.int64)]

    async def get_all_vectors_async(self) -> np.ndarray:
        """get_all_vectors() async — non-bloquant."""
        return await asyncio.to_thread(self.get_all_vectors)

    async def add_async(self, embeddings: list[list[float]], metadata: list[DocumentChunk]) -> None:
        """Ajout async avec verrou exclusif (évite les écritures concurrentes)."""
        async with self._write_lock:
            await asyncio.to_thread(self.add, embeddings, metadata)

    async def delete_by_source_async(self, source_path: str) -> int:
        """Suppression async avec verrou exclusif."""
        async with self._write_lock:
            return await asyncio.to_thread(self.delete_by_source, source_path)

    async def rebuild_async(self) -> None:
        """Rebuild async avec verrou exclusif."""
        async with self._write_lock:
            await asyncio.to_thread(self.rebuild)

    def has_deletions(self) -> bool:
        """Retourne True si des suppressions lazy sont en attente de rebuild."""
        return len(self._deleted) > 0

    def get_all_active_chunks(self) -> list:
        """Retourne tous les chunks actifs (non marqués supprimés).

        Utiliser à la place d'accéder directement à _metadata/_deleted.
        Garantit la compatibilité si la structure interne évolue.

        Returns:
            Liste de DocumentChunk (ou objets compatibles) actifs.
        """
        return [
            chunk
            for idx, chunk in self._metadata.items()
            if idx not in self._deleted
        ]

    def delete_by_source(self, source_path: str) -> int:
        """Supprime tous les vecteurs d'un document source (lazy delete).

        Returns:
            Nombre de vecteurs marqués comme supprimés.
        """
        count = 0
        for idx, chunk in self._metadata.items():
            if chunk.source_path == source_path and idx not in self._deleted:
                self._deleted.add(idx)
                count += 1
        if count:
            logger.debug("faiss supprimé %d vecteurs de %s", count, source_path)
        return count

    def rebuild(self) -> None:
        """Reconstruit l'index en supprimant physiquement les entrées marquées."""
        if not self._deleted:
            return

        active_indices = sorted(set(range(self._index.ntotal)) - self._deleted)
        if not active_indices:
            self._index = faiss.IndexFlatIP(self._dimension)
            self._metadata.clear()
            self._deleted.clear()
            return

        # Reconstruire — reconstruct_n en un seul appel C++ (évite N allers-retours Python)
        all_vectors = self._index.reconstruct_n(0, self._index.ntotal)
        vectors = all_vectors[np.array(active_indices, dtype=np.int64)]

        new_metadata: dict[int, DocumentChunk] = {}
        for new_idx, old_idx in enumerate(active_indices):
            new_metadata[new_idx] = self._metadata[old_idx]

        self._index = faiss.IndexFlatIP(self._dimension)
        self._index.add(vectors)
        self._metadata = new_metadata
        self._deleted.clear()

        logger.info("faiss rebuilt: %d vecteurs", self._index.ntotal)

    def reset(self) -> None:
        """Réinitialise l'index à zéro (vide complet, même dimension)."""
        self._index = faiss.IndexFlatIP(self._dimension)
        self._metadata.clear()
        self._deleted.clear()
        logger.info("faiss store réinitialisé (dimension=%d)", self._dimension)

    # ── Persistance locale ───────────────────────────────────────────

    def save(self, directory: str) -> None:
        """Sérialise index + métadonnées sur disque."""
        os.makedirs(directory, exist_ok=True)
        faiss_path = os.path.join(directory, "index.faiss")
        meta_path = os.path.join(directory, "metadata.pkl")

        faiss.write_index(self._index, faiss_path)
        with open(meta_path, "wb") as f:
            pickle.dump({"metadata": self._metadata, "deleted": self._deleted}, f)

        logger.debug("faiss sauvegardé dans %s (%d vecteurs)", directory, self._index.ntotal)

    def load(self, directory: str) -> None:
        """Désérialise index + métadonnées depuis disque."""
        faiss_path = os.path.join(directory, "index.faiss")
        meta_path = os.path.join(directory, "metadata.pkl")

        if not os.path.exists(faiss_path):
            raise IndexNotFoundError(f"Index FAISS introuvable: {faiss_path}")

        self._index = faiss.read_index(faiss_path)
        self._dimension = self._index.d

        if os.path.exists(meta_path):
            with open(meta_path, "rb") as f:
                data = pickle.load(f)
            self._metadata = data.get("metadata", {})
            self._deleted = data.get("deleted", set())
        else:
            self._metadata = {}
            self._deleted = set()

        logger.debug("faiss chargé depuis %s (%d vecteurs)", directory, self._index.ntotal)

    # ── Sérialisation WebDAV ─────────────────────────────────────────

    def serialize(self) -> tuple[bytes, bytes]:
        """Sérialise en bytes (index_bytes, metadata_bytes)."""
        index_bytes = faiss.serialize_index(self._index).tobytes()
        meta_bytes = pickle.dumps({"metadata": self._metadata, "deleted": self._deleted})
        return index_bytes, meta_bytes

    def deserialize(self, index_bytes: bytes, meta_bytes: bytes) -> None:
        """Désérialise depuis bytes."""
        self._index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8))
        self._dimension = self._index.d
        data = pickle.loads(meta_bytes)
        self._metadata = data.get("metadata", {})
        self._deleted = data.get("deleted", set())
