"""Tests pour colaig/rag/chunker.py — Découpage de documents."""

import pytest

from colaig.rag.chunker import MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, Chunker


@pytest.fixture
def chunker() -> Chunker:
    return Chunker(chunk_size=200, chunk_overlap=30)


class TestChunkerBasic:
    """Tests de base du chunking."""

    def test_empty_content(self, chunker):
        assert chunker.chunk_document("", "/doc.txt") == []

    def test_whitespace_only(self, chunker):
        assert chunker.chunk_document("   \n\n  ", "/doc.txt") == []

    def test_short_text(self, chunker):
        chunks = chunker.chunk_document("Court texte.", "/doc.txt")
        assert len(chunks) == 1
        assert chunks[0].text == "Court texte."
        assert chunks[0].source_path == "/doc.txt"
        assert chunks[0].source_name == "doc.txt"

    def test_source_hash_set(self, chunker):
        chunks = chunker.chunk_document("Un texte.", "/doc.txt")
        assert chunks[0].source_hash != ""
        assert len(chunks[0].source_hash) == 16

    def test_positions_sequential(self, chunker):
        long_text = "\n\n".join(f"Paragraphe numéro {i} avec du contenu suffisant." for i in range(10))
        chunks = chunker.chunk_document(long_text, "/doc.txt")
        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))


class TestChunkerMarkdown:
    """Tests du chunking Markdown."""

    def test_split_by_headings(self, chunker):
        # Chaque section doit dépasser MIN_CHUNK_SIZE (50) pour ne pas être fusionnée
        md = (
            "# Titre 1\n\n"
            "Contenu de la première section avec suffisamment de texte pour dépasser le minimum requis.\n\n"
            "## Titre 2\n\n"
            "Contenu de la deuxième section avec suffisamment de texte pour dépasser le minimum requis."
        )
        chunks = chunker.chunk_document(md, "/doc.md", doc_type="md")
        assert len(chunks) >= 2
        assert any("Titre 1" in c.text for c in chunks)
        assert any("Titre 2" in c.text for c in chunks)

    def test_sections_have_titles(self, chunker):
        md = (
            "# Introduction\n\n"
            "Voici une introduction suffisamment longue pour dépasser le seuil minimum de cinquante caractères.\n\n"
            "## Méthode\n\n"
            "La méthode décrite ici est suffisamment détaillée pour dépasser le seuil minimum de cinquante caractères."
        )
        chunks = chunker.chunk_document(md, "/doc.md", doc_type="md")
        sections = [c.section for c in chunks]
        assert "Introduction" in sections
        assert "Méthode" in sections

    def test_long_section_split(self):
        # Le split intervient quand une section dépasse MAX_CHUNK_SIZE (2000)
        chunker = Chunker(chunk_size=500, chunk_overlap=50)
        md = "# Titre\n\n" + "Mot " * 1000  # ~4000 chars, bien au-dessus de MAX_CHUNK_SIZE
        chunks = chunker.chunk_document(md, "/doc.md", doc_type="md")
        assert len(chunks) > 1


class TestChunkerText:
    """Tests du chunking texte brut."""

    def test_paragraph_split(self, chunker):
        text = "Premier paragraphe assez long.\n\nDeuxième paragraphe assez long."
        chunks = chunker.chunk_document(text, "/doc.txt")
        assert len(chunks) >= 1

    def test_long_text_sliding_window(self):
        chunker = Chunker(chunk_size=100, chunk_overlap=20)
        text = "Mot " * 200  # ~800 chars
        chunks = chunker.chunk_document(text, "/doc.txt")
        assert len(chunks) > 1

    def test_doc_type_preserved(self, chunker):
        chunks = chunker.chunk_document("Contenu.", "/doc.pdf", doc_type="pdf")
        assert chunks[0].doc_type == "pdf"


class TestChunkerPostprocessing:
    """Tests de la fusion/re-découpage."""

    def test_tiny_chunks_merged(self):
        chunker = Chunker(chunk_size=200, chunk_overlap=20)
        text = "A.\n\nB.\n\nC.\n\nD."
        chunks = chunker.chunk_document(text, "/doc.txt")
        # Les chunks très courts doivent être fusionnés
        for c in chunks:
            assert len(c.text) >= MIN_CHUNK_SIZE or len(chunks) == 1

    def test_no_chunk_exceeds_max(self):
        chunker = Chunker(chunk_size=100, chunk_overlap=20)
        text = "X" * 5000
        chunks = chunker.chunk_document(text, "/doc.txt")
        for c in chunks:
            assert len(c.text) <= MAX_CHUNK_SIZE + 100  # marge pour la coupure
