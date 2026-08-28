"""
Contrat — les gestes sont réellement proposés, et les gestes reçus vraiment traités.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.3

Pourquoi ce fichier existe séparément
--------------------------------------
`test_reactions.py` prouve que le canal sait poser et recevoir. `test_retours.py`
prouve que chaque geste a la bonne conséquence. Ni l'un ni l'autre ne prouve que **les
deux sont reliés** — et c'est précisément le défaut que ce dépôt a trouvé neuf fois :
un traitement complet, testé, et que rien n'appelle.

Le câblage est fait dans le CONSTRUCTEUR de `MessageHandler`, pas dans `main.py`. Deux
raisons :

- `main.py` branche `on_message` à **deux endroits** ; un troisième câblage à tenir en
  double se serait désynchronisé ;
- le handler possède déjà `messaging` et `storage`. Câbler là où les pièces sont
  présentes rend l'oubli impossible plutôt qu'improbable.
"""
from __future__ import annotations

import pytest

from colaig.messaging.handlers import MessageHandler
from colaig.models import ConversationType, IncomingMessage, Reaction
from tests.test_handlers import MockGenerator, MockResolver, MockRetriever

SALON = "!salon:tchap.gouv.fr"


class MessagerieQuiReagit:
    """`MessagingProtocol` **et** `ReactionProtocol`."""

    def __init__(self) -> None:
        self.envois: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.rappel_reaction = None
        self._compteur = 0

    async def connect(self): ...

    async def run(self): ...

    async def send(self, conversation_id, text, formatted=None, is_status=False):
        self.envois.append((conversation_id, text))
        if not is_status:
            self._compteur += 1

    async def send_typing(self, conversation_id, typing=True, timeout=10000): ...

    def on_message(self, callback): ...

    # ── ReactionProtocol ────────────────────────────────────────────────

    async def reagir(self, conversation_id, message_id, emoji):
        self.reactions.append((conversation_id, message_id, emoji))

    def dernier_message_envoye(self, conversation_id):
        return f"$evt{self._compteur}" if self._compteur else ""

    def on_reaction(self, callback):
        self.rappel_reaction = callback


class MessagerieMuette:
    """`MessagingProtocol` seul — un canal qui ne sait pas réagir."""

    def __init__(self) -> None:
        self.envois: list[tuple[str, str]] = []

    async def connect(self): ...

    async def run(self): ...

    async def send(self, conversation_id, text, formatted=None, is_status=False):
        self.envois.append((conversation_id, text))

    async def send_typing(self, conversation_id, typing=True, timeout=10000): ...

    def on_message(self, callback): ...


@pytest.fixture
def message() -> IncomingMessage:
    return IncomingMessage(
        user_id="@alice:tchap.gouv.fr",
        conversation_id=SALON,
        body="quel est le seuil de dispense ?",
        conversation_type=ConversationType.PRIVATE,
        message_id="$question",
        display_name="Alice",
    )


def _handler(messagerie, fake_storage, generator=None):
    return MessageHandler(messagerie, MockResolver(), MockRetriever(),
                          generator or MockGenerator(), fake_storage)


# ── Le branchement ──────────────────────────────────────────────────────────


def test_le_gestionnaire_est_branche_au_canal(fake_storage):
    """LE défaut que ce fichier empêche : un traitement écrit et non appelé."""
    m = MessagerieQuiReagit()
    handler = _handler(m, fake_storage)

    assert m.rappel_reaction is not None, "rien n'écoute les réactions"
    assert m.rappel_reaction == handler._retours.traiter


def test_un_canal_SANS_reactions_se_construit_quand_meme(fake_storage):
    """`noop` et un webchat n'ont pas de réactions ; ils doivent rester utilisables."""
    handler = _handler(MessagerieMuette(), fake_storage)
    assert handler._retours is not None


# ── La pose sous la réponse ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_les_quatre_gestes_sont_poses_sous_la_reponse(message, fake_storage):
    from colaig.messaging.retours import GESTES_PROPOSES

    m = MessagerieQuiReagit()
    await _handler(m, fake_storage).handle_message(message)

    assert [e for _, _, e in m.reactions] == list(GESTES_PROPOSES)
    assert {mid for _, mid, _ in m.reactions} == {"$evt1"}, \
        "les gestes doivent viser LA réponse, pas un autre message"


@pytest.mark.asyncio
async def test_le_tour_est_retenu_pour_pouvoir_agir_dessus(message, fake_storage):
    """Sans la question ni la réponse, ➕ et 🔄 n'auraient sur quoi travailler."""
    m = MessagerieQuiReagit()
    handler = _handler(m, fake_storage)
    await handler.handle_message(message)

    tour = handler._retours.retrouver("$evt1")
    assert tour is not None
    assert tour.question == "quel est le seuil de dispense ?"
    assert tour.reponse == "Réponse de test."
    assert tour.espace == "/test/"


@pytest.mark.asyncio
async def test_un_canal_muet_repond_quand_meme(message, fake_storage):
    """La réponse est le produit ; les gestes sont un confort."""
    m = MessagerieMuette()
    await _handler(m, fake_storage).handle_message(message)
    assert len(m.envois) == 1


@pytest.mark.asyncio
async def test_une_pose_en_ECHEC_ne_perd_pas_la_reponse(message, fake_storage):
    """Un homeserver qui refuse `m.reaction` ne doit pas casser le tour."""
    m = MessagerieQuiReagit()

    async def _tombe(*a, **k):
        raise RuntimeError("réactions refusées")

    m.reagir = _tombe
    await _handler(m, fake_storage).handle_message(message)
    assert len(m.envois) == 1


# ── Le geste reçu produit son effet, de bout en bout ────────────────────────


@pytest.mark.asyncio
async def test_de_bout_en_bout_un_pouce_baisse_est_persiste(message, fake_storage):
    """Le parcours complet : question → réponse → geste → fichier dans l'espace."""
    from colaig.messaging.retours import POUCE_BAS, lire_retours

    m = MessagerieQuiReagit()
    await _handler(m, fake_storage).handle_message(message)

    await m.rappel_reaction(Reaction(
        user_id="@bob:tchap.gouv.fr", conversation_id=SALON,
        message_id="$evt1", emoji=POUCE_BAS,
        reaction_id="$geste", horodatage=10**13))

    retours = await lire_retours(fake_storage, "/test/")
    assert len(retours) == 1
    assert retours[0]["question"] == "quel est le seuil de dispense ?"


@pytest.mark.asyncio
async def test_de_bout_en_bout_rejouer_relance_le_pipeline(message, fake_storage):
    """🔄 doit produire une SECONDE réponse à la question d'origine."""
    from colaig.messaging.retours import REJOUER

    m = MessagerieQuiReagit()
    generator = MockGenerator()
    await _handler(m, fake_storage, generator).handle_message(message)
    assert len(m.envois) == 1

    generator.last_query = None
    await m.rappel_reaction(Reaction(
        user_id="@bob:tchap.gouv.fr", conversation_id=SALON,
        message_id="$evt1", emoji=REJOUER,
        reaction_id="$geste", horodatage=10**13))

    assert len(m.envois) == 2
    assert generator.last_query == "quel est le seuil de dispense ?"


@pytest.mark.asyncio
async def test_rejouer_ne_boucle_pas_sur_lui_meme(message, fake_storage):
    """La réponse rejouée propose de nouveau les gestes — un humain doit retaper.

    Ce test épingle l'absence de boucle : un seul geste produit une seule relance.
    """
    from colaig.messaging.retours import REJOUER

    m = MessagerieQuiReagit()
    await _handler(m, fake_storage).handle_message(message)

    geste = Reaction(user_id="@bob:tchap.gouv.fr", conversation_id=SALON,
                     message_id="$evt1", emoji=REJOUER,
                     reaction_id="$geste", horodatage=10**13)
    await m.rappel_reaction(geste)
    await m.rappel_reaction(geste)        # redélivré par le serveur

    assert len(m.envois) == 2, "un geste redélivré ne doit pas répondre deux fois"


# ── Le motif « écrit et non branché », vérifié à la source ──────────────────


def test_les_deux_chemins_de_reponse_proposent_les_gestes():
    """Phase 1 et Phase 2 émettent chacune une réponse.

    N'en câbler qu'une donnerait un produit où le retour dépend de la configuration —
    et l'on conclurait « les utilisateurs ne notent pas » alors qu'on ne leur a rien
    proposé.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "messaging" / "handlers.py").read_text(encoding="utf-8"))
    assert source.count("_proposer_retour(") >= 3, (
        "attendu : la définition + un appel dans chacun des deux chemins de réponse"
    )
