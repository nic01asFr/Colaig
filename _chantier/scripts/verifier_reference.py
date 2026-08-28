"""Porte de régression : la mesure a-t-elle reculé ?

Ce que ce script change
------------------------
La référence L1.5 vivait dans des documents Markdown. **Un chiffre écrit dans un
document ne bloque rien** : il se lit après coup, quand la dégradation est déjà livrée.

Ce script lit `_chantier/reference.json`, rejoue la mesure, et **sort en échec** si un
seuil est franchi. C'est ce qui transforme un instantané en garde-fou.

Pourquoi il ne tourne pas à chaque commit
------------------------------------------
Il consomme des appels au modèle : embeddings pour la recherche, génération pour les
135 cas. Le passer sur chaque poussée coûterait sans rien apprendre — la mesure ne bouge
que si le corpus, le jeu doré, le prompt ou les réglages changent.

Il est donc **hebdomadaire et déclenchable à la main**. La suite hors ligne, elle, tourne
à chaque fois : elle vérifie l'ancrage du jeu doré, l'empreinte du corpus et les
garde-fous, sans un seul appel réseau.

Ce qu'il ne fait pas
--------------------
Il ne dit pas *pourquoi* une mesure a reculé. Il dit qu'elle a reculé, ce qui suffit à
arrêter la chaîne — le diagnostic vient après, avec `diagnostic_echecs.py` et
`reanalyse_generation.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
REFERENCE = RACINE / "_chantier" / "reference.json"
MESURES = RACINE / "_chantier" / "mesures"


def lire_reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def executer(script: str, env: dict | None = None, args: list[str] | None = None) -> str:
    """Lance un harnais de mesure et rend sa sortie."""
    complet = {**os.environ, **(env or {})}
    resultat = subprocess.run(
        [sys.executable, str(RACINE / "_chantier" / "scripts" / script)] + (args or []),
        cwd=RACINE, env=complet, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if resultat.returncode != 0:
        # stdout ET stderr : un sous-script qui rend son usage l'ecrit sur stdout, et ne
        # remonter que stderr produisait un « a echoue : » suivi de RIEN. Mesure le
        # 27/08/2026 sur `reanalyse_generation.py`. Un verificateur qui echoue sans dire
        # pourquoi cesse d'etre lu — meme defaut que `test_live.py` avant D14.
        motif = ((resultat.stderr or "") + (resultat.stdout or "")).strip()
        raise SystemExit(
            f"{script} a échoué (code {resultat.returncode}) :\n"
            + (motif[-1500:] or "aucune sortie — vérifier les arguments passés")
        )
    return resultat.stdout + resultat.stderr


def valeur(rapport: str, motif: str, groupe: int = 1) -> float:
    import re

    m = re.search(motif, rapport)
    if not m:
        raise SystemExit(f"indicateur introuvable dans le rapport : {motif}")
    return float(m.group(groupe).replace(",", "."))


def comparer(nom: str, mesure: float, seuil: dict, ecarts: list[str]) -> str:
    """Compare une mesure à son seuil et rend une ligne de rapport."""
    if "minimum" in seuil:
        ok = mesure >= seuil["minimum"]
        borne = f"≥ {seuil['minimum']}"
    else:
        ok = mesure <= seuil["maximum"]
        borne = f"≤ {seuil['maximum']}"
    marque = "  " if ok else "!!"
    if not ok:
        ecarts.append(f"{nom} : {mesure} ({borne} attendu, référence {seuil['valeur']})")
    return (f"{marque} {nom:28} {mesure:>8}   {borne:<10} "
            f"référence {seuil['valeur']}")


def canari() -> None:
    """Le modele mesure est-il encore le meme ?

    Sans ce controle, un changement de poids sous le meme nom rendrait toutes les
    valeurs de reference caduques EN SILENCE, et cette porte imputerait la derive au
    code. La soiree du 27/08/2026 a ete passee a faire cette distinction a la main.

    Verifie le 28/08 : le catalogue SSPCloud n'expose ni version ni empreinte — le nom
    du modele ne prouve rien. Le canari compare des sorties reelles.

    Absent, on avertit sans bloquer : la porte doit rester utilisable sur un poste ou
    une chaine d'integration qui ne l'a pas encore calibre. Present et en derive, on
    ARRETE : continuer produirait un diagnostic faux.
    """
    empreinte = RACINE / "_chantier" / "canari.json"
    if not empreinte.exists():
        print("canari absent — impossible de dire si les modeles ont change. "
              "Calibrer avec `canari_modeles.py --calibrer`.\n", file=sys.stderr)
        return
    resultat = subprocess.run(
        [sys.executable, str(RACINE / "_chantier" / "scripts" / "canari_modeles.py")],
        cwd=RACINE, env=os.environ.copy(), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if resultat.returncode != 0:
        raise SystemExit(
            "DERIVE DE MODELE — les valeurs de reference.json ont ete mesurees contre "
            "d'autres modeles.\n"
            + (resultat.stderr or resultat.stdout or "")[-1200:]
            + "\nNe pas imputer de regression au code avant d'avoir remesure la "
              "reference."
        )
    print("canari : modeles inchanges\n", file=sys.stderr)


def main() -> int:
    canari()
    ref = lire_reference()
    conf = ref["_configuration"]
    print(f"référence du {ref['_mesure_le']} — {conf['articles']} articles, "
          f"{conf['jeu_dore']} cas, k={conf['k']}\n")

    ecarts: list[str] = []
    lignes: list[str] = []

    # ── Recherche ───────────────────────────────────────────────────────────
    env = {"COLAIG_REF_K": str(conf["k"])}
    rapport = executer("reference_l15.py", env, ["article"])
    complets = valeur(rapport, r"récupération\s*:\s*(\d+)/")
    total = valeur(rapport, r"récupération\s*:\s*\d+/(\d+)")
    lignes.append(comparer("recherche complets", round(complets / total, 3),
                           ref["recherche"]["complets_sur_attendus"], ecarts))

    # ── Génération ──────────────────────────────────────────────────────────
    env = {"COLAIG_REF_K": str(conf["k"]),
           "COLAIG_REF_RAISONNEMENT": "0" if not conf["raisonnement"] else "1"}
    executer("reference_generation.py", env, [conf["variante"]])

    fichiers = sorted(MESURES.glob("reponses-*.json"), key=lambda f: f.stat().st_mtime)
    recompte = executer("reanalyse_generation.py", None,
                        [str(fichiers[-1]), str(conf["k"])])

    g = ref["generation"]
    refus_t = valeur(recompte, r"refus — toujours (\d+)")
    refus_n = valeur(recompte, r"sur (\d+) négatifs jugeables")
    lignes.append(comparer("refus systématique", round(refus_t / refus_n, 3),
                           g["refus_systematique"], ecarts))

    cite = valeur(recompte, r"cite l'attendu\s*:.*·\s*(\d+)/")
    cite_n = valeur(recompte, r"cite l'attendu\s*:.*·\s*\d+/(\d+)")
    lignes.append(comparer("cite l'attendu", round(cite / cite_n, 3), g["cite_attendu"], ecarts))

    for nom, cle, motif in (
        ("hors contexte", "hors_contexte_max", r"hors contexte\s*:\s*(\d+)"),
        ("fantômes", "fantomes_max", r"fantômes\s*:\s*(\d+)"),
        ("montants inventés", "montants_inventes_max", r"montants inventés\s*:\s*(\d+)"),
        ("tronquées", "tronquees_max", r"observations coupées\s*:\s*(\d+)"),
    ):
        lignes.append(comparer(nom, valeur(recompte, motif), g[cle], ecarts))

    rendues = valeur(recompte, r"garde-fou\s*:\s*rendue (\d+)")
    jugeables = valeur(recompte, r"sur (\d+) réponses jugeables")
    lignes.append(comparer("garde-fou rendues", round(rendues / jugeables, 3),
                           g["garde_fou_rendues"], ecarts))

    print("\n".join(lignes))

    if ecarts:
        print("\nRÉGRESSION — la mesure a reculé sous son seuil :")
        for e in ecarts:
            print(f"  {e}")
        print("\nDiagnostiquer avec diagnostic_echecs.py et reanalyse_generation.py.")
        print("Ne pas relâcher un seuil sans avoir compris ce qu'il protégeait :")
        print("chaque seuil de reference.json porte son motif.")
        return 1

    print("\nAucune régression.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
