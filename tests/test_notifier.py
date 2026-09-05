"""Tests pour colaig/rag/notifier.py — notifications proactives Mode A/B."""
from __future__ import annotations

from unittest.mock import MagicMock

from colaig.models import DocumentChunk, UpdateSummary
from colaig.rag.notifier import _extract_descriptions, format_notification

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_update(changed=None, removed=None):
    return UpdateSummary(
        count=len(changed or []),
        changed_paths=list(changed or []),
        removed_paths=set(removed or []),
    )


def _make_store(chunks: list[DocumentChunk]):
    store = MagicMock()
    store.get_all_active_chunks.return_value = chunks
    return store


def _chunk(source_path, prefix=""):
    return DocumentChunk(
        text="contenu du chunk",
        source_path=source_path,
        source_name=source_path.split("/")[-1],
        contextual_prefix=prefix,
    )


# ---------------------------------------------------------------------------
# UpdateSummary — rétrocompatibilité int
# ---------------------------------------------------------------------------

class TestUpdateSummary:
    def test_eq_int(self):
        u = UpdateSummary(count=2)
        assert u == 2

    def test_gt_int(self):
        u = UpdateSummary(count=3)
        assert u > 0
        assert not u > 5

    def test_ge_int(self):
        u = UpdateSummary(count=2)
        assert u >= 2
        assert u >= 1
        assert not u >= 3

    def test_lt_int(self):
        u = UpdateSummary(count=1)
        assert u < 2

    def test_bool_true(self):
        assert bool(UpdateSummary(count=1))
        assert bool(UpdateSummary(count=0, removed_paths={"/a"}))

    def test_bool_false(self):
        assert not bool(UpdateSummary(count=0))

    def test_int_cast(self):
        assert int(UpdateSummary(count=5)) == 5


# ---------------------------------------------------------------------------
# format_notification — Mode A (sans store)
# ---------------------------------------------------------------------------

class TestFormatNotificationModeA:
    def test_empty_update_returns_empty(self):
        msg = format_notification("Mon Workspace", _make_update(), store=None)
        assert msg == ""

    def test_single_new_doc(self):
        update = _make_update(changed=["/ws/guide.pdf"])
        msg = format_notification("Mon Workspace", update, store=None)
        assert "Mon Workspace" in msg
        assert "guide.pdf" in msg
        assert "mis à jour" in msg

    def test_multiple_docs(self):
        update = _make_update(changed=["/ws/a.pdf", "/ws/b.pdf", "/ws/c.docx"])
        msg = format_notification("Workspace", update, store=None)
        assert "3 documents" in msg
        assert "a.pdf" in msg
        assert "b.pdf" in msg
        assert "c.docx" in msg

    def test_removed_doc(self):
        update = _make_update(removed=["/ws/old.pdf"])
        msg = format_notification("WS", update, store=None)
        assert "old.pdf" in msg
        assert "supprimé" in msg

    def test_changed_and_removed(self):
        update = _make_update(changed=["/ws/new.pdf"], removed=["/ws/old.pdf"])
        msg = format_notification("WS", update, store=None)
        assert "new.pdf" in msg
        assert "old.pdf" in msg

    def test_truncation_at_10_docs(self):
        paths = [f"/ws/doc{i}.pdf" for i in range(15)]
        update = _make_update(changed=paths)
        msg = format_notification("WS", update, store=None)
        assert "5 autre" in msg  # 15 - 10 = 5

    def test_english_language(self):
        update = _make_update(changed=["/ws/report.pdf"])
        msg = format_notification("WS", update, store=None, language="en")
        assert "updated" in msg
        assert "📄" in msg


# ---------------------------------------------------------------------------
# format_notification — Mode B (avec store + contextual_prefix)
# ---------------------------------------------------------------------------

class TestFormatNotificationModeB:
    def test_contextual_prefix_included(self):
        chunks = [_chunk("/ws/guide.pdf", "Ce document décrit les règles de conception routière.")]
        store = _make_store(chunks)
        update = _make_update(changed=["/ws/guide.pdf"])
        msg = format_notification("WS", update, store=store)
        assert "règles de conception routière" in msg
        assert "guide.pdf" in msg

    def test_no_prefix_falls_back_to_mode_a(self):
        chunks = [_chunk("/ws/guide.pdf", "")]  # préfixe vide
        store = _make_store(chunks)
        update = _make_update(changed=["/ws/guide.pdf"])
        msg = format_notification("WS", update, store=store)
        assert "guide.pdf" in msg
        # La ligne du doc ne contient pas de description (pas de " — " après le nom)
        doc_line = next(l for l in msg.split("\n") if "guide.pdf" in l)
        assert " — " not in doc_line

    def test_prefix_truncated_at_200_chars(self):
        long_prefix = "x" * 300
        chunks = [_chunk("/ws/doc.pdf", long_prefix)]
        store = _make_store(chunks)
        update = _make_update(changed=["/ws/doc.pdf"])
        msg = format_notification("WS", update, store=store)
        assert "…" in msg
        # Le préfixe tronqué ne dépasse pas 200 + "…"
        lines = msg.split("\n")
        doc_line = next(l for l in lines if "doc.pdf" in l)
        desc_part = doc_line.split(" — ", 1)[-1] if " — " in doc_line else ""
        assert len(desc_part) <= 205  # 200 + "…" + marge

    def test_store_error_falls_back_gracefully(self):
        store = MagicMock()
        store.get_all_active_chunks.side_effect = RuntimeError("store error")
        update = _make_update(changed=["/ws/doc.pdf"])
        # Ne doit pas lever d'exception
        msg = format_notification("WS", update, store=store)
        assert "doc.pdf" in msg

    def test_first_chunk_per_source_only(self):
        """Seul le premier chunk avec préfixe par source est utilisé."""
        chunks = [
            _chunk("/ws/doc.pdf", "Premier préfixe."),
            _chunk("/ws/doc.pdf", "Deuxième préfixe — ne doit pas apparaître."),
        ]
        store = _make_store(chunks)
        update = _make_update(changed=["/ws/doc.pdf"])
        msg = format_notification("WS", update, store=store)
        assert "Premier préfixe" in msg
        assert "Deuxième" not in msg


# ---------------------------------------------------------------------------
# _extract_descriptions
# ---------------------------------------------------------------------------

class TestExtractDescriptions:
    def test_returns_empty_without_store(self):
        result = _extract_descriptions(["/a.pdf"], None)
        assert result == {}

    def test_maps_paths_to_prefix(self):
        chunks = [_chunk("/ws/a.pdf", "Préfixe A"), _chunk("/ws/b.pdf", "Préfixe B")]
        store = _make_store(chunks)
        result = _extract_descriptions(["/ws/a.pdf", "/ws/b.pdf"], store)
        assert result["/ws/a.pdf"] == "Préfixe A"
        assert result["/ws/b.pdf"] == "Préfixe B"

    def test_ignores_paths_not_in_store(self):
        chunks = [_chunk("/ws/a.pdf", "Préfixe A")]
        store = _make_store(chunks)
        result = _extract_descriptions(["/ws/a.pdf", "/ws/missing.pdf"], store)
        assert "/ws/missing.pdf" not in result
