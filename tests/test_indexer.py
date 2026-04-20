"""Tests pour colaig/rag/indexer.py — Orchestration d'indexation."""

import pytest
from datetime import datetime

from colaig.rag.indexer import Indexer
from colaig.rag.chunker import Chunker
from colaig.rag.embeddings import EmbeddingService
from colaig.rag.faiss_store import FaissStore
from colaig.models import StorageFile, StorageEvent


DIM = 384
WORKSPACE = "/espace-test/"


@pytest.fixture
def components(mock_albert):
    """Crée les composants RAG nécessaires à l'indexer."""
    chunker = Chunker(chunk_size=200, chunk_overlap=20)
    embedding_svc = EmbeddingService(mock_albert, dimension=DIM)
    store = FaissStore(DIM)
    return chunker, embedding_svc, store


@pytest.fixture
def storage_with_docs(mock_storage):
    """Storage pré-rempli avec quelques documents."""
    mock_storage.add_file(
        f"{WORKSPACE}docs/guide.txt",
        b"La procedure de validation consiste en trois etapes importantes. "
        b"Premiere etape : soumettre le formulaire au bureau des entrees. "
        b"Deuxieme etape : le chef de service valide la demande. "
        b"Troisieme etape : archivage dans le systeme informatique.",
    )
    mock_storage.add_file(
        f"{WORKSPACE}docs/notes.md",
        b"# Notes de reunion\n\n"
        b"## Participants\n\nJean, Marie, Pierre\n\n"
        b"## Decisions\n\nLe budget est approuve pour le trimestre.",
    )
    mock_storage.add_file(
        f"{WORKSPACE}.colaig/config.yaml",
        b"workspace_id: test\nname: Espace test\n",
    )
    return mock_storage


class TestIndexWorkspace:
    """Tests de l'indexation complète d'un workspace."""

    async def test_index_workspace_counts(self, storage_with_docs, components):
        """index_workspace retourne le nombre de documents indexés."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        count = await indexer.index_workspace(WORKSPACE)
        assert count == 2  # guide.txt + notes.md

    async def test_index_workspace_populates_store(self, storage_with_docs, components):
        """Après indexation, le store contient des vecteurs."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        await indexer.index_workspace(WORKSPACE)
        assert store.count > 0

    async def test_index_workspace_skips_colaig_dir(self, storage_with_docs, components):
        """Les fichiers dans .colaig/ ne sont pas indexés."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        count = await indexer.index_workspace(WORKSPACE)
        # config.yaml n'est pas compté même si c'est du texte
        assert count == 2

    async def test_index_workspace_skips_unsupported(self, mock_storage, components):
        """Les fichiers non supportés sont ignorés."""
        mock_storage.add_file(f"{WORKSPACE}data/image.png", b"\x89PNG...")
        mock_storage.add_file(f"{WORKSPACE}data/report.txt", b"Some text content here.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        count = await indexer.index_workspace(WORKSPACE)
        assert count == 1  # seulement report.txt

    async def test_index_workspace_empty(self, mock_storage, components):
        """Workspace vide retourne 0."""
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        count = await indexer.index_workspace(WORKSPACE)
        assert count == 0


class TestIndexDocument:
    """Tests de l'indexation d'un document unique."""

    async def test_index_document_success(self, mock_storage, components):
        """Un document valide est indexé avec succès."""
        mock_storage.add_file("/doc.txt", b"Contenu du document de test suffisamment long pour faire un chunk.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        result = await indexer.index_document("/doc.txt", etag='"abc"')
        assert result is True
        assert store.count > 0

    async def test_index_document_caches_etag(self, mock_storage, components):
        """L'etag est mémorisé après indexation."""
        mock_storage.add_file("/doc.txt", b"Contenu du document de test suffisamment long pour faire un chunk.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt", etag='"abc"')
        assert indexer._known_etags["/doc.txt"] == '"abc"'

    async def test_index_document_same_etag_skipped(self, mock_storage, components):
        """Un document avec le même etag n'est pas ré-indexé."""
        mock_storage.add_file("/doc.txt", b"Contenu du document de test suffisamment long pour faire un chunk.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt", etag='"v1"')
        count_after_first = store.count
        result = await indexer.index_document("/doc.txt", etag='"v1"')
        assert result is False
        assert store.count == count_after_first

    async def test_index_document_new_etag_reindexes(self, mock_storage, components):
        """Un document avec un nouvel etag est ré-indexé."""
        mock_storage.add_file("/doc.txt", b"Contenu du document de test suffisamment long pour faire un chunk.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt", etag='"v1"')
        result = await indexer.index_document("/doc.txt", etag='"v2"')
        assert result is True

    async def test_index_document_empty_content(self, mock_storage, components):
        """Un document vide n'est pas indexé."""
        mock_storage.add_file("/empty.txt", b"   \n  \n  ")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        result = await indexer.index_document("/empty.txt")
        assert result is False

    async def test_index_document_no_etag(self, mock_storage, components):
        """Sans etag, le document est toujours indexé."""
        mock_storage.add_file("/doc.txt", b"Contenu du document de test suffisamment long pour faire un chunk.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        result1 = await indexer.index_document("/doc.txt", etag="")
        result2 = await indexer.index_document("/doc.txt", etag="")
        assert result1 is True
        assert result2 is True  # pas de cache sans etag


class TestCheckUpdates:
    """Tests de la vérification de mises à jour."""

    async def test_check_updates_detects_new(self, mock_storage, components):
        """check_updates indexe les nouveaux fichiers."""
        mock_storage.add_file(f"{WORKSPACE}new.txt", b"Nouveau document avec suffisamment de contenu pour le chunker.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        count = await indexer.check_updates(WORKSPACE)
        assert count == 1

    async def test_check_updates_skips_unchanged(self, storage_with_docs, components):
        """Les documents inchangés ne sont pas ré-indexés."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        # Première indexation
        await indexer.index_workspace(WORKSPACE)
        count_after = store.count
        # Check updates — rien n'a changé
        updated = await indexer.check_updates(WORKSPACE)
        assert updated == 0
        assert store.count == count_after

    async def test_check_updates_detects_modified(self, storage_with_docs, components):
        """Un document modifié (etag changé) est ré-indexé."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        await indexer.index_workspace(WORKSPACE)

        # Modifier un fichier (change l'etag via add_file)
        storage_with_docs.add_file(
            f"{WORKSPACE}docs/guide.txt",
            b"Contenu mis a jour avec de nouvelles informations importantes.",
        )
        updated = await indexer.check_updates(WORKSPACE)
        assert updated >= 1

    async def test_check_updates_removes_deleted(self, storage_with_docs, components):
        """Les documents supprimés sont retirés de l'index."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        await indexer.index_workspace(WORKSPACE)

        # Supprimer un fichier du storage
        path = f"{WORKSPACE}docs/notes.md"
        del storage_with_docs.files[path]
        del storage_with_docs.metadata[path]

        await indexer.check_updates(WORKSPACE)
        assert path not in indexer._known_etags


class TestPersistenceStorage:
    """Tests de sauvegarde/chargement de l'index via le storage."""

    async def test_save_to_storage(self, mock_storage, components, mock_albert):
        """save_to_storage upload les fichiers index.faiss et metadata.pkl."""
        chunker, emb, store = components
        # Ajouter des données dans le store
        mock_storage.add_file("/doc.txt", b"Test document contenu suffisant pour un chunk.")
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt")

        remote = "/espace/.colaig/indexes/"
        await indexer.save_to_storage(remote)

        assert f"{remote}index.faiss" in mock_storage.files
        assert f"{remote}metadata.pkl" in mock_storage.files

    async def test_load_from_storage(self, mock_storage, components, mock_albert):
        """load_from_storage restaure l'index depuis le storage."""
        chunker, emb, store = components
        mock_storage.add_file("/doc.txt", b"Test document contenu suffisant pour un chunk.")
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt")
        original_count = store.count

        # Sauvegarder
        remote = "/espace/.colaig/indexes/"
        await indexer.save_to_storage(remote)

        # Nouveau store vide
        store2 = FaissStore(DIM)
        assert store2.count == 0

        indexer2 = Indexer(mock_storage, chunker, emb, store2)
        loaded = await indexer2.load_from_storage(remote)
        assert loaded is True
        assert store2.count == original_count

    async def test_load_from_storage_missing(self, mock_storage, components):
        """load_from_storage retourne False si l'index n'existe pas."""
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        loaded = await indexer.load_from_storage("/nonexistent/")
        assert loaded is False


class TestHandleEvent:
    """Tests de handle_event() — point d'entrée universel pour les changements de fichiers."""

    async def test_created_indexes_document(self, mock_storage, components):
        mock_storage.add_file("/doc.txt", b"Contenu suffisant pour indexation et chunking correct.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        event = StorageEvent(type="file.created", path="/doc.txt", metadata={"etag": "v1"})
        result = await indexer.handle_event(event)
        assert result is True
        assert store.count > 0

    async def test_modified_reindexes_document(self, mock_storage, components):
        mock_storage.add_file("/doc.txt", b"Contenu suffisant pour indexation et chunking correct.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt", etag="v1")
        mock_storage.add_file("/doc.txt", b"Contenu mis a jour avec de nouvelles informations.")
        event = StorageEvent(type="file.modified", path="/doc.txt", metadata={"etag": "v2"})
        result = await indexer.handle_event(event)
        assert result is True
        assert indexer._known_etags["/doc.txt"] == "v2"

    async def test_deleted_removes_from_index(self, mock_storage, components):
        mock_storage.add_file("/doc.txt", b"Contenu suffisant pour indexation et chunking correct.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        await indexer.index_document("/doc.txt", etag="v1")
        assert store.count > 0
        event = StorageEvent(type="file.deleted", path="/doc.txt")
        result = await indexer.handle_event(event)
        assert result is True
        assert "/doc.txt" not in indexer._known_etags

    async def test_deleted_unknown_path_is_noop(self, mock_storage, components):
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        event = StorageEvent(type="file.deleted", path="/nonexistent.txt")
        result = await indexer.handle_event(event)
        assert result is True  # handle_event retourne True même si path inconnu

    async def test_skips_unsupported_file(self, mock_storage, components):
        mock_storage.add_file("/image.png", b"\x89PNG data")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        event = StorageEvent(type="file.created", path="/image.png")
        result = await indexer.handle_event(event)
        assert result is False

    async def test_skips_dotcolaig_path(self, mock_storage, components):
        mock_storage.add_file("/.colaig/config.yaml", b"workspace_id: test")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        event = StorageEvent(type="file.created", path="/.colaig/config.yaml")
        result = await indexer.handle_event(event)
        assert result is False

    async def test_unknown_event_type_returns_false(self, mock_storage, components):
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        event = StorageEvent(type="file.analyzed", path="/doc.txt")
        result = await indexer.handle_event(event)
        assert result is False

    async def test_check_updates_produces_events(self, mock_storage, components):
        """check_updates() peuple UpdateSummary.events avec des StorageEvent."""
        mock_storage.add_file(f"{WORKSPACE}new.txt", b"Nouveau document avec suffisamment de contenu.")
        chunker, emb, store = components
        indexer = Indexer(mock_storage, chunker, emb, store)
        result = await indexer.check_updates(WORKSPACE)
        assert len(result.events) == 1
        assert result.events[0].type == "file.created"
        assert result.events[0].path == f"{WORKSPACE}new.txt"

    async def test_check_updates_deleted_event(self, storage_with_docs, components):
        """check_updates() produit un file.deleted pour les fichiers supprimés."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        await indexer.index_workspace(WORKSPACE)
        path = f"{WORKSPACE}docs/notes.md"
        del storage_with_docs.files[path]
        del storage_with_docs.metadata[path]
        result = await indexer.check_updates(WORKSPACE)
        deleted_events = [e for e in result.events if e.type == "file.deleted"]
        assert len(deleted_events) == 1
        assert deleted_events[0].path == path

    async def test_check_updates_modified_event_type(self, storage_with_docs, components):
        """Un fichier déjà connu modifié produit file.modified (pas file.created)."""
        chunker, emb, store = components
        indexer = Indexer(storage_with_docs, chunker, emb, store)
        await indexer.index_workspace(WORKSPACE)
        storage_with_docs.add_file(
            f"{WORKSPACE}docs/guide.txt",
            b"Contenu mis a jour avec de nouvelles informations importantes.",
        )
        result = await indexer.check_updates(WORKSPACE)
        modified = [e for e in result.events if e.type == "file.modified"]
        assert len(modified) == 1
