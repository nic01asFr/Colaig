"""
Colaig — un salon nommé n'est pas une conversation directe
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
Un salon créé délibérément — nom « Colaig - Mesure SST », sujet, invitation — a été
résolu en mode **PERSONAL**. Conséquences observées sur le fil :

- `colaig lier colaig-mesure-sst` **n'a pas été intercepté**, car
  `_handle_onboarding_command` est derrière la porte `mode == CHATBOT` ;
- un **espace personnel parasite** a été créé dans le seau, pour un salon qui n'en
  avait pas besoin ;
- `rag_enabled` étant faux en mode personnel, le corpus de 51 documents lié à ce salon
  est resté hors d'atteinte.

La cause tient en une ligne de `_resolve_conversation_type` :

    if len(response.members) == 2:
        return ConversationType.DM

**Tout salon d'équipe commence à deux membres** — l'assistant et la première personne.
La règle du compte les déclare donc tous privés au moment précis où on essaie de les
configurer, c'est-à-dire quand on en a le plus besoin. Ce n'est pas un cas limite :
c'est le premier geste de toute nouvelle équipe.

Le discriminant juste
-----------------------
Une conversation directe n'a **pas de nom**. Un salon nommé a été créé par quelqu'un
qui a écrit ce nom — c'est un acte, pas un décompte. `matrix-nio` expose exactement
cela : `MatrixRoom.is_named`.

Le compte de membres reste utile en second recours, pour les salons sans nom.
"""

from __future__ import annotations

import pytest

from colaig.models import ConversationType


class _Membres:
    def __init__(self, n: int) -> None:
        self.members = [f"@u{i}:exemple.fr" for i in range(n)]


class _Salon:
    def __init__(self, nomme: bool, join_rule: str = "invite") -> None:
        self.is_named = nomme
        self.join_rule = join_rule


class _Client:
    def __init__(self, salon: _Salon, n_membres: int) -> None:
        self._salon = salon
        self._n = n_membres
        self.rooms = {"!salon:exemple.fr": salon}

    async def joined_members(self, room_id: str):
        return _Membres(self._n)


@pytest.fixture
def messagerie():
    from colaig.messaging.matrix import MatrixMessaging

    return MatrixMessaging(homeserver="https://exemple.invalid",
                           username="@colaig:exemple.fr", password="x")


@pytest.mark.asyncio
async def test_un_salon_nomme_a_deux_n_est_pas_un_dm(messagerie):
    """LE défaut du 30/08.

    Le salon d'essai avait un nom, un sujet, et deux membres. Il a été pris pour une
    conversation directe, et la commande de liaison n'a jamais été atteinte.
    """
    messagerie._client = _Client(_Salon(nomme=True), n_membres=2)

    type_ = await messagerie._resolve_conversation_type("!salon:exemple.fr")

    assert type_ != ConversationType.DM, (
        "un salon nommé à deux membres est pris pour une conversation directe : "
        "`colaig lier` n'y sera jamais intercepté"
    )
    assert type_ == ConversationType.PRIVATE


@pytest.mark.asyncio
async def test_une_vraie_conversation_directe_reste_un_dm(messagerie):
    """La correction ne doit pas casser le cas qu'elle protège.

    Une conversation directe n'a pas de nom : le second recours au compte de membres
    la reconnaît toujours.
    """
    messagerie._client = _Client(_Salon(nomme=False), n_membres=2)

    assert await messagerie._resolve_conversation_type("!salon:exemple.fr") \
        == ConversationType.DM


@pytest.mark.asyncio
async def test_un_salon_sans_nom_a_plus_de_deux_n_est_pas_un_dm(messagerie):
    """Comportement d'origine, préservé."""
    messagerie._client = _Client(_Salon(nomme=False), n_membres=5)

    assert await messagerie._resolve_conversation_type("!salon:exemple.fr") \
        != ConversationType.DM


@pytest.mark.asyncio
async def test_un_salon_nomme_et_public_reste_public(messagerie):
    """Le nom écarte le DM ; il ne doit pas écraser la distinction public / privé."""
    messagerie._client = _Client(_Salon(nomme=True, join_rule="public"), n_membres=2)

    assert await messagerie._resolve_conversation_type("!salon:exemple.fr") \
        == ConversationType.PUBLIC
