"""
Dispersion de TOUS les indicateurs de génération, pas seulement de trois.

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

La dette que ce script solde
------------------------------
Sept blocs de `reference.json` ne déclarent pas sur combien de tirages ils reposent.
Leur attribuer un nombre au jugement reviendrait à inventer une donnée (`CLAUDE.md`
§4.8) ; `test_reference_tirages.py` les épingle donc en liste, et empêche seulement
cette liste de s'allonger.

Quatre d'entre eux sont mesurables **hors ligne** : `refus_systematique`,
`montants_inventes_max`, `tronquees_max` et `garde_fou_rendues` se lisent tous dans le
rapport de `reanalyse_generation.py`, qui ne fait aucun appel au modèle.

Ce qui coûte, et ce qui ne coûte rien
---------------------------------------
Une **archive de réponses** coûte un tirage de génération complet (~6 minutes). La
**réanalyse** d'une archive existante ne coûte rien : elle relit un fichier et compare
des numéros d'article à un corpus.

Ce script exploite donc d'abord ce qui existe, et ne produit de nouveaux tirages que
pour atteindre le plancher.

Une leçon payée
----------------
La première campagne de dispersion a produit huit archives, **supprimées avant le
commit** au motif qu'elles étaient « sans valeur une fois réanalysées ». Elles auraient
servi ici : la réanalyse est justement ce qui les rend réutilisables. Elles sont
désormais conservées localement — ignorées par git, pas effacées.

Usage
-----
    set -a; . ./.env; set +a
    python _chantier/scripts/dispersion_indicateurs.py [plancher]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
SCRIPTS = RACINE / "_chantier" / "scripts"
MESURES = RACINE / "_chantier" / "mesures"

PLANCHER = int(sys.argv[1]) if len(sys.argv) > 1 else 10
K = "10"
VARIANTE = "durci"

# Chaque indicateur : comment le lire, et de quelle nature il est. La NATURE decide de
# la regle de seuil — une fraction supporte deux sigma, un compte suit un Poisson et sa
# queue droite en demande trois (mesure du 28/08).
INDICATEURS = {
    "refus_systematique": ("fraction", r"^refus — toujours (\d+).*sur (\d+) négatifs"),
    "cite_attendu": ("fraction", r"^cite l'attendu\s*:.*·\s*(\d+)/(\d+)"),
    "garde_fou_rendues": ("fraction", r"^garde-fou\s*:\s*rendue (\d+).*sur (\d+) réponses"),
    "fantomes": ("compte", r"^fantômes\s*:\s*(\d+)"),
    "hors_contexte": ("compte", r"^hors contexte\s*:\s*(\d+)"),
    "montants_inventes": ("compte", r"^montants inventés\s*:\s*(\d+)"),
    "tronquees": ("compte", r"^observations coupées\s*:\s*(\d+)"),
}


def lancer(script: str, args: list[str], env_sup: dict | None = None) -> str:
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=RACINE, env={**os.environ, **(env_sup or {})},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        motif = ((r.stderr or "") + (r.stdout or "")).strip()
        raise SystemExit(f"{script} a échoué (code {r.returncode}) :\n{motif[-1200:]}")
    return r.stdout + r.stderr


def lire(rapport: str) -> dict:
    """Extrait tous les indicateurs d'un rapport de réanalyse.

    Les motifs sont ancrés en début de ligne : le rapport liste ensuite le détail cas
    par cas, où « fantôme » et « hors contexte » reparaissent.
    """
    valeurs = {}
    for nom, (nature, motif) in INDICATEURS.items():
        m = re.search(motif, rapport, re.MULTILINE)
        if not m:
            raise SystemExit(f"indicateur introuvable : {nom} ({motif})")
        if nature == "fraction":
            valeurs[nom] = round(int(m.group(1)) / int(m.group(2)), 4)
        else:
            valeurs[nom] = int(m.group(1))
    return valeurs


def archives() -> list[Path]:
    return sorted(MESURES.glob("dispersion-durci-*.json"))


def nouveau_tirage(indice: int) -> Path:
    lancer("reference_generation.py", [VARIANTE],
           {"COLAIG_REF_K": K, "COLAIG_REF_RAISONNEMENT": "0"})
    produits = sorted(MESURES.glob("reponses-*.json"), key=lambda f: f.stat().st_mtime)
    cible = MESURES / f"dispersion-durci-{indice}.json"
    shutil.copy2(produits[-1], cible)
    return cible


def main() -> int:
    existantes = archives()
    print(f"{len(existantes)} archive(s) disponible(s), plancher {PLANCHER}",
          file=sys.stderr)

    indice = max((int(p.stem.rsplit("-", 1)[1]) for p in existantes), default=0)
    while len(archives()) < PLANCHER:
        indice += 1
        print(f"  tirage supplémentaire {indice}", file=sys.stderr)
        nouveau_tirage(indice)

    observations = []
    for a in archives():
        obs = lire(lancer("reanalyse_generation.py", [str(a), K]))
        obs["_archive"] = a.name
        observations.append(obs)
        print(f"  {a.name} : " + "  ".join(
            f"{k}={v}" for k, v in obs.items() if not k.startswith("_")),
            file=sys.stderr)

    resume = {}
    print(f"\n{len(observations)} tirages\n")
    for nom, (nature, _) in INDICATEURS.items():
        v = sorted(o[nom] for o in observations)
        m, s = statistics.mean(v), statistics.stdev(v) if len(v) > 1 else 0.0
        # Deux sigma pour une fraction, trois pour un compte : mesure du 28/08 sur
        # quinze tirages — a deux sigma, le plafond des fantomes etait franchi par une
        # observation sur quinze, sur du code sain.
        facteur = 2 if nature == "fraction" else 3
        resume[nom] = {
            "nature": nature, "n": len(v), "observe": v,
            "moyenne": round(m, 4), "sigma": round(s, 4),
            "borne_basse": round(m - facteur * s, 4),
            "borne_haute": round(m + facteur * s, 4),
            "regle": f"moyenne ± {facteur} sigma",
        }
        print(f"{nom:20} {nature:8} n={len(v):2}  moyenne {m:8.4f}  sigma {s:7.4f}  "
              f"min {min(v)}  max {max(v)}")
        print(f"{'':29} borne basse {m - facteur * s:.4f} · "
              f"borne haute {m + facteur * s:.4f}  ({facteur} sigma)")

    (MESURES / "dispersion-indicateurs.json").write_text(
        json.dumps({"observations": observations, "resume": resume},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nécrit : {MESURES / 'dispersion-indicateurs.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
