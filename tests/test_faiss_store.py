"""Tests pour colaig/rag/faiss_store.py — Index vectoriel FAISS."""

import numpy as np
import pytest

from colaig.exceptions import FaissIndexError, IndexNotFoundError
from colaig.models import DocumentChunk, SearchResult
from colaig.rag.faiss_store import FaissStore

DIM = 64


def _random_vectors(n: int, dim: int = DIM) -> list[list[float]]:
    """Génère n vecteurs aléatoires normalisés."""
    vecs = np.random.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / norms
    return vecs.tolist()


def _make_chunks(n: int, source: str = "/doc.txt") -> list[DocumentChunk]:
    return [
        DocumentChunk(text=f"chunk {i}", source_path=source, source_name="doc.txt", position=i)
        for i in range(n)
    ]


class TestFaissStoreBasic:
    """Tests des opérations de base."""

    def test_empty_store(self):
        store = FaissStore(DIM)
        assert store.count == 0

    def test_add_and_count(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        chunks = _make_chunks(10)
        store.add(vecs, chunks)
        assert store.count == 10

    def test_add_mismatch_raises(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(5)
        chunks = _make_chunks(3)
        with pytest.raises(FaissIndexError, match="Mismatch"):
            store.add(vecs, chunks)

    def test_add_empty(self):
        store = FaissStore(DIM)
        store.add([], [])
        assert store.count == 0


class TestFaissStoreSearch:
    """Tests de la recherche."""

    def test_search_returns_results(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(20)
        chunks = _make_chunks(20)
        store.add(vecs, chunks)

        results = store.search(vecs[0], k=5)
        assert len(results) == 5
        assert all(isinstance(r, SearchResult) for r in results)

    def test_search_best_match_is_self(self):
        """Un vecteur cherché doit se trouver lui-même en premier."""
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        chunks = _make_chunks(10)
        store.add(vecs, chunks)

        results = store.search(vecs[0], k=1)
        assert results[0].chunk.text == "chunk 0"
        assert results[0].score > 0.99  # cosinus ≈ 1

    def test_search_empty_store(self):
        store = FaissStore(DIM)
        results = store.search(_random_vectors(1)[0], k=5)
        assert results == []

    def test_search_scores_sorted(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(50)
        chunks = _make_chunks(50)
        store.add(vecs, chunks)

        results = store.search(vecs[0], k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_k_larger_than_store(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(3)
        chunks = _make_chunks(3)
        store.add(vecs, chunks)

        results = store.search(vecs[0], k=10)
        assert len(results) == 3


class TestFaissStoreDelete:
    """Tests de la suppression."""

    def test_delete_by_source(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        chunks_a = _make_chunks(5, source="/doc_a.txt")
        chunks_b = _make_chunks(5, source="/doc_b.txt")
        store.add(vecs[:5], chunks_a)
        store.add(vecs[5:], chunks_b)
        assert store.count == 10

        removed = store.delete_by_source("/doc_a.txt")
        assert removed == 5
        assert store.count == 5

    def test_deleted_not_in_search(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        chunks = _make_chunks(5, source="/keep.txt") + _make_chunks(5, source="/delete.txt")
        store.add(vecs, chunks)
        store.delete_by_source("/delete.txt")

        results = store.search(vecs[5], k=10)
        sources = [r.chunk.source_path for r in results]
        assert "/delete.txt" not in sources

    def test_rebuild_cleans_deleted(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        store.add(vecs[:5], _make_chunks(5, "/keep.txt"))
        store.add(vecs[5:], _make_chunks(5, "/delete.txt"))
        store.delete_by_source("/delete.txt")

        store.rebuild()
        assert store.count == 5
        # Pas de deleted restants
        assert len(store._deleted) == 0


class TestFaissStorePersistence:
    """Tests de la sérialisation/désérialisation."""

    def test_save_and_load(self, tmp_path):
        store = FaissStore(DIM)
        vecs = _random_vectors(20)
        chunks = _make_chunks(20)
        store.add(vecs, chunks)
        store.save(str(tmp_path))

        store2 = FaissStore(DIM)
        store2.load(str(tmp_path))
        assert store2.count == 20

        # La recherche fonctionne après reload
        results = store2.search(vecs[0], k=1)
        assert results[0].chunk.text == "chunk 0"

    def test_load_nonexistent_raises(self, tmp_path):
        store = FaissStore(DIM)
        with pytest.raises(IndexNotFoundError):
            store.load(str(tmp_path / "nonexistent"))

    def test_serialize_deserialize(self):
        store = FaissStore(DIM)
        vecs = _random_vectors(10)
        chunks = _make_chunks(10)
        store.add(vecs, chunks)

        index_bytes, meta_bytes = store.serialize()

        store2 = FaissStore(DIM)
        store2.deserialize(index_bytes, meta_bytes)
        assert store2.count == 10

        results = store2.search(vecs[0], k=1)
        assert results[0].chunk.text == "chunk 0"
