"""
L4.1 — les 23 articles manquants sont-ils écartés, ou jamais trouvés ?

CE QUE LA MESURE PRÉCÉDENTE A ÉTABLI
--------------------------------------
Élargir le vivier de 10 à 30 candidats ne change **rien** : 90/113 aux quatre valeurs,
rang médian 1. Le bon passage, quand il est trouvé, est déjà en tête.

LA QUESTION QUI RESTE
-----------------------
Pour les 23 cas manquants, l'article attendu est-il :

- **dans le vivier**, puis écarté par le RRF, la déduplication, le MMR ou le seuil ?
  Alors le défaut est dans un de ces étages, et il est réparable.
- **absent du vivier** ? Alors l'embedding ne le trouve pas, et aucun réglage du
  retriever n'y changera quoi que ce soit — comme la confusion de régime, ce serait un
  problème de représentation, pas de sélection.

Les deux réponses conduisent à des lots différents. C'est pour cela qu'on mesure avant
de régler.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))
REFERENCE = RACINE / "_chantier" / "scripts" / "reference_l15.py"
K = int(os.environ.get("COLAIG_REF_K", "5"))


def _charger_reference() -> dict:
    espace: dict = {"__name__": "reference_l15_importee", "__file__": str(REFERENCE)}
    exec(compile(REFERENCE.read_text(encoding="utf-8"), str(REFERENCE), "exec"), espace)
    return espace


async def main() -> int:
    ref = _charger_reference()
    cas = [json.loads(l) for l in ref["JEU"].read_text(encoding="utf-8").splitlines()
           if l.strip()]
    cas = [c for c in cas if c.get("articles_attendus")]
    chunks = ref["decouper"](ref["STRATEGIE"])
    cle = ref["cle_albert"]()
    vecteurs = ref["embed"]([c.text for c in chunks], cle)
    vq = ref["embed"]([c["question"] for c in cas], cle)

    from colaig.rag.faiss_store import FaissStore
    store = FaissStore(dimension=ref["DIMENSION"])
    store.add(vecteurs, chunks)
    articles_du_chunk = ref["articles_du_chunk"]

    dans_le_vivier = 0
    jamais_trouve = 0
    manquants_dans_le_top = 0
    profondeurs: list[int] = []

    for c, v in zip(cas, vq):
        attendus = set(c["articles_attendus"])
        # Le top-k tel que le magasin le rend, avant tout etage du retriever.
        brut = store.search(v, k=K)
        if any(attendus & articles_du_chunk(r.chunk.text) for r in brut):
            continue                              # deja servi, rien a expliquer
        manquants_dans_le_top += 1

        # Jusqu'ou faut-il descendre pour le trouver ?
        profond = store.search(v, k=200)
        rang = 0
        for i, r in enumerate(profond, 1):
            if attendus & articles_du_chunk(r.chunk.text):
                rang = i
                break
        if rang:
            dans_le_vivier += 1
            profondeurs.append(rang)
        else:
            jamais_trouve += 1

    profondeurs.sort()
    print()
    print(f"L4.1 — OU SE PERD L'ARTICLE ATTENDU · k={K} · {len(cas)} cas")
    print()
    print(f"absent du top-{K} de FAISS : {manquants_dans_le_top}")
    print(f"  retrouve plus bas (≤200) : {dans_le_vivier}")
    if profondeurs:
        med = profondeurs[len(profondeurs) // 2]
        print(f"    rang median            : {med}")
        print(f"    rangs                  : {profondeurs[:12]}"
              f"{' …' if len(profondeurs) > 12 else ''}")
    print(f"  jamais trouve (>200)     : {jamais_trouve}")
    print()
    if jamais_trouve > dans_le_vivier:
        print("→ L'EMBEDDING NE TROUVE PAS. Aucun reglage du retriever n'y changera rien.")
    elif dans_le_vivier:
        print("→ L'ARTICLE EST LA, PLUS BAS. Le vivier utile est plus profond que k*6=30,")
        print("  ou un etage du retriever l'ecarte. Les rangs ci-dessus disent lequel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
