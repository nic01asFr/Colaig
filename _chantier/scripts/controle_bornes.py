"""La réponse attendue retient-elle la règle et laisse-t-elle tomber sa borne ?

Le défaut, tel que quatre relectures indépendantes l'ont décrit
---------------------------------------------------------------
Sur 122 cas relus article par article, le même défaut revient : **l'article cité porte
une condition restrictive que la réponse attendue omet.** Non pas une nuance, mais la
borne qui rend la règle applicable.

Trois exemples, chacun d'un lot relu séparément :

- `mp-026` — la réponse dit que l'acheteur peut autoriser un soumissionnaire à
  régulariser. R2152-2 dit « **tous les soumissionnaires concernés** ». Le cas validait
  la régularisation sélective, qui est précisément la pratique irrégulière.
- `mp-106` — la réponse énumère les trois raisons d'un opérateur unique. R2122-3 ajoute
  que le recours « n'est justifié que **lorsqu'il n'existe aucune solution de
  remplacement raisonnable et que l'absence de concurrence ne résulte pas d'une
  restriction artificielle** ».
- `mp-105` — la réponse énonce l'urgence impérieuse. R2122-1 ajoute que « le marché est
  **limité aux prestations strictement nécessaires** ».

Le sens de l'erreur est toujours le même, et c'est ce qui la rend grave : un instrument
qui omet les bornes **récompense la réponse incomplète et pénalise la réponse
complète**. Il pousse dans la mauvaise direction, silencieusement.

Ce que ce contrôle fait, et ce qu'il ne fait pas
-------------------------------------------------
Il repère, dans le texte des articles cités, les **marqueurs de restriction** — « ne
peut », « à condition que », « toutefois », « sous réserve », « seul », « limité à » —
et signale ceux dont aucune trace ne se retrouve dans la réponse attendue.

Ce n'est **pas** un test : la présence d'un marqueur ne prouve pas l'omission, et son
absence ne prouve pas la complétude. C'est un révélateur, qui dit où regarder. Le
transformer en test échouerait sur des cas sains et finirait ignoré.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

from index_corpus import index  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"

# Formules par lesquelles le code borne une règle. Elles ouvrent presque toujours la
# phrase qui décide de l'applicabilité — c'est là que se loge ce qu'on oublie.
BORNES = [
    "ne peut", "ne peuvent", "à condition", "toutefois", "sous réserve",
    "sauf", "seul", "seule", "seules", "seuls", "limité", "limitée", "limitées",
    "n'est justifié que", "ne sont pas soumis", "exclusivement", "au plus",
    "à l'exception", "ne peut dépasser", "ne peuvent excéder", "strictement",
]


def sans_accent(texte: str) -> str:
    plat = unicodedata.normalize("NFD", (texte or "").lower())
    return "".join(c for c in plat if unicodedata.category(c) != "Mn")


def phrases_bornees(texte: str) -> list[str]:
    """Phrases de l'article qui portent un marqueur de restriction."""
    dehors = []
    for phrase in re.split(r"(?<=[.;])\s+", " ".join((texte or "").split())):
        plat = sans_accent(phrase)
        if any(sans_accent(b) in plat for b in BORNES) and len(phrase) > 30:
            dehors.append(phrase)
    return dehors


def couverte(phrase: str, reponse: str) -> bool:
    """La borne se retrouve-t-elle, même reformulée, dans la réponse attendue ?

    Critère volontairement permissif : trois mots significatifs communs suffisent.
    Un révélateur trop strict signalerait tout et ne servirait à rien.
    """
    mots_phrase = {m for m in re.findall(r"[a-z]{5,}", sans_accent(phrase))}
    mots_reponse = {m for m in re.findall(r"[a-z]{5,}", sans_accent(reponse))}
    return len(mots_phrase & mots_reponse) >= 3


def main() -> int:
    articles = index()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]

    suspects = []
    for c in cas:
        if c.get("attendu_refus"):
            continue  # une réponse de refus n'a pas à restituer les bornes
        manquantes = []
        for num in c.get("articles_attendus", []):
            if num not in articles:
                continue
            for phrase in phrases_bornees(articles[num]["texte"]):
                if not couverte(phrase, c["reponse_attendue"]):
                    manquantes.append((num, phrase))
        if manquantes:
            suspects.append((c, manquantes))

    positifs = [c for c in cas if not c.get("attendu_refus")]
    print(f"{len(suspects)} cas sur {len(positifs)} portent une borne non reprise\n")
    for c, manquantes in suspects:
        print(f"### {c['id']}  [{c['type']}/{c['difficulte']}]")
        print(f"  question : {c['question'][:100]}")
        for num, phrase in manquantes[:2]:
            print(f"  {num} → {phrase[:190]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
