"""
Contrat — un message indéchiffrable ne disparaît pas en silence.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut
---------
Quatre rappels sont enregistrés auprès de `matrix-nio` : invitation, message texte,
audio clair, audio chiffré. **Aucun pour `MegolmEvent`**, que la bibliothèque délivre
quand le déchiffrement échoue.

Un tel message est donc **ignoré sans un mot**. Ce n'est pas théorique : D34 avait relevé
des `undecryptable Megolm event from a unknown device` dans le journal du bot, et noté
qu'un appareil neuf ne lit pas l'historique chiffré. L'utilisateur, lui, voit un
assistant qui ne répond pas — sans savoir pourquoi.

Deux exigences, et la seconde tempère la première
---------------------------------------------------
**1. Le journal doit le dire**, avec le salon et de quoi agir. C'est l'exploitant qui
diagnostique, et un silence ne se diagnostique pas.

**2. Le salon n'est prévenu qu'UNE FOIS.** Un appareil mal apparié peut produire des
dizaines d'événements illisibles : le dire à chaque fois inonderait la conversation, et
un message répété cesse d'être lu. Une fois par salon et par processus suffit à ce que
l'utilisateur cesse de croire l'assistant en panne.
"""
from __future__ import annotations

import logging

import pytest


class _EvenementIllisible:
    """Ce que `matrix-nio` délivre quand le déchiffrement échoue."""

    def __init__(self, sender: str = "@quelqu-un:exemple.fr") -> None:
        self.sender = sender
        self.session_id = "session-inconnue"
        self.event_id = "$evenement"


class _Salon:
    def __init__(self, room_id: str = "!salon:exemple.fr") -> None:
        self.room_id = room_id


@pytest.fixture
def messagerie():
    from colaig.messaging.matrix import MatrixMessaging

    m = MatrixMessaging(homeserver="https://exemple.invalid",
                        username="@colaig:exemple.fr", password="x")
    return m


@pytest.mark.asyncio
async def test_un_message_illisible_est_journalise(messagerie, caplog):
    """Le silence est le défaut : il faut au moins une trace."""
    with caplog.at_level(logging.WARNING):
        await messagerie._on_undecryptable(_Salon(), _EvenementIllisible())

    journal = " ".join(r.getMessage() for r in caplog.records)
    assert "!salon:exemple.fr" in journal, "le salon doit être nommé"
    assert "déchiffr" in journal.lower() or "dechiffr" in journal.lower(), (
        "le motif doit être lisible : c'est un échec de déchiffrement"
    )


@pytest.mark.asyncio
async def test_le_salon_est_prevenu_une_seule_fois(messagerie, fake_messaging):
    """Un appareil mal apparié produit des dizaines d'événements illisibles.

    Le dire à chaque fois inonderait la conversation — et un message répété cesse
    d'être lu, ce qui reviendrait au silence par un autre chemin.
    """
    envois = []

    async def _capter(conversation_id, texte, **kwargs):
        envois.append((conversation_id, texte))

    messagerie.send = _capter

    for _ in range(5):
        await messagerie._on_undecryptable(_Salon(), _EvenementIllisible())

    assert len(envois) == 1, f"{len(envois)} messages envoyés au lieu d'un"
    assert "!salon:exemple.fr" == envois[0][0]


@pytest.mark.asyncio
async def test_deux_salons_sont_prevenus_chacun(messagerie):
    """La retenue est par salon : un salon muet ne doit pas en faire taire un autre."""
    envois = []

    async def _capter(conversation_id, texte, **kwargs):
        envois.append(conversation_id)

    messagerie.send = _capter

    await messagerie._on_undecryptable(_Salon("!a:exemple.fr"), _EvenementIllisible())
    await messagerie._on_undecryptable(_Salon("!b:exemple.fr"), _EvenementIllisible())

    assert sorted(envois) == ["!a:exemple.fr", "!b:exemple.fr"]


@pytest.mark.asyncio
async def test_un_envoi_en_echec_ne_casse_pas_la_boucle(messagerie, caplog):
    """Prévenir est un mieux, pas une obligation.

    Si l'envoi échoue — salon quitté, serveur indisponible — la boucle de réception
    doit continuer. L'inverse transformerait un message illisible en panne du bot.
    """
    async def _casse(conversation_id, texte, **kwargs):
        raise RuntimeError("salon inaccessible")

    messagerie.send = _casse

    with caplog.at_level(logging.WARNING):
        await messagerie._on_undecryptable(_Salon(), _EvenementIllisible())


def test_le_rappel_est_enregistre_aupres_de_nio():
    """Un traitement écrit et non branché ne traite rien.

    Cinquième fois que ce motif est vérifié explicitement dans ce dépôt.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "messaging" / "matrix.py")
                       .read_text(encoding="utf-8"))
    assert "_on_undecryptable" in source
    assert "MegolmEvent" in source, (
        "le rappel doit être enregistré auprès de nio pour `MegolmEvent`"
    )
