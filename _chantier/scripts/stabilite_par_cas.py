"""Combien de cas sont servis A TOUS LES COUPS, et combien une fois sur deux ?

POURQUOI CET INDICATEUR-LA
----------------------------
L'agregat d'une campagne — « 75/113 » — melange deux choses tres differentes : les cas
que le systeme sait traiter, et ceux qu'il traite au tirage. Mesure du 05/09/2026 sur
six campagnes du service, au grain du passage :

    article attendu TOUJOURS servi   51
    servi PARFOIS                    53      <-- un cas sur deux
    JAMAIS servi                      9

Deux campagnes identiques different alors sur dix-huit cas, et aucun ecart de reglage
de la journee ne s'est distingue du hasard. Tant qu'on lit l'agregat, on compare des
tirages.

Cet indicateur-ci ne bouge pas pour la meme raison : il DEMANDE la constance. Un
reglage qui fait passer des cas de « parfois » a « toujours » se voit ; un reglage qui
deplace le tirage sans rien stabiliser ne se voit pas — et c'est exactement ce qu'on
veut.

    python _chantier/scripts/stabilite_par_cas.py <mesure-1.json> <mesure-2.json> [...]
"""

from __future__ import annotations

import collections
import importlib.util
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
JEU = RACINE / "tests" / "golden" / "v1.jsonl"
SOURCES = RACINE / "_chantier" / "scripts" / "sources_servies_par_le_pod.py"


def _module_des_sources():
    spec = importlib.util.spec_from_file_location("sources", SOURCES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

    m = _module_des_sources()
    journal = {(q, m._empreinte_reponse(r)): p
               for q, _s, p, r in m._journal_du_stockage(m._pod(), m.ESPACE)}
    cas = {c["id"]: c for c in
           (json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip())}

    servi = collections.Counter()
    cite = collections.Counter()
    vus = collections.Counter()

    for chemin in sys.argv[1:]:
        for r in _reponses(chemin):
            if r.get("negatif"):
                continue
            attendus = set(cas.get(r["id"], {}).get("articles_attendus") or [])
            if not attendus:
                continue
            passages = journal.get((r["question"], m._empreinte_reponse(r["reponse"])))
            if passages is None:
                continue
            vus[r["id"]] += 1
            titres = {t[len("Article "):] if t.startswith("Article ") else t for t in passages}
            if titres & attendus:
                servi[r["id"]] += 1
            if r.get("cite_attendu"):
                cite[r["id"]] += 1

    n = len(sys.argv) - 1
    complets = [i for i in vus if vus[i] == n]
    print(f"campagnes comparees : {n}")
    print(f"cas observes partout : {len(complets)}"
          f"  (sur {len(vus)} vus au moins une fois)")
    print()
    for libelle, compteur in (("ARTICLE SERVI", servi), ("ARTICLE CITE", cite)):
        toujours = [i for i in complets if compteur[i] == n]
        jamais = [i for i in complets if compteur[i] == 0]
        parfois = [i for i in complets if 0 < compteur[i] < n]
        print(f"{libelle}")
        print(f"  toujours : {len(toujours):3d}")
        print(f"  parfois  : {len(parfois):3d}")
        print(f"  jamais   : {len(jamais):3d}")
        if libelle == "ARTICLE SERVI" and jamais:
            print(f"    {' '.join(sorted(jamais))}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
