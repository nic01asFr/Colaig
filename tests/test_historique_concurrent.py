"""
Contrat — deux tours simultanés ne se perdent pas dans l'historique.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut
---------
`ConversationMemory.save_turn` fait **charger → ajouter → tronquer → écrire**. Deux
appels concurrents sur la même conversation chargent tous deux le même historique,
ajoutent chacun leur tour, et le second écrase le premier : **un tour disparaît**.

Deux messages envoyés coup sur coup dans un salon suffisent.

Correction d'une analyse antérieure
-------------------------------------
D46 affirmait que `save_turn` réutilisait l'historique **lu avant le tour**, ce qui
aurait ouvert la course sur toute la durée du pipeline. C'est faux : il **recharge**
depuis le stockage, et `existing_history` ne sert qu'au contexte sémantique. La fenêtre
est donc bien plus étroite — mais elle existe, et ce test la ferme.

Pourquoi pas `TaskExecutor`
-----------------------------
D46 proposait de brancher `TaskExecutor`, qui possède une file par conversation. À
l'examen, ce n'est pas la bonne primitive : il sert à exécuter des tâches de **fond**,
avec des poignées de statut et une limite de concurrence. Le chemin interactif n'a besoin
que d'une **exclusion mutuelle sur une lecture-modification-écriture**.

Et surtout, le verrou doit vivre là où vit la course — dans `ConversationMemory`. Le
poser dans le gestionnaire de messages protégerait un appelant et laisserait `save_turn`
exposé pour les autres : le planificateur de tâches l'utilise aussi.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from colaig import paths
from colaig.context.conversation_memory import ConversationMemory
from colaig.context.layers import _sanitize_id
from tests.fakes import FakeStorage


ESPACE = "/espace/"
CONVERSATION = "!salon:exemple.fr"


class StockageQuiRendLaMain(FakeStorage):
    """Un stockage dont la lecture CEDE la main, comme un vrai backend reseau.

    `FakeStorage` ne suspend jamais : `asyncio.gather` y execute les coroutines en
    SERIE, et la course ne se produit pas. Une premiere version de ces tests passait
    donc AVANT tout correctif — elle ne demontrait rien.

    LA MAIN EST RENDUE A L'ECRITURE, pas a la lecture. Ceder avant de lire ne suffit
    pas : le second appel lit alors l'ecriture du premier, deja posee, et rien ne se
    perd. C'est la latence d'ECRITURE qui ouvre la fenetre — A lit, A envoie, B lit
    l'ancien etat pendant que l'envoi de A est en vol, B envoie et ecrase.

    C'est exactement ce qui arrive avec un WebDAV : deux GET aboutissent avant que le
    premier PUT ne soit pose.
    """

    async def upload(self, path: str, content: bytes) -> None:
        await asyncio.sleep(0)
        await super().upload(path, content)


async def _contenu(storage, conversation: str = CONVERSATION) -> list[dict]:
    # L'identifiant est ASSAINI avant de devenir un nom de fichier — lire le chemin brut
    # rendrait une liste vide, et le test echouerait pour une mauvaise raison.
    chemin = paths.conversation_file(ESPACE, _sanitize_id(conversation))
    try:
        return json.loads((await storage.download(chemin)).decode("utf-8"))
    except Exception:
        return []


@pytest.mark.asyncio
async def test_deux_tours_simultanes_survivent_tous_les_deux(fake_storage):
    """LE défaut. Deux messages coup sur coup, et un tour disparaissait."""
    fake_storage = StockageQuiRendLaMain()
    memoire = ConversationMemory(fake_storage)

    await asyncio.gather(
        memoire.save_turn(
            workspace_path=ESPACE, conversation_id=CONVERSATION,
            user_message="première question", assistant_response="première réponse",
            existing_history=[],
        ),
        memoire.save_turn(
            workspace_path=ESPACE, conversation_id=CONVERSATION,
            user_message="seconde question", assistant_response="seconde réponse",
            existing_history=[],
        ),
    )

    contenus = [m.get("content", "") for m in await _contenu(fake_storage)]
    assert "première question" in contenus, "le premier tour a été écrasé"
    assert "seconde question" in contenus, "le second tour a été écrasé"
    assert len(contenus) == 4, f"quatre messages attendus, {len(contenus)} trouvés"


@pytest.mark.asyncio
async def test_dix_tours_simultanes_survivent(fake_storage):
    """Deux suffisent à révéler la course ; dix montrent qu'elle est vraiment fermée."""
    fake_storage = StockageQuiRendLaMain()
    memoire = ConversationMemory(fake_storage)

    await asyncio.gather(*[
        memoire.save_turn(
            workspace_path=ESPACE, conversation_id=CONVERSATION,
            user_message=f"question {i}", assistant_response=f"réponse {i}",
            existing_history=[],
        )
        for i in range(10)
    ])

    contenus = [m.get("content", "") for m in await _contenu(fake_storage)]
    manquants = [i for i in range(10) if f"question {i}" not in contenus]
    assert not manquants, f"tours perdus : {manquants}"


@pytest.mark.asyncio
async def test_deux_conversations_ne_se_bloquent_pas(fake_storage):
    """Un verrou global sérialiserait tout le service.

    Une conversation lente retiendrait alors toutes les autres — ce qui échangerait une
    perte de données contre une panne de débit. Le verrou est donc par conversation.
    """
    fake_storage = StockageQuiRendLaMain()
    memoire = ConversationMemory(fake_storage)
    autre = "!autre-salon:exemple.fr"

    await asyncio.gather(
        memoire.save_turn(workspace_path=ESPACE, conversation_id=CONVERSATION,
                          user_message="ici", assistant_response="ok",
                          existing_history=[]),
        memoire.save_turn(workspace_path=ESPACE, conversation_id=autre,
                          user_message="là", assistant_response="ok",
                          existing_history=[]),
    )

    assert [m["content"] for m in await _contenu(fake_storage)][0] == "ici"
    assert [m["content"] for m in await _contenu(fake_storage, autre)][0] == "là"


@pytest.mark.asyncio
async def test_les_verrous_ne_s_accumulent_pas_sans_fin(fake_storage):
    """Un dictionnaire de verrous qui ne se vide jamais est une fuite lente.

    Une instance servant des milliers de conversations en garderait un par salon vu,
    pour toujours. Le test borne ce que le mécanisme a le droit de retenir.
    """
    fake_storage = StockageQuiRendLaMain()
    memoire = ConversationMemory(fake_storage)
    for i in range(50):
        await memoire.save_turn(
            workspace_path=ESPACE, conversation_id=f"!salon-{i}:exemple.fr",
            user_message="q", assistant_response="r", existing_history=[],
        )

    verrous = getattr(memoire, "_verrous", {})
    assert len(verrous) <= 50, (
        f"{len(verrous)} verrous retenus — le mécanisme doit borner ce qu'il garde"
    )
