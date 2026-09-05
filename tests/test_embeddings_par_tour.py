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
async def test_les_reformulations_coutent_UN_aller_retour(pipeline, indexe):
    """Ce que L3.5 a gagné, et par quel chemin la production passe.

    L'Analyseur produit deux à trois `chunk_queries` — des reformulations de la même
    question. Chacune coûtait son propre aller-retour ; `retrieve_many` les groupe.

    Le test appelle `retrieve_many` et non `retrieve` en boucle, parce que c'est ce que
    `PreExecutionBuilder.execute_retrieval` fait désormais. Mesurer la boucle
    mesurerait un chemin que la production n'emprunte plus.
    """
    c = pipeline["compteur"]
    await pipeline["retriever"].retrieve_many(
        ["procédure de validation", "comment valider un dossier"], k=3)

    assert c.appels_groupes == [2], (
        f"attendu un seul appel groupe de 2 textes, obtenu {c.appels_groupes} "
        f"et {c.appels_unitaires}"
    )
    assert c.total == 1


def test_UN_SEUL_embed_par_tour_est_INATTEIGNABLE_et_voici_pourquoi():
    """Le critère du lot, tel qu'il est écrit, ne peut pas être atteint.

    L'ordre d'un tour est :

        1. `PreExecutionBuilder.build`  vectorise le MESSAGE (behavior, skills, memoire)
        2. Analyseur                    appel LLM -> produit les `search_directives`
        3. `execute_retrieval`          vectorise les REFORMULATIONS

    Les reformulations **n'existent pas** quand le message est vectorisé : elles sont
    produites par l'appel LLM qui sépare les deux étapes. Aucun regroupement ne peut
    donc les réunir en un seul aller-retour.

    L'optimum atteignable est : **un appel pour le message, un appel groupé par famille
    de requêtes** — soit deux à trois, dont AUCUN redondant. C'est ce que le lot livre.

    Ce test épingle la cause, pour qu'on ne repose pas la question dans six mois en
    croyant qu'un réglage a été oublié.
    """
    import inspect

    from colaig.agents.pre_execution import PreExecutionBuilder

    build = inspect.getsource(PreExecutionBuilder.build)
    retrieval = inspect.getsource(PreExecutionBuilder.execute_retrieval)

    assert "embed_text(message.body)" in build, (
        "le message est vectorise dans `build`, avant l'Analyseur"
    )
    assert "search_directives" in retrieval, (
        "les requetes de recherche viennent des directives de l'Analyseur, donc APRES"
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


# ── Le chemin agentique — ce que L3.5 vise vraiment ─────────────────────────


@pytest.fixture
async def memoire(pipeline, fake_storage):
    """Une conversation assez longue pour que la mémoire sémantique s'active.

    En deçà d'`ALWAYS_INCLUDE_RECENT + limit`, `load_relevant_history` rend les derniers
    messages sans rien vectoriser — et le compteur dirait zéro pour une bonne raison.
    """
    from colaig.context.conversation_memory import ConversationMemory

    memoire = ConversationMemory(fake_storage,
                                 embedding_service=pipeline["compteur"])
    # Construit par le VRAI chemin d'ecriture : `save_turn` ne persiste pas
    # l'`existing_history` qu'on lui passe, il y ajoute le tour courant. Lui donner une
    # liste de vingt messages n'en ecrivait que deux — et la selection semantique ne se
    # declenchait pas, faute d'historique. Le compteur disait alors zero pour une bonne
    # raison, et l'on aurait cru avoir mesure un tour.
    historique: list = []
    for i in range(10):
        historique = await memoire.save_turn(
            workspace_path="/espace/", conversation_id="!mesure:test.local",
            user_message=f"question numero {i} sur la validation",
            assistant_response=f"reponse numero {i}",
            existing_history=historique)

    assert len(historique) >= 20, (
        f"historique de {len(historique)} messages : la selection semantique ne se "
        "declenchera pas, et le compteur ne mesurera rien"
    )
    pipeline["compteur"].appels_unitaires.clear()
    pipeline["compteur"].appels_groupes.clear()
    return memoire


@pytest.mark.asyncio
def test_LE_DOUBLON_est_ferme_sur_le_CHEMIN_REEL():
    """LE défaut que L3.5 corrige, épinglé là où il se produisait.

    `PreExecutionBuilder` vectorise le message pour choisir le behavior, les skills et
    la mémoire utilisateur. `ConversationMemory` vectorisait **le même texte** pour
    classer l'historique. Deux allers-retours réseau pour un texte, sur le chemin d'un
    message reçu — donc avant que l'utilisateur voie quoi que ce soit.

    Le handler chargeait l'historique AVANT de construire la carte : le vecteur n'existait
    pas encore quand la mémoire en avait besoin. L'ordre est inversé, et la carte porte
    désormais le vecteur.

    Ce test lit la source parce que le doublon est une propriété de l'ORDRE des étapes,
    que seul un tour de phase 2 complet exercerait. Le mécanisme, lui, est éprouvé
    fonctionnellement par les deux tests qui suivent.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "messaging" / "handlers.py").read_text(encoding="utf-8"))

    assert "query_embedding=(pre_exec.message_embedding" in source, (
        "le handler ne transmet pas le vecteur deja calcule a ConversationMemory"
    )
    assert source.index("_pre_exec_builder.build(") < source.index("load_relevant_history("), (
        "l'historique est charge AVANT la carte : le vecteur n'existe pas encore, "
        "et la memoire le recalculera"
    )


@pytest.mark.asyncio
async def test_la_memoire_accepte_un_vecteur_deja_calcule(pipeline, memoire):
    """Le mécanisme de la correction, épinglé séparément.

    Même forme que pour `Retriever.retrieve` (lot précédent) : l'appelant qui a déjà le
    vecteur le passe, et la couche basse ne le recalcule pas. Le pipeline n'est pas
    dupliqué — c'est le même chemin, avec une entrée de plus.
    """
    c = pipeline["compteur"]
    vecteur = await pipeline["compteur"].embed_text("Quelle est la procédure ?")
    c.appels_unitaires.clear()
    c.appels_groupes.clear()

    await memoire.load_relevant_history(
        "/espace/", "!mesure:test.local", "Quelle est la procédure ?",
        max_messages=6, query_embedding=vecteur)

    assert "Quelle est la procédure ?" not in c.appels_unitaires, (
        "un vecteur fourni doit dispenser de le recalculer"
    )


@pytest.mark.asyncio
async def test_sans_vecteur_fourni_le_comportement_est_INCHANGE(pipeline, memoire):
    """La correction ne doit pas casser l'appelant qui ne passe rien.

    `ConversationMemory` est utilisée ailleurs que dans le pipeline agentique.
    """
    c = pipeline["compteur"]
    resultat = await memoire.load_relevant_history(
        "/espace/", "!mesure:test.local", "Quelle est la procédure ?",
        max_messages=6)

    assert c.appels_unitaires, "sans vecteur fourni, la memoire doit en calculer un"
    assert isinstance(resultat, list) and resultat
