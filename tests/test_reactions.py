"""
Contrat — le canal sait poser et recevoir des réactions.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.3

Le dessin
---------
Colaig **pose lui-même** les réactions sous chacune de ses réponses. L'utilisateur n'a
donc pas à chercher un emoji dans un sélecteur : il tape sur celle qui est déjà là, ce
qui incrémente le compte.

Cette pose automatique impose la règle centrale de ce fichier : **la réaction de Colaig
ne compte pas**. Elle est déjà présente par construction ; c'est l'ajout d'un tiers qui
porte le signal. Sans ce filtre, chaque réponse produirait quatre retours fantômes —
et le premier chiffre qu'on lirait sur la qualité serait entièrement fabriqué par nous.

Pourquoi un Protocol séparé
-----------------------------
`MessagingProtocol` compte cinq méthodes et **aucune notion de réaction**. Deux issues
étaient possibles :

- l'étendre — mais les réactions ne sont pas une propriété universelle de la
  messagerie : `noop` n'en a pas, un webchat peut ne pas en avoir. Le contrat commun
  mentirait pour la majorité de ses implémentations ;
- un Protocol **séparé** qu'un canal implémente *en plus* s'il en est capable.

C'est la seconde qui a été arbitrée (D51). Le dépôt connaît déjà cet idiome :
`capability_chain` fait exactement cela pour les LLM.
"""
from __future__ import annotations

import pytest

POUCE = "\N{THUMBS UP SIGN}"


class _EvenementReaction:
    """Ce que `matrix-nio` délivre pour une réaction (`nio.ReactionEvent`)."""

    def __init__(self, cle: str = POUCE,
                 reagit_a: str = "$notre-reponse",
                 expediteur: str = "@alice:tchap.gouv.fr") -> None:
        self.key = cle
        self.reacts_to = reagit_a
        self.sender = expediteur
        self.event_id = "$la-reaction"
        self.server_timestamp = 10**13


class _Salon:
    def __init__(self, room_id: str = "!salon:tchap.gouv.fr") -> None:
        self.room_id = room_id

    def user_name(self, uid):
        return "Alice"


def _messagerie_nue():
    from colaig.messaging.matrix import MatrixMessaging

    return MatrixMessaging(homeserver="https://exemple.invalid",
                           username="@colaig:tchap.gouv.fr", password="x")


@pytest.fixture
def messagerie():
    m = _messagerie_nue()
    m._start_time = 0.0
    m._client = object()
    m.suivre_fil("$notre-reponse")          # une réponse que nous avons émise

    recus: list = []
    m.on_reaction(lambda r: recus.append(r) or _rien())
    m._recus = recus
    return m


async def _rien():
    return None


async def _injecter(m, evenement, salon=None):
    await m._on_reaction(salon or _Salon(), evenement)
    return m._recus


# ── La capacité ─────────────────────────────────────────────────────────────


def test_matrix_declare_la_capacite_de_reaction():
    """Le canal est reconnaissable **structurellement**, sans drapeau de configuration.

    L'appelant demande `isinstance(messaging, ReactionProtocol)` ; un canal qui ne sait
    pas réagir se contente de ne pas porter ces méthodes.
    """
    from colaig.protocols import ReactionProtocol

    assert isinstance(_messagerie_nue(), ReactionProtocol)


def test_un_canal_sans_reactions_ne_ment_pas():
    """Le contraire du précédent, et c'est lui qui justifie le Protocol séparé.

    Si les réactions avaient été ajoutées à `MessagingProtocol`, tout canal aurait dû
    porter deux méthodes vides pour rester conforme — et l'appelant n'aurait eu aucun
    moyen de savoir laquelle répond vraiment.
    """
    from colaig.protocols import MessagingProtocol, ReactionProtocol

    class CanalMuet:
        async def connect(self): ...
        async def run(self): ...
        async def send(self, conversation_id, text, formatted=None, is_status=False): ...
        async def send_typing(self, conversation_id, typing=True, timeout=10000): ...
        def on_message(self, callback): ...

    muet = CanalMuet()
    assert isinstance(muet, MessagingProtocol)
    assert not isinstance(muet, ReactionProtocol)


# ── Réception ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_reaction_d_autrui_arrive_jusqu_au_rappel(messagerie):
    recus = await _injecter(messagerie, _EvenementReaction())
    assert len(recus) == 1
    r = recus[0]
    assert r.emoji == POUCE
    assert r.message_id == "$notre-reponse"
    assert r.user_id == "@alice:tchap.gouv.fr"
    assert r.conversation_id == "!salon:tchap.gouv.fr"


@pytest.mark.asyncio
async def test_NOTRE_PROPRE_reaction_ne_compte_PAS(messagerie):
    """La règle centrale du lot.

    Colaig pose les réactions sous chaque réponse. Si sa propre pose remontait comme un
    retour, chaque réponse s'auto-attribuerait quatre signaux — et la mesure de qualité
    ne mesurerait que nous-mêmes.
    """
    e = _EvenementReaction(expediteur="@colaig:tchap.gouv.fr")
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_une_reaction_sur_le_message_D_UN_AUTRE_est_ignoree(messagerie):
    """Deux collègues qui se félicitent dans un salon ne parlent pas de Colaig.

    Sans ce filtre, tout pouce levé du salon deviendrait un retour sur nos réponses.
    """
    e = _EvenementReaction(reagit_a="$message-de-bob")
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_une_reaction_trop_VIEILLE_est_ignoree(messagerie):
    """Au démarrage, le serveur rejoue l'historique.

    Sans ce garde, chaque redémarrage réenregistrerait tous les retours déjà comptés —
    et le fichier de retours gonflerait d'autant à chaque relance.
    """
    e = _EvenementReaction()
    e.server_timestamp = 0
    messagerie._start_time = 10**12
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_sans_rappel_pose_rien_n_explose(messagerie):
    """La réception ne doit pas dépendre de l'ordre de câblage."""
    messagerie._reaction_callback = None
    await messagerie._on_reaction(_Salon(), _EvenementReaction())


# ── Émission ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reagir_emet_bien_un_m_reaction():
    """La forme de l'événement est imposée par la spécification Matrix.

    `m.relates_to` avec `rel_type: m.annotation` et `key` : c'est ce qui fait qu'un
    client affiche la réaction **sous** le message plutôt qu'à la suite.
    """
    m = _messagerie_nue()
    envois: list = []

    class _Client:
        async def room_send(self, **kw):
            envois.append(kw)
            return type("R", (), {"event_id": "$r"})()

    m._client = _Client()
    await m.reagir("!salon:tchap.gouv.fr", "$notre-reponse", POUCE)

    assert len(envois) == 1
    assert envois[0]["message_type"] == "m.reaction"
    rel = envois[0]["content"]["m.relates_to"]
    assert rel["rel_type"] == "m.annotation"
    assert rel["event_id"] == "$notre-reponse"
    assert rel["key"] == POUCE


@pytest.mark.asyncio
async def test_une_pose_en_echec_ne_casse_pas_la_reponse():
    """Poser une réaction est un CONFORT ; la réponse est le produit.

    Un serveur qui refuse `m.reaction` ne doit pas faire échouer le tour de
    conversation qui vient d'aboutir.
    """
    m = _messagerie_nue()

    class _Client:
        async def room_send(self, **kw):
            raise RuntimeError("le serveur refuse les réactions")

    m._client = _Client()
    await m.reagir("!salon", "$x", POUCE)      # ne lève pas


@pytest.mark.asyncio
async def test_on_ne_reagit_pas_a_un_message_inconnu():
    """Sans identifiant de message, il n'y a rien à annoter."""
    m = _messagerie_nue()
    envois: list = []

    class _Client:
        async def room_send(self, **kw):
            envois.append(kw)

    m._client = _Client()
    await m.reagir("!salon", "", POUCE)
    assert envois == []


# ── Le dernier message envoyé ───────────────────────────────────────────────


def test_le_dernier_message_envoye_est_retrouvable_par_salon():
    """Pour poser une réaction sous SA PROPRE réponse, il faut son identifiant.

    `send()` rend `None` et le remonter modifierait `MessagingProtocol` — ce que §5
    interdit sans arbitrage. L'identifiant est donc exposé par le Protocol *optionnel*,
    là où il ne coûte rien aux canaux qui ne savent pas réagir.
    """
    m = _messagerie_nue()
    assert m.dernier_message_envoye("!salon") == ""

    m._retenir_envoi("!salon", "$un")
    m._retenir_envoi("!salon", "$deux")
    m._retenir_envoi("!autre", "$trois")

    assert m.dernier_message_envoye("!salon") == "$deux"
    assert m.dernier_message_envoye("!autre") == "$trois"


@pytest.mark.asyncio
async def test_un_message_de_STATUT_n_est_pas_le_dernier_message():
    """« Je cherche… » n'est pas une réponse : on ne pose pas de retour dessous.

    Même exclusion que pour les racines de fil (L3.2).
    """
    m = _messagerie_nue()
    ids = iter(["$reponse", "$statut"])

    class _Client:
        async def room_send(self, **kw):
            return type("R", (), {"event_id": next(ids)})()

    m._client = _Client()
    m._do_auto_trust = lambda: None

    await m.send("!salon", "voici la réponse")
    await m.send("!salon", "je cherche…", is_status=True)

    assert m.dernier_message_envoye("!salon") == "$reponse"


def test_le_registre_des_envois_est_borne():
    """Un bot qui tourne des mois ne doit pas accumuler un identifiant par salon vu.

    Même borne que le registre des fils (L3.2), pour la même raison.
    """
    from colaig.messaging.matrix import _MAX_SALONS_SUIVIS

    m = _messagerie_nue()
    for i in range(_MAX_SALONS_SUIVIS + 50):
        m._retenir_envoi(f"!salon{i}", f"$evt{i}")

    assert len(m._derniers_envois) <= _MAX_SALONS_SUIVIS
    assert m.dernier_message_envoye("!salon0") == "", "le plus ancien doit sortir"


# ── Le branchement auprès de nio ────────────────────────────────────────────


def test_le_rappel_reaction_est_enregistre():
    """Treizième vérification explicite du motif « écrit et non branché »."""
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "messaging" / "matrix.py").read_text(encoding="utf-8"))
    assert "_on_reaction" in source
    assert "ReactionEvent" in source, "le rappel doit être enregistré auprès de nio"
