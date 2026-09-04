"""Servir l'article, ou servir ses voisins ?

CE QUE LA MESURE DU 04/09/2026 MONTRE
---------------------------------------
Sur les 102 cas dores dont l'article attendu est un numero de code, le FICHIER qui le
porte est servi 89 fois. Mais dans 21 cas la reponse ne le cite pas — et en les lisant,
le motif est constant : le modele cite les articles VOISINS et declare que
l'information ne figure pas dans les passages fournis.

    attendu R2111-12   servis R2111-15
    attendu R2113-6    servis R2113-4
    attendu L2113-14   servis L2113-12, L2113-13
    attendu R2124-3    servis R2124-4
    attendu R2112-17   servis R2112-8, -9, -10, -12, -13

Le decoupage etant PAR ARTICLE, un fichier contient des dizaines de passages : servir
le fichier ne sert pas l'article. La recherche ne part pas ailleurs — elle s'arrete a
deux ou trois rangs. Et le refus qui suit est CORRECT au vu de ce qui a ete servi :
le defaut est en amont, dans la granularite de ce qu'on donne a lire.

CE QUE FAIT L'ELARGISSEMENT
-----------------------------
Un article se lit dans sa section. Autour de chaque passage retenu, on sert ses voisins
immediats dans le meme document — ceux que le decoupage a separes, et que le redacteur
du code avait ecrits ensemble.

Sous drapeau : `COLAIG_VOISINS_ENABLED`, rayon `COLAIG_VOISINS_RAYON` (defaut 1).
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk, SearchResult
from colaig.rag.retriever import Retriever


def _chunk(nom, position, section):
    return DocumentChunk(text=f"texte de {section}", source_path=f"/{nom}",
                         source_name=nom, position=position, section=section)


_CORPUS = [
    _chunk("code.md", 0, "R2111-10"),
    _chunk("code.md", 1, "R2111-12"),
    _chunk("code.md", 2, "R2111-15"),
    _chunk("code.md", 3, "R2111-16"),
    _chunk("annexe.md", 0, "Annexe 2 — texte 1"),
]


class _Embeddings:
    async def embed_text(self, texte):
        return [1.0, 0.0]


class _Store:
    """Rend un seul passage : celui que la recherche a trouve."""

    count = len(_CORPUS)

    def search(self, embedding, k=5):
        return [SearchResult(chunk=_CORPUS[2], score=0.71, rank=1)]

    def get_all_active_chunks(self):
        return list(_CORPUS)


class _StoreSansInventaire(_Store):
    """Un magasin qui n'expose pas son inventaire — le contrat ne l'exige pas."""

    get_all_active_chunks = None


def _retriever(store=None):
    return Retriever(embedding_service=_Embeddings(), store=store or _Store(),
                     albert_client=None)


@pytest.mark.asyncio
async def test_sans_le_drapeau_rien_ne_change(monkeypatch):
    monkeypatch.delenv("COLAIG_VOISINS_ENABLED", raising=False)

    trouves = await _retriever().retrieve("un label particulier", k=5)

    assert [r.chunk.section for r in trouves] == ["R2111-15"]


@pytest.mark.asyncio
async def test_les_voisins_immediats_sont_servis(monkeypatch):
    monkeypatch.setenv("COLAIG_VOISINS_ENABLED", "true")

    trouves = await _retriever().retrieve("un label particulier", k=5)

    sections = [r.chunk.section for r in trouves]
    assert "R2111-15" in sections, "le passage trouve reste servi"
    assert {"R2111-12", "R2111-16"} <= set(sections), "ses voisins immediats aussi"
    assert "Annexe 2 — texte 1" not in sections, "un voisin l'est DANS SON DOCUMENT"


@pytest.mark.asyncio
async def test_le_rayon_se_regle(monkeypatch):
    monkeypatch.setenv("COLAIG_VOISINS_ENABLED", "true")
    monkeypatch.setenv("COLAIG_VOISINS_RAYON", "2")

    trouves = await _retriever().retrieve("un label particulier", k=5)

    assert {"R2111-10", "R2111-12", "R2111-15", "R2111-16"} == {r.chunk.section for r in trouves}


@pytest.mark.asyncio
async def test_le_passage_trouve_garde_sa_place(monkeypatch):
    """Un voisin complete une reponse, il ne la precede pas."""
    monkeypatch.setenv("COLAIG_VOISINS_ENABLED", "true")

    trouves = await _retriever().retrieve("un label particulier", k=5)

    assert trouves[0].chunk.section == "R2111-15"


@pytest.mark.asyncio
async def test_un_magasin_sans_inventaire_ne_fait_pas_echouer_la_recherche(monkeypatch):
    """`get_all_active_chunks` n'est pas au contrat : son absence degrade, elle ne casse pas."""
    monkeypatch.setenv("COLAIG_VOISINS_ENABLED", "true")

    trouves = await _retriever(_StoreSansInventaire()).retrieve("un label", k=5)

    assert [r.chunk.section for r in trouves] == ["R2111-15"]
