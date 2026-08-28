"""
Mesure — combien d'embeddings un tour de conversation consomme-t-il ?

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.5 (préparation)

Pourquoi cette mesure existe avant le lot
-------------------------------------------
Le critère de L3.5 est **« un seul `embed()` par tour, vérifié par compteur »**. Un
critère de ce genre ne se vérifie qu'avec un compteur, et personne n'en avait posé : on
ignorait donc si le problème était réel, et de quelle taille.

Ce fichier pose le compteur et **épingle l'état actuel**. Il ne réclame rien encore :
il transforme une intuition du plan en un nombre, pour que L3.5 ait un point de départ
et un critère de fin mesurables plutôt qu'un objectif déclaratif.

Pourquoi le nombre compte
--------------------------
Un embedding est un aller-retour réseau. Sur le chemin d'un message reçu, chacun
s'ajoute à la latence **avant** que l'utilisateur voie quoi que ce soit. Et la mesure
du 28/08 a montré qu'un embedding n'est pas déterministe : multiplier les appels
multiplie aussi les occasions de diverger.

Ce que ce fichier ne mesure PAS
---------------------------------
Il compte les appels du pipeline **phase 1** — celui que les doublures du dépôt savent
exercer de bout en bout. Le pipeline agentique (Analyseur → Orchestrateur →
Synthétiseur) en ajoute : les `chunk_queries` de l'Analyseur sont autant de requêtes,
et `search_skill` en pose une de plus. Le compteur est écrit pour être réutilisé sur ce
chemin quand L3.5 s'y attaquera.
"""
from __future__ import annotations

import pytest

from colaig.models import ConversationType, IncomingMessage


class CompteurEmbeddings:
    """Enveloppe un `EmbeddingService` et compte ses appels.

    Compte les APPELS, pas les textes : c'est le nombre d'allers-retours réseau qui
    fait la latence, et `embed_texts` en groupe plusieurs en un seul.
    """

    def __init__(self, service) -> None:
        self._service = service
        self.appels_unitaires: list[str] = []
        self.appels_groupes: list[int] = []

    async def embed_text(self, texte: str):
        self.appels_unitaires.append(texte)
        return await self._service.embed_text(texte)

    async def embed_texts(self, textes: list[str]):
        self.appels_groupes.append(len(textes))
        return await self._service.embed_texts(textes)

    @property
    def total(self) -> int:
        return len(self.appels_unitaires) + len(self.appels_groupes)

    def __getattr__(self, nom):
        return getattr(self._service, nom)


@pytest.fixture
def pipeline(fake_storage, fake_llm):
    """Un pipeline phase 1 complet, avec le compteur intercalé."""
    from colaig.context.resolver import ContextResolver
    from colaig.rag.chunker import Chunker
    from colaig.rag.embeddings import EmbeddingService
    from colaig.rag.faiss_store import FaissStore
    from colaig.rag.generator import Generator
    from colaig.rag.indexer import Indexer
    from colaig.rag.retriever import Retriever

    dim = fake_llm.embedding_dim
    compteur = CompteurEmbeddings(EmbeddingService(fake_llm, dimension=dim))
    store = FaissStore(dim)
    return {
        "compteur": compteur,
        "store": store,
        "retriever": Retriever(compteur, store),
        "indexer": Indexer(fake_storage, Chunker(chunk_size=200, chunk_overlap=20),
                           compteur, store),
        "resolver": ContextResolver(fake_storage, cache_ttl=0),
        "generator": Generator(fake_llm),
    }


@pytest.fixture
async def indexe(pipeline, fake_storage):
    """Un espace indexé, et le compteur remis à zéro.

    Sans index, le retriever court-circuite et ne mesure rien — le compteur dirait zéro
    pour une bonne raison, et l'on croirait avoir mesuré une recherche.
    """
    await fake_storage.upload("/espace/doc.txt",
                              b"La procedure de validation exige deux signatures.")
    await pipeline["indexer"].index_workspace("/espace/")
    pipeline["compteur"].appels_unitaires.clear()
    pipeline["compteur"].appels_groupes.clear()
    return pipeline


def _message(corps: str = "Quelle est la procédure de validation ?") -> IncomingMessage:
    return IncomingMessage(
        user_id="@agent-education.gouv.fr:agent.tchap.gouv.fr",
        conversation_id="!mesure:test.local",
        body=corps,
        conversation_type=ConversationType.PRIVATE,
        message_id="$evt_mesure",
        display_name="Agent Test",
    )


# ── Le compteur lui-même ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_le_compteur_compte(pipeline):
    """Un compteur qu'on n'a jamais vu bouger ne mesure rien.

    Ce dépôt a déjà trouvé deux garde-fous verts pour de mauvaises raisons ; un
    instrument de mesure mérite la même vérification qu'une garde.
    """
    c = pipeline["compteur"]
    assert c.total == 0
    await c.embed_text("un texte")
    assert c.total == 1
    await c.embed_texts(["a", "b", "c"])
    assert c.total == 2, "un appel groupé est UN aller-retour, pas trois"
    assert c.appels_groupes == [3]


# ── L'état actuel, épinglé ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_index_vide_ne_coute_AUCUN_embedding(pipeline):
    """Bon comportement, epingle pour qu'il ne se perde pas.

    Le retriever court-circuite avant de vectoriser quand le store est vide : inutile
    de payer un aller-retour reseau pour interroger un index qui ne contient rien.

    Ce test a d'abord ete ecrit a l'envers — il attendait un embedding — et c'est le
    code qui avait raison.
    """
    c = pipeline["compteur"]
    await pipeline["retriever"].retrieve("une question", k=3)
    assert c.total == 0, "un index vide ne doit coûter aucun appel réseau"


@pytest.mark.asyncio
async def test_une_recherche_documentaire_coute_un_embedding(pipeline, indexe):
    """Le cas nominal : une requête, un vecteur.

    C'est le comportement que L3.5 veut garantir pour un TOUR entier. Il est déjà vrai
    pour une recherche isolée — le coût vient de leur multiplication en amont.
    """
    c = pipeline["compteur"]
    await pipeline["retriever"].retrieve("une question", k=3)
    assert c.total == 1, f"appels : {c.appels_unitaires}"


@pytest.mark.asyncio
async def test_deux_reformulations_coutent_deux_embeddings(pipeline, indexe):
    """LE mécanisme que L3.5 vise.

    L'Analyseur produit plusieurs `chunk_queries` — des reformulations de la même
    question. Chacune est vectorisée séparément : le coût d'un tour croît avec le
    nombre de reformulations, alors qu'un seul appel groupé suffirait.

    Ce test ne réclame pas la correction : il chiffre le mécanisme.
    """
    c = pipeline["compteur"]
    for reformulation in ("procédure de validation", "comment valider un dossier"):
        await pipeline["retriever"].retrieve(reformulation, k=3)
    assert c.total == 2
    assert len(c.appels_groupes) == 0, (
        "les reformulations ne sont PAS groupées — c'est là que L3.5 peut gagner"
    )


@pytest.mark.asyncio
async def test_l_indexation_groupe_ses_appels(pipeline, fake_storage):
    """Contre-exemple utile : l'indexation, elle, groupe déjà.

    Elle vectorise des centaines de chunks en quelques appels. Le chemin de la requête
    pourrait faire de même — la brique existe, elle n'est pas employée là.
    """
    await fake_storage.upload("/espace/doc.txt",
                              ("Une procédure de validation. " * 40).encode())
    c = pipeline["compteur"]
    await pipeline["indexer"].index_workspace("/espace/")
    assert c.appels_groupes, "l'indexation doit grouper"
    assert not c.appels_unitaires, (
        "l'indexation ne doit pas vectoriser chunk par chunk"
    )


@pytest.mark.asyncio
async def test_un_tour_complet_phase1(pipeline, fake_storage, fake_messaging, fake_llm):
    """L'état mesuré d'un tour de bout en bout, sur le pipeline phase 1.

    Le nombre est ÉPINGLÉ, pas jugé. S'il change, ce test le dira — et c'est tout ce
    qu'on lui demande avant que L3.5 ne commence.
    """
    from colaig.messaging.handlers import MessageHandler

    await fake_storage.upload("/espace/doc.txt",
                              b"La procedure de validation exige deux signatures.")
    await pipeline["indexer"].index_workspace("/espace/")
    c = pipeline["compteur"]
    c.appels_unitaires.clear()
    c.appels_groupes.clear()

    handler = MessageHandler(fake_messaging, pipeline["resolver"],
                             pipeline["retriever"], pipeline["generator"],
                             fake_storage)
    await handler.handle_message(_message())

    assert c.total <= 2, (
        f"un tour phase 1 consomme {c.total} appels d'embedding : "
        f"{c.appels_unitaires} · groupés {c.appels_groupes}. "
        "Si ce nombre a augmenté, une étape a été ajoutée sur le chemin du message."
    )
