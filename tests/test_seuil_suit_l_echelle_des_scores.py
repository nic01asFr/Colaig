"""Le seuil de score doit connaitre l'echelle de ce qu'il filtre.

CE QU'ON A OBSERVE
--------------------
`retrieve()` filtre sur `score_threshold`, un seuil de SIMILARITE COSINUS (0,3 par
defaut). Mais le score porte par un resultat ne vient pas toujours de cette echelle :

- FAISS rend une cosinus dans [0, 1] — les relevés sur le corpus reel donnent ~0,72 ;
- la fusion RRF rend `1 / (60 + rang)`, soit **0,016 au premier rang** ;
- le reranker cross-encoder rend des scores sigmoides ~0,001-0,005.

Filtrer des scores RRF a 0,3 elimine TOUT, sans erreur. Releve le 04/09/2026, en
capturant le journal d'un `retrieve()` hybride sans client de reranking :

    retriever: fusion RRF — 1 candidats vectoriels + 1 lexicaux → 2
    retriever: 0 resultat (threshold=0.30, reranker=rrf+mmr). Top scores: [0.016]

CE QUI MASQUAIT LE DEFAUT
---------------------------
`albert_reranked` etait pose a True APRES tout appel au reranker, y compris quand
celui-ci avait repondu « je n'existe pas » — le cas permanent sur SSPCloud, qui ne
sert aucun modele de reranking. Le seuil tombait alors a 0,001 et laissait passer les
scores RRF. La recherche hybride ne fonctionnait en service que par cet accident : le
jour ou l'endpoint aurait servi un reranker, ou un appelant serait passe sans client,
elle serait devenue muette.

LES DEUX PROPRIETES FIXEES ICI
--------------------------------
1. un classement par rang (RRF) ne se filtre pas avec un seuil de similarite ;
2. `albert_reranked` dit ce qui a REELLEMENT eu lieu.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk, SearchResult
from colaig.rag.retriever import Retriever


class _Embeddings:
    async def embed_query(self, texte):
        return [1.0, 0.0]

    async def embed_text(self, texte):
        return [1.0, 0.0]


def _chunk(nom):
    # Un texte DISTINCT par passage : `_deduplique_les_passages` retire les
    # doublons de texte, et un fixture qui les confond ne mesure plus le seuil.
    return DocumentChunk(text=f"un passage du corpus, extrait de {nom}",
                         source_path=f"/{nom}", source_name=nom, position=0, section="")


class _Store:
    count = 2

    def search(self, embedding, k=5):
        return [
            SearchResult(chunk=_chunk("vectoriel-a.md"), score=0.72, rank=1),
            SearchResult(chunk=_chunk("vectoriel-b.md"), score=0.61, rank=2),
        ]


class _BM25:
    count = 1

    def search(self, query, k=10):
        return [(_chunk("lexical.md"), 3.2)]


class _AlbertSansReranker:
    """SSPCloud : l'endpoint ne sert aucun modele de reranking (rend [])."""

    async def rerank(self, query, texts, top_n=None):
        return []


@pytest.mark.asyncio
async def test_la_fusion_rrf_survit_sans_client_de_reranking():
    """Sans client Albert, le seuil cosinus ecrasait toute la fusion."""
    retriever = Retriever(embedding_service=_Embeddings(), store=_Store(), albert_client=None)

    trouves = await retriever.retrieve("delai de paiement", k=5,
                                       score_threshold=0.3, bm25_store=_BM25())

    assert trouves, "la fusion RRF rend des scores ~0,016 : un seuil cosinus les tue tous"
    assert {r.chunk.source_name for r in trouves} >= {"lexical.md"}


@pytest.mark.asyncio
async def test_la_fusion_rrf_survit_a_un_reranker_absent():
    """Le cas du service : un client existe, mais l'endpoint ne reranke pas."""
    retriever = Retriever(embedding_service=_Embeddings(), store=_Store(),
                          albert_client=_AlbertSansReranker())

    trouves = await retriever.retrieve("delai de paiement", k=5,
                                       score_threshold=0.3, bm25_store=_BM25())

    assert trouves


@pytest.mark.asyncio
async def test_sans_fusion_le_seuil_de_similarite_s_applique():
    """Un reranker absent ne doit pas non plus DESACTIVER le filtrage.

    Le score cosinus garde son sens : c'est le seuil de l'espace qui doit trancher,
    pas une valeur posee pour une echelle qui n'a pas eu lieu.
    """

    class _StoreFaible:
        count = 2

        def search(self, embedding, k=5):
            return [
                SearchResult(chunk=_chunk("pertinent.md"), score=0.72, rank=1),
                SearchResult(chunk=_chunk("hors-sujet.md"), score=0.11, rank=2),
            ]

    retriever = Retriever(embedding_service=_Embeddings(), store=_StoreFaible(),
                          albert_client=_AlbertSansReranker())

    trouves = await retriever.retrieve("delai de paiement", k=5, score_threshold=0.3)

    noms = {r.chunk.source_name for r in trouves}
    assert "pertinent.md" in noms
    assert "hors-sujet.md" not in noms, "0,11 est sous le seuil de l'espace"
