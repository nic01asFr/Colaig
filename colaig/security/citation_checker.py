"""
Colaig — Vérification post-hoc des citations (audit anti-hallucination)

Vérifie que les citations [nom_fichier] présentes dans une réponse correspondent
à des sources réellement fournies au LLM. Non bloquant : logge un audit et
applique une pénalité de confiance douce si des citations sont sans source.

Crucial pour l'administration publique (réponses auditables, traçables).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Citations [X] : 2..120 chars, sans crochet ni saut de ligne interne.
_CITATION_RE = re.compile(r"\[([^\]\n]{2,120})\]")

# Pénalité de confiance appliquée si des citations ne sont pas sourcées.
_UNGROUNDED_PENALTY = 0.7


def _norm(value: str) -> str:
    """Normalise un nom (basename, minuscules) pour comparaison souple."""
    return value.strip().lower().rsplit("/", 1)[-1]


def _looks_like_ref(citation: str) -> bool:
    """Filtre le bruit : ignore les citations purement numériques/ponctuation."""
    c = citation.strip()
    return len(c) >= 2 and any(ch.isalpha() for ch in c)


def check_citations(text: str, sources: list[str]) -> dict:
    """Analyse les citations d'une réponse vs les sources fournies.

    Returns:
        {
            "cited": [...],        # citations détectées (filtrées du bruit)
            "grounded": [...],     # citations correspondant à une source
            "ungrounded": [...],   # citations sans source correspondante
            "all_grounded": bool,
        }
    """
    cited = {
        m.strip() for m in _CITATION_RE.findall(text or "") if _looks_like_ref(m)
    }
    norm_sources = {_norm(s) for s in (sources or []) if s}

    grounded, ungrounded = [], []
    for c in cited:
        nc = _norm(c)
        if norm_sources and any(nc == s or nc in s or s in nc for s in norm_sources):
            grounded.append(c)
        else:
            ungrounded.append(c)

    return {
        "cited": sorted(cited),
        "grounded": sorted(grounded),
        "ungrounded": sorted(ungrounded),
        "all_grounded": not ungrounded,
    }


def audit_and_adjust(text: str, sources: list[str], confidence: float) -> float:
    """Logge un audit si des citations sont sans source et baisse la confiance.

    Non bloquant : la réponse est toujours retournée. Sert d'alerte
    anti-hallucination + signal de confiance pour l'utilisateur/l'audit.

    Returns:
        La confiance ajustée (pénalisée si citations non sourcées).
    """
    result = check_citations(text, sources)
    if result["ungrounded"]:
        logger.warning(
            "citation_checker: %d citation(s) sans source correspondante: %s "
            "(sources fournies: %s)",
            len(result["ungrounded"]),
            result["ungrounded"],
            sources or [],
        )
        return round(max(0.0, confidence) * _UNGROUNDED_PENALTY, 4)
    return confidence
