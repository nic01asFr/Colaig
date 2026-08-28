"""
La confusion de régime, rendue mesurable.

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (élargissement du corpus)

Ce qu'on mesure, et pourquoi aucun cas nouveau n'est nécessaire
----------------------------------------------------------------
Les 135 cas du jeu doré portent **tous** sur le régime ordinaire — le constructeur du
corpus le dit : « Aucun des 117 articles attendus par le jeu doré n'en sort ».

Donc en élargissant le CORPUS sans toucher au JEU, toute citation venue d'un autre livre
devient une erreur, sans qu'aucune vérité métier nouvelle soit à écrire. La mesure du
23/08 l'avait constaté à la main (115 contre 1) ; il s'agit d'en faire un indicateur
avec sa dispersion.

Deux conditions, jamais mélangées
-----------------------------------
    restreint   1021 sources, 0 article d'un autre régime — la porte de non-régression
    complet     2128 sources, 1058 articles d'un autre régime — la surface de confusion

Les archives portent la condition dans leur nom. Les mélanger produirait une moyenne qui
ne décrit aucune des deux — le chantier a déjà payé ce piège avec des fichiers de
réponses écrasés parce que leur nom ne portait que la date.

Ce que cet indicateur voit et qu'aucun autre ne voit
------------------------------------------------------
Ni `fantomes` (l'article existe), ni `hors_contexte` (il était dans les passages), ni
`montants_inventes` (le montant figure dans le passage cité). Le livre défense pose
100 000 euros là où l'ordinaire pose 60 000 : la réponse est fluide, sourcée, et fausse.

Usage
-----
    set -a; . ./.env; set +a
    python _chantier/scripts/mesure_regime.py [tirages]
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))                              # pour `colaig.*`
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))    # pour `regimes`
MESURES = RACINE / "_chantier" / "mesures"
SCRIPTS = RACINE / "_chantier" / "scripts"

CORPUS_COMPLET = Path(os.environ.get(
    "COLAIG_CORPUS_COMPLET",
    r"C:\Users\Omen\AppData\Local\Temp\corpus-ccp-complet"))
TIRAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 3

from regimes import ORDINAIRE, attribuer, hors_regime  # noqa: E402

from colaig.rag.verification_citations import articles_cites  # noqa: E402


def tirage(indice: int) -> Path:
    """Un tirage de génération sur le corpus COMPLET, archivé sous son propre nom."""
    env = {**os.environ,
           "COLAIG_REF_CORPUS": str(CORPUS_COMPLET),
           "COLAIG_REF_K": "10",
           "COLAIG_REF_RAISONNEMENT": "0"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "reference_generation.py"), "durci"],
                       cwd=RACINE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        motif = ((r.stderr or "") + (r.stdout or "")).strip()
        raise SystemExit(f"génération en échec (code {r.returncode}) :\n{motif[-1200:]}")
    produits = sorted(MESURES.glob("reponses-*.json"), key=lambda f: f.stat().st_mtime)
    cible = MESURES / f"regime-complet-{indice}.json"
    shutil.copy2(produits[-1], cible)
    return cible


def compter(archive: Path, regimes: dict[str, str], cas: list[dict]) -> dict:
    reponses = {r["id"]: r for r in json.loads(archive.read_text(encoding="utf-8"))}
    fautifs, exemples = 0, []
    n = 0
    for c in cas:
        textes = reponses.get(c["id"], {}).get("reponses") or [""]
        if not textes or not textes[0]:
            continue
        n += 1
        mauvais = hors_regime(articles_cites(textes[0]), regimes, ORDINAIRE)
        if mauvais:
            fautifs += 1
            if len(exemples) < 5:
                exemples.append((c["id"], sorted(mauvais)[:3],
                                 {a: regimes[a] for a in sorted(mauvais)[:2]}))
    return {"cas": n, "regime_incorrect": fautifs, "exemples": exemples}


def main() -> int:
    if not CORPUS_COMPLET.exists():
        raise SystemExit(
            f"corpus complet absent : {CORPUS_COMPLET}\n"
            "Le construire : COLAIG_CORPUS_PERIMETRE=complet "
            "COLAIG_CORPUS_SORTIE=<dest> python _chantier/scripts/construire_corpus_mp.py")

    regimes = attribuer(CORPUS_COMPLET)
    surface = sum(1 for r in regimes.values()
                  if r not in (ORDINAIRE, "transverse", "contractuel"))
    print(f"corpus complet : {len(regimes)} articles · {surface} d'un AUTRE régime",
          file=sys.stderr)

    cas = [json.loads(l) for l
           in (RACINE / "tests" / "golden" / "v1.jsonl").read_text(encoding="utf-8").splitlines()
           if l.strip()]
    cas = [c for c in cas if c.get("articles_attendus")]

    existantes = sorted(MESURES.glob("regime-complet-*.json"))
    indice = max((int(p.stem.rsplit("-", 1)[1]) for p in existantes), default=0)
    while len(sorted(MESURES.glob("regime-complet-*.json"))) < TIRAGES:
        indice += 1
        print(f"  tirage {indice} sur corpus complet…", file=sys.stderr)
        tirage(indice)

    lignes = []
    for a in sorted(MESURES.glob("regime-complet-*.json")):
        d = compter(a, regimes, cas)
        lignes.append((a.name, d))
        print(f"  {a.name:26} {d['regime_incorrect']:3}/{d['cas']} réponses citant "
              f"un autre régime", file=sys.stderr)

    taux = [d["regime_incorrect"] / d["cas"] for _, d in lignes if d["cas"]]
    print(f"\n{len(lignes)} tirages · corpus COMPLET")
    print(f"  régime incorrect : moyenne {statistics.mean(taux):.1%}"
          + (f"  min {min(taux):.1%}  max {max(taux):.1%}" if len(taux) > 1 else ""))
    print("\n  mesure du 23/08 sur corpus entier : 22 % · sur corpus restreint : 0 %")

    for nom, d in lignes[:1]:
        for cid, arts, reg in d["exemples"]:
            print(f"\n  {cid} cite {arts}")
            for a, r in reg.items():
                print(f"     {a} relève de « {r} »")

    (MESURES / "regime-incorrect.json").write_text(
        json.dumps({"corpus": str(CORPUS_COMPLET), "surface_confusion": surface,
                    "tirages": [{"archive": n, **{k: v for k, v in d.items()
                                                  if k != "exemples"}}
                                for n, d in lignes]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
