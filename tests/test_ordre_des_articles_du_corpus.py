"""Un document doit presenter ses articles dans l'ordre du Code.

CE QUI A CONDUIT ICI
----------------------
Douze cas dores ne voient JAMAIS l'article attendu servi. En les lisant un a un, un
motif revient : le systeme sert des articles du meme groupe, mais jamais celui-la.

    mp-036  attendu R2194-1    servis R2194-6, R2194-7, R2194-8
    mp-018  attendu L2193-3    servis L2193-2, L2193-13, L2193-14

L'elargissement aux voisins devait precisement rattraper cela : autour de chaque
passage retenu, il sert ses voisins immediats DANS L'ORDRE DU DOCUMENT. Il ne les
rattrapait pas, et la raison est dans le corpus :

    085-…-modification-du-marche.md
      R2194-1  R2194-10  R2194-2  R2194-3  R2194-4  …

Les articles etaient tries comme des CHAINES. « R2194-10 » se place donc entre
« R2194-1 » et « R2194-2 ». Le voisin de position n'est plus le voisin logique : depuis
R2194-1, l'elargissement sert R2194-10, pas R2194-2.

VINGT-DEUX DES QUARANTE-CINQ fichiers d'articles du Code etaient dans ce cas — la
moitie du corpus. Cela explique aussi que l'elargissement n'ait jamais montre d'effet :
une fois sur deux, il servait des voisins arbitraires.

Le desordre est le fait de `construire_corpus_mp.py`, pas d'un depot d'utilisateur : il
s'agit de notre propre script, et le document qu'il produit doit etre lisible comme le
Code l'est.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
CORPUS = RACINE / "tests" / "golden" / "corpus-marches-publics"

_NUMERO = re.compile(r"^([LRD])(\d+)-(\d+)(?:-(\d+))?$")


def cle_numerique(titre: str):
    """(lettre, groupe, article, alinea) — None si le titre ne suit pas le motif.

    Les CCAG et annexes ne numerotent pas ainsi ; ils ne sont pas concernes.
    """
    m = _NUMERO.match(titre.strip())
    if not m:
        return None
    return (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))


def _articles(fichier: Path) -> list[str]:
    return [l[len("## Article "):].strip()
            for l in fichier.read_text(encoding="utf-8", errors="ignore").splitlines()
            if l.startswith("## Article ")]


def test_les_articles_du_code_suivent_l_ordre_du_code():
    fautifs = []
    for f in sorted(CORPUS.glob("*.md")):
        titres = _articles(f)
        cles = [cle_numerique(t) for t in titres]
        if len(titres) < 2 or any(c is None for c in cles):
            continue
        if cles != sorted(cles):
            premier = next(i for i in range(1, len(cles)) if cles[i] < cles[i - 1])
            fautifs.append(f"{f.name} : …{titres[premier - 1]} puis {titres[premier]}…")

    assert not fautifs, (
        "des articles se suivent dans le desordre — l'elargissement aux voisins sert "
        "alors des articles arbitraires, et un lecteur humain est trompe :\n  "
        + "\n  ".join(fautifs[:8])
    )


def test_la_cle_ordonne_bien_les_alineas():
    """R2194-2 avant R2194-10, et R2122-9-1 apres R2122-9."""
    titres = ["R2194-10", "R2194-2", "R2122-9-1", "R2122-9", "L2194-1"]
    ordonne = sorted(titres, key=cle_numerique)
    assert ordonne == ["L2194-1", "R2122-9", "R2122-9-1", "R2194-2", "R2194-10"]
