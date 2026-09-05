"""
Colaig — connaître son propre nom ne doit pas dépendre de l'état d'un salon.

LA MESURE QUI A RÉVÉLÉ LE DÉFAUT, LE 30/08/2026
-------------------------------------------------
Deux questions posées dans Tchap, **au même utilisateur, dans le même salon, sur le
même pod**. Seul le temps écoulé depuis le démarrage change :

    à ~2 min   user='@nicolas.laval-...:agent.dev-durable.tchap.gouv.fr'
               question='Colaig Assistant [Developpement-Durable]: que faut-il faire...'

    à ~8 min   user='Nicolas Laval [Developpement-Durable]'
               question='quel est le delai pour faire etablir le certificat medical ?'

Sur les premiers messages après un redémarrage, l'état des membres du salon n'est pas
encore chargé : `room.user_name()` rend `None`, et `_corps_sans_mention` n'a plus de
nom à retirer. La mention reste donc collée à la question.

CE QUE CELA COÛTE
-------------------
La question polluée part dans l'embedding de recherche, dans l'historique persisté,
et dans la reformulation de l'Analyseur — c'est très exactement ce que le lot
« La mention ne fait pas partie de la question » corrigeait. Le correctif était juste,
mais il **dépendait d'un état chargé de façon asynchrone**, donc il ne tenait pas au
démarrage. Un correctif qui ne marche qu'au bout de cinq minutes n'est pas un
correctif : c'est une course.

LA PROPRIÉTÉ FIGÉE ICI
------------------------
Colaig connaît **son propre nom** — il le tient de son profil, obtenu une fois à la
connexion. L'état d'un salon peut l'affiner, jamais le conditionner.
"""

from __future__ import annotations

from colaig.messaging.matrix import MatrixMessaging

MXID = "@colaig.assistant-developpement-durable.gouv.fr:agent.dev-durable.tchap.gouv.fr"
NOM = "Colaig Assistant [Developpement-Durable]"


def _client(nom_affiche: str = "") -> MatrixMessaging:
    m = MatrixMessaging.__new__(MatrixMessaging)
    m._identite = MXID
    m._username = MXID
    m._nom_affiche = nom_affiche
    return m


def test_le_defaut_du_30_08_sans_etat_de_salon():
    """LE cas mesuré : `room.user_name()` rend None, la mention doit tomber quand même."""
    corps = f"{NOM}: que faut-il faire apres un evenement grave ?"

    rendu = _client(nom_affiche=NOM)._corps_sans_mention(corps, "")

    assert rendu == "que faut-il faire apres un evenement grave ?", (
        f"la mention reste collee a la question : {rendu!r}"
    )


def test_l_etat_du_salon_reste_prioritaire_quand_il_existe():
    """Un salon peut porter un nom par salon ; il affine, il ne conditionne pas."""
    corps = "Colaig du service SST: quelle procedure ?"

    rendu = _client(nom_affiche=NOM)._corps_sans_mention(corps, "Colaig du service SST")

    assert rendu == "quelle procedure ?"


def test_sans_profil_ni_salon_le_localpart_sert_encore():
    """Le dernier recours d'origine n'est pas retire."""
    corps = "colaig.assistant-developpement-durable.gouv.fr: bonjour"

    rendu = _client()._corps_sans_mention(corps, "")

    assert rendu == "bonjour"


def test_une_phrase_qui_parle_du_bot_n_est_pas_amputee():
    """LA borne : sans separateur, le nom appartient a la phrase."""
    corps = f"{NOM} ne repond plus depuis ce matin"

    rendu = _client(nom_affiche=NOM)._corps_sans_mention(corps, "")

    assert rendu == corps


def test_un_message_reduit_a_la_mention_reste_intact():
    """Le vider ferait descendre une question sans texte au pipeline."""
    corps = f"{NOM}:"

    rendu = _client(nom_affiche=NOM)._corps_sans_mention(corps, "")

    assert rendu.strip() != ""


def test_le_nom_de_profil_est_recupere_a_la_connexion():
    """La propriete structurelle : l'attribut existe des la construction.

    Sans initialisation, `_corps_sans_mention` leverait `AttributeError` sur un client
    construit mais pas encore connecte — et c'est le chemin emprunte par les tests a
    doublure comme par le tout premier message recu.
    """
    import inspect

    source = inspect.getsource(MatrixMessaging.__init__)
    assert "_nom_affiche" in source, (
        "`_nom_affiche` doit etre initialise dans __init__, sinon le premier message "
        "recu avant la connexion complete leve une AttributeError"
    )

    source_connect = inspect.getsource(MatrixMessaging.connect)
    assert "displayname" in source_connect.lower(), (
        "le nom d'affichage doit etre obtenu du PROFIL a la connexion, pas de l'etat "
        "d'un salon charge de facon asynchrone"
    )
