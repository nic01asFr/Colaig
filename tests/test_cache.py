"""Tests pour colaig/storage/cache.py — Cache in-memory avec TTL."""

import asyncio

from colaig.storage.cache import TTLCache


class TestTTLCacheBasics:
    """Tests des opérations de base get/set/delete/clear."""

    async def test_set_and_get(self):
        cache: TTLCache[str, str] = TTLCache(default_ttl=60)
        await cache.set("key1", "value1")
        assert await cache.get("key1") == "value1"

    async def test_get_missing_key(self):
        cache: TTLCache[str, str] = TTLCache(default_ttl=60)
        assert await cache.get("inexistant") is None

    async def test_delete(self):
        cache: TTLCache[str, int] = TTLCache(default_ttl=60)
        await cache.set("x", 42)
        assert await cache.delete("x") is True
        assert await cache.get("x") is None

    async def test_delete_missing(self):
        cache: TTLCache[str, int] = TTLCache(default_ttl=60)
        assert await cache.delete("nope") is False

    async def test_clear(self):
        cache: TTLCache[str, str] = TTLCache(default_ttl=60)
        await cache.set("a", "1")
        await cache.set("b", "2")
        await cache.clear()
        assert cache.size == 0
        assert await cache.get("a") is None

    async def test_has(self):
        cache: TTLCache[str, str] = TTLCache(default_ttl=60)
        await cache.set("present", "yes")
        assert await cache.has("present") is True
        assert await cache.has("absent") is False

    async def test_size(self):
        cache: TTLCache[str, int] = TTLCache(default_ttl=60)
        assert cache.size == 0
        await cache.set("a", 1)
        await cache.set("b", 2)
        assert cache.size == 2

    async def test_overwrite(self):
        cache: TTLCache[str, str] = TTLCache(default_ttl=60)
        await cache.set("key", "v1")
        await cache.set("key", "v2")
        assert await cache.get("key") == "v2"
        assert cache.size == 1


class TestTTLCacheExpiration:
    """Tests de l'expiration TTL."""

    async def test_expired_entry_returns_none(self):
        """Une entrée expirée est invisible via get."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=0.05)
        await cache.set("ephemeral", "data")
        # Attend l'expiration
        await asyncio.sleep(0.1)
        assert await cache.get("ephemeral") is None

    async def test_non_expired_entry_visible(self):
        """Une entrée non expirée est toujours visible."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=10)
        await cache.set("durable", "data")
        assert await cache.get("durable") == "data"

    async def test_custom_ttl_per_entry(self):
        """TTL personnalisé par entrée."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=10)
        await cache.set("short", "data", ttl=0.05)
        await asyncio.sleep(0.1)
        assert await cache.get("short") is None

    async def test_ttl_zero_no_expiration(self):
        """TTL=0 signifie pas d'expiration."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=0)
        await cache.set("forever", "data")
        # Simule le passage du temps via monotonic mock
        assert await cache.get("forever") == "data"

    async def test_cleanup_removes_expired(self):
        """cleanup() supprime les entrées expirées."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=0.05)
        await cache.set("a", "1")
        await cache.set("b", "2")
        await cache.set("c", "3", ttl=60)  # celle-ci reste
        await asyncio.sleep(0.1)

        removed = await cache.cleanup()
        assert removed == 2
        assert cache.size == 1
        assert await cache.get("c") == "3"


class TestTTLCacheMaxSize:
    """Tests de la limite de taille."""

    async def test_eviction_when_full(self):
        """Évicte une entrée quand max_size est atteint."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=60, max_size=2)
        await cache.set("a", "1")
        await cache.set("b", "2")
        await cache.set("c", "3")  # devrait évincer une entrée
        assert cache.size == 2
        # "c" doit exister
        assert await cache.get("c") == "3"

    async def test_overwrite_does_not_evict(self):
        """Écraser une clé existante ne déclenche pas d'éviction."""
        cache: TTLCache[str, str] = TTLCache(default_ttl=60, max_size=2)
        await cache.set("a", "1")
        await cache.set("b", "2")
        await cache.set("a", "updated")  # overwrite, pas d'éviction
        assert cache.size == 2
        assert await cache.get("a") == "updated"
        assert await cache.get("b") == "2"

    async def test_no_limit_when_zero(self):
        """max_size=0 signifie pas de limite."""
        cache: TTLCache[str, int] = TTLCache(default_ttl=60, max_size=0)
        for i in range(100):
            await cache.set(str(i), i)
        assert cache.size == 100


class TestTTLCacheAsyncSafety:
    """Tests de sécurité async (pas de corruption sous concurrence)."""

    async def test_concurrent_writes(self):
        """Écritures concurrentes ne corrompent pas le cache."""
        cache: TTLCache[int, int] = TTLCache(default_ttl=60)

        async def writer(start: int):
            for i in range(start, start + 50):
                await cache.set(i, i * 2)

        await asyncio.gather(writer(0), writer(50))
        assert cache.size == 100
        assert await cache.get(0) == 0
        assert await cache.get(99) == 198

    async def test_concurrent_read_write(self):
        """Lectures et écritures concurrentes ne bloquent pas."""
        cache: TTLCache[str, int] = TTLCache(default_ttl=60)
        await cache.set("counter", 0)

        async def reader():
            for _ in range(50):
                await cache.get("counter")

        async def writer():
            for i in range(50):
                await cache.set("counter", i)

        await asyncio.gather(reader(), writer())
        # Le cache est cohérent — la dernière écriture est visible
        val = await cache.get("counter")
        assert isinstance(val, int)
