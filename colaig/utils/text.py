"""
Colaig — Extraction de texte depuis fichiers

Supporte : .pdf, .docx, .odt, .txt, .md, .html
Chaque extracteur est fail-safe : retourne une chaîne vide en cas d'erreur
plutôt que de planter le pipeline d'indexation.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import zipfile

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _suppress_mupdf_output():
    """Redirige temporairement fd 1 (stdout) et fd 2 (stderr) vers /dev/null.

    Pymupdf >= 1.18 redirige les avertissements MuPDF vers stdout via print().
    Ce context manager supprime les deux flux pour étouffer les messages parasites
    (ex: "format error: No common ancestor in structure tree") sans toucher au
    logging Python (restitué après la sortie du context).
    Sûr en contexte asyncio single-threaded.
    """
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    try:
        yield
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(null)
        os.close(saved_out)
        os.close(saved_err)

# Extensions dont on sait extraire du texte **nativement**, sans modèle.
SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".docx", ".odt", ".txt", ".md", ".html", ".htm"}

# Extensions qui ne portent aucun texte extractible mais dont un OCR peut en tirer.
#
# Elles ne sont **pas** dans SUPPORTED_EXTENSIONS : `extract_text()` n'en tire rien, et
# deux appelants (`mcp/server.py`, `rag/document_index.py`) s'appuient sur ce prédicat
# pour décider s'ils obtiendront du texte. Les élargir leur ferait recevoir des chaînes
# vides. C'est `is_indexable()` qui réunit les deux familles, et lui seul est utilisé
# par l'indexeur.
OCR_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def extract_text(content: bytes, filename: str) -> str:
    """Extrait le texte brut d'un fichier.

    Args:
        content: Contenu binaire du fichier.
        filename: Nom du fichier (utilisé pour déterminer le format).

    Returns:
        Texte extrait, nettoyé. Chaîne vide si le format n'est pas supporté
        ou si l'extraction échoue.
    """
    ext = _get_extension(filename)

    extractors = {
        ".pdf": _extract_pdf,
        ".docx": _extract_docx,
        ".odt": _extract_odt,
        ".txt": _extract_plain,
        ".md": _extract_plain,
        ".html": _extract_html,
        ".htm": _extract_html,
    }

    extractor = extractors.get(ext)
    if extractor is None:
        if ext in OCR_EXTENSIONS:
            # Rien à extraire nativement d'une image, et c'est normal : l'indexeur
            # enchaînera sur l'OCR. Un warning ici ferait du bruit à chaque image.
            logger.debug("image sans texte natif, OCR attendu : %s", filename)
        else:
            logger.warning("format non supporté : %s (ext=%s)", filename, ext)
        return ""

    try:
        text = extractor(content)
        return _clean_text(text)
    except Exception:
        logger.exception("erreur extraction texte : %s", filename)
        return ""


def is_supported(filename: str) -> bool:
    """Le format se prête-t-il à une extraction de texte **native** ?

    Répond `False` pour une image : `extract_text()` n'en tire rien. Pour savoir si
    Colaig sait indexer un fichier — OCR compris — utiliser `is_indexable()`.
    """
    return _get_extension(filename) in SUPPORTED_EXTENSIONS


def needs_ocr(filename: str) -> bool:
    """Le fichier ne peut livrer son texte que par OCR (image scannée)."""
    return _get_extension(filename) in OCR_EXTENSIONS


def is_indexable(filename: str) -> bool:
    """Colaig sait-il indexer ce fichier, par extraction native **ou** par OCR ?

    C'est le prédicat de l'indexeur. Avant le lot L1.3b il filtrait sur
    `is_supported()`, donc **les images n'atteignaient jamais le chemin OCR** — une
    image scannée déposée dans un espace était écartée en silence, alors même qu'un
    modèle d'OCR était disponible et configuré.
    """
    return is_supported(filename) or needs_ocr(filename)


def _get_extension(filename: str) -> str:
    """Extrait l'extension en minuscule."""
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        return ""
    return filename[dot_idx:].lower()


def _clean_text(text: str) -> str:
    """Nettoie le texte extrait : normalise les espaces, supprime les lignes vides excédentaires."""
    lines = text.splitlines()
    cleaned = []
    prev_empty = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not prev_empty:
                cleaned.append("")
            prev_empty = True
        else:
            cleaned.append(stripped)
            prev_empty = False
    return "\n".join(cleaned).strip()


# ── Extracteurs par format ──────────────────────────────────────────


def _extract_pdf(content: bytes) -> str:
    """Extrait le texte d'un PDF via pymupdf (fitz)."""
    import fitz  # pymupdf

    with _suppress_mupdf_output():
        doc = fitz.open(stream=content, filetype="pdf")
        pages = []
        for page in doc:
            text = page.get_text()
            if text and text.strip():
                pages.append(text.strip())
        doc.close()
    return "\n\n".join(pages)


def _extract_docx(content: bytes) -> str:
    """Extrait le texte d'un fichier Word (.docx) via python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_odt(content: bytes) -> str:
    """Extrait le texte d'un fichier ODT (OpenDocument Text).

    Un fichier ODT est un ZIP contenant content.xml.
    On parse le XML pour extraire les balises <text:p>.
    """
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        with zf.open("content.xml") as f:
            tree = ET.parse(f)

    # Namespace OpenDocument : urn:oasis:names:tc:opendocument:xmlns:text:1.0
    paragraphs = []
    for p in tree.iter("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"):
        text = "".join(p.itertext()).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_plain(content: bytes) -> str:
    """Extrait le texte brut (txt, md)."""
    # Essaie UTF-8 d'abord, puis latin-1 en fallback
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")


def _extract_html(content: bytes) -> str:
    """Extrait le texte d'un fichier HTML via BeautifulSoup."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    # Supprime scripts et styles
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n")
