"""
Deux bras, plusieurs tirages — le durcissement D50 coûte-t-il quelque chose ?

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

La question, et pourquoi elle se pose
---------------------------------------
La porte de non-régression est rouge : `cite l'attendu` = 0.770 pour un seuil à 0.78.

Le diagnostic écarte une régression du code : le harnais n'emprunte que
`Generator._build_messages`, et `rag/generator.py` n'a pas bougé depuis L2.1. Ce qui a
changé sur ce chemin, c'est **`wrap.CONSIGNE`**, durcie par D50 le 27/08 à 20 h 32.

Or la valeur de référence 0.823 a été posée **dix minutes plus tard**, sur **un seul
tirage**. Deux tirages du 27/08 donnent 0.788 et 0.770.

Ce que ce script mesure, et pourquoi deux bras
------------------------------------------------
Un seul bras dirait où l'on est aujourd'hui, pas ce qui nous y a menés. Deux bras
séparent deux hypothèses qui appellent des décisions opposées :

    bras DURCI       CONSIGNE actuelle          — l'état livré
    bras AVANT_D50   CONSIGNE sans la phrase    — l'état d'avant le durcissement

Si les deux bras se recouvrent, D50 ne coûte rien et **c'est la valeur de référence qui
est fausse** : elle a été posée sur un tirage haut. Si le bras DURCI est nettement plus
bas, D50 coûte de la fidélité de citation et l'arbitrage est un compromis
sécurité/utilité.

Pourquoi ce script existe plutôt qu'une boucle shell
------------------------------------------------------
Le nom du fichier de réponses ne contient que la variante, `k` et la **date** : deux
tirages du même jour **s'écrasent en silence**. C'est un piège déjà rencontré dans ce
chantier — un rapport écrit sous le nom d'une variante qu'il ne mesurait pas. Chaque
tirage est donc archivé sous un nom unique avant d'être réanalysé.

Usage
-----
    set -a; . ./.env; set +a; export SSPCLOUD_API_KEY="$sspcloud_api_key"
    python _chantier/scripts/dispersion_consigne.py [tirages_par_bras]
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

TIRAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 4

# La configuration de la référence — ne pas la changer sans changer reference.json.
CONF = {"COLAIG_REF_K": "10", "COLAIG_REF_RAISONNEMENT": "0"}
VARIANTE = "durci"

BRAS = {
    "durci": {},                                  # CONSIGNE actuelle
    "avant_d50": {"COLAIG_REF_CONSIGNE": "avant_d50"},
}


def lancer(script: str, env_sup: dict, args: list[str]) -> str:
    complet = {**os.environ, **CONF, **env_sup}
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=RACINE, env=complet, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        # stdout ET stderr : un sous-script qui rend son usage l'ecrit sur stdout.
        motif = ((r.stderr or "") + (r.stdout or "")).strip()
        raise SystemExit(f"{script} a échoué (code {r.returncode}) :\n{motif[-1500:]}")
    return r.stdout + r.stderr


def valeur(rapport: str, motif: str) -> float:
    # Ancrage en debut de ligne : le rapport liste ENSUITE le detail, ou « fantome » et
    # « hors contexte » reapparaissent case par case. Sans ancrage, un jour ou l'ordre
    # des sections changerait, on lirait un numero d'article pour un compte.
    m = re.search(motif, rapport, re.MULTILINE)
    if not m:
        raise SystemExit(f"motif introuvable dans le rapport : {motif}")
    return float(m.group(1))


def un_tirage(bras: str, indice: int) -> dict:
    lancer("reference_generation.py", BRAS[bras], [VARIANTE])

    # Archiver AVANT réanalyse : le prochain tirage écraserait le fichier.
    produits = sorted(MESURES.glob("reponses-*.json"), key=lambda f: f.stat().st_mtime)
    archive = MESURES / f"dispersion-{bras}-{indice}.json"
    shutil.copy2(produits[-1], archive)

    rapport = lancer("reanalyse_generation.py", {}, [str(archive), CONF["COLAIG_REF_K"]])
    return {
        "bras": bras,
        "tirage": indice,
        "cite_attendu": round(
            valeur(rapport, r"^cite l'attendu\s*:.*·\s*(\d+)/")
            / valeur(rapport, r"^cite l'attendu\s*:.*·\s*\d+/(\d+)"), 4),
        "fantomes": valeur(rapport, r"^fantômes\s*:\s*(\d+)"),
        "hors_contexte": valeur(rapport, r"^hors contexte\s*:\s*(\d+)"),
    }


def resume(nom: str, valeurs: list[float]) -> str:
    if len(valeurs) < 2:
        return f"  {nom:16} {valeurs}"
    return (f"  {nom:16} moyenne {statistics.mean(valeurs):.4f}  "
            f"min {min(valeurs):.4f}  max {max(valeurs):.4f}  "
            f"étendue {max(valeurs) - min(valeurs):.4f}")


def main() -> int:
    print(f"{TIRAGES} tirages par bras · variante {VARIANTE} · k={CONF['COLAIG_REF_K']}",
          file=sys.stderr)

    observations: list[dict] = []
    # Alterner les bras plutôt que de les enchaîner : si l'endpoint dérive en cours de
    # mesure, une dérive temporelle se retrouverait sinon entièrement imputée au bras
    # mesuré en second.
    for i in range(1, TIRAGES + 1):
        for bras in BRAS:
            o = un_tirage(bras, i)
            observations.append(o)
            print(f"  {bras:10} tirage {i} : cite {o['cite_attendu']:.4f}  "
                  f"fantômes {o['fantomes']:.0f}  hors contexte {o['hors_contexte']:.0f}",
                  file=sys.stderr)
            (MESURES / "dispersion-consigne.json").write_text(
                json.dumps(observations, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    for bras in BRAS:
        print(f"BRAS {bras.upper()}")
        for cle in ("cite_attendu", "fantomes", "hors_contexte"):
            print(resume(cle, [o[cle] for o in observations if o["bras"] == bras]))
        print()

    a = [o["cite_attendu"] for o in observations if o["bras"] == "durci"]
    b = [o["cite_attendu"] for o in observations if o["bras"] == "avant_d50"]
    if len(a) >= 2 and len(b) >= 2:
        ecart = statistics.mean(a) - statistics.mean(b)
        etendue = max(max(a) - min(a), max(b) - min(b))
        print(f"écart des moyennes (durci − avant_d50) : {ecart:+.4f}")
        print(f"plus grande étendue intra-bras          : {etendue:.4f}")
        print()
        if abs(ecart) <= etendue:
            print("LECTURE : l'écart entre les bras est INFÉRIEUR à la dispersion")
            print("interne. D50 ne coûte rien de mesurable — c'est la VALEUR DE")
            print("RÉFÉRENCE 0.823 qui est fausse, posée sur un tirage haut.")
        else:
            print("LECTURE : l'écart entre les bras DÉPASSE la dispersion interne.")
            print("Le durcissement a un coût mesurable — arbitrage sécurité/utilité.")
        print()
        print("Dans les deux cas : rebaser reference.json sur la MOYENNE du bras")
        print("retenu, jamais sur un tirage. Seuil = moyenne − étendue observée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
