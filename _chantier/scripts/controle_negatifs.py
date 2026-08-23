"""Les cas négatifs déclarent-ils absent ce qui est présent ?

Le défaut, et pourquoi il revient
----------------------------------
`mp-032` déclarait que le taux de l'avance ne figurait pas dans le corpus. `R2191-7` le
donne. `mp-060` déclarait absent le cadre des renseignements exigibles aux candidats.
`R2143-11` le porte. Chaque mesure comptait donc comme un défaut de refus le
comportement **correct** du modèle.

Ce défaut n'est pas un accident : il **revient à chaque fois que le corpus change**. Un
cas négatif est une affirmation sur ce que le corpus ne contient pas ; il devient faux
dès qu'on lui ajoute un document. L'ajout des six CCAG et des annexes du code fait
précisément cela.

Ce que ce contrôle fait
-----------------------
Pour chaque cas `attendu_refus`, il extrait les termes significatifs de la question et
cherche s'ils se retrouvent, **ensemble**, dans un article du corpus. Un cas dont la
question trouve un article dense en ses propres termes est suspect.

Ce n'est **pas un test** : la présence des mots ne prouve pas celle de la réponse. C'est
un révélateur, à relire. Le transformer en test le rendrait bruyant et donc ignoré — le
même sort que tous les garde-fous qui crient trop.
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

VIDES = set("""dois puis peut quel quelle quels quelles quoi dans pour avec cette leur
sont aux des les une que qui par sur pas plus donc ainsi entre etre elle mais tout tous
meme autre chose mon mes marche marches public publics comment combien faut est-ce""".split())


def plat(texte: str) -> str:
    sans = unicodedata.normalize("NFD", (texte or "").lower())
    return "".join(c for c in sans if unicodedata.category(c) != "Mn")


def termes(question: str) -> set[str]:
    return {m for m in re.findall(r"[a-z]{5,}", plat(question)) if m not in VIDES}


def main() -> int:
    articles = index()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    negatifs = [c for c in cas if c.get("attendu_refus")]

    print(f"{len(negatifs)} cas négatifs · {len(articles)} articles au corpus\n")
    suspects = 0
    for c in negatifs:
        mots = termes(c["question"])
        if len(mots) < 2:
            continue
        meilleurs = []
        for num, donnees in articles.items():
            texte = plat(donnees["texte"])
            communs = {m for m in mots if m in texte}
            if len(communs) >= max(2, len(mots) * 2 // 3):
                meilleurs.append((len(communs), num, sorted(communs)))
        meilleurs.sort(reverse=True)
        if meilleurs:
            suspects += 1
            n, num, communs = meilleurs[0]
            print(f"! {c['id']}  [{c.get('motif_refus', 'absence')}]")
            print(f"    question : {c['question'][:96]}")
            print(f"    {num} porte {n}/{len(mots)} de ses termes : {', '.join(communs[:7])}")
            if len(meilleurs) > 1:
                print(f"    et {len(meilleurs) - 1} autre(s) article(s) : "
                      f"{', '.join(m[1] for m in meilleurs[1:5])}")
            print()
    print(f"{suspects}/{len(negatifs)} cas négatifs à relire contre le corpus actuel.")
    print("La présence des mots ne prouve pas celle de la réponse : ceci se relit,")
    print("ne se croit pas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
