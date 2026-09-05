"""
Colaig — la taille du vivier de candidats doit être un réglage, pas une constante.

CE QUE DEMANDE L4.1
---------------------
« Retriever réglé : HyDE off par défaut, **pool ~20 → rerank → 3-5 mesuré**, seuil
adaptatif μ−2σ en option. »

Relu contre le code le 30/08/2026 : HyDE est bien `False` par défaut, mais le vivier
vaut `k * 2` — soit **10** pour k=5, pas ~20. Et il est **codé en dur**, donc la mesure
que le lot exige n'est pas exécutable : on ne peut pas comparer deux valeurs d'un
nombre qu'on ne peut pas changer.

CE QUE CE LOT FAIT, ET CE QU'IL NE FAIT PAS
---------------------------------------------
Il rend le vivier **réglable**, à comportement inchangé par défaut. Il ne choisit pas
sa valeur : ce choix appartient à la mesure, et la mesure appartient à la référence
L1.5. Poser 20 « parce que le plan le dit » serait exactement ce que ce chantier
cherche à éviter.

POURQUOI UN VIVIER PLUS LARGE N'EST PAS GRATUIT
-------------------------------------------------
Il ne coûte presque rien à FAISS, mais il change ce que voient le RRF, la
déduplication, le MMR et le reranker — quatre étages dont aucun n'est linéaire. Un
vivier trop large peut **dégrader** le résultat en noyant les bons candidats sous des
voisins médiocres. D'où la mesure, et non le réglage à l'estime.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk


def _chunk(texte: str, chemin: str) -> DocumentChunk:
    return DocumentChunk(text=texte, source_path=chemin,
                         source_name=chemin.rsplit("/", 1)[-1])


def test_le_facteur_par_defaut_ne_change_pas_le_comportement(monkeypatch):
    """Sans réglage, le vivier reste `k * 2` — aucune migration imposée."""
    monkeypatch.delenv("COLAIG_RETRIEVER_POOL_FACTOR", raising=False)
    from colaig.rag import retriever as mod

    assert mod._facteur_de_vivier() == 2


def test_le_facteur_est_reglable(monkeypatch):
    monkeypatch.setenv("COLAIG_RETRIEVER_POOL_FACTOR", "4")
    from colaig.rag import retriever as mod

    assert mod._facteur_de_vivier() == 4


def test_un_facteur_absurde_est_refuse(monkeypatch):
    """LA borne. Un vivier plus petit que `k` reviendrait à chercher moins que demandé.

    Une valeur illisible ou nulle retombe sur le défaut plutôt que de faire échouer le
    démarrage : un réglage mal saisi ne doit pas priver l'espace de sa recherche.
    """
    from colaig.rag import retriever as mod

    for valeur in ("0", "-3", "banane", ""):
        monkeypatch.setenv("COLAIG_RETRIEVER_POOL_FACTOR", valeur)
        assert mod._facteur_de_vivier() == 2, f"« {valeur} » n'est pas rejeté"


@pytest.mark.asyncio
async def test_le_facteur_agit_reellement_sur_la_recherche(monkeypatch, fake_llm):
    """Le réglage doit atteindre le magasin, pas seulement exister."""
    from colaig.rag.embeddings import EmbeddingService
    from colaig.rag.faiss_store import FaissStore
    from colaig.rag.retriever import Retriever

    store = FaissStore(dimension=fake_llm.embedding_dim)
    for i in range(40):
        store.add([await fake_llm.embed(f"passage numero {i}")],
                  [_chunk(f"passage numero {i}", f"/e/{i}.pdf")])

    demandes: list[int] = []
    vraie_recherche = store.search
    store.search = lambda v, k=5: (demandes.append(k), vraie_recherche(v, k))[1]

    r = Retriever(EmbeddingService(fake_llm, dimension=fake_llm.embedding_dim), store)

    monkeypatch.setenv("COLAIG_RETRIEVER_POOL_FACTOR", "2")
    await r.retrieve("passage numero 3", k=5, score_threshold=0.0)
    monkeypatch.setenv("COLAIG_RETRIEVER_POOL_FACTOR", "6")
    await r.retrieve("passage numero 3", k=5, score_threshold=0.0)

    assert demandes == [10, 30], f"le facteur n'atteint pas le magasin : {demandes}"
