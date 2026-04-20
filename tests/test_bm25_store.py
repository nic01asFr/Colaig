"""Tests de BM25Store — index lexical hybride."""

import pickle
from unittest.mock import MagicMock

import pytest

from colaig.models import DocumentChunk
from colaig.rag.bm25_store import BM25Store, _tokenize


def make_chunk(text: str, source_path: str = "/doc.txt", position: int = 0) -> DocumentChunk:
    return DocumentChunk(text=text, source_path=source_path, position=position)


# ── tokenize ───────────────────────────────────────────────────────────────


def test_tokenize_basic():
    tokens = _tokenize("Bonjour, monde!")
    assert "bonjour" in tokens
    assert "monde" in tokens


def test_tokenize_empty():
    assert _tokenize("") == []


def test_tokenize_lowercase():
    assert _tokenize("Python RAG") == ["python", "rag"]


# ── BM25Store — init & count ───────────────────────────────────────────────


def test_initial_count():
    store = BM25Store()
    assert store.count == 0


def test_count_after_add():
    store = BM25Store()
    store.add([make_chunk("test document"), make_chunk("autre doc")])
    assert store.count == 2


def test_add_empty_list_is_noop():
    store = BM25Store()
    store.add([])
    assert store.count == 0


# ── search ─────────────────────────────────────────────────────────────────


def test_search_returns_relevant():
    store = BM25Store()
    store.add([
        make_chunk("Le chien aboie dans le jardin"),
        make_chunk("Le chat dort sur le canapé"),
        make_chunk("La voiture roule sur la route"),
    ])
    results = store.search("chien jardin", k=2)
    assert len(results) >= 1
    # Le chunk "chien jardin" doit être en tête
    assert "chien" in results[0][0].text.lower()


def test_search_returns_tuple():
    store = BM25Store()
    store.add([make_chunk("python code test"), make_chunk("autre document sans python")])
    results = store.search("python", k=2)
    assert len(results) >= 1
    chunk, score = results[0]
    assert isinstance(chunk, DocumentChunk)
    # Chunk "python code test" doit avoir un meilleur score
    assert "python" in chunk.text.lower()


def test_search_empty_store_returns_empty():
    store = BM25Store()
    assert store.search("query") == []


def test_search_no_match_low_score():
    store = BM25Store()
    store.add([make_chunk("document totalement sans rapport")])
    store.add([make_chunk("autre document sans rapport aussi")])
    # Requête de tokens inexistants → scores négatifs ou nuls
    # BM25 retourne tout mais avec scores très bas — on vérifie juste que ça ne plante pas
    results = store.search("zzz qqq xxx")
    assert isinstance(results, list)


def test_search_respects_k():
    store = BM25Store()
    for i in range(10):
        store.add([make_chunk(f"document python numéro {i}", position=i)])
    results = store.search("python", k=3)
    assert len(results) <= 3


def test_search_sorted_by_score_desc():
    store = BM25Store()
    store.add([
        make_chunk("python python python code", position=0),
        make_chunk("python code", position=1),
        make_chunk("code générique", position=2),
    ])
    results = store.search("python", k=3)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


# ── delete_by_source ────────────────────────────────────────────────────────


def test_delete_by_source():
    store = BM25Store()
    store.add([make_chunk("doc A", source_path="/a.txt", position=0)])
    store.add([make_chunk("doc B", source_path="/b.txt", position=0)])
    n = store.delete_by_source("/a.txt")
    assert n == 1
    assert store.count == 1


def test_delete_excludes_from_search():
    store = BM25Store()
    store.add([make_chunk("données importantes RH", source_path="/rh.txt")])
    store.add([make_chunk("procédure générale", source_path="/general.txt")])
    store.delete_by_source("/rh.txt")
    results = store.search("RH données")
    sources = [r[0].source_path for r in results]
    assert "/rh.txt" not in sources


def test_delete_nonexistent_returns_zero():
    store = BM25Store()
    store.add([make_chunk("doc")])
    assert store.delete_by_source("/inexistant.txt") == 0


def test_has_deletions():
    store = BM25Store()
    store.add([make_chunk("doc", source_path="/x.txt")])
    assert not store.has_deletions()
    store.delete_by_source("/x.txt")
    assert store.has_deletions()


# ── rebuild ─────────────────────────────────────────────────────────────────


def test_rebuild_compacts_deletions():
    store = BM25Store()
    store.add([make_chunk("chunk A", source_path="/a.txt")])
    store.add([make_chunk("chunk B", source_path="/b.txt")])
    store.delete_by_source("/a.txt")
    store.rebuild()
    assert store.count == 1
    assert not store.has_deletions()


def test_rebuild_empty_store():
    store = BM25Store()
    store.rebuild()
    assert store.count == 0


# ── get_all_active_chunks ────────────────────────────────────────────────────


def test_get_all_active_chunks():
    store = BM25Store()
    store.add([make_chunk("A", source_path="/a.txt")])
    store.add([make_chunk("B", source_path="/b.txt")])
    store.delete_by_source("/a.txt")
    active = store.get_all_active_chunks()
    assert len(active) == 1
    assert active[0].source_path == "/b.txt"


# ── serialize / deserialize ─────────────────────────────────────────────────


def test_serialize_deserialize_roundtrip():
    store = BM25Store()
    store.add([
        make_chunk("document A", source_path="/a.txt"),
        make_chunk("document B", source_path="/b.txt"),
    ])
    data = store.serialize()
    assert isinstance(data, bytes)

    store2 = BM25Store()
    store2.deserialize(data)
    assert store2.count == 2


def test_serialize_compact_active_only():
    store = BM25Store()
    store.add([make_chunk("keep", source_path="/keep.txt")])
    store.add([make_chunk("remove", source_path="/remove.txt")])
    store.delete_by_source("/remove.txt")
    data = store.serialize()

    store2 = BM25Store()
    store2.deserialize(data)
    assert store2.count == 1
    assert store2.get_all_active_chunks()[0].source_path == "/keep.txt"


def test_deserialize_allows_search():
    store = BM25Store()
    store.add([make_chunk("machine learning python")])
    data = store.serialize()

    store2 = BM25Store()
    store2.deserialize(data)
    results = store2.search("python")
    assert len(results) >= 1
