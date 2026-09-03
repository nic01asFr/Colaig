"""
Colaig — a quel agent du pipeline la variance est-elle imputable ?

STATUT: COMPLET
VERSION: 2026-09-02 - v1.0
LOT: L1.5b

CE QU'IL Y A A EXPLIQUER
-------------------------
Trois tirages du meme montage, memes conditions, compteur corrige :

    COEUR    : 20/20 · 20/20 · 20/20   etendue 0
    PIPELINE : 18/20 · 19/20 · 16/20   etendue 3

Le coeur est deterministe, le pipeline non. Et son defaut n'est pas de manquer un
refus — il ne le manque JAMAIS sur les trois essais — mais de refuser deux fois puis
de repondre la troisieme. Un cas echoue systematiquement (mp-122), quatre autres une
fois sur trois.

Un chiffre global ne dit pas OU nait cette variance. Trois agents s'enchainent, et
chacun peut varier : l'Analyseur reformule, l'Orchestrateur choisit les passages, le
Synthetiseur redige.

LE DIAGNOSTIC PAR ELIMINATION
------------------------------
Les essais d'un meme cas se font DANS un meme tirage : la variance s'observe donc
sans comparer les tirages entre eux, ce qui evite d'y meler la variance du corpus ou
de la recherche — identiques par construction, le retriever etant fige.

    intent varie                                  -> ANALYSEUR
    intent stable, passages varient               -> ORCHESTRATEUR
    intent et passages stables, refus varie       -> SYNTHETISEUR

Le troisieme cas est le plus interessant : il signifierait que le Synthetiseur, avec
exactement la meme matiere et la meme consigne, refuse une fois et repond la suivante.
C'est alors sa temperature ou son prompt qu'il faut reprendre — pas la recherche.

    python _chantier/scripts/variance_agents.py traces-pipeline-t1-20260902.json [...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parents[2]
_ARGS = sys.argv[1:]
sys.path.insert(0, str(RACINE))

from colaig.rag.garde_fou_reponse import est_un_refus  # noqa: E402

MESURES = RACINE / "_chantier" / "mesures"

# CAS DORES DONT LA FAUSSETE EST PROUVEE — voir docs/verification-cas-negatifs-20260902.md.
# Sur mp-130 et mp-135, le corpus REPOND (« penalite journaliere de 1/3 000 », « ne peut
# etre superieur a 5 % »). Y refuser est le mauvais comportement, donc y varier n'est pas
# un defaut a imputer a un agent : les garder fausserait l'attribution.
CAS_FAUX = {"mp-130", "mp-135"}


def _questions_ecartees() -> set[str]:
    ecartees = set()
    for ligne in (RACINE / "tests" / "golden" / "v1.jsonl").read_text(
            encoding="utf-8").splitlines():
        if ligne.strip():
            cas = json.loads(ligne)
            if cas["id"] in CAS_FAUX:
                ecartees.add(cas["question"])
    return ecartees


def _reponses_du_tirage(marque: str) -> dict[str, list[str]]:
    """Les reponses archivees, indexees par question — pour juger le refus."""
    for f in MESURES.glob(f"reponses-*{marque}*.json"):
        par_question: dict[str, list[str]] = {}
        for o in json.loads(f.read_text(encoding="utf-8")):
            par_question[o["question"]] = o["reponses"]
        return par_question
    return {}


def analyser(fichier: Path) -> None:
    traces = json.loads(fichier.read_text(encoding="utf-8"))
    marque = fichier.stem.replace("traces-", "").rsplit("-", 1)[0]
    reponses = _reponses_du_tirage(marque)

    ecartees = _questions_ecartees()
    par_cas: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        q = t.get("question", "")
        if q not in ecartees:
            par_cas[q].append(t)

    verdicts: Counter = Counter()
    details: list[tuple] = []
    for question, essais in par_cas.items():
        if len(essais) < 2:
            continue
        intents = {(e["intent"], e["needs_rag"], e["reformulation"]) for e in essais}
        passages = {tuple(e["passages"]) for e in essais}
        textes = reponses.get(question, [])
        refus = {est_un_refus(t) for t in textes if t}

        # DISTINGUER LE CONSTANT QUI REUSSIT DE CELUI QUI ECHOUE.
        #
        # `len(refus) < 2` veut seulement dire « tous les essais pareils ». Un cas
        # systematiquement RATE y tombe aussi, et disparaissait alors du rapport :
        # un tirage a 16/20 pouvait afficher « 0 cas inconstant », ce qui laissait
        # croire a une stabilite parfaite la ou quatre cas echouaient a chaque essai.
        if len(refus) < 2:
            verdicts["constant" if (refus and True in refus) else "echec constant"] += 1
            continue
        if len(intents) > 1:
            verdicts["ANALYSEUR"] += 1
            qui = "ANALYSEUR"
        elif len(passages) > 1:
            verdicts["ORCHESTRATEUR"] += 1
            qui = "ORCHESTRATEUR"
        else:
            verdicts["SYNTHETISEUR"] += 1
            qui = "SYNTHETISEUR"
        details.append((qui, question[:70], len(intents), len(passages)))

    print(f"\n=== {fichier.name} — {len(par_cas)} cas, {len(traces)} passages")
    total = sum(v for k, v in verdicts.items() if k != "constant")
    print(f"  refus constant (reussite) : {verdicts['constant']}")
    print(f"  ECHEC CONSTANT            : {verdicts['echec constant']}")
    print(f"  cas INCONSTANTS       : {total}")
    for agent in ("ANALYSEUR", "ORCHESTRATEUR", "SYNTHETISEUR"):
        if verdicts[agent]:
            print(f"     imputables a l'{agent} : {verdicts[agent]}")
    for qui, q, ni, np in details:
        print(f"       [{qui}] {q}  (intents distincts={ni}, jeux de passages={np})")

    # Ce que la trace revele en passant : les doublons accumules par l'Orchestrateur.
    gonfle = [t for t in traces if t["sources"] > t["passages_distincts"]]
    if gonfle:
        ex = gonfle[0]
        print(f"  passages en doublon : {len(gonfle)}/{len(traces)} appels "
              f"(ex. {ex['sources']} servis pour {ex['passages_distincts']} distincts, "
              f"{ex['etapes']} etapes)")
    ms = [(t["ms_analyse"], t["ms_orchestration"], t["ms_synthese"]) for t in traces]
    if ms:
        n = len(ms)
        print(f"  temps moyen : analyse {sum(a for a,_,_ in ms)//n} ms · "
              f"orchestration {sum(b for _,b,_ in ms)//n} ms · "
              f"synthese {sum(c for _,_,c in ms)//n} ms")


def main() -> int:
    fichiers = [Path(a) if Path(a).is_absolute() else MESURES / a for a in _ARGS]
    fichiers = [f for f in fichiers if f.exists()] or sorted(MESURES.glob("traces-*.json"))
    if not fichiers:
        print("aucun fichier de traces — lancer reference_pipeline.py d'abord")
        return 1
    for f in fichiers:
        analyser(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
