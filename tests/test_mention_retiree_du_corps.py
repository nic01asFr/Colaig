"""
Colaig — la mention ne fait pas partie de la question
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
En salon, une question adressée par mention arrive avec le nom affiché du bot **collé
en tête du corps**, parce que c'est ainsi que les clients Matrix rendent une mention en
texte brut :

    question='Colaig Assistant [Developpement-Durable]: quelle est la procedure …'

Dans un fil, où la mention n'est pas nécessaire, la même question arrive propre :

    question='et pour un agent en teletravail, la procedure change-t-elle ?'

Ce préfixe n'est pas cosmétique. Il se propage :

- dans le **retour** persisté (`.colaig/feedback/*.json`), donc dans la seule mesure de
  qualité issue des usages réels ;
- dans le **titre** des notes versées par ➕ dans `.colaig/notes.md` ;
- dans la **reformulation** de l'Analyseur, à qui l'on donne à interpréter un nom propre
  qui n'appartient pas à la question.

Le nom du destinataire n'est pas une partie de ce qu'on lui demande. On ne l'écrit pas
dans un compte rendu, et on ne le vectorise pas.

La borne
----------
Seul un préfixe **en tête**, suivi d'un séparateur, est retiré — et seulement s'il
désigne le bot. « demande à Colaig Assistant : … » n'est pas touché, parce que le nom
n'y est pas en tête.
"""

from __future__ import annotations

import pytest

IDENTITE = "@colaig.assistant-dd.gouv.fr:tchap.gouv.fr"
NOM = "Colaig Assistant [Developpement-Durable]"


@pytest.fixture
def messagerie():
    from colaig.messaging.matrix import MatrixMessaging

    return MatrixMessaging(homeserver="https://exemple.invalid",
                           username=IDENTITE, password="x")


@pytest.mark.parametrize(("corps", "attendu"), [
    (f"{NOM}: quelle est la procedure ?", "quelle est la procedure ?"),
    (f"{NOM} : quelle est la procedure ?", "quelle est la procedure ?"),
    (f"{NOM}, quelle est la procedure ?", "quelle est la procedure ?"),
    ("colaig.assistant-dd.gouv.fr: bonjour", "bonjour"),
])
def test_la_mention_en_tete_est_retiree(messagerie, corps, attendu):
    """LE défaut du 30/08 : le nom du bot entrait dans la question mesurée."""
    assert messagerie._corps_sans_mention(corps, NOM) == attendu


@pytest.mark.parametrize("corps", [
    "et pour un agent en teletravail ?",
    "demande a Colaig Assistant [Developpement-Durable] ce qu'il en pense",
    "Colaig Assistant [Developpement-Durable] est un assistant documentaire",
    "",
])
def test_ce_qui_n_est_pas_une_mention_en_tete_est_intact(messagerie, corps):
    """La borne : en tête, et suivi d'un séparateur. Sinon on ne touche à rien.

    Le troisième cas compte : une phrase qui COMMENCE par le nom sans séparateur est
    une phrase qui parle du bot, pas une question qui lui est posée.
    """
    assert messagerie._corps_sans_mention(corps, NOM) == corps


def test_un_corps_reduit_a_la_mention_reste_intact(messagerie):
    """Retirer la mention d'un message qui n'est QUE la mention laisserait le vide.

    Un message vide descendrait au pipeline sans question — mieux vaut le laisser tel
    quel et laisser l'assistant répondre à une interpellation nue.
    """
    assert messagerie._corps_sans_mention(f"{NOM}:", NOM) == f"{NOM}:"


def test_sans_nom_affiche_le_localpart_suffit(messagerie):
    """Le nom affiché n'est pas toujours connu — le localpart doit suffire."""
    assert messagerie._corps_sans_mention(
        "colaig.assistant-dd.gouv.fr: bonjour", "") == "bonjour"
