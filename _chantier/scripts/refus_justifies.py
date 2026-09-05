"""
Un refus est-il justifié ? La recherche avait-elle apporté la réponse ?

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (interprétation de la référence)

Ce que cette mesure sépare
----------------------------
Mesuré le 28/08/2026 sur douze tirages : **99,4 % des échecs de `cite_attendu` sont des
REFUS**, pas des citations erronées. Colaig ne cite presque jamais le mauvais article —
il dit « cette information ne figure pas dans les passages fournis ».

`cite_attendu` ne mesure donc pas une *fidélité de citation*, mais une **couverture**.
0,78 ne veut pas dire « 22 % de réponses fausses » : il veut dire « 22 % du temps,
l'assistant dit qu'il ne sait pas ». Pour un assistant juridique, c'est le mode de
défaillance sûr.

Reste la question qui décide où porter l'effort :

    refus JUSTIFIÉ   — l'article attendu n'était pas dans les passages reçus.
                       Le modèle a bien agi. Le défaut est dans la RECHERCHE.

    refus INJUSTIFIÉ — l'article attendu ÉTAIT dans les passages, et le modèle a
                       refusé quand même. Le défaut est dans la GÉNÉRATION.

Deux réponses observées suggèrent que le second cas existe, et qu'il est frappant :

    mp-121  « Cette information ne figure pas dans les passages fournis.
              Le Document 1 (Annexe 2 — Seuils de procédure) liste les seuils… »
    mp-125  « … ne figure pas … Les documents fournis contiennent les articles
              CCAG Travaux 4, 11, 12 … »   ← CCAG Travaux 4 EST la réponse attendue

Il tient la source et déclare ne pas l'avoir. Ce n'est plus de la prudence.

Pourquoi cette mesure ne coûte rien
-------------------------------------
Les réponses sont archivées et la recherche est reproductible : le cache d'embeddings
rend les passages identiques d'une réanalyse à l'autre. Aucun appel de génération.

Usage
-----
    set -a; . ./.env; set +a
    python _chantier/scripts/refus_justifies.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))
MESURES = RACINE / "_chantier" / "mesures"

# Les archives a lire. Le motif etait fige sur `dispersion-durci-*` : la meme mesure
# ne pouvait donc pas etre faite sur la variante TEMOIN, alors que c'est exactement
# la comparaison que la reserve de la reference demandait — « ce sur-refus pourrait
# etre fabrique par notre propre instrument ».
#
# Capture AVANT que sys.argv soit ecrase pour le module importe ci-dessous.
_MOTIF = sys.argv[1] if len(sys.argv) > 1 else "dispersion-durci-*.json"

# On réutilise le chargement des harnais : même découpage, même recherche, même k.
SRC = (RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
_ns: dict = {"__name__": "gen",
             "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py")}
sys.argv = ["gen", "article"]
exec(compile(SRC.replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"),
     _ns)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]
FaissStore = _ns["FaissStore"]

from colaig.rag.verification_citations import articles_cites  # noqa: E402

K = 10
JEU = RACINE / "tests" / "golden" / "v1.jsonl"

# Les mêmes marqueurs que `reanalyse_generation`, lus depuis lui : deux listes qui
# divergeraient feraient dire deux choses différentes au même mot « refus ».
_SRC_RE = (RACINE / "_chantier" / "scripts" / "reanalyse_generation.py").read_text(
    encoding="utf-8")
MARQUEURS_REFUS = [
    x.strip().strip("\"'")
    for x in re.search(r"MARQUEURS_REFUS\s*=\s*\((.*?)\)", _SRC_RE, re.S).group(1).split(",")
    if x.strip().strip("\"'")
]


def main() -> int:
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Seuls les cas dont la référence attendue est un identifiant que le motif sait
    # produire. Les 11 autres (CCAG, annexes) sont invisibles a la metrique et
    # fausseraient ce comptage — ils sont traites a part.
    positifs = [c for c in cas
                if c.get("articles_attendus")
                and any(articles_cites(f"article {a}") & {a} for a in c["articles_attendus"])]
    print(f"{len(positifs)} cas positifs a reference codifiee", file=sys.stderr)

    cle = cle_albert()
    chunks = decouper("article")
    # Meme raison : le defaut de FaissStore est 1024, ce qui ne vaut que pour bge-m3.
    store = FaissStore(dimension=_ns["DIMENSION"])
    store.add(embed([c.text for c in chunks], cle), chunks)
    vq = embed([c["question"] for c in positifs], cle)

    # Les passages ne dependent que de la question : calcules UNE fois pour toutes les
    # archives, au lieu d'une fois par archive.
    fournis: dict[str, set[str]] = {}
    for c, v in zip(positifs, vq):
        refs: set[str] = set()
        for r in store.search(v, k=K):
            refs |= articles_cites(r.chunk.text)
        fournis[c["id"]] = refs

    lignes, exemples = [], []
    archives = sorted(MESURES.glob(_MOTIF))
    if not archives:
        raise SystemExit(f"aucune archive pour le motif {_MOTIF!r} dans {MESURES}")
    for arch in archives:
        rep = {r["id"]: r for r in json.loads(arch.read_text(encoding="utf-8"))}
        justifie = injustifie = succes = autre = 0
        for c in positifs:
            t = (rep.get(c["id"], {}).get("reponses") or [""])[0]
            attendus = set(c["articles_attendus"])
            if attendus & articles_cites(t):
                succes += 1
                continue
            if not any(m in t.lower() for m in MARQUEURS_REFUS):
                autre += 1
                continue
            if attendus & fournis[c["id"]]:
                injustifie += 1
                if len(exemples) < 6:
                    exemples.append((arch.name, c["id"], sorted(attendus & fournis[c["id"]]),
                                     t[:120].replace("\n", " ")))
            else:
                justifie += 1
        lignes.append((arch.name, succes, justifie, injustifie, autre))

    n = len(positifs)
    print(f"\n{'archive':28} {'succes':>7} {'refus just.':>12} {'refus INJUST.':>14} {'autre':>6}")
    for nom, s, j, i, a in lignes:
        print(f"{nom:28} {s:7} {j:12} {i:14} {a:6}")
    moy = lambda idx: statistics.mean(x[idx] for x in lignes)  # noqa: E731
    print(f"\nmoyenne sur {len(lignes)} tirages, {n} cas :")
    print(f"  succes            {moy(1):6.1f}  ({moy(1)/n:6.1%})")
    print(f"  refus JUSTIFIE    {moy(2):6.1f}  ({moy(2)/n:6.1%})   -> defaut de RECHERCHE")
    print(f"  refus INJUSTIFIE  {moy(3):6.1f}  ({moy(3)/n:6.1%})   -> defaut de GENERATION")
    print(f"  autre echec       {moy(4):6.1f}  ({moy(4)/n:6.1%})   -> mauvaise citation")

    if exemples:
        print("\nExemples de refus INJUSTIFIES (l'article etait dans les passages) :")
        for nom, cid, arts, extrait in exemples:
            print(f"  {cid} [{nom}] articles fournis et attendus : {arts}")
            print(f"     {extrait}")

    (MESURES / "refus-justifies.json").write_text(
        json.dumps({"cas": n, "tirages": len(lignes),
                    "lignes": [{"archive": x[0], "succes": x[1], "refus_justifie": x[2],
                                "refus_injustifie": x[3], "autre": x[4]} for x in lignes]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
