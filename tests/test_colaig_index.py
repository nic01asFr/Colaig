"""Tests pour ColaigIndex — source de vérité clés/chemins FAISS."""

import pickle
from unittest.mock import AsyncMock

import numpy as np
import pytest

from colaig.rag.colaig_index import ColaigIndex

# ── Clés de registry ──────────────────────────────────────────────────────────


class TestDocsKey:
    def test_basic(self):
        assert ColaigIndex.docs_key("/espace-rh/") == "/espace-rh::docs"

    def test_strips_trailing_slash(self):
        assert ColaigIndex.docs_key("/espace-rh/") == ColaigIndex.docs_key("/espace-rh")

    def test_nested(self):
        assert ColaigIndex.docs_key("/org/ministere/") == "/org/ministere::docs"


class TestBehaviorsKey:
    def test_basic(self):
        assert ColaigIndex.behaviors_key("/ws/") == "/ws::behaviors"

    def test_strips_trailing_slash(self):
        assert ColaigIndex.behaviors_key("/ws/") == ColaigIndex.behaviors_key("/ws")


class TestSkillsKey:
    def test_basic(self):
        assert ColaigIndex.skills_key("/ws/") == "/ws::skills"

    def test_strips_trailing_slash(self):
        assert ColaigIndex.skills_key("/ws/") == ColaigIndex.skills_key("/ws")


class TestUserMemoryKey:
    def test_basic(self):
        key = ColaigIndex.user_memory_key("/espace-rh/", "alice_tchap_fr")
        assert key == "user::/espace-rh::alice_tchap_fr"

    def test_strips_trailing_slash(self):
        assert (
            ColaigIndex.user_memory_key("/ws/", "alice")
            == ColaigIndex.user_memory_key("/ws", "alice")
        )


class TestKnowledgeKey:
    def test_basic(self):
        assert ColaigIndex.knowledge_key("/ws/") == "/ws::knowledge"


class TestFederationConstants:
    def test_key(self):
        assert ColaigIndex.FEDERATION_KEY == "federation::workspaces"

    def test_faiss_path(self):
        assert ColaigIndex.FEDERATION_FAISS_PATH == "/.colaig/federation/workspaces.faiss"

    def test_meta_path(self):
        assert ColaigIndex.FEDERATION_META_PATH == "/.colaig/federation/workspaces.pkl"


# ── Chemins de persistance ────────────────────────────────────────────────────


class TestDocsPaths:
    def test_basic(self):
        faiss, meta = ColaigIndex.docs_paths("/espace-rh/")
        assert faiss == "/espace-rh/.colaig/indexes/index.faiss"
        assert meta == "/espace-rh/.colaig/indexes/metadata.pkl"

    def test_strips_trailing_slash(self):
        assert ColaigIndex.docs_paths("/ws/") == ColaigIndex.docs_paths("/ws")


class TestBehaviorsPaths:
    def test_basic(self):
        faiss, meta = ColaigIndex.behaviors_paths("/ws/")
        assert faiss == "/ws/.colaig/indexes/behaviors.faiss"
        assert meta == "/ws/.colaig/indexes/behaviors.pkl"


class TestSkillsPaths:
    def test_basic(self):
        faiss, meta = ColaigIndex.skills_paths("/ws/")
        assert faiss == "/ws/.colaig/indexes/skills.faiss"
        assert meta == "/ws/.colaig/indexes/skills.pkl"


class TestUserMemoryPaths:
    def test_faiss(self):
        faiss, meta = ColaigIndex.user_memory_paths("/ws/", "alice_tchap_fr")
        assert faiss == "/ws/.colaig/users/alice_tchap_fr/memory.faiss"
        assert meta == "/ws/.colaig/users/alice_tchap_fr/memory.pkl"

    def test_strips_trailing_slash(self):
        assert (
            ColaigIndex.user_memory_paths("/ws/", "alice")
            == ColaigIndex.user_memory_paths("/ws", "alice")
        )


class TestUserProfilePath:
    def test_basic(self):
        path = ColaigIndex.user_profile_path("/ws/", "alice_tchap_fr")
        assert path == "/ws/.colaig/users/alice_tchap_fr/profile.json"


class TestUserDir:
    def test_basic(self):
        d = ColaigIndex.user_dir("/ws/", "alice")
        assert d == "/ws/.colaig/users/alice/"


class TestKnowledgeJsonPath:
    def test_basic(self):
        path = ColaigIndex.knowledge_json_path("/ws/")
        assert path == "/ws/.colaig/workspace_knowledge.json"


# ── load_store ────────────────────────────────────────────────────────────────


class TestLoadStore:
    """Tests du chargement lazy via StorageProtocol."""

    def _make_store_bytes(self, dimension: int = 4, n: int = 2) -> tuple[bytes, bytes]:
        """Crée des bytes de FaissStore sérialisé valides."""
        import faiss

        index = faiss.IndexFlatIP(dimension)
        vecs = np.random.rand(n, dimension).astype("float32")
        faiss.normalize_L2(vecs)
        index.add(vecs)

        buf = faiss.serialize_index(index)
        faiss_bytes = np.array(buf).tobytes()

        from colaig.models import DocumentChunk

        chunks = [
            DocumentChunk(
                text=f"chunk {i}",
                source_path="/ws/doc.pdf",
                source_name="doc.pdf",
                position=i,
            )
            for i in range(n)
        ]
        meta_bytes = pickle.dumps(
            {"metadata": chunks, "deleted": set(), "dimension": dimension}
        )
        return faiss_bytes, meta_bytes

    @pytest.mark.asyncio
    async def test_returns_store_when_files_present(self):
        faiss_bytes, meta_bytes = self._make_store_bytes()
        storage = AsyncMock()
        storage.download.side_effect = [faiss_bytes, meta_bytes]

        store = await ColaigIndex.load_store(storage, "/ws/.colaig/indexes/index.faiss", "/ws/.colaig/indexes/metadata.pkl", dimension=4)

        assert store is not None
        assert store.count == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_file_absent(self):
        from colaig.exceptions import StorageFileNotFoundError

        storage = AsyncMock()
        storage.download.side_effect = StorageFileNotFoundError("absent")

        store = await ColaigIndex.load_store(storage, "/ws/.colaig/indexes/index.faiss", "/ws/.colaig/indexes/metadata.pkl")

        assert store is None

    @pytest.mark.asyncio
    async def test_returns_none_on_any_error(self):
        storage = AsyncMock()
        storage.download.side_effect = Exception("erreur réseau")

        store = await ColaigIndex.load_store(storage, "/ws/a.faiss", "/ws/a.pkl")

        assert store is None


# ── Cohérence clés/chemins ────────────────────────────────────────────────────


class TestKeyPathCoherence:
    """Vérifie que les clés et chemins sont cohérents entre eux."""

    def test_docs_prefix_matches_key(self):
        ws = "/espace-rh/"
        faiss, _ = ColaigIndex.docs_paths(ws)
        key = ColaigIndex.docs_key(ws)
        # Le chemin doit commencer par le workspace
        assert faiss.startswith(ws.rstrip("/"))
        assert key.startswith(ws.rstrip("/"))

    def test_behaviors_prefix_matches_key(self):
        ws = "/ws/"
        faiss, _ = ColaigIndex.behaviors_paths(ws)
        key = ColaigIndex.behaviors_key(ws)
        assert faiss.startswith(ws.rstrip("/"))
        assert key.startswith(ws.rstrip("/"))

    def test_user_memory_key_contains_ws_and_uid(self):
        ws = "/espace-rh/"
        uid = "alice_tchap_fr"
        key = ColaigIndex.user_memory_key(ws, uid)
        assert "espace-rh" in key
        assert uid in key

    def test_user_memory_paths_contain_uid(self):
        ws = "/ws/"
        uid = "bob_matrix_org"
        faiss, meta = ColaigIndex.user_memory_paths(ws, uid)
        assert uid in faiss
        assert uid in meta

    def test_all_index_keys_differ_for_same_workspace(self):
        ws = "/ws/"
        keys = {
            ColaigIndex.docs_key(ws),
            ColaigIndex.behaviors_key(ws),
            ColaigIndex.skills_key(ws),
            ColaigIndex.knowledge_key(ws),
        }
        assert len(keys) == 4, "Toutes les clés doivent être distinctes"

    def test_all_faiss_paths_differ_for_same_workspace(self):
        ws = "/ws/"
        paths = {
            ColaigIndex.docs_paths(ws)[0],
            ColaigIndex.behaviors_paths(ws)[0],
            ColaigIndex.skills_paths(ws)[0],
        }
        assert len(paths) == 3, "Tous les chemins .faiss doivent être distincts"
