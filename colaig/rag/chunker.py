"""
Colaig — Découpage de documents en chunks

Implémente ChunkerProtocol.
Stratégies adaptées par type de document :
- Markdown : split par sections (titres #)
- PDF/DOCX : split par paragraphes avec overlap
- Texte brut : sliding window
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from colaig.models import DocumentChunk

logger = logging.getLogger(__name__)

# Bornes de taille
MIN_CHUNK_SIZE = 50
MAX_CHUNK_SIZE = 2000


class Chunker:
    """Service de découpage de documents en chunks.

    Args:
        chunk_size: Taille cible des chunks en caractères.
        chunk_overlap: Chevauchement entre chunks en caractères.
    """

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 200) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def chunk_document(
        self,
        content: str,
        source_path: str,
        doc_type: str = "text",
        metadata: Optional[dict] = None,
    ) -> list[DocumentChunk]:
        """Découpe un document en chunks.

        Args:
            content: Texte du document.
            source_path: Chemin WebDAV du document source.
            doc_type: Type de document (md, pdf, docx, txt, odt, html).
            metadata: Métadonnées additionnelles.

        Returns:
            Liste de DocumentChunk.
        """
        if not content or not content.strip():
            return []

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        source_name = source_path.rstrip("/").split("/")[-1]

        if doc_type == "md":
            raw_chunks = self._chunk_markdown(content)
        else:
            raw_chunks = self._chunk_text(content)

        # Post-traitement : fusionner les petits, découper les gros
        processed = self._postprocess(raw_chunks)

        chunks: list[DocumentChunk] = []
        for i, (text, section) in enumerate(processed):
            # Préfixer le texte avec la section pour enrichir le contexte de l'embedding
            embedded_text = f"[{section}]\n{text}" if section else text
            chunks.append(DocumentChunk(
                text=embedded_text,
                source_path=source_path,
                source_name=source_name,
                section=section,
                position=i,
                doc_type=doc_type,
                source_hash=source_hash,
                metadata=metadata or {},
            ))

        logger.debug(
            "chunked %s → %d chunks (type=%s)",
            source_name, len(chunks), doc_type,
        )
        return chunks

    def _chunk_markdown(self, content: str) -> list[tuple[str, str]]:
        """Découpe un markdown par sections de titres (#)."""
        # Split sur les lignes de titre
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if re.match(r"^#{1,6}\s+", line):
                # Nouveau titre — sauver la section précédente
                if current_lines:
                    text = "\n".join(current_lines).strip()
                    if text:
                        sections.append((text, current_title))
                current_title = line.strip().lstrip("#").strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        # Dernière section
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append((text, current_title))

        # Si les sections sont trop longues, les re-découper
        result: list[tuple[str, str]] = []
        for text, title in sections:
            if len(text) > MAX_CHUNK_SIZE:
                sub_chunks = self._sliding_window(text)
                result.extend((chunk, title) for chunk in sub_chunks)
            else:
                result.append((text, title))

        return result

    def _chunk_text(self, content: str) -> list[tuple[str, str]]:
        """Découpe un texte par paragraphes puis sliding window."""
        # Essayer de splitter par double saut de ligne (paragraphes)
        paragraphs = re.split(r"\n\s*\n", content)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        # Si un paragraphe unique est trop long, utiliser sliding window
        result: list[tuple[str, str]] = []
        buffer = ""

        for para in paragraphs:
            if len(buffer) + len(para) + 2 <= self._chunk_size:
                buffer = f"{buffer}\n\n{para}".strip() if buffer else para
            else:
                if buffer:
                    result.append((buffer, ""))
                if len(para) > self._chunk_size:
                    sub_chunks = self._sliding_window(para)
                    result.extend((chunk, "") for chunk in sub_chunks)
                else:
                    buffer = para
                    continue
                buffer = ""

        if buffer:
            result.append((buffer, ""))

        return result

    def _sliding_window(self, text: str) -> list[str]:
        """Découpe un texte long en fenêtres glissantes."""
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk = text[start:end]

            # Essayer de couper sur un espace ou saut de ligne
            if end < len(text):
                last_break = max(
                    chunk.rfind("\n"),
                    chunk.rfind(". "),
                    chunk.rfind(" "),
                )
                if last_break > self._chunk_size // 2:
                    chunk = chunk[: last_break + 1]
                    end = start + last_break + 1

            chunks.append(chunk.strip())
            start = end - self._chunk_overlap

        return [c for c in chunks if c]

    def _postprocess(self, chunks: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Fusionne les chunks trop petits, re-découpe les trop gros."""
        result: list[tuple[str, str]] = []
        buffer_text = ""
        buffer_section = ""

        for text, section in chunks:
            if len(text) < MIN_CHUNK_SIZE:
                # Fusionner avec le précédent
                if buffer_text:
                    buffer_text = f"{buffer_text}\n\n{text}"
                else:
                    buffer_text = text
                    buffer_section = section
                continue

            # Flush le buffer si on en a un
            if buffer_text:
                combined = f"{buffer_text}\n\n{text}"
                if len(combined) <= MAX_CHUNK_SIZE:
                    buffer_text = combined
                    continue
                else:
                    result.append((buffer_text, buffer_section))
                    buffer_text = ""
                    buffer_section = ""

            if len(text) > MAX_CHUNK_SIZE:
                for sub in self._sliding_window(text):
                    result.append((sub, section))
            else:
                result.append((text, section))

        if buffer_text:
            result.append((buffer_text, buffer_section))

        return result
