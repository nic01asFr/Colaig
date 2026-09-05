"""
Combien de fois la porte `needs_rag` fermait-elle le corpus à tort ?

STATUT: COMPLET
VERSION: 2026-08-30 - v1.0
LOT: L1.5 / D68

CE QUE CETTE MESURE ÉTABLIT, ET POURQUOI ELLE EXISTE
------------------------------------------------------
La référence L1.5 appelle `generator.py` **directement** : elle ne passe ni par
l'Analyseur ni par l'Orchestrateur, et retrouve donc toujours ses passages. Elle est
**structurellement aveugle** à la porte `needs_rag`, qui vit dans le pipeline agent.

Retirer cette porte (D68) ne peut donc pas se juger sur la référence. Ce harnais mesure
autre chose, et de façon décisive : **à quelle fréquence l'Analyseur fermait le corpus
sur des questions qui en avaient besoin.**

LE JEU DORÉ REND LA VÉRITÉ MÉCANIQUE
--------------------------------------
Les 113 cas positifs attendent tous un article du corpus. **Ils ont donc tous besoin du
corpus, par construction.** Chaque `needs_rag=False` y est un faux négatif — sans
qu'aucun jugement soit à porter.

Les 22 cas négatifs sont l'inverse : la réponse n'est dans aucun passage. Un
`needs_rag=False` y serait défendable. On les compte à part, jamais ensemble.

    python _chantier/scripts/mesure_porte_needs_rag.py [tirages]
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import statistics
import sys

RACINE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

from mesure_ancre_empoisonnee import LLMDistant, cle  # noqa: E402

from colaig.agents.analyser import Analyser  # noqa: E402
from colaig.models import (  # noqa: E402
    ContextMode,
    IncomingMessage,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.fakes import FakeStorage  # noqa: E402

TIRAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 1
JEU = RACINE / "tests" / "golden" / "v1.jsonl"


def contexte() -> WorkspaceContext:
    """Le même espace que la référence, description comprise.

    La description est versée dans le prompt de l'Analyseur : la donner fausse ferait
    mesurer autre chose que le produit — ce qui est arrivé à la référence, dont la
    configuration annonçait 1762 articles là où le corpus en a 1021 (corrigé le 30/08).
    """
    espace = WorkspaceConfig(
        workspace_id="reference-marches-publics",
        name="Rédaction de marchés publics",
        storage_path="/colaig-reference-marches-publics/",
        description=("Assistance à la rédaction de marchés publics. Corpus : Code de la "
                     "commande publique, version consolidée, articles en vigueur "
                     "uniquement — 1021 articles répartis en 108 documents."),
        language="fr",
    )
    return WorkspaceContext(workspace=espace, mode=ContextMode.ASSISTANT)


async def un_tirage(cle_api: str, cas: list[dict]) -> dict:
    positifs_fermes, negatifs_fermes = 0, 0
    positifs, negatifs = 0, 0
    rejetes = 0
    exemples: list[str] = []

    for c in cas:
        llm = LLMDistant(cle_api)
        analyseur = Analyser(albert=llm, storage=FakeStorage())
        try:
            intent = await analyseur.analyse(
                IncomingMessage(user_id="@a:tchap.gouv.fr",
                                conversation_id="!salon:tchap.gouv.fr",
                                body=c["question"]),
                contexte())
        except Exception as erreur:                                # noqa: BLE001
            print(f"  {c['id']} echec ({erreur})", file=sys.stderr)
            rejetes += 1
            continue

        # Un repli de l'Analyseur rend une Intent par defaut : la compter fausserait la
        # mesure dans un sens qu'on ne controle pas. Meme garde que les autres harnais.
        if "needs_rag" not in (llm.derniere_reponse or ""):
            rejetes += 1
            continue

        if c.get("attendu_refus"):
            negatifs += 1
            negatifs_fermes += int(not intent.needs_rag)
        else:
            positifs += 1
            if not intent.needs_rag:
                positifs_fermes += 1
                if len(exemples) < 5:
                    exemples.append(f"{c['id']} — {c['question'][:80]}")

    return {"positifs": positifs, "positifs_fermes": positifs_fermes,
            "negatifs": negatifs, "negatifs_fermes": negatifs_fermes,
            "rejetes": rejetes, "exemples": exemples}


async def campagne(cle_api: str) -> None:
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines()
           if l.strip()]
    print(f"{len(cas)} cas · {TIRAGES} tirage(s)")

    tours = []
    for i in range(TIRAGES):
        r = await un_tirage(cle_api, cas)
        tours.append(r)
        print(f"  tirage {i + 1} : {r['positifs_fermes']}/{r['positifs']} positifs "
              f"fermés · {r['negatifs_fermes']}/{r['negatifs']} négatifs fermés "
              f"· {r['rejetes']} rejeté(s)")

    pf = statistics.mean(t["positifs_fermes"] for t in tours)
    p = tours[0]["positifs"]
    nf = statistics.mean(t["negatifs_fermes"] for t in tours)
    n = tours[0]["negatifs"]

    print()
    print("CAS POSITIFS — tous ont besoin du corpus, par construction")
    print(f"  corpus fermé à tort : {pf:.1f}/{p}  ({pf / p:.1%})" if p else "  —")
    print()
    print("CAS NÉGATIFS — la réponse n'est dans aucun passage")
    print(f"  corpus fermé : {nf:.1f}/{n}  ({nf / n:.1%})" if n else "  —")

    if tours[0]["exemples"]:
        print("\nQuestions dont l'Analyseur a jugé le corpus inutile :")
        for e in tours[0]["exemples"]:
            print("   ", e)


if __name__ == "__main__":
    asyncio.run(campagne(cle()))
