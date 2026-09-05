"""Deux campagnes identiques different de combien de CAS, et pas de combien de points ?

POURQUOI CETTE QUESTION-LA
----------------------------
Deux campagnes du 05/09/2026, montage strictement identique, ont donne 68/113 puis
75/113. Sept points d'ecart pour zero changement — soit plus que tous les ecarts
qu'on cherchait a trancher dans la journee.

Mais l'agregat cache le principal. Deux campagnes qui rendent toutes deux 75/113
peuvent tres bien ne pas reussir les MEMES 75 cas : l'agregat serait stable et la
mesure, elle, ne le serait pas du tout. C'est le nombre de cas qui BASCULENT qui dit
ce qu'une comparaison de montages peut esperer detecter.

Un ecart de montage n'est interpretable que s'il depasse ce bruit-la.

    python _chantier/scripts/dispersion_entre_campagnes.py <mesure-a.json> <mesure-b.json>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
JEU = RACINE / "tests" / "golden" / "v1.jsonl"


def _mcnemar(gagnes: int, perdus: int) -> float:
    """Probabilite d'observer un desequilibre au moins aussi marque, par pur hasard.

    Test des signes exact sur les cas DISCORDANTS — ceux qui reussissent d'un cote et
    echouent de l'autre. Les cas identiques n'apportent rien : ils ne distinguent pas
    les deux campagnes.

    Comparer deux agregats (73 contre 77) ignore que ce ne sont pas les MEMES cas. Le
    test apparie regarde le seul chiffre qui informe : parmi ceux qui ont change,
    combien ont change dans chaque sens. Onze gagnes contre sept perdus, c'est ce
    qu'un tirage a pile ou face produit sans peine ; dix-huit contre zero, non.
    """
    n = gagnes + perdus
    if n == 0:
        return 1.0
    k = min(gagnes, perdus)
    queue = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * queue)


def _charger(chemin: str) -> dict[str, dict]:
    return {r["id"]: r for r in _reponses(chemin)}


def _reponses(chemin) -> list:
    """Les reponses d'un fichier de mesure, quelle que soit sa forme.

    Les fichiers anterieurs au 05/09/2026 sont une liste nue ; depuis, ils portent
    aussi le montage qui les a produits.
    """
    d = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return d["reponses"] if isinstance(d, dict) else d


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a, b = _charger(sys.argv[1]), _charger(sys.argv[2])
    communs = sorted(set(a) & set(b))

    positifs = [i for i in communs if not a[i].get("negatif")]
    negatifs = [i for i in communs if a[i].get("negatif")]

    gagnes = [i for i in positifs if b[i]["cite_attendu"] and not a[i]["cite_attendu"]]
    perdus = [i for i in positifs if a[i]["cite_attendu"] and not b[i]["cite_attendu"]]
    stables = len(positifs) - len(gagnes) - len(perdus)

    refus_change = [i for i in negatifs if a[i]["refus"] != b[i]["refus"]]

    ta = sum(x["cite_attendu"] for x in a.values() if not x.get("negatif"))
    tb = sum(x["cite_attendu"] for x in b.values() if not x.get("negatif"))

    print(f"cas positifs compares      : {len(positifs)}")
    print(f"  total A                  : {ta}")
    print(f"  total B                  : {tb}")
    print(f"  ecart d'AGREGAT          : {tb - ta:+d}")
    print()
    print(f"  cas qui BASCULENT        : {len(gagnes) + len(perdus)}"
          f"  ({100 * (len(gagnes) + len(perdus)) / max(len(positifs), 1):.0f} %)")
    print(f"    gagnes en B            : {len(gagnes)}  {' '.join(gagnes) or '-'}")
    print(f"    perdus en B            : {len(perdus)}  {' '.join(perdus) or '-'}")
    print(f"  cas identiques           : {stables}")
    p = _mcnemar(len(gagnes), len(perdus))
    verdict = ("un tirage produit cela sans peine" if p > 0.05
               else "un tirage ne produit pas cela facilement")
    print(f"  test apparie (signes)    : p = {p:.3f} — {verdict}")
    print()
    print(f"cas negatifs               : {len(negatifs)}")
    print(f"  refus qui changent       : {len(refus_change)}  {' '.join(refus_change) or '-'}")
    print()
    print("CE QUE CELA IMPOSE : un ecart de montage inferieur au nombre de cas qui")
    print("basculent entre deux campagnes identiques n'est pas interpretable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
