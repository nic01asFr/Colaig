"""
Tests — Vérification post-hoc des citations (audit anti-hallucination).
"""

import logging

from colaig.security.citation_checker import audit_and_adjust, check_citations


class TestCheckCitations:

    def test_grounded_citation(self):
        r = check_citations("La règle [guide.pdf] dit X.", ["guide.pdf"])
        assert r["all_grounded"] is True
        assert r["ungrounded"] == []

    def test_ungrounded_citation_flagged(self):
        r = check_citations("Selon [inexistant.pdf], X.", ["guide.pdf"])
        assert r["all_grounded"] is False
        assert "inexistant.pdf" in r["ungrounded"]

    def test_basename_match(self):
        # citation sans extension vs source avec chemin/extension
        r = check_citations("Voir [guide].", ["/docs/guide.pdf"])
        assert r["all_grounded"] is True

    def test_numeric_noise_ignored(self):
        # [1] n'est pas considéré comme une référence de source
        r = check_citations("Point [1] et [2].", ["guide.pdf"])
        assert r["cited"] == []

    def test_no_sources_with_citation_is_ungrounded(self):
        r = check_citations("Selon [doc.pdf], X.", [])
        assert "doc.pdf" in r["ungrounded"]


class TestAuditAndAdjust:

    def test_penalizes_when_ungrounded(self, caplog):
        with caplog.at_level(logging.WARNING):
            adjusted = audit_and_adjust("Selon [faux.pdf].", ["vrai.pdf"], 0.9)
        assert adjusted < 0.9
        assert any("citation" in r.message.lower() for r in caplog.records)

    def test_no_penalty_when_all_grounded(self):
        assert audit_and_adjust("Selon [vrai.pdf].", ["vrai.pdf"], 0.9) == 0.9

    def test_no_penalty_when_no_citation(self):
        assert audit_and_adjust("Réponse sans citation.", ["vrai.pdf"], 0.8) == 0.8
