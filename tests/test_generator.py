"""Tests pour colaig/rag/generator.py — Générateur de réponses."""

import pytest

from colaig.exceptions import GenerationError
from colaig.models import (
    ContextMode,
    DocumentChunk,
    GeneratedResponse,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.rag.generator import Generator, _extract_sources, _format_documents


@pytest.fixture
def context() -> WorkspaceContext:
    ws = WorkspaceConfig(
        workspace_id="test",
        name="Test",
        storage_path="/test/",
        system_prompt="Tu es un assistant de test.",
        rag_enabled=True,
    )
    return WorkspaceContext(
        workspace=ws,
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es un assistant de test.",
    )


@pytest.fixture
def search_results() -> list[SearchResult]:
    return [
        SearchResult(
            chunk=DocumentChunk(
                text="La procédure consiste en 3 étapes.",
                source_path="/docs/guide.txt",
                source_name="guide.txt",
                section="Procédure",
            ),
            score=0.85,
        ),
        SearchResult(
            chunk=DocumentChunk(
                text="Le formulaire doit être rempli avant le 15.",
                source_path="/docs/formulaire.txt",
                source_name="formulaire.txt",
            ),
            score=0.72,
        ),
    ]


class TestGenerator:
    """Tests du générateur de réponses."""

    async def test_generate_success(self, mock_albert, context, search_results):
        """Génère une réponse avec succès."""
        gen = Generator(mock_albert)
        response = await gen.generate("Quelle procédure ?", context, search_results)
        assert isinstance(response, GeneratedResponse)
        assert response.text != ""

    async def test_generate_sources_extracted(self, mock_albert, context, search_results):
        """Les sources sont extraites des résultats RAG."""
        gen = Generator(mock_albert)
        response = await gen.generate("Quelle procédure ?", context, search_results)
        assert "guide.txt" in response.sources
        assert "formulaire.txt" in response.sources

    async def test_generate_confidence_score(self, mock_albert, context, search_results):
        """Le score de confiance est la moyenne des scores RAG."""
        gen = Generator(mock_albert)
        response = await gen.generate("Test", context, search_results)
        expected = (0.85 + 0.72) / 2
        assert abs(response.confidence - expected) < 0.01

    async def test_generate_no_results(self, mock_albert, context):
        """Génère une réponse même sans résultats RAG."""
        gen = Generator(mock_albert)
        response = await gen.generate("Bonjour", context, [])
        assert response.text != ""
        assert response.confidence == 0.0
        assert response.sources == []

    async def test_generate_time_tracked(self, mock_albert, context):
        """Le temps de génération est mesuré."""
        gen = Generator(mock_albert)
        response = await gen.generate("Test", context, [])
        assert response.generation_time_ms >= 0

    async def test_generate_with_history(self, mock_albert, context):
        """L'historique est inclus dans les messages envoyés."""
        gen = Generator(mock_albert)
        history = [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Bonjour !"},
        ]
        response = await gen.generate("Suite", context, [], conversation_history=history)
        assert response.text != ""

    async def test_generate_albert_error(self, context):
        """Lève GenerationError si Albert échoue."""
        class FailingAlbert:
            async def chat(self, **kwargs):
                raise ConnectionError("timeout")

        gen = Generator(FailingAlbert())
        with pytest.raises(GenerationError):
            await gen.generate("Test", context, [])


class TestFormatDocuments:
    """Tests du formatage des documents pour le prompt."""

    def test_format_documents(self, search_results):
        text = _format_documents(search_results)
        assert "Document 1" in text
        assert "guide.txt" in text
        assert "Procédure" in text
        assert "score: 0.85" in text

    def test_format_empty(self):
        assert _format_documents([]) == ""


class TestExtractSources:
    """Tests de l'extraction des sources."""

    def test_extract_unique_sources(self, search_results):
        sources = _extract_sources(search_results)
        assert sources == ["guide.txt", "formulaire.txt"]

    def test_extract_deduplicates(self):
        results = [
            SearchResult(
                chunk=DocumentChunk(text="a", source_path="/a", source_name="doc.txt"),
                score=0.9,
            ),
            SearchResult(
                chunk=DocumentChunk(text="b", source_path="/a", source_name="doc.txt"),
                score=0.8,
            ),
        ]
        sources = _extract_sources(results)
        assert sources == ["doc.txt"]

    def test_extract_empty(self):
        assert _extract_sources([]) == []
