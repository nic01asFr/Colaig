"""Que coute le sommaire dans le vivier ?

CE QUE LA MESURE MONTRE
-------------------------
`000-SOMMAIRE.md` est servi dans 83 % des questions posees a l'espace des marches
publics. C'est une table des matieres : elle porte les intitules de TOUS les articles,
donc elle ressemble a toute question — et elle ne porte le texte d'AUCUN. Elle prend
une place dans le vivier, puis une place parmi les `k` passages servis, sans jamais
pouvoir repondre.

L'EXPERIENCE
--------------
On ne construit rien avant de savoir ce que ca vaut. Ecarter le sommaire de
l'indexation et remesurer dit, en un chiffre, si la place qu'il occupe explique une
part des 113 - 64 cas manquants.

Il n'y a pas d'exclusion par fichier dans Colaig : `.colaig-ignore` ecarte un DOSSIER
de la decouverte des espaces, pas un document de l'indexation. On renomme donc le
document en une extension non indexable — `is_indexable()` ne le prend plus, et
`check_updates()` le voit comme supprime.

RIEN N'EST DETRUIT : le contenu est recopie sous un autre nom avant d'etre retire, et
`--restaurer` remet l'original en place.

    python _chantier/scripts/ecarter_le_sommaire.py --ecarter
    python _chantier/scripts/ecarter_le_sommaire.py --restaurer
"""

from __future__ import annotations

import subprocess
import sys

NAMESPACE = "user-nic01asfr"
ESPACE = "/colaig-mesure-marches-publics/"
DOCUMENT = "000-SOMMAIRE.md"
ECARTE = "000-SOMMAIRE.md.ecarte"

_SCRIPT = """
import asyncio
from colaig.config import load_config
from colaig.main import create_storage
async def m():
    s = create_storage(load_config())
    depuis, vers = '{depuis}', '{vers}'
    if not await s.exists(depuis):
        print('ABSENT ' + depuis); return
    if await s.exists(vers):
        print('DEJA_LA ' + vers); return
    await s.upload(vers, await s.download(depuis))
    await s.delete(depuis)
    print('DEPLACE ' + depuis + ' -> ' + vers)
asyncio.run(m())
"""


def _pod() -> str:
    out = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/instance=colaig-test",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _deplacer(depuis: str, vers: str) -> int:
    out = subprocess.run(
        ["kubectl", "exec", "-n", NAMESPACE, _pod(), "--", "python", "-c",
         _SCRIPT.format(depuis=depuis, vers=vers)],
        capture_output=True, text=True, encoding="utf-8")
    print(out.stdout.strip() or out.stderr.strip()[-400:])
    return out.returncode


def main() -> int:
    if "--ecarter" in sys.argv:
        return _deplacer(ESPACE + DOCUMENT, ESPACE + ECARTE)
    if "--restaurer" in sys.argv:
        return _deplacer(ESPACE + ECARTE, ESPACE + DOCUMENT)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
