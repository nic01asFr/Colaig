"""
Contrat — l'accueil ne doit pas ouvrir les espaces des autres.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1d

Le raisonnement
---------------
L'invitation est la porte d'entrée voulue : on invite Colaig dans un salon, et selon
qu'il connaît ou non l'interlocuteur, il accueille ou il travaille. Un salon inconnu
tombe en mode `CHATBOT`, avec un espace par défaut sans stockage et `rag_enabled=False`
— posture saine : Colaig ne peut rien lire tant que rien n'est lié.

Deux commandes d'accueil permettent d'en sortir : `colaig créer <nom>` et
`colaig lier <workspace_id>`.

Ce que ces tests ferment
-------------------------
**L'appariement salon → espace EST la frontière d'accès.** `WorkspaceACL` garde les
outils d'administration, la délégation entre espaces et les tâches de fond ; il ne garde
**pas** le chemin conversationnel, où l'appartenance au salon fait foi. C'est cohérent
tant que l'appariement est digne de foi.

`colaig lier` le rendait forgeable, et sans aucun contrôle :

- **sans argument, il énumérait tous les espaces de l'instance** — soit la liste des
  équipes et directions qui utilisent Colaig ;
- **avec un identifiant, il liait n'importe quel salon à n'importe quel espace.**

Deux messages suffisaient donc, depuis n'importe quel salon où l'on peut inviter Colaig,
pour lire le corpus de n'importe quel espace. La cloison multi-tenant — « un dossier, une
instance » — tombait sans qu'aucune garde ne se déclenche.

Ce que ces tests n'exigent pas
-------------------------------
Ils n'exigent pas que l'accueil disparaisse. `colaig créer` reste ouvert : créer son
propre espace est l'objet même de l'accueil, et n'expose rien de personne. C'est
**rejoindre l'espace d'autrui** qui demande d'y être admis.
"""
from __future__ import annotations

import pytest

from colaig.messaging.handlers import MessageHandler
from colaig.models import ConversationType, IncomingMessage, WorkspaceConfig


class _ResolverAvecEspaces:
    """Un résolveur portant deux espaces métier, dont l'intrus n'est membre d'aucun."""

    def __init__(self, espaces) -> None:
        self.workspaces = list(espaces)
        self._default_workspace_id = ""

    async def resolve(self, message):
        from colaig.context.workspace import create_default_workspace
        from colaig.models import ContextMode, WorkspaceContext

        return WorkspaceContext(
            workspace=create_default_workspace(),
            mode=ContextMode.CHATBOT,
            user_id=message.user_id,
        )

    async def register_workspace(self, ws) -> None:
        if ws not in self.workspaces:
            self.workspaces.append(ws)


def _espaces():
    return [
        WorkspaceConfig(
            workspace_id="espace-rh", name="Ressources humaines",
            storage_path="/espace-rh/", user_ids=["@membre-rh:exemple.fr"],
        ),
        WorkspaceConfig(
            workspace_id="espace-juridique", name="Juridique",
            storage_path="/espace-juridique/", user_ids=["@membre-juridique:exemple.fr"],
        ),
    ]


def _message(corps: str, expediteur: str = "@intrus:exemple.fr") -> IncomingMessage:
    return IncomingMessage(
        conversation_id="!salon-de-l-intrus:exemple.fr",
        user_id=expediteur,
        body=corps,
        conversation_type=ConversationType.PRIVATE,
    )


async def _relire(storage, chemin):
    """Recharge un espace depuis le stockage — la seule source de vérité."""
    from colaig.context.workspace import load_workspace

    return await load_workspace(storage, chemin)


async def _handler(fake_messaging, fake_storage):
    """Monte réellement les espaces sur le stockage.

    Sans cela, `add_conversation_to_workspace` échoue faute de `config.yaml`, et le test
    « l'intrus ne peut pas lier » passe **pour une mauvaise raison** : le lien n'a pas
    été refusé, il a raté. Mesuré : c'est ce qui s'est produit à la première écriture de
    ce fichier. Un test vert par accident est pire qu'un test absent.
    """
    from colaig.context.workspace import _save_workspace_config

    espaces = _espaces()
    for ws in espaces:
        await _save_workspace_config(fake_storage, ws.storage_path, ws)
    resolver = _ResolverAvecEspaces(espaces)
    return MessageHandler(fake_messaging, resolver, None, None, fake_storage), resolver


@pytest.mark.asyncio
async def test_l_accueil_n_enumere_pas_les_espaces_des_autres(fake_messaging, fake_storage):
    """`colaig lier` sans argument listait toute l'instance.

    La liste des espaces d'une instance est en soi une information : elle nomme les
    équipes, les directions, parfois les dossiers en cours. La rendre à quiconque sait
    inviter le bot est une fuite, même sans accès aux documents.
    """
    handler, _ = await _handler(fake_messaging, fake_storage)

    await handler._handle_onboarding_command(_message("colaig lier"))

    envoye = " ".join(fake_messaging.textes_envoyes())
    assert "espace-rh" not in envoye, (
        f"les espaces de l'instance ont été énumérés à un inconnu : {envoye}"
    )
    assert "espace-juridique" not in envoye


@pytest.mark.asyncio
async def test_un_inconnu_ne_peut_pas_lier_son_salon_a_l_espace_d_autrui(
    fake_messaging, fake_storage,
):
    """Le cœur du sujet : l'appariement salon → espace est la frontière d'accès.

    S'il se forge depuis n'importe quel salon, la cloison multi-tenant n'existe plus.
    """
    handler, resolver = await _handler(fake_messaging, fake_storage)

    await handler._handle_onboarding_command(_message("colaig lier espace-rh"))

    # Lire le STOCKAGE, pas l'objet en mémoire : `add_conversation_to_workspace`
    # recharge et réécrit, et le résolveur garde l'ancien exemplaire. Assertionner sur
    # la liste en mémoire ne verrait donc jamais le lien, réussi ou non.
    rh = await _relire(fake_storage, "/espace-rh/")
    assert "!salon-de-l-intrus:exemple.fr" not in rh.conversations, (
        "un salon étranger a été rattaché à l'espace RH sans autorisation"
    )


@pytest.mark.asyncio
async def test_un_membre_peut_lier_son_salon(fake_messaging, fake_storage):
    """Un garde qui refuse tout ne protège rien : il se fait désactiver.

    Sans ce test, la façon la plus simple de faire passer les précédents serait de
    supprimer la commande.
    """
    handler, resolver = await _handler(fake_messaging, fake_storage)

    await handler._handle_onboarding_command(
        _message("colaig lier espace-rh", expediteur="@membre-rh:exemple.fr")
    )

    rh = await _relire(fake_storage, "/espace-rh/")
    assert "!salon-de-l-intrus:exemple.fr" in rh.conversations, (
        "un membre déclaré de l'espace doit pouvoir y rattacher son salon"
    )


@pytest.mark.asyncio
async def test_creer_son_propre_espace_reste_ouvert(fake_messaging, fake_storage):
    """L'accueil garde sa raison d'être.

    Créer son espace n'expose rien de personne — c'est rejoindre celui d'autrui qui
    demande d'y être admis.
    """
    handler, resolver = await _handler(fake_messaging, fake_storage)

    traite = await handler._handle_onboarding_command(_message("colaig créer Mon Espace"))

    assert traite, "la commande de création doit rester interceptée"
    assert any(ws.workspace_id not in ("espace-rh", "espace-juridique")
               for ws in resolver.workspaces), "aucun espace n'a été créé"
