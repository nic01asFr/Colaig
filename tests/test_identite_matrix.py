"""
Contrat — l'identité qui décide vient du homeserver, pas de la configuration.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Le défaut
---------
Trois contrôles de `matrix.py` comparaient l'expéditeur d'un événement à
`self._username` — **la valeur tapée dans la configuration** :

    matrix.py  ignorer ses propres messages       event.sender == self._username
    matrix.py  détecter une invitation le visant  event.state_key != self._username
    matrix.py  mention native `m.mentions`        self._username in mentions.user_ids

Or Matrix ne délivre jamais que des MXID complets — `@smoke:agent.tchap.gouv.fr`. Une
configuration portant l'identifiant nu, `smoke`, fait échouer les trois **en silence** :

- le bot **ne reconnaît plus ses propres messages** et peut se répondre à lui-même ;
- il n'accepte plus les invitations qui le visent ;
- il retombe sur la recherche dans le corps du message, moins fiable que `m.mentions`.

Ce n'est pas une hypothèse : l'instance précédente passait `MATRIX_BOT_USERNAME=smoke`,
et c'est ce qui a mis le défaut au jour au moment de brancher Tchap.

La correction
-------------
Le homeserver **dit qui l'on est** — à la connexion (`LoginResponse.user_id`), à la
restauration d'un token (`whoami()`), et dans le fichier de token persisté. Cette
valeur-là fait foi ; la configuration ne sert plus qu'à **se connecter**.

Une identité déduite d'un champ de configuration est une identité qu'une faute de frappe
peut casser sans un message d'erreur. Une identité obtenue du serveur ne se trompe pas.
"""
from __future__ import annotations

import pytest

MXID = "@smoke:agent.tchap.gouv.fr"


def _messagerie(username: str):
    from colaig.messaging.matrix import MatrixMessaging

    return MatrixMessaging(homeserver="https://matrix.agent.tchap.gouv.fr",
                           username=username, password="x")


class _Salon:
    room_id = "!salon:agent.tchap.gouv.fr"

    def user_name(self, uid):
        return "Bot"


class _Evenement:
    def __init__(self, sender: str, corps: str = "bonjour") -> None:
        self.sender = sender
        self.body = corps
        self.event_id = "$evt"
        self.server_timestamp = 10**13
        self.source = {"content": {"body": corps}}


# ── L'identité de référence ─────────────────────────────────────────────────


def test_avant_connexion_on_se_rabat_sur_la_configuration():
    """Le pipeline doit fonctionner en test, sans homeserver.

    Sans ce repli, tout test de réception exigerait une connexion réelle.
    """
    m = _messagerie(MXID)
    assert m.identite == MXID


def test_apres_connexion_c_est_le_HOMESERVER_qui_fait_foi():
    """Même si la configuration portait l'identifiant nu."""
    m = _messagerie("smoke")
    m._retenir_identite(MXID)
    assert m.identite == MXID


def test_une_identite_vide_rendue_par_le_serveur_est_ignoree():
    """Un `whoami` dégradé ne doit pas effacer ce qu'on savait.

    Retenir une chaîne vide ferait comparer `event.sender` à `""` — donc ne jamais
    reconnaître ses propres messages.
    """
    m = _messagerie(MXID)
    m._retenir_identite("")
    assert m.identite == MXID


# ── Ce que le défaut cassait ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_le_bot_reconnait_ses_propres_messages_MALGRE_une_config_nue():
    """LE défaut le plus grave : sans cela, le bot se répond à lui-même.

    `event.sender` vaut toujours `@smoke:agent.tchap.gouv.fr` ; une configuration
    portant `smoke` ne correspondait à rien, et le message du bot était traité comme
    celui d'un tiers.
    """
    m = _messagerie("smoke")          # configuration NUE, comme l'ancienne plateforme
    m._retenir_identite(MXID)
    m._start_time = 0.0
    m._client = object()

    recus: list = []
    m.on_message(lambda msg: recus.append(msg) or _rien())

    await m._on_room_message(_Salon(), _Evenement(sender=MXID))
    assert recus == [], "le bot a traite son propre message comme celui d'un tiers"


@pytest.mark.asyncio
async def test_une_mention_native_est_reconnue_MALGRE_une_config_nue():
    """`m.mentions` porte des MXID complets — jamais un identifiant nu."""
    m = _messagerie("smoke")
    m._retenir_identite(MXID)

    contenu = {"m.mentions": {"user_ids": [MXID]}}
    assert m._nous_concerne(_Evenement(sender="@alice:t.fr"), contenu, "") is True


def test_une_invitation_qui_NOUS_vise_est_reconnue():
    """`state_key` porte le MXID de l'invité."""
    import inspect

    from colaig.messaging.matrix import MatrixMessaging

    source = inspect.getsource(MatrixMessaging._on_invite)
    assert "self.identite" in source, (
        "l'invitation se compare encore a la configuration, pas a l'identite reelle"
    )


# ── Aucune comparaison ne doit rester sur la configuration ──────────────────


def test_plus_aucune_comparaison_d_identite_sur_la_CONFIGURATION():
    """La garde qui empêche le retour en arrière.

    `self._username` reste légitime pour SE CONNECTER — c'est ce qu'on tape. Il ne doit
    plus servir à décider si un événement nous concerne.
    """
    import inspect

    from colaig.messaging.matrix import MatrixMessaging

    for methode in ("_on_room_message", "_on_invite", "_nous_concerne", "_on_reaction",
                    "_on_room_audio", "_on_room_file"):
        source = inspect.getsource(getattr(MatrixMessaging, methode))
        assert "self._username" not in source, (
            f"{methode} compare encore a la configuration au lieu de `self.identite`"
        )


async def _rien():
    return None
