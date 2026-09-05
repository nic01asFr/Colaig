"""Le vérificateur dit-il « étayé » de ce qui l'est ?

Pourquoi calibrer avant de mesurer
-----------------------------------
Le vérificateur de fidélité est **lui-même un modèle**. Lui faire juger les réponses
produites sans savoir ce qu'il vaut reviendrait à mesurer une chose inconnue avec un
instrument inconnu.

Il existe pourtant un jeu de couples dont on sait qu'ils sont **fidèles par
construction** : les `reponse_attendue` du jeu doré, confrontées au texte des articles
qu'elles citent. Elles ont été écrites d'après ces articles, puis relues une par une
contre eux par quatre relectures indépendantes. Si le vérificateur y répond autre chose
qu'« étayé », l'écart lui est imputable, pas à la réponse.

Ce que ce banc mesure donc : le **taux de faux négatifs** du vérificateur. C'est le
chiffre qui décide s'il peut servir — un contrôleur qui accuse à tort une réponse sur
quatre serait pire qu'absent, comme tous les garde-fous trop bavards.

Ce qu'il ne mesure pas
----------------------
Le taux de faux positifs — dire « étayé » d'une réponse qui déborde. Il faudrait pour
cela des couples dont on sait qu'ils sont infidèles, et le jeu doré n'en contient pas.
On peut en fabriquer, et c'est la suite naturelle de ce banc.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

_ARGS = list(sys.argv[1:])

from index_corpus import index  # noqa: E402

from colaig.rag.verificateur_fidelite import verifier_fidelite  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"

_mesure = RACINE / "_chantier" / "scripts" / "mesure_fidelite.py"
_ns: dict = {"__name__": "_m", "__file__": str(_mesure)}
exec(  # noqa: S102 — on reprend le client, pas une copie
    compile(_mesure.read_text(encoding="utf-8").split("def cle_ssp")[0]
            .replace("raise SystemExit(asyncio.run(main()))", "pass"), "mesure.py", "exec"),
    _ns, _ns,
)
ClientSSP = _ns["ClientSSP"]


def cle_ssp() -> str:
    """Clé SSPCloud : l'environnement d'abord, un `.env` local ensuite.

    Huitième exemplaire de cette fonction dans le chantier. Toutes lisaient
    **uniquement** un fichier local, ce qui rendait les harnais inexécutables en
    intégration continue — la porte de régression aurait été inerte sans que rien ne le
    signale.
    """
    depuis_env = os.environ.get("SSPCLOUD_API_KEY")
    if depuis_env:
        return depuis_env.strip()
    for fichier in (RACINE / ".env", RACINE.parent / "colaig-v3" / ".env"):
        try:
            for ligne in open(fichier, encoding="utf-8"):
                if ligne.strip().lower().startswith("sspcloud_api_key="):
                    valeur = ligne.split("=", 1)[1].strip()
                    if valeur:
                        return valeur
        except OSError:
            continue
    raise SystemExit(
        "SSPCLOUD_API_KEY introuvable : ni dans l'environnement, ni dans un .env local. "
        "En intégration continue, l'ajouter aux secrets du dépôt."
    )


async def main() -> int:
    limite = int(_ARGS[0]) if _ARGS and _ARGS[0].isdigit() else 30
    articles = index()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]

    # Couples fidèles par construction : réponse attendue × texte de l'article attendu.
    # L'extrait est la REUNION de tous les articles cites par le cas, pas le premier.
    #
    # Une premiere version appariait la reponse attendue avec le seul premier article
    # attendu, et relevait 30 % de « faux negatifs ». La lecture des motifs les a tous
    # innocentes : « l'extrait mentionne uniquement le delai de base sans evoquer les
    # exceptions de reduction » — le verificateur avait raison, et le banc avait tort.
    #
    # L'ironie est exacte : les corrections apportees au jeu dore le 23/08/2026
    # consistaient precisement a AJOUTER les bornes portees par les articles voisins.
    # Ce sont elles qui rendaient l'appariement a un seul article intenable.
    couples = []
    for c in cas:
        if c.get("attendu_refus"):
            continue
        cites = [n for n in (c.get("articles_attendus") or []) + (c.get("articles_utiles") or [])
                 if n in articles]
        if not cites:
            continue
        morceaux = [f"Article {n}. " + articles[n]["texte"] for n in cites]
        extrait = (chr(10) * 2).join(morceaux)
        couples.append((c["id"], "+".join(cites), c["reponse_attendue"], extrait))
    couples = couples[:limite]

    client = ClientSSP(cle_ssp())
    compte: dict[str, int] = {}
    fabrique = 0
    faux_negatifs = []
    for identifiant, num, reponse, texte in couples:
        f = await verifier_fidelite(reponse, texte, client)
        compte[f.verdict] = compte.get(f.verdict, 0) + 1
        if f.verdict in ("etaye", "etaye_partiellement") and not f.appui_dans_extrait:
            fabrique += 1
        if f.verdict in ("ne_dit_pas_cela", "contredit", "illisible"):
            faux_negatifs.append((identifiant, num, f.verdict, f.motif[:110]))
        print(f"  {identifiant:8} {num:10} {f.verdict:20} appui={'oui' if f.appui_dans_extrait else 'non'}")

    n = len(couples)
    print(f"\n{n} couples FIDÈLES PAR CONSTRUCTION")
    for k, v in sorted(compte.items(), key=lambda x: -x[1]):
        print(f"  {k:22} {v:3}  ({100 * v / n:.0f} %)")
    print(f"  {'appui fabriqué':22} {fabrique:3}  ({100 * fabrique / n:.0f} %)")
    print(f"\nFAUX NÉGATIFS : {len(faux_negatifs)}/{n} — {100 * len(faux_negatifs) / n:.0f} %")
    for fn in faux_negatifs:
        print(f"  {fn[0]} {fn[1]} → {fn[2]} : {fn[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
