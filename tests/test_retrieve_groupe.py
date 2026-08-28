"""
Contrat — plusieurs reformulations coûtent UN aller-retour, pas N.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.5

Le mécanisme, mesuré avant d'être corrigé
-------------------------------------------
`PreExecutionBuilder._execute_retrieval` boucle sur `chunk_queries` **et** sur
`document_queries`, appelant `Retriever.retrieve()` une fois par requête. Chaque appel
vectorise sa requête séparément.

L'Analyseur produit 2 à 3 reformulations, plus des `document_queries` : **deux à six
allers-retours réseau par tour**, sur le chemin du message, avant que l'utilisateur
voie quoi que ce soit. Et si HyDE est actif, chaque requête en ajoute un.

Or `embed_texts` existe et l'indexation s'en sert déjà pour des centaines de chunks.
**La brique du groupage existait ; elle n'était pas employée là.** Ce n'est pas une
réécriture, c'est un raccordement — le même motif que la phase 2 a trouvé neuf fois.

Ce que `retrieve_many` change, et ce qu'il ne change pas
---------------------------------------------------------
Il groupe **la vectorisation**, qui est le seul aller-retour réseau de l'étape. La
recherche FAISS, le RRF, le MMR restent par requête : ils sont locaux et ne coûtent pas
de latence réseau.

Il ne touche pas au chemin HyDE, qui demande une **génération** par requête et relève
d'un autre arbitrage. Ce cas retombe sur la boucle d'origine, et un test l'épingle pour
qu'on ne croie pas le gain acquis partout.
"""
from __future__ import annotations

import pytest

from colaig.rag.embeddings import EmbeddingService
from colaig.rag.faiss_store import FaissStore
from colaig.rag.retriever import Retriever
from tests.test_embeddings_par_tour import CompteurEmbeddings


@pytest.fixture
async def moteur(fake_llm, fake_storage):
    """Un retriever sur un index non vide, avec compteur d'allers-retours."""
    from colaig.rag.chunker import Chunker
    from colaig.rag.indexer import Indexer

    dim = fake_llm.embedding_dim
    compteur = CompteurEmbeddings(EmbeddingService(fake_llm, dimension=dim))
    store = FaissStore(dim)
    await fake_storage.upload(
        "/espace/doc.txt",
        ("La procedure de validation exige deux signatures. " * 20).encode())
    await Indexer(fake_storage, Chunker(chunk_size=200, chunk_overlap=20),
                  compteur, store).index_workspace("/espace/")
    compteur.appels_unitaires.clear()
    compteur.appels_groupes.clear()
    return Retriever(compteur, store), compteur


REFORMULATIONS = [
    "procedure de validation",
    "comment valider un dossier",
    "qui signe la validation",
]


# ── Le gain ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trois_reformulations_coutent_UN_aller_retour(moteur):
    """LE critère de L3.5, sur l'étape qui le portait."""
    retriever, c = moteur
    await retriever.retrieve_many(REFORMULATIONS, k=3)
    assert c.total == 1, (
        f"{c.total} appels pour trois reformulations — "
        f"unitaires {c.appels_unitaires}, groupés {c.appels_groupes}"
    )
    assert c.appels_groupes == [3]


@pytest.mark.asyncio
async def test_la_boucle_d_origine_en_coute_trois(moteur):
    """Le contre-exemple, garde le chiffre visible.

    Sans lui, personne ne saurait plus ce que le groupage a fait gagner — et un gain
    qu'on ne sait plus mesurer se perd à la première refonte.
    """
    retriever, c = moteur
    for q in REFORMULATIONS:
        await retriever.retrieve(q, k=3)
    assert c.total == 3


# ── Les résultats doivent être les mêmes ────────────────────────────────────


@pytest.mark.asyncio
async def test_le_groupage_rend_les_MEMES_resultats(moteur):
    """Un gain de latence qui change les réponses n'est pas un gain.

    C'est l'exigence qui compte : `retrieve_many` doit être indiscernable de la boucle,
    résultat par résultat.
    """
    retriever, c = moteur
    groupes = await retriever.retrieve_many(REFORMULATIONS, k=3)
    un_par_un = [await retriever.retrieve(q, k=3) for q in REFORMULATIONS]

    assert len(groupes) == len(un_par_un) == 3
    for a, b in zip(groupes, un_par_un):
        assert [r.chunk.text for r in a] == [r.chunk.text for r in b]
        assert [round(r.score, 6) for r in a] == [round(r.score, 6) for r in b]


@pytest.mark.asyncio
async def test_l_ordre_des_resultats_suit_l_ordre_des_requetes(moteur):
    """Une liste rendue dans le désordre ferait attribuer des passages à la mauvaise
    reformulation, sans qu'aucune erreur ne se voie.
    """
    retriever, _ = moteur
    groupes = await retriever.retrieve_many(REFORMULATIONS, k=3)
    for q, resultats in zip(REFORMULATIONS, groupes):
        attendu = await retriever.retrieve(q, k=3)
        assert [r.chunk.text for r in resultats] == [r.chunk.text for r in attendu]


# ── Les cas limites ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_liste_vide_ne_coute_rien(moteur):
    retriever, c = moteur
    assert await retriever.retrieve_many([], k=3) == []
    assert c.total == 0


@pytest.mark.asyncio
async def test_une_seule_requete_coute_un_appel(moteur):
    """Le groupage ne doit pas rendre le cas simple plus cher."""
    retriever, c = moteur
    await retriever.retrieve_many(["une question"], k=3)
    assert c.total == 1


@pytest.mark.asyncio
async def test_un_index_vide_ne_coute_AUCUN_appel(fake_llm):
    """Le court-circuit de `retrieve` doit valoir aussi pour la version groupée."""
    dim = fake_llm.embedding_dim
    c = CompteurEmbeddings(EmbeddingService(fake_llm, dimension=dim))
    retriever = Retriever(c, FaissStore(dim))
    assert await retriever.retrieve_many(REFORMULATIONS, k=3) == [[], [], []]
    assert c.total == 0


# ── La limite, écrite plutôt que découverte ─────────────────────────────────


@pytest.mark.asyncio
async def test_HyDE_retombe_sur_la_boucle(fake_llm, fake_storage):
    """Limite connue, épinglée : le gain n'est pas acquis partout.

    HyDE demande une **génération** par requête avant de vectoriser. Grouper cela est
    un autre arbitrage — et le faire à l'aveugle produirait un gain apparent en
    changeant les résultats.

    Si ce test échoue un jour, c'est que HyDE a été groupé lui aussi : bonne nouvelle,
    mettre à jour cette docstring.
    """
    from colaig.rag.chunker import Chunker
    from colaig.rag.indexer import Indexer

    dim = fake_llm.embedding_dim
    c = CompteurEmbeddings(EmbeddingService(fake_llm, dimension=dim))
    store = FaissStore(dim)
    await fake_storage.upload("/espace/doc.txt", b"Une procedure de validation.")
    await Indexer(fake_storage, Chunker(chunk_size=200, chunk_overlap=20),
                  c, store).index_workspace("/espace/")
    c.appels_unitaires.clear()
    c.appels_groupes.clear()

    retriever = Retriever(c, store, albert_client=fake_llm, hyde_enabled=True)
    await retriever.retrieve_many(REFORMULATIONS, k=3)
    assert c.appels_unitaires, (
        "avec HyDE, chaque requête garde son propre aller-retour — c'est documenté"
    )
