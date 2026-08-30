"""
Colaig — le même passage ne doit pas occuper deux places dans une recherche.

CE QUE MESURE LE CORPUS RÉEL, LE 30/08/2026
---------------------------------------------
L'espace SST contient 60 documents, dont **18 sont des copies exactes** de 12 autres —
deux arborescences se recouvrent, et l'un des fichiers s'appelle `… - Copie.odt`. Ce
n'est pas un corpus fabriqué : c'est un espace déposé par quelqu'un qui travaille.

Effet mesuré sur 120 requêtes, chaque chunk servant de requête à son tour :

    k= 3   requêtes touchées  20 %   places évincées  7 %
    k= 5   requêtes touchées  22 %   places évincées  8 %
    k=10   requêtes touchées  32 %   places évincées  9 %

**Une requête sur cinq rend le même passage deux fois**, et une place sur douze est
gaspillée — au détriment d'un passage qui aurait pu être pertinent.

> Une première mesure donnait 68 %. Elle était fausse : elle groupait par document, donc
> comptait deux chunks d'un même document comme une redondance, alors que c'est le
> comportement normal. Ce qui est redondant, c'est **le même texte à deux chemins**.

POURQUOI DÉDUPLIQUER À LA RECHERCHE, ET NON À L'INDEXATION
-------------------------------------------------------------
Refuser d'indexer un doublon obligerait l'espace à être propre. Or un espace réel ne
l'est pas, et **c'est précisément ce que Colaig doit savoir traverser** : l'utilisateur
dépose ce qu'il a, Colaig s'en arrange. Dédupliquer au moment de servir compose avec le
désordre au lieu de l'interdire ; c'est local, réversible, et sans ré-indexation.

Le compte rendu d'espace, lui, **dit** ce qui a été vu — c'est l'autre moitié, et elle
n'impose rien.

OÙ, DANS LE PIPELINE
----------------------
Avant le MMR. Celui-ci réduit le vivier à `k` : dédupliquer après lui laisserait un trou
là où l'on veut un passage de plus. Avant, la place libérée est **reprise** par le
candidat suivant.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk, SearchResult


def _chunk(texte: str, chemin: str) -> DocumentChunk:
    return DocumentChunk(text=texte, source_path=chemin,
                         source_name=chemin.rsplit("/", 1)[-1])


def _resultat(texte: str, chemin: str, score: float) -> SearchResult:
    return SearchResult(chunk=_chunk(texte, chemin), score=score)


def test_le_meme_texte_a_deux_chemins_ne_compte_qu_une_fois():
    from colaig.rag.retriever import _deduplique_les_passages

    candidats = [
        _resultat("le debriefing dure deux seances", "/e/support/a.pdf", .90),
        _resultat("le debriefing dure deux seances", "/e/copie/a.pdf",   .89),
        _resultat("le certificat sous quinze jours", "/e/fiche.pdf",     .70),
    ]

    gardes = _deduplique_les_passages(candidats)

    assert len(gardes) == 2, f"le doublon n'est pas retire : {gardes}"
    assert gardes[0].chunk.source_path == "/e/support/a.pdf", (
        "c'est le mieux classe qui doit rester"
    )


def test_la_place_liberee_profite_au_suivant():
    """LE point. Dédupliquer avant le MMR rend une place, il ne la perd pas."""
    from colaig.rag.retriever import _deduplique_les_passages

    candidats = [
        _resultat("passage A", "/e/1.pdf", .90),
        _resultat("passage A", "/e/copie/1.pdf", .89),
        _resultat("passage B", "/e/2.pdf", .80),
        _resultat("passage C", "/e/3.pdf", .70),
    ]

    textes = [r.chunk.text for r in _deduplique_les_passages(candidats)]

    assert textes == ["passage A", "passage B", "passage C"]


def test_deux_passages_differents_du_meme_document_restent():
    """LA borne. Un document qui répond deux fois n'est pas un doublon.

    C'est l'erreur qu'a commise la première mesure, et l'inverser ici couperait la
    moitié des résultats d'un document long et pertinent.
    """
    from colaig.rag.retriever import _deduplique_les_passages

    candidats = [
        _resultat("premiere partie", "/e/guide.pdf", .90),
        _resultat("seconde partie",  "/e/guide.pdf", .85),
    ]

    assert len(_deduplique_les_passages(candidats)) == 2


def test_les_espaces_ne_font_pas_deux_passages():
    """Deux copies d'un fichier peuvent différer d'un blanc après conversion."""
    from colaig.rag.retriever import _deduplique_les_passages

    candidats = [
        _resultat("le  debriefing dure\ndeux seances", "/e/a.pdf", .90),
        _resultat("le debriefing dure deux seances",   "/e/b.pdf", .89),
    ]

    assert len(_deduplique_les_passages(candidats)) == 1


def test_une_liste_vide_reste_vide():
    from colaig.rag.retriever import _deduplique_les_passages

    assert _deduplique_les_passages([]) == []


@pytest.mark.asyncio
async def test_la_recherche_complete_ne_rend_pas_deux_fois_le_meme_passage(fake_llm):
    """De bout en bout, à travers `retrieve()`."""
    from colaig.rag.faiss_store import FaissStore
    from colaig.rag.retriever import Retriever

    store = FaissStore(dimension=fake_llm.embedding_dim)
    textes = [("le debriefing dure deux seances", "/e/support/a.pdf"),
              ("le debriefing dure deux seances", "/e/copie/a.pdf"),
              ("le certificat sous quinze jours", "/e/fiche.pdf"),
              ("la hierarchie accompagne l'agent", "/e/plainte.pdf")]
    for texte, chemin in textes:
        store.add([await fake_llm.embed(texte)], [_chunk(texte, chemin)])

    from colaig.rag.embeddings import EmbeddingService
    r = Retriever(EmbeddingService(fake_llm, dimension=fake_llm.embedding_dim), store)
    resultats = await r.retrieve("le debriefing dure deux seances", k=3,
                                 score_threshold=0.0)

    vus = [" ".join((x.chunk.text or "").split()) for x in resultats]
    assert len(vus) == len(set(vus)), f"un passage revient deux fois : {vus}"
