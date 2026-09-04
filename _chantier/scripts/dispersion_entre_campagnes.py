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
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
JEU = RACINE / "tests" / "golden" / "v1.jsonl"


def _charger(chemin: str) -> dict[str, dict]:
    return {r["id"]: r for r in json.loads(Path(chemin).read_text(encoding="utf-8"))}


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
    print()
    print(f"cas negatifs               : {len(negatifs)}")
    print(f"  refus qui changent       : {len(refus_change)}  {' '.join(refus_change) or '-'}")
    print()
    print("CE QUE CELA IMPOSE : un ecart de montage inferieur au nombre de cas qui")
    print("basculent entre deux campagnes identiques n'est pas interpretable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
