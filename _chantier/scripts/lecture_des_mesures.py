"""Lire une campagne archivee — une seule fois, pour tous les scripts d'analyse.

POURQUOI UN MODULE PARTAGE
----------------------------
Trois scripts d'analyse ouvraient les fichiers de mesure, chacun a sa maniere. Le
chantier a deja paye cinq copies divergentes du motif d'en-tete d'article ; le
commentaire de `construire_corpus_mp.py` le dit sans detour — « cinquieme copie de ce
motif, cinquieme divergence, chacune a produit une mesure fausse avant d'etre
trouvee ».

CE QUE CE MODULE DECIDE, ET QUI COMPTE
----------------------------------------
`cite_attendu` etait FIGE dans le fichier au moment de la campagne. Le compteur a ete
corrige quatre fois en deux jours — la derniere le 05/09/2026, en rapprochant « l'article
4.1 du CCAG Travaux » de « CCAG Travaux 4 », ce qui a rendu quatre a sept reponses
justes par campagne. Lire le champ fige revient a garder l'erreur du jour ou la mesure
a ete prise, et a devoir TOUT RELANCER a chaque correction du compteur.

Les reponses, elles, sont archivees. On recompte donc a la lecture, avec le compteur
courant et le vocabulaire du corpus. Une campagne ancienne se relit ainsi sous la
mesure d'aujourd'hui, sans rien reposer au service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
# Le module s'importe depuis `_chantier/scripts/`, ou la racine du depot n'est pas
# forcement sur le chemin — un script d'analyse peut etre lance de n'importe ou.
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
JEU = RACINE / "tests" / "golden" / "v1.jsonl"
CORPUS = RACINE / "tests" / "golden" / "corpus-marches-publics"


def reponses(chemin) -> list:
    """Les reponses d'un fichier de mesure, quelle que soit sa forme.

    Les fichiers anterieurs au 05/09/2026 sont une liste nue ; depuis, ils portent
    aussi le montage qui les a produits.
    """
    d = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return d["reponses"] if isinstance(d, dict) else d


def montage(chemin) -> dict:
    """Image et reglages du pod au moment de la campagne, ou {} pour un ancien fichier."""
    d = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return d.get("montage", {}) if isinstance(d, dict) else {}


def cas_dores() -> dict:
    return {c["id"]: c for c in
            (json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip())}


def vocabulaire_du_corpus() -> set[str]:
    """Les identifiants d'articles que porte le corpus, lus dans ses en-tetes."""
    vocabulaire: set[str] = set()
    for f in sorted(CORPUS.glob("*.md")):
        for ligne in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ligne.startswith("## Article "):
                vocabulaire.add(ligne[len("## Article "):].strip())
    return vocabulaire


_VOCABULAIRE: set[str] | None = None


def cite_attendu(reponse: dict, cas: dict) -> bool:
    """Recompte la citation avec le compteur COURANT, pas celui du jour de la campagne."""
    global _VOCABULAIRE
    from colaig.rag.verification_citations import articles_cites

    if _VOCABULAIRE is None:
        _VOCABULAIRE = vocabulaire_du_corpus()
    attendus = set((cas.get(reponse["id"], {}) or {}).get("articles_attendus") or [])
    if not attendus:
        return False
    return bool(attendus & articles_cites(reponse["reponse"], identifiants=_VOCABULAIRE))
