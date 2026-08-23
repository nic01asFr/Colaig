"""Que vaut un « étayé » du vérificateur ?

Ce que la calibration précédente ne pouvait pas dire
-----------------------------------------------------
`calibrer_verificateur.py` mesure les **faux négatifs** : sur 30 couples fidèles par
construction, le vérificateur n'a produit aucun verdict négatif. C'est la moitié de ce
qu'il faut savoir.

L'autre moitié — dire « étayé » d'une réponse qui déborde — demandait des couples dont
on sait qu'ils sont **infidèles**. Le jeu doré n'en contient pas : il est écrit pour être
juste. On les fabrique donc, en partant de réponses justes et en y introduisant des
dérives **contrôlées**.

Les cinq dérives, et pourquoi celles-là
----------------------------------------
Chacune reproduit une faute réellement observée dans les mesures de ce chantier :

| dérive | ce qu'elle imite | gravité en pratique |
|---|---|---|
| `portee` | « peut » devient « doit » | une faculté présentée comme obligatoire |
| `negation` | « ne peut » devient « peut » | l'interdit devient permis |
| `seuil` | un montant est déplacé | procédure irrégulière, invisible à la lecture |
| `suppression` | la borne après « à condition », « sauf » disparaît | **le défaut mesuré sur 31 cas dorés** (D20) |
| `ajout` | une affirmation plausible non étayée est ajoutée | l'inférence qui déborde |

La quatrième est la plus importante : c'est exactement le défaut que quatre relectures
ont trouvé dans un quart du jeu doré, et celui qu'aucun contrôle mécanique n'attrape.

Ce que ce banc ne prouve pas
-----------------------------
Une dérive fabriquée n'est pas une dérive naturelle. Un modèle qui déborde le fait de
façon plus subtile qu'un `replace()`. Le taux mesuré ici est donc un **majorant de la
détection** — s'il ne voit pas une dérive grossière, il ne verra pas les fines ; s'il la
voit, cela ne prouve pas qu'il verra les autres.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

_ARGS = list(sys.argv[1:])

from index_corpus import index  # noqa: E402

from colaig.rag.verificateur_fidelite import verifier_fidelite  # noqa: E402

_mesure = RACINE / "_chantier" / "scripts" / "mesure_fidelite.py"
_ns: dict = {"__name__": "_m", "__file__": str(_mesure)}
exec(  # noqa: S102
    compile(_mesure.read_text(encoding="utf-8").split("def cle_ssp")[0]
            .replace("raise SystemExit(asyncio.run(main()))", "pass"), "mesure.py", "exec"),
    _ns, _ns,
)
ClientSSP = _ns["ClientSSP"]

JEU = RACINE / "tests" / "golden" / "v1.jsonl"


def cle_ssp() -> str:
    for ligne in open(RACINE / ".env", encoding="utf-8"):
        if ligne.strip().lower().startswith("sspcloud_api_key="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("clé SSPCloud introuvable")


def portee(texte: str) -> str | None:
    """Une faculté devient une obligation."""
    for avant, apres in (("peut ", "doit "), ("peuvent ", "doivent "),
                         ("peut être", "doit être"), ("Le corpus", None)):
        if apres and avant in texte:
            return texte.replace(avant, apres, 1)
    return None


def negation(texte: str) -> str | None:
    """L'interdit devient permis."""
    for avant, apres in (("ne peut pas ", "peut "), ("ne peuvent pas ", "peuvent "),
                         ("ne peut ", "peut "), ("ne peuvent ", "peuvent "),
                         ("Non. ", "Oui. "), ("Non, ", "Oui, ")):
        if avant in texte:
            return texte.replace(avant, apres, 1)
    return None


def seuil(texte: str) -> str | None:
    """Un montant est déplacé — la faute la plus invisible à la lecture."""
    m = re.search(r"\b(\d{1,3}(?: \d{3})+)\b", texte)
    if not m:
        return None
    chiffres = m.group(1).replace(" ", "")
    fausse = f"{int(chiffres) + 20000:,}".replace(",", " ")
    return texte[:m.start()] + fausse + texte[m.end():]


def suppression(texte: str) -> str | None:
    """La borne disparaît — le défaut mesuré sur un quart du jeu doré."""
    for marque in (" à condition ", " sauf ", " sous réserve ", " dès lors que ",
                   " lorsqu", " à moins ", " Toutefois", " toutefois"):
        i = texte.find(marque)
        if i > 60:
            return texte[:i].rstrip(" ,;") + "."
    return None


def ajout(texte: str) -> str | None:
    """Une affirmation plausible, non étayée, greffée sur une réponse juste."""
    return texte.rstrip() + " Ce délai est par ailleurs suspendu pendant la période de congés."


DERIVES = {"portee": portee, "negation": negation, "seuil": seuil,
           "suppression": suppression, "ajout": ajout}


async def main() -> int:
    limite = int(_ARGS[0]) if _ARGS and _ARGS[0].isdigit() else 12
    articles = index()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]

    couples = []
    for c in cas:
        if c.get("attendu_refus"):
            continue
        cites = [n for n in (c.get("articles_attendus") or []) + (c.get("articles_utiles") or [])
                 if n in articles]
        if not cites:
            continue
        morceaux = [f"Article {n}. " + articles[n]["texte"] for n in cites]
        couples.append((c["id"], c["reponse_attendue"], (chr(10) * 2).join(morceaux)))
    couples = couples[:limite]

    client = ClientSSP(cle_ssp())
    resultats: dict[str, list] = {d: [] for d in DERIVES}
    for identifiant, reponse, extrait in couples:
        for nom, deriver in DERIVES.items():
            derivee = deriver(reponse)
            if derivee is None or derivee == reponse:
                continue
            f = await verifier_fidelite(derivee, extrait, client)
            vu = f.verdict != "etaye"
            resultats[nom].append(vu)
            print(f"  {identifiant:8} {nom:12} {f.verdict:20} {'vu' if vu else 'PASSE'}")

    print("\n=== détection par type de dérive ===")
    total_vus = total = 0
    for nom, vus in resultats.items():
        if not vus:
            print(f"  {nom:14} — aucune dérive applicable")
            continue
        total_vus += sum(vus)
        total += len(vus)
        print(f"  {nom:14} {sum(vus):2}/{len(vus):2}  ({100 * sum(vus) / len(vus):3.0f} %)")
    if total:
        print(f"\n  {'ENSEMBLE':14} {total_vus:2}/{total:2}  ({100 * total_vus / total:.0f} %)")
        print(f"  faux positifs : {total - total_vus}/{total} — "
              f"{100 * (total - total_vus) / total:.0f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
