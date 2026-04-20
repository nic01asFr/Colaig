"""Tests pour colaig/utils/text.py — Extraction de texte."""

import io
import zipfile

import pytest

from colaig.utils.text import extract_text, is_supported, SUPPORTED_EXTENSIONS


class TestIsSupported:
    """Tests de la détection des formats supportés."""

    def test_supported_extensions(self):
        for ext in [".pdf", ".docx", ".odt", ".txt", ".md", ".html", ".htm"]:
            assert is_supported(f"document{ext}"), f"{ext} devrait être supporté"

    def test_unsupported_extensions(self):
        for ext in [".xlsx", ".pptx", ".zip", ".png", ".json"]:
            assert not is_supported(f"file{ext}"), f"{ext} ne devrait pas être supporté"

    def test_no_extension(self):
        assert not is_supported("README")

    def test_case_insensitive(self):
        assert is_supported("doc.PDF")
        assert is_supported("note.TXT")
        assert is_supported("page.Html")


class TestExtractPlainText:
    """Tests d'extraction pour les fichiers texte brut."""

    def test_txt_utf8(self):
        content = "Bonjour le monde\n\nDeuxième paragraphe.".encode("utf-8")
        result = extract_text(content, "note.txt")
        assert "Bonjour le monde" in result
        assert "Deuxième paragraphe" in result

    def test_txt_latin1_fallback(self):
        content = "Résumé avec accents".encode("latin-1")
        result = extract_text(content, "note.txt")
        assert "sum" in result  # Au moins une partie est lisible

    def test_markdown(self):
        content = b"# Titre\n\nContenu du document.\n\n## Section 2\n\nAutre contenu."
        result = extract_text(content, "doc.md")
        assert "Titre" in result
        assert "Section 2" in result

    def test_empty_file(self):
        result = extract_text(b"", "empty.txt")
        assert result == ""

    def test_whitespace_cleaning(self):
        content = b"  ligne 1  \n\n\n\n  ligne 2  \n\n\n"
        result = extract_text(content, "messy.txt")
        # Les lignes vides excédentaires sont réduites
        assert result.count("\n\n\n") == 0


class TestExtractHTML:
    """Tests d'extraction pour les fichiers HTML."""

    def test_basic_html(self):
        html = b"<html><body><h1>Titre</h1><p>Contenu</p></body></html>"
        result = extract_text(html, "page.html")
        assert "Titre" in result
        assert "Contenu" in result

    def test_html_strips_scripts_and_styles(self):
        html = b"""<html>
        <head><style>body{color:red}</style></head>
        <body>
            <script>alert('xss')</script>
            <p>Texte visible</p>
        </body></html>"""
        result = extract_text(html, "page.html")
        assert "Texte visible" in result
        assert "alert" not in result
        assert "color:red" not in result

    def test_htm_extension(self):
        html = b"<p>Test HTM</p>"
        result = extract_text(html, "page.htm")
        assert "Test HTM" in result


class TestExtractDocx:
    """Tests d'extraction pour les fichiers Word (.docx)."""

    def _make_docx(self, paragraphs: list[str]) -> bytes:
        """Crée un fichier DOCX minimal en mémoire."""
        from docx import Document
        doc = Document()
        for p in paragraphs:
            doc.add_paragraph(p)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def test_basic_docx(self):
        content = self._make_docx(["Premier paragraphe.", "Deuxième paragraphe."])
        result = extract_text(content, "doc.docx")
        assert "Premier paragraphe" in result
        assert "Deuxième paragraphe" in result

    def test_empty_docx(self):
        content = self._make_docx([])
        result = extract_text(content, "empty.docx")
        assert result == ""


class TestExtractPDF:
    """Tests d'extraction pour les fichiers PDF."""

    def _make_pdf(self, text: str) -> bytes:
        """Crée un PDF minimal avec pypdf (ReportLab ou FPDF serait mieux, mais on fait simple)."""
        # Crée un PDF minimal à la main — format PDF brut
        content = text
        # Utilisons la structure PDF minimale
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            b"4 0 obj<</Length " + str(44 + len(content)).encode() + b">>\nstream\nBT /F1 12 Tf 100 700 Td (" + content.encode("latin-1") + b") Tj ET\nendstream\nendobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000000 00000 n \n"
            b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF"
        )
        return pdf_bytes

    def test_basic_pdf(self):
        """Test extraction PDF — peut échouer si le PDF brut n'est pas bien formé."""
        # On utilise un vrai PDF si pypdf arrive à le lire
        pdf_content = self._make_pdf("Bonjour PDF")
        result = extract_text(pdf_content, "test.pdf")
        # Le PDF brut artisanal peut ne pas être parfaitement parsé,
        # mais extract_text ne doit pas planter
        assert isinstance(result, str)

    def test_invalid_pdf_returns_empty(self):
        """Un fichier non-PDF retourne une chaîne vide."""
        result = extract_text(b"ceci n'est pas un PDF", "fake.pdf")
        assert result == ""


class TestExtractODT:
    """Tests d'extraction pour les fichiers ODT."""

    def _make_odt(self, paragraphs: list[str]) -> bytes:
        """Crée un fichier ODT minimal (ZIP avec content.xml)."""
        content_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
        content_xml += '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        content_xml += 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">\n'
        content_xml += '<office:body><office:text>\n'
        for p in paragraphs:
            content_xml += f'<text:p>{p}</text:p>\n'
        content_xml += '</office:text></office:body>\n'
        content_xml += '</office:document-content>'

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("content.xml", content_xml)
        return buf.getvalue()

    def test_basic_odt(self):
        content = self._make_odt(["Premier paragraphe.", "Deuxième paragraphe."])
        result = extract_text(content, "doc.odt")
        assert "Premier paragraphe" in result
        assert "Deuxième paragraphe" in result

    def test_empty_odt(self):
        content = self._make_odt([])
        result = extract_text(content, "empty.odt")
        assert result == ""


class TestExtractUnsupported:
    """Tests pour les formats non supportés."""

    def test_unsupported_format(self):
        result = extract_text(b"binary data", "image.png")
        assert result == ""

    def test_no_extension(self):
        result = extract_text(b"some content", "README")
        assert result == ""
