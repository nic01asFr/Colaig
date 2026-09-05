"""
Dispersion des scores et effet du reranking — diagnostic de la référence L1.5.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5b

Deux questions, une seule exécution.

**1. L'écrasement des scores est-il général ?** Sur le cas `mp-015`, les dix premiers
candidats tenaient dans 1,8 % — le classement y était du bruit. Mesuré sur un seul cas,
cela ne prouve rien. Ici la dispersion est calculée sur l'ensemble du jeu doré, et
confrontée aux échecs : un écrasement fort **prédit-il** l'échec ?

**2. Le reranker répare-t-il ?** Les vingt premiers candidats denses sont rescorés par
`bge-reranker-v2-m3`. Si le bon article remonte dans les six premiers après reranking
alors qu'il n'y était pas, la réponse est oui — et l'arbitrage H2 change de nature.

Aucune conclusion n'est tirée d'un cas isolé : c'est précisément l'erreur que ce
diagnostic corrige.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))

SRC = (RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
_ns: dict = {"__name__": "diag", "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py")}
sys.argv = ["diag", "article"]
exec(compile(SRC.replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"), _ns)

decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]
articles_du_chunk, FaissStore = _ns["articles_du_chunk"], _ns["FaissStore"]
BASE = _ns["BASE_ALBERT"]
JEU = RACINE / "tests" / "golden" / "v1.jsonl"

K = 6
CANDIDATS = 20


def reranker(query: str, documents: list[str], cle: str) -> list[int]:
    """→ indices des documents, du plus au moins pertinent."""
    charge = json.dumps({
        "model": "bge-reranker-v2-m3", "query": query, "documents": documents,
    }).encode()
    req = urllib.request.Request(BASE + "/rerank", data=charge, method="POST")
    req.add_header("Authorization", "Bearer " + cle)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as rep:
        resultats = json.loads(rep.read().decode())["results"]
    return [r["index"] for r in sorted(resultats, key=lambda r: -r["relevance_score"])]


def main() -> int:
    cle = cle_albert()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    chunks = decouper("article")
    print(f"{len(chunks)} chunks, {len(cas)} cas", file=sys.stderr)

    store = FaissStore(dimension=1024)
    store.add(embed([c.text for c in chunks], cle), chunks)
    vq = embed([c["question"] for c in cas], cle)

    lignes = []
    dispersions_ok, dispersions_ko = [], []
    dense_ok = rerank_ok = 0
    evalues = 0
    duree_rerank = []

    for c, v in zip(cas, vq):
        attendus = set(c.get("articles_attendus", []))
        trouves = store.search(v, k=CANDIDATS)
        scores = [r.score for r in trouves]
        dispersion = (scores[0] - scores[min(9, len(scores) - 1)]) / scores[0] * 100

        def rang_de(liste):
            for i, r in enumerate(liste, 1):
                if attendus & articles_du_chunk(r.chunk.text):
                    return i
            return None

        rang_dense = rang_de(trouves)
        t0 = time.monotonic()
        ordre = reranker(c["question"], [r.chunk.text for r in trouves], cle)
        duree_rerank.append(time.monotonic() - t0)
        reordonnes = [trouves[i] for i in ordre]
        rang_rerank = rang_de(reordonnes)

        if attendus:
            evalues += 1
            d_ok = rang_dense is not None and rang_dense <= K
            r_ok = rang_rerank is not None and rang_rerank <= K
            dense_ok += d_ok
            rerank_ok += r_ok
            (dispersions_ok if d_ok else dispersions_ko).append(dispersion)
            fleche = "→" if d_ok == r_ok else ("↑ **gagné**" if r_ok else "↓ *perdu*")
            lignes.append(
                f"| {c['id']} | {dispersion:.2f} % | {rang_dense or '—'} | "
                f"{rang_rerank or '—'} | {fleche} |"
            )
        else:
            lignes.append(f"| {c['id']} (négatif) | {dispersion:.2f} % | — | — | — |")

    print()
    print(f"dense    : {dense_ok}/{evalues} dans le top {K}")
    print(f"reranké  : {rerank_ok}/{evalues} dans le top {K}")
    print(f"dispersion des cas réussis : médiane {statistics.median(dispersions_ok):.2f} %"
          if dispersions_ok else "")
    print(f"dispersion des cas échoués : médiane {statistics.median(dispersions_ko):.2f} %"
          if dispersions_ko else "aucun échec")
    print(f"latence rerank : médiane {statistics.median(duree_rerank)*1000:.0f} ms")

    sortie = RACINE / "docs" / "diagnostic-dispersion-20260823.md"
    L = [
        "# Dispersion des scores et effet du reranking",
        "",
        "**23/08/2026.** Diagnostic déclenché par la régression de `mp-015` : les dix",
        "premiers candidats y tenaient dans 1,8 %, rendant leur classement arbitraire.",
        "Mesuré sur un seul cas, cela ne prouvait rien. Voici la mesure sur les vingt.",
        "",
        "Montage : découpage par article, 1 762 chunks, `bge-m3` 1024 dimensions,",
        f"{CANDIDATS} candidats denses rescorés par `bge-reranker-v2-m3`, succès = top {K}.",
        "",
        "## Résultat",
        "",
        "| | dans le top 6 |",
        "|---|---|",
        f"| Recherche dense seule | **{dense_ok}/{evalues}** |",
        f"| Dense puis reranking | **{rerank_ok}/{evalues}** |",
        "",
    ]
    if dispersions_ok and dispersions_ko:
        L += [
            "## L'écrasement des scores prédit-il l'échec ?",
            "",
            "| | dispersion médiane du top 10 |",
            "|---|---|",
            f"| cas réussis en dense | {statistics.median(dispersions_ok):.2f} % |",
            f"| cas échoués en dense | {statistics.median(dispersions_ko):.2f} % |",
            "",
        ]
    L += [
        f"Latence de reranking : médiane **{statistics.median(duree_rerank)*1000:.0f} ms** "
        f"pour {CANDIDATS} candidats.",
        "",
        "## Détail",
        "",
        "| cas | dispersion top 10 | rang dense | rang reranké | |",
        "|---|---|---|---|---|",
        *lignes,
        "",
        "## Rejouer",
        "",
        "```bash",
        "python _chantier/scripts/diagnostic_dispersion.py",
        "```",
    ]
    sortie.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nrapport : {sortie}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
