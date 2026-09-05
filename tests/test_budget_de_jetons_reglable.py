"""Le budget de passages etait code en dur, et c'est lui qui tranchait.

CE QUE LA MESURE COMPARATIVE A MONTRE (04/09/2026, sur le service)
--------------------------------------------------------------------
L'elargissement aux voisins fait ce pour quoi il est ecrit : les cas ou l'article
attendu n'est pas servi ALORS QUE SON FICHIER l'est tombent de 22 a 7. Mais le nombre
de cas servis ne bouge presque pas — 79 puis 81 — parce que treize autres cas ont
PERDU leur document.

Le journal dit pourquoi : le nombre de passages effectivement servis est le meme des
deux cotes — mediane 10, moyenne 14,7 contre 14,8. Les voisins n'ont rien ajoute, ils
ont REMPLACE. Le budget etait sature avant comme apres.

    TOKEN_BUDGET = 6000  # code en dur dans retriever.retrieve()

Releve sur l'endpoint le 04/09/2026, en poussant une requete jusqu'au refus :

    « This model's maximum context length is 131072 tokens »

Le budget documentaire consommait donc 4,6 % de la fenetre du modele, et personne ne
pouvait le mesurer autrement : la valeur n'etait pas atteignable depuis une campagne,
et sa troncature ne se journalisait qu'en DEBUG.

CE QUI EST FIXE ICI
---------------------
Le budget se regle, la troncature se voit, et le defaut ne change pas — c'est la
mesure qui choisira la valeur, pas ce test.
"""

from __future__ import annotations

import logging

import pytest

from colaig.models import DocumentChunk, SearchResult
from colaig.rag.retriever import Retriever


def _chunk(i):
    # 4000 caracteres ~ 1000 jetons : quatre passages saturent le defaut de 6000.
    return DocumentChunk(text=f"passage {i} " + ("x" * 4000), source_path=f"/doc{i}.md",
                         source_name=f"doc{i}.md", position=i, section=f"Article A{i}")


class _Embeddings:
    async def embed_text(self, texte):
        return [1.0, 0.0]


class _Store:
    count = 10

    def search(self, embedding, k=5):
        return [SearchResult(chunk=_chunk(i), score=0.9 - i / 100, rank=i) for i in range(10)]


def _retriever():
    return Retriever(embedding_service=_Embeddings(), store=_Store(), albert_client=None)


@pytest.mark.asyncio
async def test_le_defaut_est_inchange(monkeypatch):
    monkeypatch.delenv("COLAIG_BUDGET_JETONS", raising=False)

    trouves = await _retriever().retrieve("une question", k=10)

    assert len(trouves) == 5, "6000 jetons pour des passages de ~1000"


@pytest.mark.asyncio
async def test_un_budget_plus_large_sert_plus_de_passages(monkeypatch):
    monkeypatch.setenv("COLAIG_BUDGET_JETONS", "20000")

    trouves = await _retriever().retrieve("une question", k=10)

    assert len(trouves) == 10


@pytest.mark.asyncio
async def test_une_valeur_invalide_retombe_sur_le_defaut(monkeypatch):
    """Un reglage mal saisi ne doit pas priver l'espace de sa recherche."""
    monkeypatch.setenv("COLAIG_BUDGET_JETONS", "beaucoup")

    trouves = await _retriever().retrieve("une question", k=10)

    assert len(trouves) == 5


@pytest.mark.asyncio
async def test_la_troncature_se_voit(monkeypatch, caplog):
    """Elle ne se journalisait qu'en DEBUG — et c'est elle qui decidait du resultat."""
    monkeypatch.delenv("COLAIG_BUDGET_JETONS", raising=False)

    with caplog.at_level(logging.INFO, logger="colaig.rag.retriever"):
        await _retriever().retrieve("une question", k=10)

    assert any("budget" in r.getMessage() for r in caplog.records), caplog.text
