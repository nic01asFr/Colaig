"""
Colaig — l'avertissement de déchiffrement ne se répète pas à chaque redémarrage
(campagne d'usage réel du 29/08/2026, défaut B).

Ce que la campagne a montré
-----------------------------
**22 avertissements « je ne parviens pas à déchiffrer » pour 46 messages** dans une
seule conversation — près d'un message sur deux.

La retenue existante (`_salons_prevenus_indechiffrable`) est juste et ne suffit pas :
elle vit **en mémoire de processus**. Six redéploiements dans la journée ont donc
produit six avertissements, chacun déclenché par la **relecture de l'historique
chiffré** au démarrage — des messages vieux de plusieurs heures, que l'utilisateur
n'attendait plus.

En CrashLoopBackOff, ce comportement remplit le salon d'un utilisateur.

La correction
---------------
Les quatre autres rappels de `matrix.py` écartent déjà les événements antérieurs au
démarrage (`_STALE_MESSAGE_SECONDS`). Celui-ci ne le faisait pas. Un message illisible
**ancien** est de l'historique : le signaler n'apprend rien à personne, puisque
personne n'attend de réponse. Un message illisible **frais** reste signalé — c'est
tout l'objet du traitement.
"""

from __future__ import annotations

import time

import pytest


class _EvenementIllisible:
    """Ce que `matrix-nio` délivre quand le déchiffrement échoue."""

    def __init__(self, horodatage_ms: float | None = None) -> None:
        self.sender = "@quelqu-un:exemple.fr"
        self.session_id = "session-inconnue"
        self.event_id = "$evenement"
        if horodatage_ms is not None:
            self.server_timestamp = horodatage_ms


class _Salon:
    def __init__(self, room_id: str = "!salon:exemple.fr") -> None:
        self.room_id = room_id


@pytest.fixture
def messagerie():
    from colaig.messaging.matrix import MatrixMessaging

    return MatrixMessaging(homeserver="https://exemple.invalid",
                           username="@colaig:exemple.fr", password="x")


@pytest.fixture
def envois(messagerie):
    captes = []

    async def _capter(conversation_id, texte, **kwargs):
        captes.append(conversation_id)

    messagerie.send = _capter
    return captes


@pytest.mark.asyncio
async def test_un_message_illisible_ancien_ne_previent_personne(messagerie, envois):
    """LE test de la campagne : la relecture de l'historique au démarrage.

    Six redémarrages ont produit six avertissements sur des messages vieux de
    plusieurs heures. Personne n'attendait de réponse à ceux-là.
    """
    vieux = (messagerie._start_time - 3600) * 1000
    await messagerie._on_undecryptable(_Salon(), _EvenementIllisible(vieux))

    assert envois == [], "un message d'historique a déclenché un avertissement"


@pytest.mark.asyncio
async def test_un_message_illisible_frais_previent_toujours(messagerie, envois):
    """La correction ne doit pas rendre le traitement muet.

    Sans ce test, écarter l'ancien pourrait écarter tout court — et l'utilisateur
    retrouverait l'assistant silencieux que D34 avait relevé.
    """
    maintenant = time.time() * 1000
    await messagerie._on_undecryptable(_Salon(), _EvenementIllisible(maintenant))

    assert envois == ["!salon:exemple.fr"]


@pytest.mark.asyncio
async def test_un_evenement_sans_horodatage_previent_encore(messagerie, envois):
    """Prudence : l'absence d'horodatage ne vaut pas « ancien ».

    Un défaut par excès de silence est le plus coûteux ici — c'est celui qui a motivé
    le traitement à l'origine.
    """
    await messagerie._on_undecryptable(_Salon(), _EvenementIllisible(None))

    assert envois == ["!salon:exemple.fr"]


@pytest.mark.asyncio
async def test_la_retenue_par_salon_tient_toujours(messagerie, envois):
    """La garde d'origine reste en vigueur : un seul avertissement par salon."""
    maintenant = time.time() * 1000
    for _ in range(5):
        await messagerie._on_undecryptable(_Salon(), _EvenementIllisible(maintenant))

    assert len(envois) == 1
