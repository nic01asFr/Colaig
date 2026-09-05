"""
Tests pour colaig/agents/tools/document_index_tools.py

Couvre :
- SEARCH_DOCUMENT_INDEX_DEFINITION + create_search_document_index_handler
- LIST_DOCUMENT_INDEX_DEFINITION + create_list_document_index_handler
- GET_DOCUMENT_METADATA_DEFINITION + create_get_document_metadata_handler
"""

import json
from datetime import datetime

import pytest

from colaig.agents.tools.document_index_tools import (
    GET_DOCUMENT_METADATA_DEFINITION,
    LIST_DOCUMENT_INDEX_DEFINITION,
    SEARCH_DOCUMENT_INDEX_DEFINITION,
    create_get_document_metadata_handler,
    create_list_document_index_handler,
    create_search_document_index_handler,
)
from colaig.models import DocumentIndexSearchResult, DocumentRecord, DocumentStatus

WORKSPACE = "/espace-test/"


# =============================================================================
# Mock DocumentIndex
# =============================================================================


def _check_filters(record: DocumentRecord, filters: dict) -> bool:
    for key, value in filters.items():
        if key == "ai_category" and record.ai_category != value:
            return False
        if key == "status" and record.status.value != value:
            return False
        if key == "name_contains" and value.lower() not in record.name.lower():
            return False
    return True


class MockDocumentIndex:
    """Mock minimal du DocumentIndex pour les tests des outils."""

    def __init__(self):
        self.records: list[DocumentRecord] = []
        self.search_results: list[DocumentIndexSearchResult] = []

    async def search(
        self, query, workspace_path, k=5, filters=None
    ) -> list[DocumentIndexSearchResult]:
        results = self.search_results
        if filters:
            results = [r for r in results if _check_filters(r.record, filters)]
        return results[:k]

    async def list_documents(
        self, workspace_path, filters=None, limit=50
    ) -> list[DocumentRecord]:
        records = self.records
        if filters:
            records = [r for r in records if _check_filters(r, filters)]
        return records[:limit]

    async def get_document(
        self, workspace_path, doc_path
    ) -> DocumentRecord | None:
        for r in self.records:
            if r.path == doc_path:
                return r
        return None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_records() -> list[DocumentRecord]:
    return [
        DocumentRecord(
            path=f"{WORKSPACE}documents/guide-rh.pdf",
            name="guide-rh.pdf",
            size=12345,
            status=DocumentStatus.ANALYZED,
            ai_summary="Guide des procédures RH couvrant les congés.",
            ai_category="guide",
            ai_keywords=["congés", "RH"],
            ai_entities=["Direction RH"],
            ai_language="fr",
            ai_doc_type="guide pratique",
            chunk_count=5,
            faiss_doc_id=0,
            analyzed_at=datetime(2026, 2, 1, 10, 0, 0),
            indexed_at=datetime(2026, 2, 1, 9, 0, 0),
        ),
        DocumentRecord(
            path=f"{WORKSPACE}documents/rapport-annuel.docx",
            name="rapport-annuel.docx",
            size=98765,
            status=DocumentStatus.ANALYZED,
            ai_summary="Rapport annuel 2025 de la direction générale.",
            ai_category="rapport",
            ai_keywords=["bilan", "2025"],
            ai_entities=[],
            ai_language="fr",
            ai_doc_type="rapport",
            chunk_count=12,
            faiss_doc_id=1,
            analyzed_at=datetime(2026, 2, 2, 9, 0, 0),
            indexed_at=datetime(2026, 2, 2, 8, 0, 0),
        ),
    ]


@pytest.fixture
def mock_doc_index(sample_records) -> MockDocumentIndex:
    idx = MockDocumentIndex()
    idx.records = sample_records
    idx.search_results = [
        DocumentIndexSearchResult(record=sample_records[0], score=0.92, rank=0),
        DocumentIndexSearchResult(record=sample_records[1], score=0.71, rank=1),
    ]
    return idx


# =============================================================================
# Tests : SEARCH_DOCUMENT_INDEX
# =============================================================================


class TestSearchDocumentIndexHandler:

    async def test_returns_json_list(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure RH")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1

    async def test_result_contains_expected_fields(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure")
        data = json.loads(result)
        expected_keys = {"path", "name", "summary", "category", "keywords", "entities", "score", "status"}
        assert expected_keys <= set(data[0].keys())

    async def test_score_is_rounded_float(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure")
        data = json.loads(result)
        assert isinstance(data[0]["score"], float)

    async def test_respects_k_parameter(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure", k=1)
        data = json.loads(result)
        assert len(data) <= 1

    async def test_default_k_is_five(self, mock_doc_index):
        """Sans k explicite, k=5 est utilisé (comportement du mock)."""
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure")
        data = json.loads(result)
        assert len(data) <= 5

    async def test_category_filter_applied(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure", category="guide")
        data = json.loads(result)
        assert all(d["category"] == "guide" for d in data)

    async def test_status_filter_applied(self, mock_doc_index):
        handler = create_search_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler("procédure", status="analyzed")
        data = json.loads(result)
        assert all(d["status"] == "analyzed" for d in data)

    async def test_returns_empty_list_when_no_results(self):
        idx = MockDocumentIndex()  # search_results vide
        handler = create_search_document_index_handler(idx, WORKSPACE)
        result = await handler("inconnu")
        data = json.loads(result)
        assert data == []

    def test_definition_name(self):
        assert SEARCH_DOCUMENT_INDEX_DEFINITION.name == "search_document_index"

    def test_definition_has_required_query_param(self):
        params = {p.name: p for p in SEARCH_DOCUMENT_INDEX_DEFINITION.parameters}
        assert "query" in params
        assert params["query"].required is True

    def test_definition_has_optional_k_param(self):
        params = {p.name: p for p in SEARCH_DOCUMENT_INDEX_DEFINITION.parameters}
        assert "k" in params
        assert params["k"].required is False

    def test_definition_has_optional_category_param(self):
        params = {p.name: p for p in SEARCH_DOCUMENT_INDEX_DEFINITION.parameters}
        assert "category" in params
        assert params["category"].required is False

    def test_definition_category_rag(self):
        assert SEARCH_DOCUMENT_INDEX_DEFINITION.category == "rag"


# =============================================================================
# Tests : LIST_DOCUMENT_INDEX
# =============================================================================


class TestListDocumentIndexHandler:

    async def test_returns_json_list(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler()
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2

    async def test_result_contains_expected_fields(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler()
        data = json.loads(result)
        expected_keys = {"path", "name", "category", "summary", "keywords", "status", "size"}
        assert expected_keys <= set(data[0].keys())

    async def test_analyzed_at_is_isoformat_or_none(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler()
        data = json.loads(result)
        for item in data:
            val = item.get("analyzed_at")
            if val is not None:
                # Vérifier que c'est parseable comme datetime ISO
                datetime.fromisoformat(val)

    async def test_category_filter(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler(category="guide")
        data = json.loads(result)
        assert all(d["category"] == "guide" for d in data)

    async def test_status_filter(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler(status="analyzed")
        data = json.loads(result)
        assert all(d["status"] == "analyzed" for d in data)

    async def test_name_contains_filter(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler(name_contains="guide")
        data = json.loads(result)
        assert all("guide" in d["name"].lower() for d in data)

    async def test_respects_limit(self, mock_doc_index):
        handler = create_list_document_index_handler(mock_doc_index, WORKSPACE)
        result = await handler(limit=1)
        data = json.loads(result)
        assert len(data) <= 1

    async def test_empty_index_returns_empty_list(self):
        idx = MockDocumentIndex()  # records vide
        handler = create_list_document_index_handler(idx, WORKSPACE)
        result = await handler()
        data = json.loads(result)
        assert data == []

    def test_definition_name(self):
        assert LIST_DOCUMENT_INDEX_DEFINITION.name == "list_document_index"

    def test_definition_has_no_required_params(self):
        for param in LIST_DOCUMENT_INDEX_DEFINITION.parameters:
            assert param.required is False

    def test_definition_category_rag(self):
        assert LIST_DOCUMENT_INDEX_DEFINITION.category == "rag"


# =============================================================================
# Tests : GET_DOCUMENT_METADATA
# =============================================================================


class TestGetDocumentMetadataHandler:

    async def test_returns_json_object_for_existing(self, mock_doc_index, sample_records):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler(sample_records[0].path)
        data = json.loads(result)
        assert data["path"] == sample_records[0].path

    async def test_returns_all_ai_fields(self, mock_doc_index, sample_records):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler(sample_records[0].path)
        data = json.loads(result)
        expected_keys = {
            "path", "name", "size", "mime_type", "status",
            "ai_summary", "ai_category", "ai_entities", "ai_keywords",
            "ai_language", "ai_doc_type", "chunk_count",
            "analyzed_at", "indexed_at",
        }
        assert expected_keys <= set(data.keys())

    async def test_returns_correct_category(self, mock_doc_index, sample_records):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler(sample_records[0].path)
        data = json.loads(result)
        assert data["ai_category"] == "guide"

    async def test_not_found_returns_error_key(self, mock_doc_index):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler("/chemin/inexistant.pdf")
        data = json.loads(result)
        assert "error" in data

    async def test_error_message_contains_path(self, mock_doc_index):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler("/chemin/inexistant.pdf")
        data = json.loads(result)
        assert "/chemin/inexistant.pdf" in data["error"]

    async def test_analyzed_at_is_isoformat(self, mock_doc_index, sample_records):
        handler = create_get_document_metadata_handler(mock_doc_index, WORKSPACE)
        result = await handler(sample_records[0].path)
        data = json.loads(result)
        if data.get("analyzed_at"):
            datetime.fromisoformat(data["analyzed_at"])

    def test_definition_name(self):
        assert GET_DOCUMENT_METADATA_DEFINITION.name == "get_document_metadata"

    def test_definition_has_required_path_param(self):
        params = {p.name: p for p in GET_DOCUMENT_METADATA_DEFINITION.parameters}
        assert "path" in params
        assert params["path"].required is True

    def test_definition_category_rag(self):
        assert GET_DOCUMENT_METADATA_DEFINITION.category == "rag"
