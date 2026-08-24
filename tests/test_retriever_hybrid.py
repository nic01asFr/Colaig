"""Tests du pipeline de recherche hybride : BM25 + RRF + HyDE."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.models import DocumentChunk, SearchResult
from colaig.rag.bm25_store import BM25Store
from colaig.rag.retriever import Retriever, _chunk_key, _mmr_rerank, _rrf_combine


def make_chunk(text: str, source_path: str = "/doc.txt", position: int = 0, section: str = "") -> DocumentChunk:
    return DocumentChunk(text=text, source_path=source_path, position=position, section=section)


def make_result(text: str, score: float = 0.9, source_path: str = "/doc.txt", position: int = 0) -> SearchResult:
    return SearchResult(chunk=make_chunk(text, source_path=source_path, position=position), score=score)


def make_embedding_service(embedding=None):
    svc = MagicMock()
    svc.embed_text = AsyncMock(return_value=embedding or [0.1] * 8)
    svc.embed_texts = AsyncMock(return_value=[[0.1] * 8])
    return svc


def make_store(results=None, count=3):
    store = MagicMock()
    store.count = count
    store.search = MagicMock(return_value=results or [])
    return store


# ── _chunk_key ─────────────────────────────────────────────────────────────


def test_chunk_key_unique_per_position():
    r1 = make_result("A", position=0)
    r2 = make_result("B", position=1)
    assert _chunk_key(r1) != _chunk_key(r2)


def test_chunk_key_same_chunk_same_key():
    r1 = make_result("A", source_path="/x.txt", position=5)
    r2 = make_result("A", source_path="/x.txt", position=5)
    assert _chunk_key(r1) == _chunk_key(r2)


# ── _rrf_combine ───────────────────────────────────────────────────────────


def test_rrf_combine_merges_results():
    faiss = [make_result("doc python", position=0, score=0.9)]
    bm25_chunk = make_chunk("doc python", position=0)
    bm25 = [(bm25_chunk, 5.0)]
    combined = _rrf_combine(faiss, bm25, k=5)
    assert len(combined) == 1
    # Score RRF = 1/(60+1) + 1/(60+1) > 1/(60+1)
    assert combined[0].score > 1 / 62


def test_rrf_combine_unique_docs_both_indexes():
    faiss = [make_result("doc A", position=0, score=0.9)]
    bm25 = [(make_chunk("doc B", position=1), 3.0)]
    combined = _rrf_combine(faiss, bm25, k=5)
    assert len(combined) == 2


def test_rrf_combine_sorted_desc():
    # doc commun en tête → score cumulé > docs uniques
    faiss = [
        make_result("commun", position=0, score=0.9),
        make_result("only_faiss", position=1, score=0.8),
    ]
    bm25 = [
        (make_chunk("commun", position=0), 5.0),
        (make_chunk("only_bm25", position=2), 4.0),
    ]
    combined = _rrf_combine(faiss, bm25, k=5)
    scores = [r.score for r in combined]
    assert scores == sorted(scores, reverse=True)


def test_rrf_combine_empty_bm25():
    faiss = [make_result("doc", position=0)]
    combined = _rrf_combine(faiss, [], k=5)
    assert len(combined) == 1


def test_rrf_combine_empty_faiss():
    bm25 = [(make_chunk("doc", position=0), 3.0)]
    combined = _rrf_combine([], bm25, k=5)
    assert len(combined) == 1


def test_rrf_combine_respects_k():
    faiss = [make_result(f"f{i}", position=i, score=0.9 - i * 0.1) for i in range(5)]
    bm25 = [(make_chunk(f"b{i}", position=i + 10), 5.0 - i) for i in range(5)]
    combined = _rrf_combine(faiss, bm25, k=3)
    assert len(combined) <= 3


# ── Retriever.retrieve — BM25 integration ────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_without_bm25():
    """Sans bm25_store, pipeline classique FAISS → MMR."""
    faiss_results = [make_result("doc", position=0, score=0.8)]
    store = make_store(faiss_results)
    svc = make_embedding_service()
    r = Retriever(svc, store=store)
    results = await r.retrieve("query", k=5)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_retrieve_with_bm25_activates_rrf():
    """Avec bm25_store, le pipeline passe par RRF."""
    faiss_results = [make_result("doc python", position=0, score=0.8)]
    store = make_store(faiss_results)
    svc = make_embedding_service()

    bm25 = BM25Store()
    bm25.add([make_chunk("doc python", position=0)])

    r = Retriever(svc, store=store)
    results = await r.retrieve("python", k=5, bm25_store=bm25)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_retrieve_empty_store_returns_empty():
    store = make_store([], count=0)
    svc = make_embedding_service()
    r = Retriever(svc, store=store)
    results = await r.retrieve("query")
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_no_store_returns_empty():
    svc = make_embedding_service()
    r = Retriever(svc)
    results = await r.retrieve("query")
    assert results == []


# ── HyDE ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hyde_expands_query():
    """HyDE combine l'embedding query + embedding réponse hypothétique."""
    faiss_results = [make_result("doc", position=0, score=0.8)]
    store = make_store(faiss_results)

    embed_calls = []

    async def mock_embed_text(text):
        embed_calls.append(text)
        return [0.5] * 8

    svc = make_embedding_service()
    svc.embed_text = AsyncMock(side_effect=mock_embed_text)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Réponse hypothétique générée par HyDE.")

    r = Retriever(svc, store=store, albert_client=llm, hyde_enabled=True)
    await r.retrieve("Quelle est la procédure ?", k=3)

    # embed_text doit avoir été appelé 2 fois : query + réponse HyDE
    assert len(embed_calls) >= 2


@pytest.mark.asyncio
async def test_hyde_fallback_on_error():
    """Si HyDE échoue, on continue avec l'embedding original."""
    faiss_results = [make_result("doc", position=0, score=0.8)]
    store = make_store(faiss_results)
    svc = make_embedding_service()

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=Exception("LLM down"))

    r = Retriever(svc, store=store, albert_client=llm, hyde_enabled=True)
    # Ne doit pas lever d'exception
    results = await r.retrieve("query", k=3)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_hyde_disabled_by_default():
    """Sans hyde_enabled, le LLM ne doit pas être appelé pour HyDE."""
    faiss_results = [make_result("doc", position=0, score=0.8)]
    store = make_store(faiss_results)
    svc = make_embedding_service()

    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Réponse hypothétique.")
    llm.rerank = AsyncMock(return_value=[])

    r = Retriever(svc, store=store, albert_client=llm, hyde_enabled=False)
    await r.retrieve("query", k=3)
    llm.chat.assert_not_called()


# ── _mmr_rerank (validation intégration) ────────────────────────────────


def test_mmr_returns_k_or_less():
    candidates = [make_result(f"doc {i}", position=i, score=0.9 - i * 0.05) for i in range(8)]
    selected = _mmr_rerank(candidates, [0.1] * 8, k=5)
    assert len(selected) <= 5


def test_mmr_single_candidate_passthrough():
    candidates = [make_result("seul", position=0, score=0.9)]
    assert _mmr_rerank(candidates, [0.1] * 8, k=3) == candidates


def test_mmr_includes_first_candidate():
    """Le premier candidat (meilleur score) est toujours sélectionné."""
    c1 = make_result("meilleur", source_path="/a.txt", position=0, score=0.95)
    c2 = make_result("second", source_path="/b.txt", position=0, score=0.80)
    c3 = make_result("troisième", source_path="/c.txt", position=0, score=0.70)
    selected = _mmr_rerank([c1, c2, c3], [0.1] * 8, k=2)
    # c1 toujours en premier
    assert selected[0].chunk.source_path == "/a.txt"
    assert len(selected) == 2
