"""Tests — FaissIndexRegistry + FaissStore async."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from colaig.models import DocumentChunk
from colaig.rag.faiss_store import FaissStore
from colaig.rag.index_registry import FaissIndexRegistry

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_store(n: int = 5, dim: int = 4) -> FaissStore:
    store = FaissStore(dimension=dim)
    vecs = np.random.rand(n, dim).astype(np.float32)
    chunks = [
        DocumentChunk(
            text=f"chunk {i}",
            source_path=f"/doc{i}.md",
            source_name=f"doc{i}",
        )
        for i in range(n)
    ]
    store.add(vecs.tolist(), chunks)
    return store


def make_chunk(i: int) -> DocumentChunk:
    return DocumentChunk(text=f"chunk {i}", source_path=f"/doc{i}.md", source_name=f"doc{i}")


# ── FaissStore async ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_async_returns_results():
    store = make_store(5, dim=4)
    query = [0.5, 0.5, 0.5, 0.5]
    results = await store.search_async(query, k=3)
    assert len(results) <= 3
    assert all(hasattr(r, "score") for r in results)


@pytest.mark.asyncio
async def test_add_async_increases_count():
    store = FaissStore(dimension=4)
    vec = [[0.1, 0.2, 0.3, 0.4]]
    chunk = make_chunk(0)
    await store.add_async(vec, [chunk])
    assert store.count == 1


@pytest.mark.asyncio
async def test_delete_by_source_async():
    store = FaissStore(dimension=4)
    vecs = [[0.1, 0.2, 0.3, 0.4]] * 3
    chunks = [make_chunk(i) for i in range(3)]
    chunks[0] = DocumentChunk(text="target", source_path="/target.md", source_name="t")
    await store.add_async(vecs, chunks)
    deleted = await store.delete_by_source_async("/target.md")
    assert deleted == 1
    assert store.count == 2


@pytest.mark.asyncio
async def test_rebuild_async():
    store = FaissStore(dimension=4)
    vecs = [[0.1, 0.2, 0.3, 0.4]] * 4
    chunks = [make_chunk(i) for i in range(4)]
    store.add(vecs, chunks)
    store.delete_by_source(chunks[0].source_path)
    assert len(store._deleted) == 1
    await store.rebuild_async()
    assert len(store._deleted) == 0
    assert store.count == 3


@pytest.mark.asyncio
async def test_concurrent_searches_non_blocking():
    """Plusieurs search_async en parallèle ne se bloquent pas mutuellement."""
    store = make_store(10, dim=8)
    query = [0.1] * 8
    results = await asyncio.gather(*[store.search_async(query, k=3) for _ in range(5)])
    assert len(results) == 5
    assert all(len(r) <= 3 for r in results)


# ── FaissIndexRegistry ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_set_and_get():
    registry = FaissIndexRegistry()
    store = make_store(3, dim=4)
    registry.set("ws1::docs", store)
    assert registry.get("ws1::docs") is store


@pytest.mark.asyncio
async def test_registry_get_or_load_calls_loader():
    registry = FaissIndexRegistry()
    store = make_store(3, dim=4)
    loaded = []

    async def loader():
        loaded.append(True)
        return store

    result = await registry.get_or_load("ws1::docs", loader)
    assert result is store
    assert len(loaded) == 1


@pytest.mark.asyncio
async def test_registry_get_or_load_caches():
    """Le loader n'est appelé qu'une fois même en concurrence."""
    registry = FaissIndexRegistry()
    store = make_store(3, dim=4)
    call_count = [0]

    async def loader():
        call_count[0] += 1
        await asyncio.sleep(0)  # yield
        return store

    results = await asyncio.gather(*[registry.get_or_load("ws1::docs", loader) for _ in range(5)])
    assert all(r is store for r in results)
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_registry_get_or_load_returns_none_on_error():
    registry = FaissIndexRegistry()

    async def failing_loader():
        raise FileNotFoundError("absent")

    result = await registry.get_or_load("ws1::missing", failing_loader)
    assert result is None


@pytest.mark.asyncio
async def test_registry_evict():
    registry = FaissIndexRegistry()
    registry.set("ws1::docs", make_store(2, dim=4))
    assert registry.count() == 1
    registry.evict("ws1::docs")
    assert registry.count() == 0
    assert registry.get("ws1::docs") is None


@pytest.mark.asyncio
async def test_registry_evict_prefix():
    registry = FaissIndexRegistry()
    registry.set("ws1::docs", make_store(2, dim=4))
    registry.set("ws1::behaviors", make_store(2, dim=4))
    registry.set("ws2::docs", make_store(2, dim=4))
    n = registry.evict_prefix("ws1::")
    assert n == 2
    assert registry.get("ws2::docs") is not None


@pytest.mark.asyncio
async def test_registry_search_returns_empty_for_missing_key():
    registry = FaissIndexRegistry()
    results = await registry.search("nonexistent", [0.1, 0.2, 0.3, 0.4], k=3)
    assert results == []


@pytest.mark.asyncio
async def test_registry_search_returns_results():
    registry = FaissIndexRegistry()
    store = make_store(5, dim=4)
    registry.set("ws1::docs", store)
    results = await registry.search("ws1::docs", [0.1, 0.2, 0.3, 0.4], k=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_registry_search_multi_parallel():
    """search_multi interroge plusieurs indexes en parallèle."""
    registry = FaissIndexRegistry()
    registry.set("ws1::docs", make_store(5, dim=4))
    registry.set("ws1::behaviors", make_store(3, dim=4))
    # "ws1::missing" n'existe pas → doit retourner []

    query = [0.1, 0.2, 0.3, 0.4]
    results = await registry.search_multi({
        "ws1::docs": (query, 3),
        "ws1::behaviors": (query, 2),
        "ws1::missing": (query, 5),
    })
    assert set(results.keys()) == {"ws1::docs", "ws1::behaviors", "ws1::missing"}
    assert len(results["ws1::docs"]) <= 3
    assert len(results["ws1::behaviors"]) <= 2
    assert results["ws1::missing"] == []


@pytest.mark.asyncio
async def test_registry_search_multi_empty():
    registry = FaissIndexRegistry()
    results = await registry.search_multi({})
    assert results == {}


@pytest.mark.asyncio
async def test_registry_save(tmp_path):
    """registry.save() sérialise et uploade via un storage mock."""
    from unittest.mock import AsyncMock

    registry = FaissIndexRegistry()
    store = make_store(3, dim=4)
    registry.set("ws1::docs", store)

    storage = AsyncMock()
    storage.upload = AsyncMock()

    await registry.save("ws1::docs", storage, "/ws1/.colaig/indexes/docs.faiss", "/ws1/.colaig/indexes/docs.pkl")

    assert storage.upload.call_count == 2
    call_paths = [c.args[0] for c in storage.upload.call_args_list]
    assert "/ws1/.colaig/indexes/docs.faiss" in call_paths
    assert "/ws1/.colaig/indexes/docs.pkl" in call_paths


@pytest.mark.asyncio
async def test_registry_save_missing_key():
    """save() sur une clé absente log un warning sans exception."""
    from unittest.mock import AsyncMock
    registry = FaissIndexRegistry()
    storage = AsyncMock()
    await registry.save("nonexistent", storage, "/a.faiss", "/a.pkl")
    storage.upload.assert_not_called()
