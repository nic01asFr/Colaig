"""
Colaig — un reranker absent ne doit pas effacer la recherche
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
Première question posée à un corpus réel de 51 documents, sur un sujet que le corpus
couvre précisément :

    retriever: FAISS top scores avant rerank: [0.7188, 0.7188, 0.7188]
    reranker Albert scores: []
    échange workspace=colaig-mesure-sst … sources=[] confiance=0.00

FAISS a trouvé. La réponse, elle, commençait par « Je vous réponds donc sur la base de
**connaissances générales** » : le corpus n'a jamais atteint le Synthétiseur.

La cause
----------
    ranked_pairs = await self._albert_client.rerank(...)   # [] si non supporté
    reordered = []
    for orig_idx, score in ranked_pairs:                   # la boucle ne tourne pas
        ...
    return reordered                                       # -> []

Le `except` attrape les erreurs, mais **une liste vide n'est pas une erreur**. Le
contrat est pourtant écrit dans `colaig/integrations/CLAUDE.md` :

    rerank — Retourne [] si le provider ne supporte pas (404/405)
             → l'appelant peut utiliser MMR comme fallback

L'appelant ne retombait pas : il effaçait tout.

Pourquoi c'est grave
----------------------
`OpenAIClient` — donc SSPCloud, la **cible de production** — n'expose pas d'endpoint de
reranking. Sur cette pile, **toute recherche documentaire rendait zéro résultat**, sans
erreur, sans avertissement. Colaig répondait de mémoire en paraissant fonctionner.

Aucun test ne pouvait l'attraper : ils simulent tous un reranker qui répond.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk, SearchResult


def _resultat(nom: str, score: float) -> SearchResult:
    return SearchResult(
        chunk=DocumentChunk(text=f"contenu de {nom}", source_path=f"/{nom}",
                    source_name=nom, position=0),
        score=score,
    )


class _LLMSansRerank:
    """Le cas de SSPCloud : pas d'endpoint de reranking, donc une liste vide."""

    async def rerank(self, query, texts, top_n=None):
        return []


class _LLMQuiRerank:
    """Un fournisseur qui reclasse réellement — l'ordre doit alors changer."""

    async def rerank(self, query, texts, top_n=None):
        return [(2, 0.9), (0, 0.5), (1, 0.1)]


class _LLMQuiEchoue:
    async def rerank(self, query, texts, top_n=None):
        raise RuntimeError("503")


@pytest.fixture
def resultats():
    return [_resultat("a", 0.72), _resultat("b", 0.71), _resultat("c", 0.70)]


def _retriever(client):
    from colaig.rag.retriever import Retriever

    r = Retriever.__new__(Retriever)
    r._albert_client = client
    return r


@pytest.mark.asyncio
async def test_un_rerank_vide_conserve_les_resultats(resultats):
    """LE défaut du 30/08 : le corpus disparaissait entre FAISS et la réponse."""
    r = _retriever(_LLMSansRerank())

    obtenus, reranke = await r._albert_rerank("une question", list(resultats))

    assert reranke is False, (
        "un reranking qui n'a pas eu lieu ne doit pas etre annonce : "
        "l'appelant en deduit l'echelle de ses scores, donc son seuil"
    )
    assert len(obtenus) == 3, (
        "un fournisseur sans reranking efface la recherche : sur SSPCloud, toute "
        "question au corpus rendait zéro source"
    )
    assert [x.chunk.source_name for x in obtenus] == ["a", "b", "c"], (
        "l'ordre MMR d'origine doit être conservé tel quel"
    )


@pytest.mark.asyncio
async def test_un_rerank_qui_repond_reclasse_bien(resultats):
    """La correction ne doit pas neutraliser le reranking quand il existe."""
    r = _retriever(_LLMQuiRerank())

    obtenus, reranke = await r._albert_rerank("une question", list(resultats))

    assert reranke is True
    assert [x.chunk.source_name for x in obtenus] == ["c", "a", "b"]
    assert obtenus[0].score == 0.9


@pytest.mark.asyncio
async def test_une_erreur_conserve_toujours_les_resultats(resultats):
    """Comportement d'origine du `except`, préservé."""
    r = _retriever(_LLMQuiEchoue())

    obtenus, reranke = await r._albert_rerank("une question", list(resultats))

    assert reranke is False
    assert len(obtenus) == 3
