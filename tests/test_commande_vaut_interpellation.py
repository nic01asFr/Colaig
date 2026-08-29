"""
Colaig — une commande explicite est une interpellation
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
Une fois le salon correctement reconnu comme salon — et non plus comme conversation
directe — `colaig lier colaig-mesure-sst` a été **reçu et ignoré** :

    message reçu: … room=!idtsoJgSJshkYoXSJt … body_chars=29
    (rien)

`_nous_concerne` applique une règle juste : en salon, `m.mentions` fait foi, et un
message qui ne nomme pas le bot ne le réveille pas. Elle corrige un vrai défaut —
« il faudrait demander à colaig » ne doit pas déclencher de réponse.

**Mais elle rend inatteignable la commande qui sert à configurer le salon.** Et pas
seulement elle : `!aide`, `!space`, `!index`, `!classer`, `!skills` passent tous par
la même porte. Dans un salon, aucune des cinq commandes de L3.7 n'était atteignable
sans mentionner le bot à chaque fois.

Le raisonnement
-----------------
Une mention est une **déclaration d'intention**. Une commande explicite en est une
aussi, et même plus nette : `!aide` en tête de message, ou `colaig lier <id>`, ne sont
pas des façons de *parler de* l'assistant — ce sont des impératifs qui lui sont
adressés. Personne n'écrit `!space` au début d'une phrase qui parle d'autre chose.

D'où l'élargissement, et il est **étroit à dessein** : seul un message qui **commence
par** une commande connue compte. « je me demande si !aide existe » ne déclenche rien,
parce que la commande n'est pas en tête.
"""

from __future__ import annotations

import pytest


class _Evenement:
    def __init__(self, body: str) -> None:
        self.body = body


@pytest.fixture
def messagerie():
    from colaig.messaging.matrix import MatrixMessaging

    m = MatrixMessaging(homeserver="https://exemple.invalid",
                        username="@colaig.assistant-dd.gouv.fr:tchap.gouv.fr",
                        password="x")
    return m


# `m.mentions` présent et ne nommant PAS le bot : c'est le cas qui bloquait tout,
# puisque les clients Matrix récents le posent systématiquement.
_SANS_MENTION = {"m.mentions": {"user_ids": []}}


@pytest.mark.parametrize("corps", [
    "colaig lier colaig-mesure-sst",
    "colaig créer Équipe SST",
    "!aide",
    "!space",
    "!index",
    "!classer",
    "!skills",
    "  !aide  ",
])
def test_une_commande_en_tete_reveille_l_assistant(messagerie, corps):
    """LE défaut du 30/08 : la commande de configuration était inatteignable."""
    assert messagerie._nous_concerne(_Evenement(corps), _SANS_MENTION, "") is True, (
        f"« {corps} » ne réveille pas l'assistant : la commande est inatteignable "
        f"en salon sans mention explicite"
    )


@pytest.mark.parametrize("corps", [
    "il faudrait demander à colaig ce qu'il en pense",
    "je me demande si !aide existe encore",
    "on a parlé de colaig lier hier, tu te souviens ?",
    "le space est trop petit",
])
def test_parler_d_une_commande_ne_la_declenche_pas(messagerie, corps):
    """La règle reste étroite : seule une commande EN TÊTE compte.

    Sans cette borne, on remplacerait un excès de zèle par un autre — exactement le
    défaut que `m.mentions` avait corrigé.
    """
    assert messagerie._nous_concerne(_Evenement(corps), _SANS_MENTION, "") is False, (
        f"« {corps} » réveille l'assistant à tort"
    )


def test_une_commande_inconnue_ne_reveille_pas(messagerie):
    """`!` seul ne suffit pas : le nom doit être une commande réelle.

    Sinon toute exclamation en tête de message deviendrait une interpellation.
    """
    assert messagerie._nous_concerne(_Evenement("!bonjour"), _SANS_MENTION, "") is False


def test_une_vraie_mention_reveille_toujours(messagerie):
    """La règle d'origine n'est pas remplacée, seulement complétée."""
    contenu = {"m.mentions": {"user_ids": [messagerie.identite]}}
    assert messagerie._nous_concerne(_Evenement("bonjour"), contenu, "") is True


def test_la_liste_des_commandes_vient_de_capacites():
    """Une seconde liste écrite à la main divergerait au premier ajout.

    C'est l'histoire de `_AIDE`, corrigée le 29/08 : une source, plusieurs lecteurs.
    """
    from colaig import capacites

    origine = capacites.COMMANDES
    try:
        capacites.COMMANDES = origine + (("!inventee", "commande de contrôle"),)
        assert capacites.est_une_commande("!inventee maintenant") is True
    finally:
        capacites.COMMANDES = origine
