"""Tests pour colaig/rag/retriever.py — Recherche hybride."""

import pytest
import numpy as np

from colaig.rag.retriever import Retriever, _mmr_rerank
from colaig.rag.faiss_store import FaissStore
from colaig.rag.embeddings import EmbeddingService
from colaig.models import DocumentChunk, SearchResult


DIM = 64


def _random_vec(dim: int = DIM) -> list[float]:
    v = np.random.randn(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tolist()


def _make_chunks(n: int, source: str = "/doc.txt") -> list[DocumentChunk]:
    return [
        DocumentChunk(text=f"chunk {i}", source_path=source, source_name="doc.txt", position=i)
        for i in range(n)
    ]


class TestRetriever:
    """Tests du retriever complet."""

    async def test_retrieve_with_results(self, mock_albert):
        """Le retriever retourne des résultats pertinents."""
        # Setup
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        store = FaissStore(384)

        # Indexer quelques chunks
        chunks = _make_chunks(5)
        embeddings = await embedding_svc.embed_texts([c.text for c in chunks])
        store.add(embeddings, chunks)

        retriever = Retriever(embedding_svc, store)
        results = await retriever.retrieve("chunk 0", k=3, score_threshold=0.0)
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)

    async def test_retrieve_no_store(self, mock_albert):
        """Sans store, retourne une liste vide."""
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        retriever = Retriever(embedding_svc, store=None)
        results = await retriever.retrieve("query")
        assert results == []

    async def test_retrieve_empty_store(self, mock_albert):
        """Store vide retourne une liste vide."""
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        store = FaissStore(384)
        retriever = Retriever(embedding_svc, store)
        results = await retriever.retrieve("query")
        assert results == []

    async def test_retrieve_score_threshold(self, mock_albert):
        """Le threshold filtre les résultats avec un score trop bas."""
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        store = FaissStore(384)
        chunks = _make_chunks(5)
        embeddings = await embedding_svc.embed_texts([c.text for c in chunks])
        store.add(embeddings, chunks)

        retriever = Retriever(embedding_svc, store)
        # Avec threshold très haut, peu de résultats
        results_high = await retriever.retrieve("chunk 0", k=5, score_threshold=0.99)
        results_low = await retriever.retrieve("chunk 0", k=5, score_threshold=0.0)
        assert len(results_high) <= len(results_low)

    async def test_set_store(self, mock_albert):
        """set_store change le store utilisé."""
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        store1 = FaissStore(384)
        store2 = FaissStore(384)

        chunks = _make_chunks(3)
        embeddings = await embedding_svc.embed_texts([c.text for c in chunks])
        store2.add(embeddings, chunks)

        retriever = Retriever(embedding_svc, store1)
        assert (await retriever.retrieve("test", score_threshold=0.0)) == []

        retriever.set_store(store2)
        results = await retriever.retrieve("test", k=3, score_threshold=0.0)
        assert len(results) > 0

    async def test_ranks_sequential(self, mock_albert):
        """Les rangs sont séquentiels dans les résultats."""
        embedding_svc = EmbeddingService(mock_albert, dimension=384)
        store = FaissStore(384)
        chunks = _make_chunks(10)
        embeddings = await embedding_svc.embed_texts([c.text for c in chunks])
        store.add(embeddings, chunks)

        retriever = Retriever(embedding_svc, store)
        results = await retriever.retrieve("chunk 0", k=5, score_threshold=0.0)
        ranks = [r.rank for r in results]
        assert ranks == list(range(len(results)))


class TestMMRRerank:
    """Tests du reranking MMR."""

    def test_mmr_preserves_best(self):
        """Le premier résultat est toujours le plus pertinent."""
        results = [
            SearchResult(chunk=DocumentChunk(text="best", source_path="/a"), score=0.9),
            SearchResult(chunk=DocumentChunk(text="ok", source_path="/b"), score=0.7),
            SearchResult(chunk=DocumentChunk(text="meh", source_path="/c"), score=0.5),
        ]
        reranked = _mmr_rerank(results, _random_vec(), k=3)
        assert reranked[0].score == 0.9

    def test_mmr_diversifies(self):
        """MMR favorise la diversité (sources différentes)."""
        # a2 score éloigné de a1 → pénalité source 0.8 active
        # b1 avec score similaire mais source différente → pas de pénalité source
        results = [
            SearchResult(chunk=DocumentChunk(text="a1", source_path="/a"), score=0.9),
            SearchResult(chunk=DocumentChunk(text="a2", source_path="/a"), score=0.5),
            SearchResult(chunk=DocumentChunk(text="b1", source_path="/b"), score=0.6),
        ]
        reranked = _mmr_rerank(results, _random_vec(), k=3, lambda_param=0.5)
        # Avec λ=0.5, /b devrait être 2ème car source différente
        sources = [r.chunk.source_path for r in reranked[:2]]
        assert "/b" in sources

    def test_mmr_single_result(self):
        results = [SearchResult(chunk=DocumentChunk(text="only", source_path="/a"), score=0.9)]
        reranked = _mmr_rerank(results, _random_vec(), k=5)
        assert len(reranked) == 1

    def test_mmr_empty(self):
        assert _mmr_rerank([], _random_vec(), k=5) == []
