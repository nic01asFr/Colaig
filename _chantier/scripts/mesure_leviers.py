"""Banc des leviers de recherche — arbitrage H2, enfin mesurable.

FAISS dense, BM25 lexical, fusion RRF et reclassement MMR existent tous dans
`colaig/rag/` depuis l'origine du dépôt. **Aucun n'avait jamais été mesuré.** Ils
étaient activables par configuration, sans que personne puisse dire ce qu'ils
apportent — exactement la situation que le principe « rien n'est activé sans mesure »
existe pour interdire.

Ce qui rend l'arbitrage possible aujourd'hui : la référence L1.5 existe, sur 103 cas
dorés porteurs d'articles attendus.

Ce que ce banc mesure
---------------------
Une seule métrique, la même que la référence : **tous les articles attendus
remontent-ils** dans les passages rendus. C'est le seul critère qui ait un sens en
amont de la génération — un article manquant ne peut pas être cité.

Les embeddings sont calculés **une fois** et toutes les variantes sont évaluées
dessus. Le coût du banc est donc celui d'une seule mesure, ce qui permet de balayer
large sans arbitrer sur le prix.

Le diagnostic qui a motivé ce banc
----------------------------------
Sur les 11 échecs de la référence, `diagnostic_echecs.py` a mesuré le rang **réel**
de l'article attendu :

| | cas | rang |
|---|---|---|
| mal classés | mp-012, mp-015, mp-051, mp-057, mp-093, mp-104 | 9 à 15 |
| hors de portée | mp-025, mp-047, mp-069, mp-070, mp-116 | > 60 |

Six échecs sur onze ne sont pas des absences mais des **erreurs de classement**. Le
plafond atteignable en jouant sur la profondeur et le classement est donc 94/103 ;
au-delà, ce n'est plus un problème de moteur.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))

_ns: dict = {
    "__name__": "_harnais",
    "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py"),
}
exec(  # noqa: S102 — on réutilise le harnais de mesure, jamais une copie
    compile((RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8"),
            "reference_l15.py", "exec"),
    _ns, _ns,
)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]
articles_du_chunk = _ns["articles_du_chunk"]

from colaig.rag.bm25_store import BM25Store  # noqa: E402
from colaig.rag.faiss_store import FaissStore  # noqa: E402
from colaig.rag.retriever import _mmr_rerank, _rrf_combine  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
SORTIE = RACINE / "docs" / "leviers-recherche-20260823.md"


def articles_de(resultats) -> set[str]:
    trouves: set[str] = set()
    for r in resultats:
        trouves |= articles_du_chunk(r.chunk.text)
    return trouves


def main() -> int:
    cle = cle_albert()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    avec_articles = [c for c in cas if c.get("articles_attendus")]

    chunks = decouper("article")
    dense = FaissStore()
    dense.add(embed([c.text for c in chunks], cle), chunks)
    lexical = BM25Store()
    lexical.add(chunks)
    vq = embed([c["question"] for c in avec_articles], cle)

    def evaluer(nom: str, rendre) -> dict:
        complets = partiels = nuls = 0
        echoues: list[str] = []
        for c, v in zip(avec_articles, vq):
            attendus = set(c["articles_attendus"])
            trouves = articles_de(rendre(c["question"], v))
            inter = attendus & trouves
            if inter == attendus:
                complets += 1
            elif inter:
                partiels += 1
            else:
                nuls += 1
                echoues.append(c["id"])
        return {"nom": nom, "complets": complets, "partiels": partiels,
                "nuls": nuls, "echoues": echoues}

    variantes = []

    # 1. Dense seul, à profondeur croissante. La profondeur n'est pas gratuite : chaque
    #    passage supplémentaire entre dans le prompt de génération.
    for k in (6, 10, 15, 20):
        variantes.append(evaluer(f"dense k={k}", lambda q, v, k=k: dense.search(v, k=k)))

    # 2. BM25 seul — pour savoir ce que le lexical apporte *par lui-même*. Sans cette
    #    ligne, un gain de la fusion serait attribué à la fusion sans preuve.
    def _bm25(q, v, k=6):
        return [type("R", (), {"chunk": ch, "score": s})() for ch, s in lexical.search(q, k=k)]

    variantes.append(evaluer("BM25 k=6", _bm25))

    # 3. Fusion RRF dense + lexical, à la profondeur de la production.
    def _rrf(q, v, k=6):
        return _rrf_combine(dense.search(v, k=k * 2), lexical.search(q, k=k * 2), k=k)[:k]

    variantes.append(evaluer("RRF dense+BM25 k=6", _rrf))
    variantes.append(evaluer("RRF dense+BM25 k=10",
                             lambda q, v: _rrf_combine(dense.search(v, k=20),
                                                       lexical.search(q, k=20), k=10)[:10]))

    # 4. MMR par-dessus la fusion : il arbitre pertinence contre diversité, donc il peut
    #    faire remonter un article isolé noyé par une famille d'articles voisins — c'est
    #    précisément la forme de plusieurs échecs mesurés.
    def _rrf_mmr(q, v, k=6):
        fusion = _rrf_combine(dense.search(v, k=k * 3), lexical.search(q, k=k * 3), k=k * 3)
        return _mmr_rerank(fusion, v, k=k, lambda_param=0.7)

    variantes.append(evaluer("RRF + MMR k=6", _rrf_mmr))
    variantes.append(evaluer("dense + MMR k=6",
                             lambda q, v: _mmr_rerank(dense.search(v, k=18), v, k=6,
                                                      lambda_param=0.7)))

    n = len(avec_articles)
    L = [
        "# Banc des leviers de recherche — arbitrage H2",
        "",
        f"**{n} cas dorés porteurs d'articles attendus.** Métrique : *tous* les articles",
        "attendus remontent dans les passages rendus. Un article manquant ne peut pas être cité.",
        "",
        "FAISS, BM25, RRF et MMR existaient tous dans le code sans avoir jamais été mesurés.",
        "",
        "| variante | complets | partiels | nuls |",
        "|---|---|---|---|",
    ]
    for v in variantes:
        pc = 100 * v["complets"] / n
        L.append(f"| {v['nom']} | **{v['complets']}** ({pc:.0f} %) | {v['partiels']} | {v['nuls']} |")
    L += ["", "## Cas encore en échec, par variante", ""]
    for v in variantes:
        L.append(f"- **{v['nom']}** — {', '.join(v['echoues']) or 'aucun'}")
    SORTIE.write_text("\n".join(L) + "\n", encoding="utf-8")

    for v in variantes:
        print(f"{v['nom']:24} complets {v['complets']:3}/{n}  partiels {v['partiels']:2}  nuls {v['nuls']:2}")
    print(f"\nrapport : {SORTIE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
