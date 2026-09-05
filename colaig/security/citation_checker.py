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
import unicodedata

logger = logging.getLogger(__name__)

# Citations [X] : 2..120 chars, sans crochet ni saut de ligne interne.
_CITATION_RE = re.compile(r"\[([^\]\n]{2,120})\]")

# Pénalité de confiance appliquée si des citations ne sont pas sourcées.
_UNGROUNDED_PENALTY = 0.7


# Toute suite d'espaces blancs vaut un espace.
_ESPACES = re.compile(r"\s+")


def _norm(value: str) -> str:
    """Normalise un nom pour le comparer a ce qu'un LECTEUR verrait.

    Releve en production le 30/08/2026, sur une question reelle :

        1 citation(s) sans source: ['... participants septembre 2024.pdf']
        (sources: ['... participants  septembre  2024.pdf'])

    Le meme document. La source porte des espaces doubles, le modele les a
    ecrits simples en redigeant. La citation a ete comptee fantome, et
    `audit_and_adjust` a retranche 30 % de confiance a une reponse juste.

    Deux differences sont invisibles a l'oeil, donc doivent l'etre ici :

    - **les suites d'espaces**, qu'un modele qui redige normalise seul ;
    - **la composition Unicode** : « e » precompose (NFC) et « e » suivi d'un
      accent combinant (NFD) s'affichent a l'identique. Un depot alimente
      depuis macOS produit du NFD, depuis Windows du NFC — le meme corpus
      contient les deux formes du meme nom.

    On ne va pas plus loin. Effacer la ponctuation ou les chiffres echangerait
    ce faux positif contre un faux NEGATIF, qui laisserait passer une citation
    inventee — le signal meme pour lequel ce module existe.
    """
    base = unicodedata.normalize("NFC", value).strip().lower().rsplit("/", 1)[-1]
    return _ESPACES.sub(" ", base)


# Ce qui, entre crochets, désigne plausiblement un document : une extension de fichier,
# ou un chemin. `[nom de l'espace]` n'est ni l'un ni l'autre.
_REF_RE = re.compile(r"(\.[A-Za-z0-9]{1,5}$)|/")


def _looks_like_ref(citation: str, norm_sources: set[str] | None = None) -> bool:
    """Distingue une référence documentaire d'un espace réservé.

    Le critère d'origine — « contient une lettre » — retenait toute phrase entre
    crochets. La campagne du 29/08/2026 a relevé sur le fil :

        citation_checker: 4 citation(s) sans source: ['espace', "nom de l'espace", ...]

    Ce sont des crochets que Colaig avait écrits lui-même, dans ses propres consignes
    à l'utilisateur, relus comme des citations sans source. Et ce n'est pas cosmétique :
    `audit_and_adjust` retranche 30 % de confiance sur ces fausses détections, donc une
    réponse correcte était dégradée parce qu'elle contenait un exemple.

    Deux façons d'être une référence, et la seconde est ce qui évite d'échanger un
    défaut contre un autre :

    1. **La forme** — une extension ou un chemin. C'est ce qui préserve le signal utile :
       un nom de fichier inventé reste signalé, y compris lorsqu'aucune source n'a été
       fournie — le cas où citer un document serait le plus trompeur.
    2. **La correspondance** — un titre sans extension compte s'il correspond à une
       source réellement transmise. Sans cette voie, `[Rapport annuel]` cité à bon droit
       cesserait d'être reconnu comme sourcé.
    """
    c = citation.strip()
    if len(c) < 2 or not any(ch.isalpha() for ch in c):
        return False
    if _REF_RE.search(c):
        return True
    if norm_sources:
        nc = _norm(c)
        return any(nc == s or nc in s or s in nc for s in norm_sources)
    return False


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
    # Les sources d'abord : elles servent aussi à reconnaître une citation dont la
    # forme seule ne dit rien — un titre de document sans extension.
    norm_sources = {_norm(s) for s in (sources or []) if s}
    cited = {
        m.strip() for m in _CITATION_RE.findall(text or "")
        if _looks_like_ref(m, norm_sources)
    }

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
