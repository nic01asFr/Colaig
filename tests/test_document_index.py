"""
Tests pour colaig/rag/document_index.py — Service de métadonnées documentaires.

Couvre :
- scan_workspace : détection nouveaux/modifiés/supprimés
- analyze_pending : analyse IA via Albert
- search : recherche sémantique + filtres
- list_documents : requêtes structurées
- get_document : lookup par path
- save / load : persistance
- Helpers : _parse_json_from_response, _match_filters
"""

import json
import pytest

from colaig.rag.document_index import (
    DocumentIndex,
    _parse_json_from_response,
    _match_filters,
    _remote_dir,
)
from colaig.rag.embeddings import EmbeddingService
from colaig.models import DocumentRecord, DocumentStatus


DIM = 384
WORKSPACE = "/espace-test/"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def embedding_svc(mock_albert):
    return EmbeddingService(mock_albert, dimension=DIM)


@pytest.fixture
def doc_index(mock_storage, embedding_svc, mock_albert):
    return DocumentIndex(mock_storage, embedding_svc, mock_albert)


@pytest.fixture
def storage_with_docs(mock_storage):
    """Storage pré-rempli avec 2 documents texte + 1 fichier .colaig/ à ignorer."""
    mock_storage.add_file(
        f"{WORKSPACE}documents/guide.txt",
        b"La procedure de validation consiste en 3 etapes.",
    )
    mock_storage.add_file(
        f"{WORKSPACE}documents/note.md",
        b"# Note de service\n\nContenu important de la note.",
    )
    # Ce fichier doit être ignoré par scan_workspace (chemin .colaig/)
    mock_storage.add_file(
        f"{WORKSPACE}.colaig/config.yaml",
        b"workspace_id: test",
        "text/yaml",
    )
    return mock_storage


def _make_analysis_json(category: str = "guide") -> str:
    """Réponse JSON valide simulée par Albert."""
    return json.dumps({
        "summary": f"Document de type {category} couvrant la procédure.",
        "category": category,
        "entities": ["Service RH"],
        "keywords": ["procédure", "validation"],
        "language": "fr",
        "doc_type": "guide pratique",
    })


@pytest.fixture
async def analyzed_doc_index(storage_with_docs, mock_albert, embedding_svc):
    """DocumentIndex avec 2 documents analysés (ANALYZED + vecteurs)."""
    mock_albert.chat_responses = [_make_analysis_json("guide")]
    idx = DocumentIndex(storage_with_docs, embedding_svc, mock_albert)
    await idx.scan_workspace(WORKSPACE)
    await idx.analyze_pending(WORKSPACE, max_docs=10)
    return idx


# =============================================================================
# TestScanWorkspace
# =============================================================================


class TestScanWorkspace:

    async def test_scan_detects_new_files(self, storage_with_docs, doc_index):
        """Nouveaux fichiers → retourne leur nombre."""
        count = await doc_index.scan_workspace(WORKSPACE)
        assert count == 2  # guide.txt + note.md (.colaig/ ignoré)

    async def test_scan_creates_pending_records(self, storage_with_docs, doc_index):
        """Après scan, tous les records sont PENDING."""
        await doc_index.scan_workspace(WORKSPACE)
        docs = await doc_index.list_documents(WORKSPACE)
        assert len(docs) == 2
        assert all(d.status == DocumentStatus.PENDING for d in docs)

    async def test_scan_records_have_correct_names(self, storage_with_docs, doc_index):
        """Les records ont les bons noms de fichier."""
        await doc_index.scan_workspace(WORKSPACE)
        docs = await doc_index.list_documents(WORKSPACE)
        names = {d.name for d in docs}
        assert names == {"guide.txt", "note.md"}

    async def test_scan_detects_modified_file(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Fichier modifié (etag changé) → count = 1."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json()]
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)

        # Modifier un fichier
        storage_with_docs.add_file(
            f"{WORKSPACE}documents/guide.txt",
            b"Nouveau contenu completement different.",
        )
        count = await doc_index.scan_workspace(WORKSPACE)
        assert count == 1

    async def test_scan_detects_modified_resets_to_pending(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Fichier modifié → status repasse à PENDING."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json()]
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)

        storage_with_docs.add_file(
            f"{WORKSPACE}documents/guide.txt",
            b"Nouveau contenu completement different.",
        )
        await doc_index.scan_workspace(WORKSPACE)

        record = await doc_index.get_document(
            WORKSPACE, f"{WORKSPACE}documents/guide.txt"
        )
        assert record is not None
        assert record.status == DocumentStatus.PENDING

    async def test_scan_removes_deleted_files(self, storage_with_docs, doc_index):
        """Fichier supprimé du storage → retiré du registry."""
        await doc_index.scan_workspace(WORKSPACE)
        await storage_with_docs.delete(f"{WORKSPACE}documents/guide.txt")
        await doc_index.scan_workspace(WORKSPACE)

        docs = await doc_index.list_documents(WORKSPACE)
        paths = {d.path for d in docs}
        assert f"{WORKSPACE}documents/guide.txt" not in paths

    async def test_scan_ignores_colaig_dir(self, storage_with_docs, doc_index):
        """Les fichiers dans .colaig/ sont ignorés."""
        await doc_index.scan_workspace(WORKSPACE)
        docs = await doc_index.list_documents(WORKSPACE)
        assert all("/.colaig/" not in d.path for d in docs)

    async def test_scan_empty_workspace_returns_zero(self, mock_storage, doc_index):
        """Workspace sans fichiers supportés → count = 0."""
        count = await doc_index.scan_workspace(WORKSPACE)
        assert count == 0


# =============================================================================
# TestAnalyzePending
# =============================================================================


class TestAnalyzePending:

    async def test_analyze_pending_changes_status(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Après analyze_pending, les docs sont ANALYZED."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json()]
        analyzed = await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        assert analyzed == 2

        docs = await doc_index.list_documents(WORKSPACE, filters={"status": "analyzed"})
        assert len(docs) == 2

    async def test_analyze_pending_fills_ai_fields(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Les champs IA sont remplis après analyse."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json("rapport")]
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        docs = await doc_index.list_documents(WORKSPACE)
        for d in docs:
            assert d.ai_category == "rapport"
            assert d.ai_language == "fr"
            assert d.ai_summary
            assert d.ai_keywords

    async def test_analyze_pending_fills_summary(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """ai_summary est non vide après analyse."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json()]
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        docs = await doc_index.list_documents(WORKSPACE)
        assert all(d.ai_summary for d in docs)

    async def test_analyze_pending_adds_faiss_vector(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Un vecteur FAISS est créé pour chaque document analysé."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = [_make_analysis_json()]
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        docs = await doc_index.list_documents(WORKSPACE)
        assert all(d.faiss_doc_id >= 0 for d in docs)

    async def test_analyze_pending_respects_max_docs(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """max_docs limite le nombre de documents analysés."""
        await doc_index.scan_workspace(WORKSPACE)  # 2 PENDING
        mock_albert.chat_responses = [_make_analysis_json()]
        analyzed = await doc_index.analyze_pending(WORKSPACE, max_docs=1)
        assert analyzed == 1

    async def test_analyze_pending_empty_document_becomes_error(
        self, mock_storage, mock_albert, doc_index
    ):
        """Document avec contenu vide → status ERROR."""
        mock_storage.add_file(
            f"{WORKSPACE}docs/empty.txt", b"", "text/plain"
        )
        await doc_index.scan_workspace(WORKSPACE)
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        docs = await doc_index.list_documents(WORKSPACE)
        assert docs[0].status == DocumentStatus.ERROR

    async def test_analyze_pending_non_json_response_uses_fallback(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """Réponse Albert non-JSON → fallback activé, docs quand même ANALYZED."""
        await doc_index.scan_workspace(WORKSPACE)
        mock_albert.chat_responses = ["Pas de JSON ici, juste du texte libre."]
        analyzed = await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        # Le fallback retourne un dict minimal → analyse réussit
        assert analyzed == 2
        docs = await doc_index.list_documents(WORKSPACE)
        assert all(d.status == DocumentStatus.ANALYZED for d in docs)

    async def test_analyze_pending_disabled_via_config(
        self, storage_with_docs, mock_albert, embedding_svc
    ):
        """Si document_index_ai_analysis=False, analyze_pending retourne 0."""
        from colaig.models import ColaigConfig
        cfg = ColaigConfig(
            storage_backend="local",
            messaging_backend="matrix",
            document_index_ai_analysis=False,
        )
        idx = DocumentIndex(
            storage_with_docs, embedding_svc, mock_albert, config=cfg
        )
        await idx.scan_workspace(WORKSPACE)
        result = await idx.analyze_pending(WORKSPACE, max_docs=10)
        assert result == 0


# =============================================================================
# TestSearch
# =============================================================================


class TestSearch:

    async def test_search_returns_results(self, analyzed_doc_index):
        results = await analyzed_doc_index.search("procédure validation", WORKSPACE, k=5)
        assert len(results) >= 1

    async def test_search_result_has_document_record(self, analyzed_doc_index):
        results = await analyzed_doc_index.search("procédure", WORKSPACE)
        assert all(isinstance(r.record, DocumentRecord) for r in results)

    async def test_search_results_have_score(self, analyzed_doc_index):
        results = await analyzed_doc_index.search("procédure", WORKSPACE)
        assert all(isinstance(r.score, float) for r in results)

    async def test_search_respects_k(self, analyzed_doc_index):
        results = await analyzed_doc_index.search("procédure", WORKSPACE, k=1)
        assert len(results) <= 1

    async def test_search_with_category_filter(self, analyzed_doc_index):
        results = await analyzed_doc_index.search(
            "procédure", WORKSPACE, filters={"ai_category": "guide"}
        )
        assert all(r.record.ai_category == "guide" for r in results)

    async def test_search_with_status_filter(self, analyzed_doc_index):
        results = await analyzed_doc_index.search(
            "procédure", WORKSPACE, filters={"status": "analyzed"}
        )
        assert all(r.record.status == DocumentStatus.ANALYZED for r in results)

    async def test_search_empty_query_returns_all(self, analyzed_doc_index):
        """Requête vide → liste tous les docs filtrés."""
        results = await analyzed_doc_index.search("", WORKSPACE, k=10)
        assert len(results) == 2

    async def test_search_on_empty_index_returns_empty(self, doc_index):
        """Index vide → résultats vides."""
        results = await doc_index.search("procédure", WORKSPACE)
        assert results == []

    async def test_search_unknown_category_filter_returns_empty(
        self, analyzed_doc_index
    ):
        """Filtre sur catégorie inexistante → 0 résultats."""
        results = await analyzed_doc_index.search(
            "procédure", WORKSPACE, filters={"ai_category": "categorie-inexistante"}
        )
        assert results == []


# =============================================================================
# TestListDocuments
# =============================================================================


class TestListDocuments:

    async def test_list_all(self, analyzed_doc_index):
        docs = await analyzed_doc_index.list_documents(WORKSPACE)
        assert len(docs) == 2

    async def test_list_filter_by_status_pending(
        self, storage_with_docs, doc_index
    ):
        """Après scan seul (pas d'analyse), tous sont PENDING."""
        await doc_index.scan_workspace(WORKSPACE)
        pending = await doc_index.list_documents(
            WORKSPACE, filters={"status": "pending"}
        )
        assert len(pending) == 2

    async def test_list_filter_by_category(self, analyzed_doc_index):
        guides = await analyzed_doc_index.list_documents(
            WORKSPACE, filters={"ai_category": "guide"}
        )
        assert all(d.ai_category == "guide" for d in guides)

    async def test_list_filter_by_name_contains(self, analyzed_doc_index):
        docs = await analyzed_doc_index.list_documents(
            WORKSPACE, filters={"name_contains": "guide"}
        )
        assert all("guide" in d.name.lower() for d in docs)

    async def test_list_filter_name_case_insensitive(self, analyzed_doc_index):
        docs = await analyzed_doc_index.list_documents(
            WORKSPACE, filters={"name_contains": "GUIDE"}
        )
        assert len(docs) >= 1

    async def test_list_respects_limit(self, analyzed_doc_index):
        docs = await analyzed_doc_index.list_documents(WORKSPACE, limit=1)
        assert len(docs) == 1

    async def test_list_empty_workspace(self, doc_index):
        docs = await doc_index.list_documents(WORKSPACE)
        assert docs == []


# =============================================================================
# TestGetDocument
# =============================================================================


class TestGetDocument:

    async def test_get_existing_document(self, storage_with_docs, doc_index):
        await doc_index.scan_workspace(WORKSPACE)
        path = f"{WORKSPACE}documents/guide.txt"
        record = await doc_index.get_document(WORKSPACE, path)
        assert record is not None
        assert record.path == path

    async def test_get_missing_document_returns_none(self, doc_index):
        record = await doc_index.get_document(WORKSPACE, "/inexistant.pdf")
        assert record is None

    async def test_get_correct_name(self, storage_with_docs, doc_index):
        await doc_index.scan_workspace(WORKSPACE)
        record = await doc_index.get_document(
            WORKSPACE, f"{WORKSPACE}documents/guide.txt"
        )
        assert record.name == "guide.txt"


# =============================================================================
# TestPersistence
# =============================================================================


class TestPersistence:

    async def test_save_and_load_restores_registry(
        self, storage_with_docs, mock_albert, embedding_svc
    ):
        """Après save + load, le registry est intact."""
        mock_albert.chat_responses = [_make_analysis_json()]
        idx = DocumentIndex(storage_with_docs, embedding_svc, mock_albert)
        await idx.scan_workspace(WORKSPACE)
        await idx.analyze_pending(WORKSPACE, max_docs=10)
        await idx.save(WORKSPACE)

        # Nouvel index chargé depuis le storage
        idx2 = DocumentIndex(storage_with_docs, embedding_svc, mock_albert)
        loaded = await idx2.load(WORKSPACE)
        assert loaded is True
        docs = await idx2.list_documents(WORKSPACE)
        assert len(docs) == 2

    async def test_save_and_load_restores_status(
        self, storage_with_docs, mock_albert, embedding_svc
    ):
        """Les statuts ANALYZED sont préservés après reload."""
        mock_albert.chat_responses = [_make_analysis_json()]
        idx = DocumentIndex(storage_with_docs, embedding_svc, mock_albert)
        await idx.scan_workspace(WORKSPACE)
        await idx.analyze_pending(WORKSPACE, max_docs=10)
        await idx.save(WORKSPACE)

        idx2 = DocumentIndex(storage_with_docs, embedding_svc, mock_albert)
        await idx2.load(WORKSPACE)
        docs = await idx2.list_documents(WORKSPACE)
        assert all(d.status == DocumentStatus.ANALYZED for d in docs)

    async def test_save_creates_three_files(
        self, storage_with_docs, mock_albert, doc_index
    ):
        """save() crée index.faiss + metadata.pkl + registry.json."""
        mock_albert.chat_responses = [_make_analysis_json()]
        await doc_index.scan_workspace(WORKSPACE)
        await doc_index.analyze_pending(WORKSPACE, max_docs=10)
        await doc_index.save(WORKSPACE)

        remote = _remote_dir(WORKSPACE)
        assert await storage_with_docs.exists(f"{remote}/index.faiss")
        assert await storage_with_docs.exists(f"{remote}/metadata.pkl")
        assert await storage_with_docs.exists(f"{remote}/registry.json")

    async def test_load_missing_returns_false(self, doc_index):
        """load() retourne False si les fichiers n'existent pas."""
        loaded = await doc_index.load("/workspace-inexistant/")
        assert loaded is False

    async def test_save_without_loaded_state_is_noop(self, doc_index):
        """save() sans état en mémoire ne lève pas d'exception."""
        await doc_index.save(WORKSPACE)  # état vide → no-op silencieux


# =============================================================================
# TestHelpers
# =============================================================================


class TestParseJsonFromResponse:

    def test_direct_json(self):
        raw = '{"summary": "test", "category": "guide"}'
        result = _parse_json_from_response(raw)
        assert result["category"] == "guide"

    def test_json_with_surrounding_text(self):
        raw = 'Voici le résultat : {"summary": "test", "category": "rapport"} fin.'
        result = _parse_json_from_response(raw)
        assert result["category"] == "rapport"

    def test_json_fallback_on_invalid(self):
        raw = "Ce n'est pas du JSON du tout."
        result = _parse_json_from_response(raw)
        assert "summary" in result
        assert result["category"] == "autre"
        assert result["entities"] == []

    def test_fallback_preserves_text_in_summary(self):
        raw = "Texte libre quelconque."
        result = _parse_json_from_response(raw)
        assert raw[:200] in result["summary"] or result["summary"] == raw.strip()


class TestMatchFilters:

    def test_status_match(self):
        record = DocumentRecord(path="/test.txt", status=DocumentStatus.ANALYZED)
        assert _match_filters(record, {"status": "analyzed"})

    def test_status_no_match(self):
        record = DocumentRecord(path="/test.txt", status=DocumentStatus.ANALYZED)
        assert not _match_filters(record, {"status": "pending"})

    def test_category_match(self):
        record = DocumentRecord(path="/test.txt", ai_category="guide")
        assert _match_filters(record, {"ai_category": "guide"})

    def test_category_no_match(self):
        record = DocumentRecord(path="/test.txt", ai_category="guide")
        assert not _match_filters(record, {"ai_category": "rapport"})

    def test_name_contains_case_insensitive(self):
        record = DocumentRecord(path="/test.txt", name="Guide-RH.pdf")
        assert _match_filters(record, {"name_contains": "guide"})
        assert _match_filters(record, {"name_contains": "RH"})
        assert not _match_filters(record, {"name_contains": "rapport"})

    def test_multiple_filters_all_must_match(self):
        record = DocumentRecord(
            path="/test.txt", ai_category="guide",
            status=DocumentStatus.ANALYZED
        )
        assert _match_filters(record, {"ai_category": "guide", "status": "analyzed"})
        assert not _match_filters(record, {"ai_category": "guide", "status": "pending"})

    def test_empty_filters_always_true(self):
        record = DocumentRecord(path="/test.txt")
        assert _match_filters(record, {})
