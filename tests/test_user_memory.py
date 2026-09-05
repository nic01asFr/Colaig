"""Tests — UserMemory (3 rythmes)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from colaig.context.user_memory import MemoryFact, UserMemory, _auto_tag, _safe_user_id
from colaig.rag.faiss_store import FaissStore
from colaig.rag.index_registry import FaissIndexRegistry

# ── Helpers ───────────────────────────────────────────────────────────────────

DIM = 4


class _FakeFact:
    """Chunk compatible FaissStore._metadata (picklable, niveau module)."""
    def __init__(self, i: int = 0):
        self.text = f"fait {i}"
        self.source_path = f"memory::conv{i}"
        self.source_name = "user_memory"
        self.section = "general"
        self.tags = []
        self.ts = ""
        self.conversation_id = f"conv{i}"
        self.workspace_id = "/ws"


def make_registry_with_store(key: str, n: int = 3) -> tuple[FaissIndexRegistry, FaissStore]:
    import numpy as np
    registry = FaissIndexRegistry()
    store = FaissStore(dimension=DIM)
    vecs = np.random.rand(n, DIM).astype(np.float32)
    store.add(vecs.tolist(), [_FakeFact(i) for i in range(n)])
    registry.set(key, store)
    return registry, store


def make_user_memory(registry=None, albert=None, storage=None, embeddings=None) -> UserMemory:
    if registry is None:
        registry = FaissIndexRegistry()
    if storage is None:
        storage = AsyncMock()
        storage.download = AsyncMock(side_effect=FileNotFoundError)
        storage.upload = AsyncMock()
        storage.mkdir = AsyncMock()
    if embeddings is None:
        embeddings = AsyncMock()
        embeddings.embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
        embeddings.embed_text = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    return UserMemory(
        storage=storage,
        embeddings=embeddings,
        registry=registry,
        albert_client=albert,
        dimension=DIM,
    )


# ── _safe_user_id ─────────────────────────────────────────────────────────────

def test_safe_user_id_basic():
    # Cohérent avec workspace.py:personal_workspace_slug() — source unique de vérité
    assert _safe_user_id("@alice:tchap.fr") == "alice_tchap_fr"
    assert _safe_user_id("alice/bob?foo") == "alice_bob_foo"


def test_safe_user_id_max_length():
    long_id = "a" * 100
    assert len(_safe_user_id(long_id)) <= 64


def test_safe_user_id_matches_workspace_slug():
    """_safe_user_id() doit être identique à personal_workspace_slug()."""
    from colaig.context.workspace import personal_workspace_slug
    test_ids = [
        "@alice:tchap.fr",
        "@nicolas.laval:agent.tchap.gouv.fr",
        "alice/bob?foo",
        "",
        "françoise@org.fr",
    ]
    for uid in test_ids:
        assert _safe_user_id(uid) == personal_workspace_slug(uid), (
            f"Divergence pour {uid!r}: _safe_user_id={_safe_user_id(uid)!r} "
            f"!= personal_workspace_slug={personal_workspace_slug(uid)!r}"
        )


# ── _auto_tag ─────────────────────────────────────────────────────────────────

def test_auto_tag_preference():
    tags = _auto_tag(["L'utilisateur préfère les résumés courts"])
    assert "preference" in tags


def test_auto_tag_role():
    tags = _auto_tag(["Il est responsable de projet"])
    assert "role" in tags


def test_auto_tag_general_fallback():
    tags = _auto_tag(["texte sans indice"])
    assert tags == ["general"]


# ── Rythme 1 : read ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_returns_empty_when_no_store():
    um = make_user_memory()
    facts = await um.read("alice", "/ws", [0.1, 0.2, 0.3, 0.4])
    assert facts == []


@pytest.mark.asyncio
async def test_read_returns_facts_from_registry():
    key = "user::/ws::alice"
    registry, store = make_registry_with_store(key, n=3)
    um = make_user_memory(registry=registry)
    facts = await um.read("alice", "/ws", [0.1, 0.2, 0.3, 0.4], k=2)
    assert len(facts) <= 2
    assert all(isinstance(f, MemoryFact) for f in facts)


@pytest.mark.asyncio
async def test_read_loads_store_from_storage():
    """Si absent du registry, tente de charger depuis le storage."""
    import numpy as np

    from colaig.rag.faiss_store import FaissStore

    store = FaissStore(dimension=DIM)
    vecs = np.random.rand(2, DIM).astype(np.float32)
    store.add(vecs.tolist(), [_FakeFact(0), _FakeFact(1)])
    faiss_bytes, meta_bytes = store.serialize()

    storage = AsyncMock()
    storage.download = AsyncMock(side_effect=[faiss_bytes, meta_bytes])
    storage.mkdir = AsyncMock()
    storage.upload = AsyncMock()

    registry = FaissIndexRegistry()
    um = make_user_memory(registry=registry, storage=storage)
    facts = await um.read("alice", "/ws", [0.1, 0.2, 0.3, 0.4], k=3)
    # Le store a été chargé et mis en registry
    assert registry.get("user::/ws::alice") is not None


# ── Rythme 2 : schedule_extract / _extract_and_store ─────────────────────────

@pytest.mark.asyncio
async def test_schedule_extract_noop_without_albert():
    um = make_user_memory(albert=None)
    # Ne doit pas lever d'exception
    um.schedule_extract("alice", "/ws", "ma question", "la réponse", "conv1")


@pytest.mark.asyncio
async def test_extract_and_store_adds_facts():
    albert = AsyncMock()
    albert.chat = AsyncMock(return_value='{"facts": ["Alice aime les résumés"]}')

    embeddings = AsyncMock()
    embeddings.embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])

    storage = AsyncMock()
    storage.download = AsyncMock(side_effect=FileNotFoundError)
    storage.upload = AsyncMock()
    storage.mkdir = AsyncMock()

    registry = FaissIndexRegistry()
    um = UserMemory(storage=storage, embeddings=embeddings, registry=registry, albert_client=albert, dimension=DIM)

    await um._extract_and_store("alice", "/ws", "je préfère les résumés", "ok", "conv1")

    key = "user::/ws::alice"
    store = registry.get(key)
    assert store is not None
    assert store.count >= 1


@pytest.mark.asyncio
async def test_extract_and_store_skips_empty_facts():
    albert = AsyncMock()
    albert.chat = AsyncMock(return_value='{"facts": []}')

    registry = FaissIndexRegistry()
    um = make_user_memory(registry=registry, albert=albert)

    await um._extract_and_store("alice", "/ws", "bonjour", "bonjour", "conv1")
    assert registry.get("user::/ws::alice") is None


@pytest.mark.asyncio
async def test_extract_and_store_handles_malformed_json():
    albert = AsyncMock()
    albert.chat = AsyncMock(return_value="texte non-JSON")

    registry = FaissIndexRegistry()
    um = make_user_memory(registry=registry, albert=albert)
    # Ne doit pas lever d'exception
    await um._extract_and_store("alice", "/ws", "msg", "rep", "c")


# ── Rythme 3 : consolidate ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consolidate_noop_without_albert():
    key = "user::/ws::alice"
    registry, store = make_registry_with_store(key)
    um = make_user_memory(registry=registry, albert=None)
    await um.consolidate("alice", "/ws")  # pas d'erreur


@pytest.mark.asyncio
async def test_consolidate_saves_profile():
    albert = AsyncMock()
    albert.chat = AsyncMock(return_value='{"role": "agent", "expertise_areas": ["droit"]}')

    storage = AsyncMock()
    storage.upload = AsyncMock()
    storage.mkdir = AsyncMock()

    key = "user::/ws::alice"
    registry, store = make_registry_with_store(key)

    um = UserMemory(storage=storage, embeddings=AsyncMock(), registry=registry, albert_client=albert, dimension=DIM)
    await um.consolidate("alice", "/ws")

    storage.upload.assert_called_once()
    call_path = storage.upload.call_args.args[0]
    assert "profile.json" in call_path


@pytest.mark.asyncio
async def test_consolidate_noop_empty_store():
    albert = AsyncMock()
    registry = FaissIndexRegistry()
    store = FaissStore(dimension=DIM)  # store vide
    registry.set("user::/ws::alice", store)
    um = make_user_memory(registry=registry, albert=albert)
    await um.consolidate("alice", "/ws")
    albert.chat.assert_not_called()


# ── load_profile ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_profile_returns_dict():
    from colaig.models import UserProfile
    storage = AsyncMock()
    storage.download = AsyncMock(return_value=b'{"role": "agent"}')
    um = make_user_memory(storage=storage)
    profile = await um.load_profile("alice", "/ws")
    assert isinstance(profile, UserProfile)
    assert profile.role == "agent"


@pytest.mark.asyncio
async def test_load_profile_returns_empty_on_missing():
    storage = AsyncMock()
    storage.download = AsyncMock(side_effect=FileNotFoundError)
    um = make_user_memory(storage=storage)
    profile = await um.load_profile("alice", "/ws")
    assert profile is None


# ── MemoryFact properties ─────────────────────────────────────────────────────

def test_memory_fact_source_path():
    f = MemoryFact(text="test", conversation_id="conv123")
    assert f.source_path == "memory::conv123"


def test_memory_fact_source_name():
    f = MemoryFact(text="test")
    assert f.source_name == "user_memory"
