"""
Colaig — la notice de soi repare-t-elle les reponses sur Colaig ?

CE QUI EST MESURE
-------------------
La campagne d'usage du 29/08/2026 a releve, en conversation directe, trois reponses
fausses aux trois questions que tout nouvel utilisateur pose. Colaig y niait posseder
des commandes qu'il possede, et inventait une procedure de configuration (Notion,
Confluence) faute de savoir ce qu'il offre.

`colaig/capacites.py` verse desormais ses capacites dans le prompt systeme. Reste a
savoir si cela CHANGE LA REPONSE, ou si c'est une correction qui se contente d'avoir
l'air juste. Ce depot a produit assez de « ca a l'air mieux » pour ne pas en ajouter un.

DEUX BRAS, ALTERNES
---------------------
    sans_notice   le prompt d'avant la correction — `notice_de_soi` rendue muette
    avec_notice   le prompt actuel

Le prompt vient du VRAI `_build_system_prompt`, pas d'une copie : un harnais qui
reecrit le prompt qu'il mesure ne mesure que lui-meme. Le bras temoin est obtenu en
faisant taire la notice, ce qui rejoue exactement l'etat anterieur plutot que de le
decrire de memoire.

TROIS INDICATEURS MECANIQUES, AUCUN JUGE LLM
----------------------------------------------
    nomme        la reponse cite au moins une commande REELLE (`!aide`, `!space`, ...)
    invente      la reponse cite un produit que Colaig n'integre pas (Notion, Jira...)
    egare        la reponse annonce `colaig lier`, INOPERANTE en conversation directe

Le troisieme est la garde contre la sur-correction, et il compte autant que les deux
autres : une notice qui ferait annoncer `colaig lier` en DM remplacerait un mensonge
par un autre. C'est precisement le defaut que la campagne a trouve dans `!aide`.

Pas de juge LLM — `mesure_adversariale.py` pose la regle, et la citation d'un nom de
produit se lit sans modele.

CE QUE CETTE MESURE NE COUVRE PAS
-----------------------------------
Le prompt systeme, pas le Synthetiseur entier. En conversation directe sans document,
`sources` est vide et le Synthetiseur n'ajoute rien de determinant — mais le tour
complet passe par des etages que ce harnais ne rejoue pas.

    python _chantier/scripts/mesure_notice_de_soi.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

RACINE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

from mesure_ancre_empoisonnee import LLMDistant, cle  # noqa: E402

from colaig import capacites  # noqa: E402
from colaig.context.layers import _build_system_prompt  # noqa: E402
from colaig.models import ContextMode  # noqa: E402

TIRAGES = int(os.environ.get("COLAIG_NOTICE_TIRAGES", "8"))

# Les trois questions POSEES SUR LE FIL le 29/08/2026, mot pour mot. Les reecrire plus
# clairement mesurerait une question que personne n'a posee.
QUESTIONS = (
    "Comment te configurer et te donner acces a un espace documentaire ?",
    "Quelle est la commande exacte, dans Tchap, pour lier ce salon a un espace "
    "documentaire ?",
    "Comment je t'invite dans un espace documentaire ?",
)

# Ce qui atteste que Colaig parle de LUI : au moins une de ses commandes reelles.
VRAIES_COMMANDES = tuple(nom.lower() for nom, _ in capacites.COMMANDES)

# Ce que Colaig n'integre pas. Cites sur le fil : Notion et Confluence. Les autres sont
# du meme registre — des produits qu'un modele nomme quand il comble un silence.
PRODUITS_ABSENTS = ("notion", "confluence", "jira", "sharepoint", "google drive",
                    "dropbox", "slack", "microsoft teams")

# INOPERANT en conversation directe : `_handle_onboarding_command` est sous la porte
# `mode == CHATBOT`. C'est l'erreur que `!aide` commettait.
EGAREMENT = ("colaig lier", "colaig creer", "colaig créer")


def prompt_du_bras(bras: str) -> str:
    """Le vrai constructeur, avec ou sans la notice."""
    if bras == "avec_notice":
        return _build_system_prompt(None, ContextMode.PERSONAL)

    origine = capacites.notice_de_soi
    try:
        capacites.notice_de_soi = lambda mode: ""
        return _build_system_prompt(None, ContextMode.PERSONAL).strip()
    finally:
        capacites.notice_de_soi = origine


async def un_tirage(cle_api: str, bras: str, question: str) -> tuple[dict, str, bool]:
    llm = LLMDistant(cle_api)
    reponse = await llm.chat(
        [{"role": "system", "content": prompt_du_bras(bras)},
         {"role": "user", "content": question}],
        temperature=0.3, max_tokens=1024)

    # Une reponse vide n'est pas une reponse fausse : la compter ferait lire « la
    # notice n'aide pas » la ou l'on n'a rien mesure. Meme garde que les autres harnais
    # de ce dossier, ou ce piege a produit un 0 % rassurant et faux.
    exploitable = len((reponse or "").strip()) > 40
    bas = (reponse or "").lower()

    return ({
        "nomme": any(c in bas for c in VRAIES_COMMANDES),
        "invente": any(p in bas for p in PRODUITS_ABSENTS),
        "egare": any(e in bas for e in EGAREMENT),
    }, reponse or "", exploitable)


BRAS = ("sans_notice", "avec_notice")
INDICATEURS = ("nomme", "invente", "egare")


async def campagne(cle_api: str) -> dict:
    compte = {b: {i: 0 for i in INDICATEURS} for b in BRAS}
    total = dict.fromkeys(BRAS, 0)
    rejetes = dict.fromkeys(BRAS, 0)
    exemples: dict[str, list[str]] = {b: [] for b in BRAS}

    for i in range(TIRAGES):
        question = QUESTIONS[i % len(QUESTIONS)]
        ordre = BRAS if i % 2 == 0 else tuple(reversed(BRAS))
        for bras in ordre:
            try:
                marques, texte, exploitable = await un_tirage(cle_api, bras, question)
            except Exception as erreur:              # noqa: BLE001
                print(f"  {i + 1:2d} {bras:12s} echec ({erreur})", file=sys.stderr)
                rejetes[bras] += 1
                continue
            if not exploitable:
                print(f"  {i + 1:2d} {bras:12s} REJETE (reponse vide)")
                rejetes[bras] += 1
                continue

            total[bras] += 1
            for ind in INDICATEURS:
                compte[bras][ind] += int(marques[ind])
            if len(exemples[bras]) < 2:
                exemples[bras].append(texte)

            marquage = " ".join(f"{ind}={'O' if marques[ind] else '.'}"
                                for ind in INDICATEURS)
            print(f"  {i + 1:2d} {bras:12s} {marquage}")

    return {"compte": compte, "total": total, "rejetes": rejetes,
            "exemples": exemples}


def rapport(resultat: dict) -> None:
    compte, total = resultat["compte"], resultat["total"]

    print()
    print("bras          tirages  nomme une   invente un   annonce une")
    print("                       commande    produit      commande inoperante")
    for bras in BRAS:
        n = total[bras]
        if not n:
            print(f"{bras:14s} {n:5d}   — aucun tirage exploitable")
            continue
        print(f"{bras:14s} {n:5d}    "
              f"{compte[bras]['nomme']}/{n} ({compte[bras]['nomme'] / n:5.0%})  "
              f"{compte[bras]['invente']}/{n} ({compte[bras]['invente'] / n:5.0%})  "
              f"{compte[bras]['egare']}/{n} ({compte[bras]['egare'] / n:5.0%})")

    if resultat["rejetes"]:
        print(f"\nrejetes : {resultat['rejetes']}")

    for bras in BRAS:
        for exemple in resultat["exemples"][bras][:1]:
            extrait = " ".join(exemple.split())[:400]
            print(f"\n--- {bras} ---\n{extrait}")


if __name__ == "__main__":
    resultat = asyncio.run(campagne(cle()))
    rapport(resultat)
