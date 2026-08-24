"""Tests de EmbeddingService — cache, namespace, stats, batch."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.rag.embeddings import EmbeddingService, _normalize_l2, _text_hash


def make_albert(vector=None):
    albert = MagicMock()
    albert.embed = AsyncMock(return_value=vector or [0.5, 0.5, 0.0])
    albert.embed_batch = AsyncMock(side_effect=lambda texts, **kw: [[0.5, 0.5, 0.0]] * len(texts))
    return albert


# ── _text_hash / _normalize_l2 ─────────────────────────────────────────────


def test_text_hash_stable():
    assert _text_hash("bonjour") == _text_hash("bonjour")


def test_text_hash_different():
    assert _text_hash("a") != _text_hash("b")


def test_normalize_l2_unit_vector():
    v = _normalize_l2([3.0, 4.0])
    norm = sum(x ** 2 for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_normalize_l2_zero_vector():
    # Vecteur nul → retourné tel quel (pas de division par zéro)
    v = _normalize_l2([0.0, 0.0, 0.0])
    assert v == [0.0, 0.0, 0.0]


# ── embed_text — cache ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_text_returns_normalized():
    albert = make_albert([3.0, 4.0, 0.0])
    svc = EmbeddingService(albert, dimension=3)
    result = await svc.embed_text("test")
    norm = sum(x ** 2 for x in result) ** 0.5
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_embed_text_cache_hit_skips_api():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_text("bonjour")
    await svc.embed_text("bonjour")
    assert albert.embed.call_count == 1  # 2e appel → cache


@pytest.mark.asyncio
async def test_embed_text_different_texts_call_api():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_text("un")
    await svc.embed_text("deux")
    assert albert.embed.call_count == 2


# ── embed_texts — batch cache-aware ────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_texts_empty():
    svc = EmbeddingService(make_albert(), dimension=3)
    assert await svc.embed_texts([]) == []


@pytest.mark.asyncio
async def test_embed_texts_all_uncached():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    result = await svc.embed_texts(["a", "b", "c"])
    assert len(result) == 3
    albert.embed_batch.assert_called_once()


@pytest.mark.asyncio
async def test_embed_texts_partial_cache():
    """Si un texte est déjà en cache, seuls les autres sont envoyés à l'API."""
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    # Pre-cacher "a"
    await svc.embed_text("a")
    albert.embed.reset_mock()

    # embed_texts avec "a" (en cache) + "b" (pas en cache)
    result = await svc.embed_texts(["a", "b"])
    assert len(result) == 2
    # Un seul texte envoyé en batch
    albert.embed_batch.assert_called_once()
    batch_sent = albert.embed_batch.call_args[0][0]
    assert "b" in batch_sent
    assert "a" not in batch_sent


@pytest.mark.asyncio
async def test_embed_texts_all_cached_no_api_call():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_texts(["x", "y"])
    albert.embed_batch.reset_mock()
    # Deuxième appel — tout en cache
    result = await svc.embed_texts(["x", "y"])
    assert len(result) == 2
    albert.embed_batch.assert_not_called()


# ── cache_stats ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_stats_initial():
    svc = EmbeddingService(make_albert(), dimension=3)
    stats = svc.cache_stats()
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["hit_rate"] == 0.0
    assert stats["size"] == 0


@pytest.mark.asyncio
async def test_cache_stats_after_hits_misses():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_text("a")   # miss
    await svc.embed_text("a")   # hit
    await svc.embed_text("b")   # miss
    stats = svc.cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["hit_rate"] == pytest.approx(1 / 3, abs=0.01)
    assert stats["size"] == 2


@pytest.mark.asyncio
async def test_clear_cache_resets_stats():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_text("a")
    await svc.embed_text("a")  # hit
    svc.clear_cache()
    stats = svc.cache_stats()
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["size"] == 0


# ── cache_namespace — isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_namespace_isolates_cache_between_instances():
    """Deux services avec des namespaces différents ne partagent pas leurs entrées de cache."""
    albert = make_albert()
    svc_a = EmbeddingService(albert, dimension=3, cache_namespace="client-A")
    svc_b = EmbeddingService(albert, dimension=3, cache_namespace="client-B")

    # svc_a embed "bonjour"
    await svc_a.embed_text("bonjour")
    assert albert.embed.call_count == 1

    # svc_b embed le même texte → doit appeler l'API (cache différent)
    await svc_b.embed_text("bonjour")
    assert albert.embed.call_count == 2


@pytest.mark.asyncio
async def test_namespace_same_namespace_shares_cache():
    """Même namespace → même clé → cache partagé (même instance)."""
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3, cache_namespace="client-A")
    await svc.embed_text("bonjour")
    await svc.embed_text("bonjour")  # doit être servi depuis le cache
    assert albert.embed.call_count == 1


@pytest.mark.asyncio
async def test_no_namespace_uses_plain_hash():
    """Sans namespace, le cache fonctionne normalement (pas de régression)."""
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3)
    await svc.embed_text("test")
    await svc.embed_text("test")
    assert albert.embed.call_count == 1


def test_cache_key_different_for_different_namespaces():
    """La même chaîne produit des clés différentes selon le namespace."""
    svc_a = EmbeddingService(MagicMock(), cache_namespace="ns-A")
    svc_b = EmbeddingService(MagicMock(), cache_namespace="ns-B")
    svc_none = EmbeddingService(MagicMock(), cache_namespace="")
    text = "même texte"
    assert svc_a._cache_key(text) != svc_b._cache_key(text)
    assert svc_a._cache_key(text) != svc_none._cache_key(text)


# ── cache_max_size ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_respects_max_size():
    """Le cache ne dépasse pas cache_max_size entrées."""
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3, cache_max_size=2)
    await svc.embed_text("a")
    await svc.embed_text("b")
    await svc.embed_text("c")  # ne doit pas être ajouté (cache plein)
    assert len(svc._cache) <= 2


@pytest.mark.asyncio
async def test_cache_unlimited_when_zero():
    albert = make_albert()
    svc = EmbeddingService(albert, dimension=3, cache_max_size=0)
    for i in range(50):
        await svc.embed_text(f"text_{i}")
    assert len(svc._cache) == 50
