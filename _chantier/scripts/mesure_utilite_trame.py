"""
La trame sert-elle a quelque chose ? Mesure.

LOT: L4.0 (prealable a la phase 4)
DATE: 2026-08-29

POURQUOI
----------
La trame vivante — ancres, documents connus, vocabulaire, phase — est un mecanisme de
compaction de conversation qui tourne EN PRODUCTION, et dont on ignore ce qu'il
rapporte. La reference L1.5 est purement retrieval et n'exerce aucun prompt d'agent ;
rien ne l'a jamais mesuree.

Et l'on sait desormais ce qu'elle COUTE : `mesure_ancre_empoisonnee.py` a montre qu'une
ancre empoisonnee fait basculer `needs_tools` de 0/8 a 8/8 (D52). C'est le seul chemin
par lequel un contenu documentaire atteint le catalogue d'outils.

Un mecanisme dont on connait le cout et pas le benefice ne peut pas etre arbitre. La
phase 4 va regler le retriever contre la reference L1.5 ; commencer a regler avec une
variable non mesuree assise dans le pipeline, c'est la trajectoire qui a produit seize
versions du projet.

CE QUI EST MESURE
-------------------
La CONTINUITE, qui est la raison d'etre annoncee de la trame.

Une question elliptique — « et pour les travaux ? » — ne se reformule correctement que
si le sujet du tour precedent est disponible. Sans trame, l'Analyseur ne peut pas
savoir de quoi l'on parle ; avec, il le peut.

On lit `query_reformulated` et l'on cherche MECANIQUEMENT le sujet. Pas de juge LLM :
`mesure_adversariale.py` pose deja la regle — on ne fait pas garder la porte par
quelqu'un qui lit les consignes de l'attaquant, ni evaluer une reformulation par un
modele qui pourrait la produire.

QUATRE BRAS, ALTERNES
-----------------------
    explicite    question NON elliptique — le TEMOIN
    sans_trame   aucune ancre
    ref_seule    ancres reduites a `anchor_type` + `ref`, sans texte libre — l'ISSUE 2
                 de D52
    avec_trame   les ancres completes du tour precedent

Le temoin est indispensable, et son absence a deja fausse une mesure ce mois-ci. Sans
lui, un taux bas partout se lirait « la trame n'aide pas » aussi bien que « le harnais
ne sait pas detecter une bonne reformulation ». Il etablit que la detection fonctionne
quand l'information EST la.

CE QUE LA CAMPAGNE DU 29/08 A DONNE — 8 tirages par bras
----------------------------------------------------------
    explicite    8/8   100 %
    sans_trame   0/8     0 %
    ref_seule    0/8     0 %
    avec_trame   8/8   100 %

La trame a un benefice TOTAL sur la continuite, sans variance. Et `ref_seule` se
comporte exactement comme l'absence de trame : C'EST LE TEXTE LIBRE QUI PORTE LE SENS.

Consequence pour D52, dont les trois issues etaient proposees sans mesure :
  1. couper le canal des ancres        -> couterait 100 % du benefice. REFUTEE.
  2. contraindre les ancres a type+ref -> couterait 100 % du benefice. REFUTEE.
  3. ne plus faire dependre le catalogue d'un verdict LLM seul -> la seule qui tienne.

CE QUE CE HARNAIS NE DIT PAS
------------------------------
Il mesure UNE forme d'ellipse sur UN sujet. Un ecart mesure ici ne dit rien de la
valeur de la trame sur les documents connus, le vocabulaire metier ou la phase de
conversation. Elargir demanderait un jeu de scenarios — c'est le lot suivant si celui-ci
montre quelque chose.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

from mesure_ancre_empoisonnee import LLMDistant, cle  # noqa: E402

from colaig.agents.analyser import Analyser  # noqa: E402
from colaig.models import (  # noqa: E402
    ContextAnchor,
    ContextMode,
    IncomingMessage,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.fakes import FakeStorage  # noqa: E402

TIRAGES = int(os.environ.get("COLAIG_TRAME_TIRAGES", "8"))

# Le tour precedent, tel que le Synthetiseur l'aurait resume en ancres.
ANCRES = [
    ContextAnchor(anchor_type="fait", ref="seuil-fournitures",
                  description="Le seuil de dispense de publicite et de mise en "
                              "concurrence est de 40 000 euros HT pour les marches "
                              "de fournitures et de services."),
    ContextAnchor(anchor_type="sujet", ref="objet",
                  description="La conversation porte sur les seuils de dispense de "
                              "procedure dans la commande publique."),
]

# La question ELLIPTIQUE. Seule, elle ne dit pas de quoi l'on parle.
ELLIPTIQUE = "et pour les travaux ?"

# La meme, EXPLICITE — le temoin. L'information est dans la question elle-meme.
EXPLICITE = ("et quel est le seuil de dispense de publicite pour les marches de "
             "travaux ?")

# Ce qu'une reformulation reussie doit porter : le CONCEPT du tour precedent, pas un
# mot de vocabulaire juridique quelconque.
#
# LA PREMIERE VERSION ETAIT TROP LACHE et il faut le dire. Elle acceptait « procedure »
# et « montant » — des mots qui apparaissent dans une reformulation N'AYANT RIEN
# RESOLU. Mesure : « Quelles sont les procedures et obligations legales relatives aux
# travaux » comptait pour un succes SANS trame, ce qui gonflait le bras temoin de
# l'ellipse et minorait l'apport de la trame.
#
# Sur les reformulations observees, le mot qui DISCRIMINE est « seuil » — present dans
# tous les cas resolus, absent de tous les cas generiques. « dispense » et « en deca »
# le sont aussi et disent la meme chose.
MARQUEURS = ("seuil", "dispense", "deçà", "deca", "40 000", "40000")


# LES MEMES ANCRES, REDUITES A LEUR REFERENCE. C'est l'issue 2 de D52 : contraindre la
# forme des ancres a `anchor_type` + `ref`, sans texte libre. `_build_workspace_info`
# retombe sur `ref` quand `description` est vide.
#
# Un identifiant court porte moins facilement un ordre administratif qu'une phrase. La
# question est de savoir s'il porte encore assez de sens pour resoudre l'ellipse.
ANCRES_REDUITES = [
    ContextAnchor(anchor_type=a.anchor_type, ref=a.ref, description="")
    for a in ANCRES
]


def contexte(bras: str) -> WorkspaceContext:
    espace = WorkspaceConfig(workspace_id="marches", name="Marches publics",
                             storage_path="/espace-marches/",
                             description="Commande publique", language="fr")
    ancres: list = []
    if bras == "avec_trame":
        ancres = list(ANCRES)
    elif bras == "ref_seule":
        ancres = list(ANCRES_REDUITES)
    return WorkspaceContext(workspace=espace, mode=ContextMode.ASSISTANT,
                            context_anchors=ancres)


async def un_tirage(cle_api: str, bras: str) -> tuple[bool, str, bool]:
    """Rend (ellipse resolue, reformulation, tirage exploitable)."""
    llm = LLMDistant(cle_api)
    analyseur = Analyser(albert=llm, storage=FakeStorage())

    question = EXPLICITE if bras == "explicite" else ELLIPTIQUE
    intent = await analyseur.analyse(
        IncomingMessage(user_id="@a:tchap.gouv.fr",
                        conversation_id="!salon:tchap.gouv.fr", body=question),
        contexte(bras))

    # Un repli de l'Analyseur rend une Intent vide : la compter comme un echec ferait
    # lire « la trame n'aide pas » la ou l'on n'a rien mesure. Meme garde que dans
    # `mesure_ancre_empoisonnee.py`, ou ce piege a produit un 0 % rassurant et faux.
    exploitable = "query_reformulated" in (llm.derniere_reponse or "")

    reformulation = (intent.query_reformulated or "").lower()
    resolue = any(m in reformulation for m in MARQUEURS)
    return resolue, intent.query_reformulated or "", exploitable


BRAS = ("explicite", "sans_trame", "ref_seule", "avec_trame")


async def campagne(cle_api: str) -> dict:
    resultats: dict[str, list[bool]] = {b: [] for b in BRAS}
    exemples: dict[str, list[str]] = {b: [] for b in BRAS}
    rejetes: dict[str, int] = dict.fromkeys(BRAS, 0)

    for i in range(TIRAGES):
        ordre = BRAS if i % 2 == 0 else tuple(reversed(BRAS))
        for bras in ordre:
            try:
                resolue, texte, exploitable = await un_tirage(cle_api, bras)
            except Exception as erreur:              # noqa: BLE001
                print(f"  {i + 1} {bras} : echec ({erreur})", file=sys.stderr)
                rejetes[bras] += 1
                continue
            if not exploitable:
                print(f"  {i + 1:2d} {bras:11s} REJETE (repli)")
                rejetes[bras] += 1
                continue
            resultats[bras].append(resolue)
            if len(exemples[bras]) < 2:
                exemples[bras].append(texte)
            print(f"  {i + 1:2d} {bras:11s} {'OUI' if resolue else 'non':3s}  {texte[:60]}")

    return {"resultats": resultats, "exemples": exemples, "rejetes": rejetes}


def rapport(donnees: dict) -> int:
    res = donnees["resultats"]
    lignes = ["", "=" * 72,
              "LA TRAME RESOUT-ELLE UNE QUESTION ELLIPTIQUE ?",
              "=" * 72, ""]

    taux = {}
    for bras in BRAS:
        tirages = res[bras]
        if not tirages:
            lignes.append(f"{bras:12s} : aucun tirage abouti")
            continue
        t = sum(tirages) / len(tirages)
        taux[bras] = t
        sigma = (t * (1 - t) / len(tirages)) ** 0.5
        lignes.append(f"{bras:12s} : sujet retrouve {sum(tirages)}/{len(tirages)} "
                      f"— {t:.1%} (sigma {sigma:.1%})")

    lignes.append("")
    if "explicite" in taux and taux["explicite"] < 0.75:
        lignes.append("LE HARNAIS NE MESURE RIEN. Le temoin — dont la question porte")
        lignes.append("elle-meme le sujet — echoue trop souvent : c'est la DETECTION")
        lignes.append("qui est en cause, pas la trame. Tout ce qui suit est sans objet.")
    elif "sans_trame" in taux and "avec_trame" in taux:
        ecart = taux["avec_trame"] - taux["sans_trame"]
        lignes.append(f"Apport de la trame : {ecart:+.1%}")
        lignes.append("")
        if ecart >= 0.25:
            lignes.append("VERDICT : la trame RESOUT l'ellipse. Elle porte une valeur")
            lignes.append("mesurable, a mettre en regard du canal d'injection qu'elle")
            lignes.append("ouvre (D52 : 0/8 -> 8/8).")
        elif abs(ecart) < 0.25:
            lignes.append(f"VERDICT : sur {TIRAGES} tirages, aucun apport separable du")
            lignes.append("bruit. La trame coute un canal d'injection mesure et ne")
            lignes.append("montre pas de benefice sur cette forme d'ellipse.")
        else:
            lignes.append("VERDICT : la trame DEGRADE la reformulation — a expliquer")
            lignes.append("avant toute conclusion.")

    if "ref_seule" in taux and "avec_trame" in taux and "sans_trame" in taux:
        lignes.append("")
        lignes.append("ISSUE 2 DE D52 — ancres reduites a leur reference, sans texte libre :")
        if taux["ref_seule"] >= taux["avec_trame"] - 0.25:
            lignes.append("  La continuite TIENT sans texte libre. L'issue est viable :")
            lignes.append("  on garde le benefice en reduisant la surface d'injection.")
        elif taux["ref_seule"] > taux["sans_trame"] + 0.25:
            lignes.append("  La continuite tient PARTIELLEMENT. Compromis a arbitrer.")
        else:
            lignes.append("  La continuite NE TIENT PAS. C'est le texte libre qui porte")
            lignes.append("  le sens : l'issue 2 couterait le benefice de la trame.")

    lignes.append("")
    lignes.append("Reformulations observees :")
    for bras in BRAS:
        for texte in donnees["exemples"][bras]:
            lignes.append(f"  {bras:12s} {texte[:80]}")

    for bras in BRAS:
        if donnees["rejetes"][bras]:
            lignes.append(f"  {bras:12s} {donnees['rejetes'][bras]} rejete(s) — repli, "
                          "aucun verdict")

    lignes.append("")
    print("\n".join(lignes))

    sortie = RACINE / "_chantier" / "mesures" / "utilite-trame.txt"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text("\n".join(lignes), encoding="utf-8")
    print(f"Ecrit : {sortie}")
    return 0


def main() -> int:
    import asyncio

    print(f"Utilite de la trame — {TIRAGES} tirages par bras, bras alternes.")
    return rapport(asyncio.run(campagne(cle())))


if __name__ == "__main__":
    raise SystemExit(main())
