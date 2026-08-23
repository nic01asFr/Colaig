"""Pourquoi ces cas-là ne remontent-ils rien ?

Diagnostiquer avant d'optimiser. La mesure du 23/08/2026 donne 88/103 cas complets ;
les 11 échecs complets ne sont pas répartis au hasard, et une hypothèse — l'écart entre
le vocabulaire du praticien et celui du code — ne vaut que si on la confronte à ce qui
remonte réellement à la place.

Pour chaque échec, ce script affiche la question, l'article attendu, et ce que la
recherche a effectivement rendu. Trois causes se distinguent ainsi :

- **vocabulaire** : les articles rendus sont du bon domaine, l'attendu emploie d'autres
  mots que la question (« CCAG » contre « cahiers des clauses administratives générales ») ;
- **cas mal posé** : l'article attendu n'est pas celui qui répond le mieux — c'est le jeu
  doré qu'il faut corriger, pas le moteur ;
- **dispersion** : rien de cohérent ne remonte, la question est trop large.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))

# Un SEUL dictionnaire sert de globals et de locals : avec deux dictionnaires
# distincts, les affectations de niveau module vont dans l'un et les closures des
# fonctions cherchent dans l'autre — `RACINE` reste alors introuvable depuis
# `cle_albert()`.
_ns: dict = {
    "__name__": "_harnais",
    "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py"),
}
exec(  # noqa: S102 — on réutilise le harnais de mesure, pas une copie
    compile((RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8"),
            "reference_l15.py", "exec"),
    _ns, _ns,
)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]
articles_du_chunk = _ns["articles_du_chunk"]

from colaig.rag.faiss_store import FaissStore as VectorStore  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
K = 6
PROFOND = 60


def main() -> int:
    cle = cle_albert()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    chunks = decouper("article")
    store = VectorStore()
    store.add(embed([c.text for c in chunks], cle), chunks)

    vq = embed([c["question"] for c in cas], cle)

    for c, v in zip(cas, vq):
        attendus = set(c.get("articles_attendus") or [])
        if not attendus:
            continue
        trouves = store.search(v, k=K)
        # Le test porte sur l'ENSEMBLE des articles des passages rendus. Une première
        # version tronquait à deux articles par passage avant de tester, et déclarait
        # donc en échec des cas qui remontaient bien l'attendu — un diagnostic faussé
        # aurait fait optimiser contre un problème inexistant.
        rendus: set[str] = set()
        apercu: list[str] = []
        for r in trouves:
            arts = sorted(articles_du_chunk(r.chunk.text))
            rendus |= set(arts)
            apercu.extend(arts[:2])
        if attendus & rendus:
            continue  # au moins un attendu remonte : ce n'est pas un échec complet

        # À quel rang l'attendu apparaît-il si l'on cherche beaucoup plus loin ?
        # C'est la question qui partage les échecs en deux : ce qu'un reclassement
        # peut sauver — l'article est là, mal classé — et ce qui est hors de portée
        # du moteur, parce que la question et l'article n'ont pas de mots communs.
        profond = store.search(v, k=PROFOND)
        rang = None
        for i, r in enumerate(profond, 1):
            if attendus & set(articles_du_chunk(r.chunk.text)):
                rang = i
                break

        print(f"### {c['id']}  [{c['type']}/{c['difficulte']}]")
        print(f"  question : {c['question']}")
        print(f"  attendu  : {', '.join(sorted(attendus))}")
        print(f"  rendu    : {', '.join(apercu[:8])}")
        print(f"  rang réel de l'attendu : {rang if rang else f'> {PROFOND}'}"
              f"   (score du 1er : {trouves[0].score:.4f})")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
