"""
L4.1 — un vivier de candidats plus large améliore-t-il le rappel ?

CE QUE LE LOT DEMANDE
-----------------------
« Retriever réglé : pool ~20 → rerank → 3-5 **mesuré**. » Le vivier valait `k * 2`,
codé en dur. Le rendre réglable était le préalable ; ce script est la mesure.

CE QUI EST MESURÉ, ET POURQUOI PAS PAR LA RÉFÉRENCE
------------------------------------------------------
`reference_l15.py` interroge le magasin FAISS **directement** — `store.search(v, k=K)`.
Elle mesure donc le rappel brut de l'index, pas le chemin de production, qui compte
quatre étages de plus : RRF, déduplication, MMR, reranker. C'est précisément ces étages
que le vivier alimente, donc c'est le vrai `Retriever` qu'il faut exercer.

Aucun appel de génération : on ne mesure pas la réponse, on mesure ce qui lui est servi.
Les embeddings viennent du cache de la référence — mesure reproductible et gratuite.

CE QUE « TROUVÉ » VEUT DIRE
-----------------------------
Un cas est trouvé si **au moins un des articles attendus** apparaît dans les `k`
passages que le retriever aurait donnés au modèle. C'est la borne haute de ce que le
modèle peut citer : ce qu'il ne reçoit pas, il ne peut que l'inventer.
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
FACTEURS = [int(x) for x in os.environ.get("COLAIG_FACTEURS", "2,3,4,6").split(",")]


def _charger_reference() -> dict:
    """Réutilise le découpage, l'embedding caché et l'extraction d'articles.

    On exécute le module sans lancer son `main()` : c'est le même procédé que
    `reference_pipeline.py`, et il évite de recopier une mécanique qui divergerait.
    """
    espace: dict = {"__name__": "reference_l15_importee", "__file__": str(REFERENCE)}
    exec(compile(REFERENCE.read_text(encoding="utf-8"), str(REFERENCE), "exec"), espace)
    return espace


async def main() -> int:
    ref = _charger_reference()

    cas = [json.loads(l) for l in ref["JEU"].read_text(encoding="utf-8").splitlines()
           if l.strip()]
    cas = [c for c in cas if c.get("articles_attendus")]
    print(f"jeu doré : {len(cas)} cas avec article attendu", file=sys.stderr)

    chunks = ref["decouper"](ref["STRATEGIE"])
    print(f"corpus : {len(chunks)} chunks", file=sys.stderr)

    cle = ref["cle_albert"]()
    vecteurs = ref["embed"]([c.text for c in chunks], cle)

    from colaig.rag.embeddings import EmbeddingService
    from colaig.rag.faiss_store import FaissStore
    from colaig.rag.retriever import Retriever

    store = FaissStore(dimension=ref["DIMENSION"])
    store.add(vecteurs, chunks)

    # Les questions, embarquées une fois pour toutes.
    vq = ref["embed"]([c["question"] for c in cas], cle)

    class _EmbeddingsPreCalcules:
        """Sert le vecteur déjà connu — aucune requête réseau pendant la mesure."""

        def __init__(self):
            self.dimension = ref["DIMENSION"]
            self.courant: list[float] = []

        async def embed_text(self, texte: str) -> list[float]:
            return self.courant

    faux = _EmbeddingsPreCalcules()
    retriever = Retriever(faux, store)

    articles_du_chunk = ref["articles_du_chunk"]
    resultats: dict[int, dict] = {}

    for facteur in FACTEURS:
        os.environ["COLAIG_RETRIEVER_POOL_FACTOR"] = str(facteur)
        trouves = 0
        rangs: list[int] = []
        for c, v in zip(cas, vq):
            faux.courant = v
            passages = await retriever.retrieve(c["question"], k=K, score_threshold=0.0)
            attendus = set(c["articles_attendus"])
            rang = 0
            for i, p in enumerate(passages, 1):
                if attendus & articles_du_chunk(p.chunk.text):
                    rang = i
                    break
            if rang:
                trouves += 1
                rangs.append(rang)
        rangs.sort()
        resultats[facteur] = {
            "trouves": trouves,
            "rang_median": rangs[len(rangs) // 2] if rangs else 0,
        }
        print(f"  facteur {facteur} (vivier {K * facteur}) : "
              f"{trouves}/{len(cas)}", file=sys.stderr)

    print()
    print(f"L4.1 — VIVIER DE CANDIDATS · k={K} · {len(cas)} cas")
    print()
    print("| facteur | vivier | article attendu servi | rang médian |")
    print("|---|---|---|---|")
    for f, r in resultats.items():
        pct = r["trouves"] * 100 / len(cas)
        print(f"| ×{f} | {K * f} | {r['trouves']}/{len(cas)} ({pct:.1f} %) "
              f"| {r['rang_median']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
