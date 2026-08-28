"""
Contrat — les fils Matrix, et la mention qui décide de répondre.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.2

Le critère du lot
------------------
« Un fil ouvert sur une réponse du bot est suivi sans nouvelle mention. »

C'est l'usage réel : quelqu'un pose une question, Colaig répond, et la conversation
continue dans le fil. Exiger une mention à chaque tour rendrait le fil inutile — autant
écrire dans le salon.

Le défaut mesuré, et il est plus grave qu'une gêne
----------------------------------------------------
La décision de répondre en salon se prend ainsi, aujourd'hui :

    bot_display = self._username.split(":")[0].lstrip("@")     # « colaig »
    if bot_display not in event.body and self._username not in event.body:
        return

C'est une **recherche de sous-chaîne dans le corps du message**. Donc :

- « il faudrait demander à colaig » **déclenche le bot**, alors que personne ne
  l'appelle. Dans un salon actif, il répond à des messages qui parlent de lui ;
- et le mot est le **localpart** de l'identifiant, donc n'importe quelle occurrence
  suffit — y compris dans une URL, un nom de fichier, ou le mot cité par un tiers.

Matrix a un champ prévu pour cela depuis la version 1.7 : `m.mentions.user_ids`, que
les clients renseignent quand l'utilisateur pose une vraie mention. C'est une
**déclaration d'intention**, pas une coïncidence de vocabulaire.

Pourquoi la sous-chaîne est CONSERVÉE en repli
------------------------------------------------
Tous les clients ne posent pas `m.mentions` — les anciens, les ponts, les bots. La
retirer d'un coup rendrait Colaig sourd à ces clients, ce qui serait une régression
franche pour corriger un excès de zèle. Elle devient donc le **repli**, et la mention
native fait foi quand elle existe.
"""
from __future__ import annotations

import pytest

from colaig.models import ConversationType


class _Evenement:
    """Ce que `matrix-nio` délivre pour un message texte."""

    def __init__(self, corps: str, expediteur: str = "@alice:tchap.gouv.fr",
                 contenu: dict | None = None, event_id: str = "$evt") -> None:
        self.body = corps
        self.sender = expediteur
        self.event_id = event_id
        self.server_timestamp = 10**13          # très récent, jamais « trop vieux »
        self.source = {"content": {"body": corps, **(contenu or {})}}


class _Salon:
    def __init__(self, room_id: str = "!salon:tchap.gouv.fr") -> None:
        self.room_id = room_id

    def user_name(self, uid):
        return "Alice"


@pytest.fixture
def messagerie(monkeypatch):
    """Une messagerie Matrix en salon PUBLIC — le cas où la mention décide."""
    from colaig.messaging.matrix import MatrixMessaging

    m = MatrixMessaging(homeserver="https://exemple.invalid",
                        username="@colaig:tchap.gouv.fr", password="x")
    m._start_time = 0.0
    # `_on_room_message` sort immediatement si le client nio est absent. On en pose un
    # factice : ce lot ne teste pas la connexion, il teste la REGLE DE DECISION.
    m._client = object()

    async def _salon(_room_id):
        return ConversationType.PUBLIC

    monkeypatch.setattr(m, "_resolve_conversation_type", _salon)
    recus: list = []
    m.on_message(lambda msg: recus.append(msg) or _rien())
    m._recus = recus
    return m


async def _rien():
    return None


async def _injecter(m, evenement, salon=None):
    await m._on_room_message(salon or _Salon(), evenement)
    return m._recus


# ── La mention native ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_mention_native_declenche_le_bot(messagerie):
    """`m.mentions.user_ids` est une déclaration d'intention, pas une coïncidence."""
    e = _Evenement("Quel est le seuil de procédure formalisée ?",
                   contenu={"m.mentions": {"user_ids": ["@colaig:tchap.gouv.fr"]}})
    assert len(await _injecter(messagerie, e)) == 1


@pytest.mark.asyncio
async def test_une_mention_native_d_UN_AUTRE_ne_declenche_pas(messagerie):
    """Mentionner un collègue ne doit pas réveiller l'assistant."""
    e = _Evenement("peux-tu regarder ?",
                   contenu={"m.mentions": {"user_ids": ["@bob:tchap.gouv.fr"]}})
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_PARLER_du_bot_ne_le_declenche_PAS(messagerie):
    """LE défaut. « il faudrait demander à colaig » n'est pas une convocation.

    Le champ `m.mentions` est présent et ne nomme pas le bot : l'intention est donc
    déclarée, et elle dit non. La sous-chaîne du corps ne doit pas la contredire.
    """
    e = _Evenement("il faudrait demander à colaig ce qu'il en pense",
                   contenu={"m.mentions": {"user_ids": []}})
    assert await _injecter(messagerie, e) == [], (
        "un message qui PARLE du bot a déclenché une réponse"
    )


@pytest.mark.asyncio
async def test_sans_m_mentions_le_repli_par_le_corps_fonctionne(messagerie):
    """Tous les clients ne posent pas `m.mentions` — anciens clients, ponts, bots.

    Retirer le repli d'un coup rendrait Colaig sourd à ces clients : une régression
    franche pour corriger un excès de zèle.
    """
    e = _Evenement("@colaig:tchap.gouv.fr quelle est la procédure ?")
    assert len(await _injecter(messagerie, e)) == 1


# ── Les fils ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_message_de_fil_porte_sa_racine(messagerie):
    """Sans la racine, un fil est indiscernable du salon : on ne peut ni le suivre,
    ni y répondre au bon endroit.
    """
    e = _Evenement("et pour les marchés de travaux ?",
                   contenu={"m.mentions": {"user_ids": ["@colaig:tchap.gouv.fr"]},
                            "m.relates_to": {"rel_type": "m.thread",
                                             "event_id": "$racine"}})
    recus = await _injecter(messagerie, e)
    assert recus and recus[0].thread_root == "$racine"


@pytest.mark.asyncio
async def test_une_REPONSE_RICHE_n_est_pas_un_fil(messagerie):
    """`m.in_reply_to` sans `rel_type` est une citation, pas un fil.

    Les confondre ferait suivre comme un fil toute réponse citée du salon — et le bot
    s'inviterait dans des échanges qui ne le concernent pas.
    """
    e = _Evenement("@colaig oui",
                   contenu={"m.relates_to": {"m.in_reply_to": {"event_id": "$autre"}}})
    recus = await _injecter(messagerie, e)
    assert recus and recus[0].is_reply is True
    assert recus[0].thread_root == ""


# ── LE critère du lot ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_fil_ouvert_sur_une_REPONSE_DU_BOT_est_suivi_sans_mention(messagerie):
    """LE critère : « un fil ouvert sur une réponse du bot est suivi sans mention ».

    Exiger une mention à chaque tour rendrait le fil inutile — autant écrire dans le
    salon. Le bot doit reconnaître les fils qu'il a lui-même ouverts.
    """
    messagerie.suivre_fil("$ma_reponse")
    e = _Evenement("et si le marché dépasse le seuil ?",
                   contenu={"m.relates_to": {"rel_type": "m.thread",
                                             "event_id": "$ma_reponse"}})
    assert len(await _injecter(messagerie, e)) == 1, (
        "un fil ouvert sur une réponse du bot doit être suivi sans nouvelle mention"
    )


@pytest.mark.asyncio
async def test_un_fil_QUELCONQUE_n_est_pas_suivi(messagerie):
    """Le bot ne s'invite pas dans les fils des autres.

    C'est la contrepartie du test précédent : sans elle, « suivre les fils »
    reviendrait à répondre à tout, et l'on aurait remplacé un excès de zèle par un
    autre.
    """
    e = _Evenement("on se voit demain ?",
                   contenu={"m.relates_to": {"rel_type": "m.thread",
                                             "event_id": "$fil_entre_collegues"}})
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_le_registre_des_fils_est_borne(messagerie):
    """Un processus qui tourne des semaines ne peut pas retenir tous ses fils.

    Même exigence que le verrou d'historique et que la retenue des messages
    indéchiffrables : une structure qui ne croît jamais finit par tenir la mémoire.
    """
    for i in range(2000):
        messagerie.suivre_fil(f"$fil{i}")
    assert len(messagerie._fils_suivis) <= 1024, (
        f"{len(messagerie._fils_suivis)} fils retenus — le registre ne se borne pas"
    )


@pytest.mark.asyncio
async def test_le_fil_le_plus_RECENT_survit_a_la_purge(messagerie):
    """Purger le plus ancien est un choix : c'est le fil actif qu'il faut garder.

    Purger au hasard, ou purger le dernier, ferait perdre la conversation en cours —
    exactement celle que l'utilisateur est en train d'écrire.
    """
    for i in range(2000):
        messagerie.suivre_fil(f"$fil{i}")
    assert "$fil1999" in messagerie._fils_suivis


# ── En message direct, la mention n'a jamais été requise ────────────────────


@pytest.mark.asyncio
async def test_en_DM_aucune_mention_n_est_requise(messagerie, monkeypatch):
    """Comportement existant, épinglé pour qu'une refonte des mentions ne le casse pas."""
    async def _dm(_room_id):
        return ConversationType.DM

    monkeypatch.setattr(messagerie, "_resolve_conversation_type", _dm)
    assert len(await _injecter(messagerie, _Evenement("bonjour"))) == 1


# ── Le branchement : sans lui, la fonctionnalite est inerte ─────────────────


class _ClientQuiEnvoie:
    """Le minimum de `matrix-nio` pour eprouver `send`."""

    def __init__(self, event_id: str = "$ma_reponse") -> None:
        self.event_id = event_id
        self.envois: list = []

    async def room_send(self, **kwargs):
        self.envois.append(kwargs)
        return type("Reponse", (), {"event_id": self.event_id})()


@pytest.mark.asyncio
async def test_repondre_ENREGISTRE_le_fil(messagerie):
    """LE branchement. Sans lui, `suivre_fil` existerait sans que rien ne l'appelle.

    C'est le motif « ecrit et non branche » que ce depot a trouve neuf fois — dont une
    fois sur le filtre de masquage des secrets, installe au mauvais endroit et ne
    protegeant aucun module.
    """
    messagerie._client = _ClientQuiEnvoie("$reponse42")
    messagerie._do_auto_trust = lambda: None

    await messagerie.send("!salon:tchap.gouv.fr", "Le seuil est de 25 000 euros.")

    assert "$reponse42" in messagerie._fils_suivis, (
        "la reponse du bot doit devenir une racine de fil suivable"
    )


@pytest.mark.asyncio
async def test_un_message_de_STATUT_n_ouvre_pas_de_fil(messagerie):
    """Un indicateur de progression n'est pas une reponse.

    Ouvrir un fil dessus n'aurait pas de sens, et cela remplirait le registre borne
    avec des racines sans interet — chassant les vraies conversations.
    """
    messagerie._client = _ClientQuiEnvoie("$statut")
    messagerie._do_auto_trust = lambda: None

    await messagerie.send("!salon:tchap.gouv.fr", "Je cherche…", is_status=True)

    assert "$statut" not in messagerie._fils_suivis


@pytest.mark.asyncio
async def test_le_cycle_COMPLET_repondre_puis_suivre(messagerie):
    """De bout en bout : le bot repond, quelqu'un ouvre un fil, le bot suit.

    Les tests unitaires peuvent chacun passer sans que la chaine tienne. Celui-ci
    n'ajoute aucune regle — il verifie que les deux moities se rejoignent.
    """
    messagerie._client = _ClientQuiEnvoie("$reponse_du_bot")
    messagerie._do_auto_trust = lambda: None
    await messagerie.send("!salon:tchap.gouv.fr", "Le seuil est de 25 000 euros.")

    messagerie._client = object()          # on repasse en reception
    suite = _Evenement("et pour les travaux ?",
                       contenu={"m.relates_to": {"rel_type": "m.thread",
                                                 "event_id": "$reponse_du_bot"}})
    recus = await _injecter(messagerie, suite)
    assert len(recus) == 1 and recus[0].thread_root == "$reponse_du_bot"
