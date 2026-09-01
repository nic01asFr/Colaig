"""
Colaig — effet mesure du garde-fou de provenance sur des reponses deja obtenues.

STATUT: COMPLET
VERSION: 2026-09-01 - v1.0
LOT: L1.5b

Pourquoi ce script existe
-------------------------
`garde_fou_reponse.appliquer()` porte dans `generator.py` une mesure flatteuse — 24
reponses annotees, 5 remplacees, sur 164. Mais il n'est actif NULLE PART :
`COLAIG_GARDE_FOU_ENABLED` vaut "0" par defaut, le drapeau est absent du chart Helm,
et `synthesiser.py` n'en a meme pas le code. Le pipeline agent repond donc sans
aucun controle de provenance.

Avant de le cabler, il faut savoir ce qu'il ferait AUJOURD'HUI, sur le montage
courant. Le garde-fou est **post-hoc et pur** : il ne depend que de la reponse et des
passages. Il se rejoue donc sur les reponses archivees, sans un seul appel au modele.

Les DEUX cotes sont comptes, et c'est le point :

- ce qu'il **rattrape** : reponses portant un fantome ou une citation hors contexte,
  qu'il annote ou remplace ;
- ce qu'il **abime** : reponses saines qu'il annote, ou remplace par un refus.

Une mesure qui ne dirait que le premier ferait activer un garde-fou qui degrade. Le
commentaire de `verification_citations` rappelle que c'est deja arrive : le garde-fou
detruisait la bonne reponse qu'il etait cense proteger.

Usage
-----
    COLAIG_REF_K=10 python _chantier/scripts/effet_garde_fou.py [fichier-reponses.json]

Le montage doit etre celui qui a produit le fichier — K et perimetre compris, sans
quoi les passages rejoues ne sont pas ceux qui ont ete servis.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))
sys.path.insert(0, str(RACINE))

# `reference_generation` REECRIT sys.argv au chargement (sys.argv = ["gen", "article"]).
# L'argument doit donc etre lu AVANT l'import, sinon on cherche un fichier « article ».
_ARG = sys.argv[1] if len(sys.argv) > 1 else ""

import reference_generation as R  # noqa: E402
from colaig.models import WorkspaceConfig  # noqa: E402
from colaig.rag.garde_fou_reponse import appliquer_selon_espace  # noqa: E402
from colaig.rag.verification_citations import FORMAT_CLAUSE, FORMAT_CODE  # noqa: E402

# La grammaire de citation du corpus, telle qu'un espace la declarerait. Mise a
# COLAIG_EGF_GRAMMAIRE=0, le script rejoue le garde-fou aveugle — ce qui permet de
# mesurer ce que la declaration change, au lieu de l'affirmer.
GRAMMAIRE = os.environ.get("COLAIG_EGF_GRAMMAIRE", "1") != "0"

DEFAUT = (RACINE / "_chantier" / "mesures"
          / "reponses-durci-k10-sansraisonnement-qwen3-embedding-8b-coeur-etape0-20260831.json")


# Piles d'embedding pouvant figurer dans un nom de fichier de reponses. Le defaut du
# harnais (`BAAI/bge-m3`) ne laisse aucune marque, il est donc deduit par absence.
_PILES = {"qwen3-embedding-8b": ("qwen3-embedding-8b", 4096)}
_PILE_DEFAUT = ("BAAI/bge-m3", 1024)


def _verifier_montage(nom: str) -> None:
    """Refuse de rejouer un fichier avec une pile qui n'est pas la sienne.

    Rejoue le 01/09/2026 avec la pile par defaut (`BAAI/bge-m3`, 1024 dim) un fichier
    produit avec `qwen3-embedding-8b` en 4096 : les passages retrouves n'etaient pas
    ceux servis. La mesure annoncait 57 citations hors contexte la ou le rapport joint
    en comptait 16 — et rien dans la sortie ne le signalait.

    Quatrieme occurrence du meme defaut dans ce chantier : `reference_generation` le
    decrit deja en commentaire. Un commentaire ne l'a pas empeche, un controle si.
    """
    attendu, dim = next((v for k, v in _PILES.items() if k in nom), _PILE_DEFAUT)
    reel = R._ns["MODELE_EMBED"]
    if reel != attendu:
        raise SystemExit(
            f"MONTAGE INCOHERENT — ce fichier a ete produit avec « {attendu} », la "
            f"session tourne avec « {reel} ». Les passages rejoues ne seraient pas ceux "
            f"servis. Relancer avec : COLAIG_REF_EMBED_MODELE={attendu} "
            f"COLAIG_REF_EMBED_DIM={dim} COLAIG_REF_K=<k du fichier>")
    k_nom = re.search(r"-k(\d+)", nom)
    if k_nom and int(k_nom.group(1)) != R.K:
        raise SystemExit(
            f"MONTAGE INCOHERENT — ce fichier a ete produit a k={k_nom.group(1)}, la "
            f"session tourne a k={R.K}. Relancer avec COLAIG_REF_K={k_nom.group(1)}.")


def main() -> int:
    fichier = Path(_ARG) if _ARG else DEFAUT
    if not fichier.is_absolute():
        fichier = RACINE / "_chantier" / "mesures" / fichier.name
    obs = json.loads(fichier.read_text(encoding="utf-8"))
    print(f"reponses  : {fichier.name}")
    _verifier_montage(fichier.name)
    print(f"montage   : perimetre={R.PERIMETRE} k={R.K} "
          f"embed={R._ns['MODELE_EMBED']} {R._ns['DIMENSION']} dim")
    print(f"grammaire : {'corpus declaree (code + clause + identifiants)' if GRAMMAIRE else 'defaut (code seul)'}")

    jeu = {}
    for ligne in R.JEU.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            c = json.loads(ligne)
            jeu[c["id"]] = c

    cle = R.cle_albert()
    chunks = R.decouper(R.PERIMETRE)
    for ch in chunks:
        if ch.section.startswith("Article "):
            R._IDENTIFIANTS.add(ch.section[len("Article "):])
    articles_existants = set(R._IDENTIFIANTS)
    exist = articles_existants
    print(f"corpus    : {len(chunks)} chunks, {len(articles_existants)} identifiants")

    # L'espace tel qu'il serait declare : c'est LUI qui decide, pas le script.
    espace = WorkspaceConfig(
        workspace_id="marches-publics", name="Marches publics", storage_path="/mp/",
        garde_fou_provenance=True,
        format_citation=[FORMAT_CODE, FORMAT_CLAUSE] if GRAMMAIRE else [FORMAT_CODE])

    store = R.FaissStore(dimension=R._ns["DIMENSION"])
    store.add(R.embed([c.text for c in chunks], cle), chunks)
    vq = R.embed([o["question"] for o in obs], cle)

    # Deux populations disjointes, comptees separement : c'est la seule facon de voir
    # un garde-fou qui rattrape beaucoup ET casse beaucoup.
    avec_defaut: Counter = Counter()
    sain: Counter = Counter()
    sains_touches: list = []   # le cout, nomme : un faux positif anonyme ne se corrige pas
    perte_attendu = 0        # citait l'article attendu, et se fait remplacer
    gain_refus = 0           # cas negatif qui ne refusait pas, remplace par un refus
    total = 0

    for o, v in zip(obs, vq):
        trouves = store.search(v, k=R.K)
        passages = [r.chunk.text for r in trouves]
        fournis: set[str] = set()
        for p in passages:
            fournis |= R.articles_cites(p)

        attendus = set(jeu.get(o["id"], {}).get("articles_attendus", []))
        negatif = bool(o.get("negatif"))

        for reponse in o["reponses"]:
            texte = reponse if isinstance(reponse, str) else reponse.get("reponse", "")
            if not texte:
                continue
            total += 1
            cites = R.articles_cites(texte)
            fantomes = cites - articles_existants - fournis
            hors_ctx = (cites & articles_existants) - fournis

            d = appliquer_selon_espace(texte, trouves, espace)
            porte_un_defaut = bool(fantomes or hors_ctx)
            (avec_defaut if porte_un_defaut else sain)[d.action] += 1
            if not porte_un_defaut and d.action != "rendue":
                sains_touches.append((o["id"], d.action, d.motif))

            if d.action == "remplacée":
                if attendus & cites:
                    perte_attendu += 1
                refusait = any(m in texte.lower() for m in R.MARQUEURS_REFUS)
                if negatif and not refusait:
                    gain_refus += 1

    def bloc(titre: str, c: Counter) -> None:
        n = sum(c.values())
        print(f"\n{titre} — {n} reponses")
        for action in ("rendue", "annotée", "remplacée"):
            part = f"{c[action] * 100 // n} %" if n else "-"
            print(f"   {action:11s} : {c[action]:4d}  ({part})")

    print(f"\n{'=' * 58}\ntotal reponses rejouees : {total}")
    bloc("PORTANT UN DEFAUT (fantome ou hors contexte)", avec_defaut)
    bloc("SAINES (aucun defaut mesure)", sain)

    rattrape = avec_defaut["annotée"] + avec_defaut["remplacée"]
    abime = sain["annotée"] + sain["remplacée"]
    print(f"\nrattrape : {rattrape} reponses fautives signalees ou ecartees")
    print(f"abime    : {abime} reponses saines touchees "
          f"(dont {sain['remplacée']} remplacees par un refus)")
    print(f"cout     : {perte_attendu} reponses citant l'article attendu, remplacees")
    print(f"gain     : {gain_refus} cas negatifs sans refus, ramenes a un refus")
    for ident, action, motif in sains_touches:
        print(f"   faux positif : {ident} — {action} — {motif}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
