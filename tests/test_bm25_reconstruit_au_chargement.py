"""
Colaig — activer la recherche hybride sur un index existant doit avoir un effet
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
Un document déposé dans le salon arrive bien sur le stockage, est bien indexé — 52
documents distincts, 1056 vecteurs, son chunk à la clé 1055 — et **ne remonte jamais**,
même à une question qui le nomme.

La recherche vectorielle seule ne le trouve pas : un chunk de 748 octets contre 1055
autres, sur un corpus dense et proche thématiquement. La recherche **lexicale** l'aurait
trouvé sur « note d'essai ».

`COLAIG_HYBRID_SEARCH_ENABLED=true` a donc été posé. Le démarrage l'annonce :

    recherche hybride activée (BM25 + RRF k=60)

Et le résultat est **rigoureusement identique** : mêmes trois sources, même confiance.

La cause
----------
`load_from_storage` restaure BM25 depuis `bm25.pkl` — mais ce fichier **n'existe pas**,
parce que le corpus a été indexé avant l'activation du drapeau, quand `bm25_store`
valait None. Et comme les etags des 52 documents n'ont pas changé, `check_updates` ne
réindexe rien : **BM25 reste vide pour toujours**, et la recherche hybride retombe en
silence sur FAISS seul.

Activer le drapeau sur un index existant n'a donc aucun effet, et rien ne le dit.

Même famille que le reranker absent, corrigé quelques heures plus tôt : **une capacité
déclarée active qui ne fait rien, sans un mot.** C'est la forme de défaut la plus
coûteuse, parce qu'on croit mesurer une option qu'on n'a pas.

La correction
---------------
Les textes des chunks sont **déjà** dans `metadata.pkl`. Quand BM25 est demandé, que son
fichier est absent et que l'index vectoriel n'est pas vide, on le reconstruit depuis les
métadonnées. Aucun appel réseau, aucun ré-embedding : c'est une relecture locale.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk


def _chunk(nom: str, texte: str) -> DocumentChunk:
    return DocumentChunk(text=texte, source_path=f"/{nom}", source_name=nom)


class _StockageSansBm25:
    """Un stockage qui a un index vectoriel mais pas de `bm25.pkl`.

    C'est l'état exact d'un espace indexé avant l'activation du drapeau.
    """

    def __init__(self) -> None:
        self.demandes: list[str] = []

    async def download(self, chemin: str) -> bytes:
        self.demandes.append(chemin)
        if chemin.endswith("bm25.pkl"):
            raise FileNotFoundError(chemin)
        return b"charge-simule"


@pytest.fixture
def indexeur():
    """Un indexeur dont le store vectoriel est peuplé et le BM25 vide."""
    from colaig.rag.bm25_store import BM25Store
    from colaig.rag.indexer import Indexer

    chunks = [
        _chunk("note-essai.md", "conduite a tenir en cas de malaise, note d essai"),
        _chunk("fiche.pdf", "procedure en cas d agression sur le lieu de travail"),
    ]

    class _StoreVectoriel:
        count = len(chunks)

        def deserialize(self, index_bytes, meta_bytes):
            pass

        def get_all_active_chunks(self):
            return list(chunks)

    ix = Indexer.__new__(Indexer)
    ix._storage = _StockageSansBm25()
    ix._store = _StoreVectoriel()
    ix._bm25_store = BM25Store()
    ix._known_etags = {}
    return ix


@pytest.mark.asyncio
async def test_bm25_est_reconstruit_quand_son_fichier_manque(indexeur):
    """LE défaut du 30/08 : la recherche hybride restait vide en silence."""
    charge = await indexeur.load_from_storage("/espace/.colaig/indexes")

    assert charge is True
    assert indexeur._bm25_store.count == 2, (
        "BM25 est vide alors que l'index vectoriel contient des chunks : la recherche "
        "hybride retombe silencieusement sur FAISS seul"
    )


@pytest.mark.asyncio
async def test_le_document_reconstruit_est_retrouvable_lexicalement(indexeur):
    """La reconstruction doit servir à quelque chose, pas seulement remplir un compteur."""
    await indexeur.load_from_storage("/espace/.colaig/indexes")

    resultats = indexeur._bm25_store.search("note essai", k=2)

    assert resultats, "aucun résultat lexical après reconstruction"
    assert "note-essai" in resultats[0][0].source_name


@pytest.mark.asyncio
async def test_sans_bm25_demande_rien_n_est_reconstruit():
    """Le drapeau éteint reste éteint : on ne paie pas pour une option non demandée."""
    from colaig.rag.indexer import Indexer

    class _Store:
        count = 3

        def deserialize(self, a, b):
            pass

        def get_all_active_chunks(self):
            raise AssertionError("ne doit pas être appelé sans BM25 demandé")

    ix = Indexer.__new__(Indexer)
    ix._storage = _StockageSansBm25()
    ix._store = _Store()
    ix._bm25_store = None
    ix._known_etags = {}

    assert await ix.load_from_storage("/espace/.colaig/indexes") is True
