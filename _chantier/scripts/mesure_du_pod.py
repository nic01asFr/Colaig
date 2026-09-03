"""
Colaig — mesurer L'INSTANCE, et non une reconstitution de son code.

STATUT: COMPLET
VERSION: 2026-09-03 - v1.0
LOT: L1.5b

POURQUOI CE HARNAIS EXISTE
---------------------------
`reference_pipeline.py` reconstitue Colaig sur la machine : corpus local, index en
memoire, decoupage choisi. Il a servi a trouver de vrais defauts, mais il DIVERGE du
service sur au moins deux reglages majeurs, releves le 03/09/2026 :

    decoupage   par article (1021 chunks)   contre   fenetre 800/100 (2388 chunks)
    profondeur  k=10                        contre   k=5 (workspace.max_results)

Ses chiffres decrivent donc un montage que personne n'utilise. Ce harnais-ci ne
reconstitue RIEN : il pose une question a `/ask`, lit la reponse, et note. Ce qu'il
mesure est ce que l'utilisateur recoit.

LA GARDE EST DANS LE CODE, PAS DANS LES INTENTIONS
---------------------------------------------------
Toute la journee du 03/09 a ete passee a corriger un instrument qui mesurait autre
chose que ce qu'il pretendait — modeles absents de l'endpoint, temperature
differente, Orchestrateur inerte, troncature devinee. Chaque correction en revelait
une autre, et aucune ne remettait le cadre en cause.

Ce harnais REFUSE donc de mesurer si l'image du pod n'est pas celle attendue. Un
chiffre produit sur une image inconnue ne vaut rien, et rien ne le signalerait.

    python _chantier/scripts/mesure_du_pod.py --negatifs        # 22 cas, ~5 min
    python _chantier/scripts/mesure_du_pod.py --complet         # 135 cas, ~30 min

ATTENTION : l'instance est en usage reel. Une campagne complete la monopolise une
demi-heure. `--pause` espace les appels pour la laisser respirable.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parents[2]
_ARGS = sys.argv[1:]
sys.path.insert(0, str(RACINE))

from colaig.rag.garde_fou_reponse import est_un_refus  # noqa: E402
from colaig.rag.verification_citations import articles_cites  # noqa: E402

BASE = "https://user-nic01asfr-colaig-test.user.lab.sspcloud.fr"
NS = "user-nic01asfr"
SALON = "!GVJlwHqwWSTxStFIGh:agent.dev-durable.tchap.gouv.fr"
UTILISATEUR = "@nicolas.laval-developpement-durable.gouv.fr1:agent.dev-durable.tchap.gouv.fr"
JEU = RACINE / "tests" / "golden" / "v1.jsonl"
MESURES = RACINE / "_chantier" / "mesures"


def _image_du_pod() -> str:
    r = subprocess.run(
        ["kubectl", "get", "deploy", "colaig-test-colaig", "-n", NS,
         "-o", "jsonpath={.spec.template.spec.containers[0].image}"],
        capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip()


def _commit_attendu() -> str:
    """Le dernier commit qui touche le CODE, pas le HEAD.

    Compare au HEAD, cette garde refusait de mesurer alors que seul le fichier de
    valeurs Helm avait bouge depuis l'image — un faux positif qui aurait pousse a
    redeployer pour rien, ou a la contourner. Ce qui doit coincider est `colaig/` :
    les scripts de mesure et les fichiers de deploiement ne partent pas dans
    l'image.
    """
    r = subprocess.run(["git", "log", "-1", "--format=%h", "--", "colaig"],
                       cwd=RACINE, capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()


def _verifier_le_montage() -> str:
    """Refuse de mesurer si l'image ne CONTIENT pas le code presente.

    La bonne question n'est pas « les deux noms coincident-ils » — le tag porte le
    commit de construction, qui est normalement en avance sur le dernier commit du
    code. Elle est : le commit de l'image est-il un descendant du dernier commit de
    `colaig/` ? Si oui, l'image porte ce code.

    Deux versions fausses de ce controle avant celle-ci, le 03/09/2026 : comparee au
    HEAD, la garde refusait alors que seul un fichier de valeurs Helm avait bouge ;
    comparee par egalite de noms, elle refusait une image pourtant plus recente que
    le code. Une garde qui crie a tort finit contournee — c'est le defaut meme que ce
    chantier passe son temps a fermer.

    Sans ce controle, un chiffre peut sortir d'une image inconnue et rien ne le dit :
    c'est ce qui a fait porter quatre campagnes sur un pipeline qui n'aurait pas
    demarre en production.
    """
    image = _image_du_pod()
    if not image:
        raise SystemExit("MONTAGE INCONNU — impossible de lire l'image du pod")
    tag = image.rsplit(":", 1)[-1]
    commit_image = tag.replace("tronc-", "")
    commit_code = _commit_attendu()

    connu = subprocess.run(["git", "cat-file", "-e", f"{commit_image}^{{commit}}"],
                           cwd=RACINE, capture_output=True, timeout=30).returncode == 0
    if not connu:
        raise SystemExit(
            f"MONTAGE INCONNU — le pod tourne « {tag} », commit absent de ce depot. "
            f"Impossible de dire quel code il porte.")

    inclut = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit_code, commit_image],
        cwd=RACINE, capture_output=True, timeout=30).returncode == 0
    if not inclut:
        raise SystemExit(
            f"MONTAGE INCOHERENT — le pod tourne « {tag} », qui NE CONTIENT PAS le "
            f"dernier commit de colaig/ ({commit_code}). Deployer d'abord.")

    ecart = subprocess.run(
        ["git", "log", "--oneline", f"{commit_image}..HEAD", "--", "colaig"],
        cwd=RACINE, capture_output=True, text=True, timeout=30).stdout.strip()
    print(f"montage  : {tag} — contient le code de colaig/ jusqu'a {commit_code}")
    if ecart:
        raise SystemExit(
            "MONTAGE INCOHERENT — code de colaig/ en avance sur l'image : " + ecart)
    return image


def _demander(question: str, pause: float) -> tuple[str, float]:
    charge = json.dumps({"message": question, "conversation_id": SALON,
                         "user_id": UTILISATEUR}).encode()
    r = urllib.request.Request(BASE + "/ask", data=charge, method="POST")
    r.add_header("Content-Type", "application/json")
    debut = time.monotonic()
    with urllib.request.urlopen(r, timeout=300) as rep:
        d = json.loads(rep.read().decode())
    duree = time.monotonic() - debut
    if pause:
        time.sleep(pause)
    return d.get("response", "") or "", duree


def main() -> int:
    _verifier_le_montage()
    negatifs_seuls = "--negatifs" in _ARGS
    pause = 0.0
    for a in _ARGS:
        if a.startswith("--pause="):
            pause = float(a.split("=", 1)[1])

    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    if negatifs_seuls:
        cas = [c for c in cas if c.get("attendu_refus")]
    print(f"cas      : {len(cas)}{' (negatifs seuls)' if negatifs_seuls else ' (jeu complet)'}")
    print(f"pause    : {pause} s entre appels\n")

    resultats, latences = [], []
    for i, c in enumerate(cas, 1):
        try:
            texte, duree = _demander(c["question"], pause)
        except Exception as e:  # noqa: BLE001
            print(f"  {c['id']} : echec ({type(e).__name__})", file=sys.stderr)
            continue
        latences.append(duree)
        resultats.append({
            "id": c["id"], "negatif": bool(c.get("attendu_refus")),
            "question": c["question"], "reponse": texte,
            "refus": est_un_refus(texte),
            "articles_cites": sorted(articles_cites(texte)),
            "cite_attendu": bool(set(c.get("articles_attendus", [])) & articles_cites(texte)),
            "annote_par_le_garde_fou": "non vérifiable" in texte,
            "latence_s": round(duree, 1),
        })
        print(f"  {i}/{len(cas)} {c['id']} {duree:.1f}s", end="\r", flush=True)

    MESURES.mkdir(exist_ok=True)
    suffixe = "negatifs" if negatifs_seuls else "complet"
    sortie = MESURES / f"pod-{suffixe}-{time.strftime('%Y%m%d-%H%M')}.json"
    sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=1), encoding="utf-8")

    negatifs = [r for r in resultats if r["negatif"]]
    positifs = [r for r in resultats if not r["negatif"]]
    import statistics
    print("\n" + "=" * 60)
    print(f"reponses           : {len(resultats)}")
    if negatifs:
        print(f"refus sur negatifs : {sum(r['refus'] for r in negatifs)}/{len(negatifs)}")
    if positifs:
        print(f"cite l'attendu     : {sum(r['cite_attendu'] for r in positifs)}/{len(positifs)}")
    print(f"annotees garde-fou : {sum(r['annote_par_le_garde_fou'] for r in resultats)}")
    if latences:
        print(f"latence mediane    : {statistics.median(latences):.1f} s")
    print(f"\nreponses conservees : {sortie.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
