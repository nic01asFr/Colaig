"""
Contrat `MessagingProtocol` — L1.2.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.2

Même principe qu'en L1.1 : **une seule suite, exécutée contre chaque implémentation**,
`FakeMessaging` compris. Une doublure qui ne passe pas le contrat des vraies fait
mesurer autre chose que la production.

Ce que ce lot a corrigé dans la doublure
----------------------------------------
`FakeMessaging`, écrit au lot L0.4, divergeait du Protocol sur deux points — et c'est
ce contrat qui les a révélés :

1. `send()` acceptait un `reply_to` **qui n'existe nulle part** : ni dans
   `MessagingProtocol`, ni dans `matrix.py`, ni dans `webchat.py`. Et il omettait
   `is_status`, qui rend un message en `m.notice` sur Tchap. Une doublure plus
   permissive que le contrat laisse écrire des appels que la production refuse.
2. `run()` retournait immédiatement. Le Protocol dit « boucle d'écoute **infinie** », et
   `NoopMessaging` comme `WebChatMessaging` bouclent effectivement. Un test écrit contre
   une doublure complaisante passe, puis pend en production.

Ce que le contrat vérifie, et ce qu'il ne peut pas vérifier
-----------------------------------------------------------
La livraison n'est observable que sur les backends qui exposent leurs destinataires.
`NoopMessaging` **jette tout par construction** — c'est son objet. Pour lui, le contrat
se limite donc à la conformité de signature et à l'absence de levée. C'est dit ici
plutôt que masqué par une assertion creuse.

| backend | signature | boucle | livraison |
|---|---|---|---|
| `fake` | ✅ | ✅ | ✅ |
| `noop` | ✅ | ✅ | — jette par construction |
| `webchat` | ✅ | ✅ | ✅ via une WebSocket factice |
| `matrix` | ⏭️ homeserver requis |
"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from colaig.messaging.noop import NoopMessaging
from colaig.messaging.webchat import WebChatMessaging
from colaig.models import ConversationType, IncomingMessage
from colaig.protocols import MessagingProtocol
from tests.fakes import FakeMessaging

pytestmark = pytest.mark.asyncio

SALON = "!salon-de-test:tchap.gouv.fr"


class WebSocketFactice:
    """Juste assez de WebSocket pour observer ce que `webchat` envoie."""

    def __init__(self) -> None:
        self.recus: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.recus.append(json.loads(payload))


def _fabrique_fake():
    m = FakeMessaging()
    return m, lambda: [e["text"] for e in m.envois]


def _fabrique_noop():
    return NoopMessaging(), None  # jette tout : rien à observer


def _fabrique_webchat():
    m = WebChatMessaging()
    ws = WebSocketFactice()
    # `handle_websocket()` exige une vraie WebSocket FastAPI ; on enregistre donc la
    # connexion directement. C'est le seul raccourci du fichier, et il est ici parce
    # que l'alternative serait de ne pas tester la livraison du tout.
    m._connections[SALON].append(ws)
    return m, lambda: [
        e["text"] for e in ws.recus if e.get("type") == "chat_message"
    ]


def _fabrique_matrix():
    pytest.skip("matrix : homeserver et compte bot requis (MATRIX_*)")


FABRIQUES = {
    "fake": _fabrique_fake,
    "noop": _fabrique_noop,
    "webchat": _fabrique_webchat,
    "matrix": _fabrique_matrix,
}


@pytest.fixture(params=list(FABRIQUES), ids=list(FABRIQUES))
def canal(request):
    """(implémentation, observateur|None) — l'observateur rend les textes livrés."""
    return FABRIQUES[request.param]()


# ── Conformité de signature ─────────────────────────────────────────────────


async def test_signature_conforme_au_protocole(canal):
    """Les paramètres déclarés par le Protocol doivent être acceptés, nommés ainsi.

    Un appelant écrit `send(conv, texte, is_status=True)`. Si une implémentation
    nomme ce paramètre autrement, l'appel lève `TypeError` **à l'exécution**, dans
    une boucle de messagerie, en production. Le vérifier statiquement coûte moins cher.
    """
    messagerie, _ = canal
    for nom_methode in ("connect", "run", "send", "send_typing", "on_message"):
        attendue = inspect.signature(getattr(MessagingProtocol, nom_methode))
        reelle = inspect.signature(getattr(type(messagerie), nom_methode))
        manquants = [
            p
            for p in attendue.parameters
            if p not in ("self", "kwargs") and p not in reelle.parameters
        ]
        accepte_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in reelle.parameters.values()
        )
        assert not manquants or accepte_kwargs, (
            f"{type(messagerie).__name__}.{nom_methode} n'accepte pas {manquants} — "
            f"attendu {attendue}, obtenu {reelle}"
        )


# ── Comportement ────────────────────────────────────────────────────────────


async def test_connect_ne_leve_pas(canal):
    messagerie, _ = canal
    await messagerie.connect()


async def test_send_accepte_la_forme_complete(canal):
    """Les quatre paramètres du Protocol, dans leur forme nommée."""
    messagerie, _ = canal
    await messagerie.send(SALON, "bonjour")
    await messagerie.send(SALON, "gras", formatted="<b>gras</b>")
    await messagerie.send(SALON, "indexation en cours", is_status=True)


async def test_send_typing_accepte_la_forme_complete(canal):
    messagerie, _ = canal
    await messagerie.send_typing(SALON, True)
    await messagerie.send_typing(SALON, typing=False, timeout=5000)


async def test_livraison_observable(canal):
    """Ce qui est envoyé arrive — là où c'est observable."""
    messagerie, observateur = canal
    if observateur is None:
        pytest.skip(
            f"{type(messagerie).__name__} n'expose aucun destinataire "
            "(NoopMessaging jette par construction)"
        )
    await messagerie.send(SALON, "premier")
    await messagerie.send(SALON, "second")
    assert observateur() == ["premier", "second"]


async def test_on_message_accepte_un_callback(canal):
    """`on_message` est **synchrone** et enregistre — il ne consomme rien."""
    messagerie, _ = canal

    async def rappel(message: IncomingMessage) -> None:
        pass

    resultat = messagerie.on_message(rappel)
    assert resultat is None, "on_message ne doit rien retourner"


async def test_run_boucle_et_reste_annulable(canal):
    """« Boucle d'écoute infinie », dit le Protocol — et elle doit rester annulable.

    Les deux moitiés comptent. Une boucle qui retourne d'elle-même arrête Colaig en
    silence ; une boucle inannulable transforme l'arrêt en `kill -9`.
    """
    messagerie, _ = canal

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(messagerie.run(), timeout=0.2)

    tache = asyncio.create_task(messagerie.run())
    await asyncio.sleep(0.05)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache


# ── Réception ───────────────────────────────────────────────────────────────


async def test_le_callback_recoit_un_incoming_message():
    """Contrat de réception, vérifiable sur la doublure seule.

    Les implémentations réelles fabriquent l'`IncomingMessage` depuis leur transport
    (événement Matrix, trame WebSocket) : le vérifier exigerait ce transport. Ce qui
    est fixé ici, c'est le **type** que le callback reçoit — `handlers.py` en dépend.
    """
    m = FakeMessaging()
    recus: list[IncomingMessage] = []

    async def rappel(message: IncomingMessage) -> None:
        recus.append(message)

    m.on_message(rappel)
    await m.injecter(
        IncomingMessage(
            user_id="@jean:tchap.gouv.fr",
            conversation_id=SALON,
            body="quelles sont les procédures ?",
            conversation_type=ConversationType.DM,
        )
    )

    assert len(recus) == 1
    message = recus[0]
    assert isinstance(message, IncomingMessage)
    assert message.conversation_id == SALON
    assert message.body == "quelles sont les procédures ?"
