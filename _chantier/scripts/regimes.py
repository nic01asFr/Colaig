"""
À quel régime appartient un article du Code de la commande publique ?

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (élargissement du corpus)

Le mode de défaillance qu'aucun garde-fou ne voit
---------------------------------------------------
Mesuré le 23/08/2026, génération sur 124 cas :

    corpus entier   115 citations du mauvais régime (22 %)
    corpus restreint    1 (0 %)

Le livre défense-sécurité pose **100 000 euros** là où le régime ordinaire pose
**60 000**. Une citation du mauvais régime délivre donc du droit faux comme s'il était
juste — et **aucun garde-fou ne peut la voir**, puisque l'article était bien dans les
passages fournis. Ni `fantomes` (il existe), ni `hors_contexte` (il était là), ni
`montants_inventes` (le montant figure dans le passage cité).

Le périmètre a donc été restreint à 38 % du code. C'était la bonne décision de mesure,
et c'est aussi le point où la référence s'écarte le plus de la production : **Colaig
n'a pas le droit de restreindre son corpus**. Il indexe ce que contient le dossier
partagé, et un espace mêlant les deux régimes expose l'utilisateur à ce défaut.

Ce module rend la confusion mesurable au lieu de latente.

Ce qui compte comme erreur, et ce qui n'en est pas
----------------------------------------------------
| régime | verdict pour une question du régime ordinaire |
|---|---|
| 2e partie, livre Ier — dispositions générales | **correct** |
| 2e partie, autres livres — défense, outre-mer, autres marchés | **erreur** |
| 3e partie — concessions | **erreur** |
| 1re partie — définitions, champ d'application | **jamais une erreur** |
| CCAG, annexes | **jamais une erreur** |

La première partie est **transverse** : elle définit ce qu'est un marché public et
s'applique à tous les régimes. Citer `L1111-1` pour répondre sur un marché ordinaire
est juste, pas confus. La compter comme erreur gonflerait l'indicateur d'un bruit qui
n'en est pas.

Les CCAG sont **contractuels**, pas réglementaires : ils s'appliquent quand le marché
y renvoie, quel que soit le régime.

Pourquoi l'attribution se lit dans le CORPUS et non dans le numéro
--------------------------------------------------------------------
On pourrait croire que `L23xx` désigne le livre III de la deuxième partie. Le numéro
porte effectivement la partie et le livre — mais l'écrire ici reviendrait à réinventer
une règle que le corpus porte déjà, explicitement, dans son fil d'Ariane :

    > Partie législative › DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : …

Lire la source plutôt que déduire d'un motif : c'est ce que ce chantier a payé cher
d'apprendre ailleurs — un motif « [LRD] suivi de quatre chiffres » ne reconnaissait
aucune référence de la forme « L. 2113-10 », soit 53,7 % de celles du corpus.
"""
from __future__ import annotations

import re
from pathlib import Path

# Le fil d'Ariane, tel que `construire_corpus_mp.py` l'écrit.
_FIL = re.compile(r"^> (?:Partie \w+|CCAG .+?|Annexe .+?|Texte) › (.+)$", re.M)
_TITRE_ARTICLE = re.compile(r"^## Article (.+?)\s*$", re.M)

ORDINAIRE = "2e partie · livre Ier"
TRANSVERSE = "transverse"
CONTRACTUEL = "contractuel"


def _regime_du_fil(fil: str) -> str:
    """Le régime, depuis le fil d'Ariane d'un document du corpus."""
    niveaux = [x.strip() for x in fil.split("›")]
    if not niveaux:
        return CONTRACTUEL
    partie = niveaux[0]
    livre = niveaux[1] if len(niveaux) > 1 else ""

    if partie.startswith("PREMIÈRE PARTIE"):
        return TRANSVERSE
    if partie.startswith("DEUXIÈME PARTIE"):
        if livre.startswith("Livre Ier"):
            return ORDINAIRE
        return f"2e partie · {livre.split(':')[0].strip() or 'autre livre'}"
    if partie.startswith("TROISIÈME PARTIE"):
        return f"3e partie · {livre.split(':')[0].strip() or 'concessions'}"
    # Les chapitres de CCAG et les annexes n'ont pas de partie : ils sont contractuels.
    return CONTRACTUEL


def attribuer(corpus: Path) -> dict[str, str]:
    """Rend, pour chaque numéro d'article du corpus, le régime dont il relève.

    Un article défini dans DEUX documents (ce qui n'arrive pas dans ce corpus, vérifié :
    699 en-têtes pour 699 numéros distincts) garderait la première attribution. Le cas
    est signalé par `incoherences()` plutôt que résolu en silence.
    """
    regimes: dict[str, str] = {}
    for p in sorted(corpus.glob("*.md")):
        texte = p.read_text(encoding="utf-8")
        m = _FIL.search(texte)
        regime = _regime_du_fil(m.group(1)) if m else CONTRACTUEL
        for art in _TITRE_ARTICLE.findall(texte):
            regimes.setdefault(art.strip(), regime)
    return regimes


def incoherences(corpus: Path) -> list[tuple[str, set[str]]]:
    """Les articles attribués à plusieurs régimes — un silence serait pire."""
    vus: dict[str, set[str]] = {}
    for p in sorted(corpus.glob("*.md")):
        texte = p.read_text(encoding="utf-8")
        m = _FIL.search(texte)
        regime = _regime_du_fil(m.group(1)) if m else CONTRACTUEL
        for art in _TITRE_ARTICLE.findall(texte):
            vus.setdefault(art.strip(), set()).add(regime)
    return sorted((a, r) for a, r in vus.items() if len(r) > 1)


def hors_regime(cites: set[str], regimes: dict[str, str],
                regime_attendu: str = ORDINAIRE) -> set[str]:
    """Les articles cités qui relèvent d'un AUTRE régime que celui de la question.

    Le transverse et le contractuel ne comptent jamais — voir l'en-tête. Un article
    inconnu du corpus ne compte pas non plus : c'est un fantôme, et `fantomes` le voit.
    """
    return {a for a in cites
            if regimes.get(a) not in (None, regime_attendu, TRANSVERSE, CONTRACTUEL)}


if __name__ == "__main__":
    import collections
    import sys

    corpus = Path(sys.argv[1])
    reg = attribuer(corpus)
    compte = collections.Counter(reg.values())
    print(f"{len(reg)} articles attribués depuis {corpus}\n")
    for nom, n in compte.most_common():
        marque = "  <- régime de référence" if nom == ORDINAIRE else ""
        print(f"  {n:5}  {nom}{marque}")
    mauvais = sum(n for r, n in compte.items()
                  if r not in (ORDINAIRE, TRANSVERSE, CONTRACTUEL))
    print(f"\n  {mauvais} articles d'un AUTRE régime — c'est la surface de confusion")
    inc = incoherences(corpus)
    print(f"  {len(inc)} article(s) attribué(s) à plusieurs régimes")
    for a, r in inc[:5]:
        print(f"     {a} : {sorted(r)}")
