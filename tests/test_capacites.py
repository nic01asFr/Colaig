"""
Colaig — Colaig doit se connaître (campagne d'usage réel du 29/08/2026, défaut A).

Ce que la campagne a montré
-----------------------------
En conversation directe, `!aide` affichait « Pour lier ce salon à un espace :
`colaig lier <identifiant>` ». **Cette commande n'y est jamais interceptée** :
`_handle_onboarding_command` ne s'exécute que derrière la porte
`mode == ContextMode.CHATBOT`, alors que `_repondre_commande` répond dans tous les
modes. L'aide annonçait donc une commande que le mode rendait inopérante.

Interrogé sur la même question, le modèle a répondu « il n'existe pas de commande
native » — plus proche du vrai que le texte d'aide — puis a inventé une procédure
(`ask_workspace` sur Notion, Confluence) faute de savoir ce qu'il offre réellement.

Deux défauts d'une seule cause : **rien ne déclare, en un seul endroit, ce que Colaig
sait faire selon le mode.** L'aide le codait en dur et faux ; le prompt système n'en
disait rien.

Ce que ces tests tiennent
---------------------------
Une source, deux lecteurs — la commande et le modèle — et la vérité du mode dans les
deux. Un test qui ne comparerait que le texte d'aide à lui-même laisserait revenir
exactement le défaut observé.
"""

from __future__ import annotations

import pytest

from colaig import capacites
from colaig.models import ContextMode

TOUS_LES_MODES = (ContextMode.ASSISTANT, ContextMode.CHATBOT, ContextMode.PERSONAL)


# ─────────────────────────────────────────────────────────────────────────────
# Une source, deux lecteurs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", TOUS_LES_MODES)
def test_l_aide_et_la_notice_nomment_les_memes_commandes(mode):
    """Le texte lu par l'humain et celui lu par le modèle ne divergent pas.

    C'est le cœur du défaut : `!aide` disait une chose, le prompt système en disait une
    autre, et l'utilisateur a reçu les deux à trois messages d'intervalle.
    """
    aide = capacites.texte_aide(mode)
    notice = capacites.notice_de_soi(mode)

    for nom, _ in capacites.COMMANDES:
        assert nom in aide, f"{nom} absente de l'aide en mode {mode}"
        assert nom in notice, f"{nom} absente de la notice en mode {mode}"


def test_l_aide_ne_recopie_pas_une_liste_ecrite_a_la_main():
    """Ajouter une commande à la table doit suffire à la voir partout.

    Sans cela, la table serait décorative et la duplication reviendrait au premier
    ajout — ce qui est précisément l'histoire de `_AIDE`.
    """
    inventee = ("!inventee", "commande de contrôle")
    origine = capacites.COMMANDES
    try:
        capacites.COMMANDES = origine + (inventee,)
        assert "!inventee" in capacites.texte_aide(ContextMode.ASSISTANT)
        assert "!inventee" in capacites.notice_de_soi(ContextMode.ASSISTANT)
    finally:
        capacites.COMMANDES = origine


# ─────────────────────────────────────────────────────────────────────────────
# La vérité du mode — le défaut observé sur le fil
# ─────────────────────────────────────────────────────────────────────────────


def test_les_commandes_de_liaison_sont_annoncees_en_chatbot():
    """En salon non lié, `colaig créer` et `colaig lier` sont réellement interceptées."""
    aide = capacites.texte_aide(ContextMode.CHATBOT)
    notice = capacites.notice_de_soi(ContextMode.CHATBOT)

    assert "colaig lier" in aide
    assert "colaig créer" in aide
    assert "colaig lier" in notice


@pytest.mark.parametrize("mode", (ContextMode.ASSISTANT, ContextMode.PERSONAL))
def test_les_commandes_de_liaison_ne_sont_pas_annoncees_ailleurs(mode):
    """LE test de la campagne.

    `_handle_onboarding_command` est derrière une porte `mode == CHATBOT`. Annoncer
    `colaig lier` hors de ce mode, c'est envoyer l'utilisateur taper une commande qui
    descendra au pipeline comme une phrase ordinaire.
    """
    aide = capacites.texte_aide(mode)
    notice = capacites.notice_de_soi(mode)

    assert "colaig lier" not in aide, (
        f"l'aide annonce `colaig lier` en mode {mode}, où elle n'est pas interceptée"
    )
    assert "colaig lier" not in notice
    assert "colaig créer" not in aide


def test_la_notice_interdit_d_inventer_une_procedure():
    """Le modèle a inventé Notion et Confluence faute d'une consigne le lui interdisant.

    Nommer les commandes ne suffit pas : sans cette phrase, le modèle comble le silence.
    """
    notice = capacites.notice_de_soi(ContextMode.PERSONAL)
    assert "invente" in notice.lower() or "inventer" in notice.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Les gestes — eux non plus n'étaient déclarés nulle part
# ─────────────────────────────────────────────────────────────────────────────


def test_l_aide_explique_les_gestes_que_colaig_pose():
    """Colaig pose quatre réactions sous chaque réponse et n'en expliquait aucune."""
    aide = capacites.texte_aide(ContextMode.ASSISTANT)
    for emoji, _ in capacites.GESTES:
        assert emoji in aide, f"le geste {emoji} n'est expliqué nulle part"


def test_l_emoji_de_reprise_est_celui_de_la_documentation():
    """Défaut D : le code émettait 🔁 (U+1F501), toute la documentation dit 🔄 (U+1F504)."""
    assert capacites.REJOUER == "\U0001f504"
    assert capacites.REJOUER != "\U0001f501"


def test_les_gestes_du_mecanisme_viennent_de_la_meme_source():
    """`retours.py` ne redéfinit pas sa propre liste d'emojis.

    Deux définitions, c'est deux vérités : celle que Colaig pose et celle qu'il
    explique. C'est exactement ainsi que 🔁 et 🔄 ont divergé.
    """
    from colaig.messaging import retours

    assert retours.REJOUER is capacites.REJOUER
    assert set(retours.GESTES_PROPOSES) == {emoji for emoji, _ in capacites.GESTES}


# ─────────────────────────────────────────────────────────────────────────────
# Le câblage — la notice atteint bien le prompt système
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", TOUS_LES_MODES)
def test_le_prompt_systeme_porte_la_notice(mode):
    """Sans ce câblage, la table est juste et le modèle l'ignore toujours."""
    from colaig.context.layers import _build_system_prompt

    prompt = _build_system_prompt(None, mode)
    for nom, _ in capacites.COMMANDES:
        assert nom in prompt, f"{nom} absente du prompt système en mode {mode}"


def test_un_prompt_d_espace_personnalise_n_efface_pas_la_notice():
    """Le cas qui casse silencieusement.

    Un espace configuré remplace tout le prompt bâti. Un Colaig personnalisé
    redeviendrait ignorant de lui-même — et c'est le déploiement réel, pas un cas
    limite.
    """
    from colaig.context.layers import _build_system_prompt
    from colaig.models import WorkspaceConfig

    espace = WorkspaceConfig(
        workspace_id="essai",
        name="Essai",
        storage_path="/essai/",
        system_prompt="Tu es un assistant spécialisé en marchés publics.",
    )

    prompt = _build_system_prompt(espace, ContextMode.ASSISTANT)

    assert "marchés publics" in prompt, "le prompt de l'espace doit rester en tête"
    assert "!space" in prompt, "la notice est effacée par le prompt de l'espace"


# ─────────────────────────────────────────────────────────────────────────────
# Les gestes sont posés PAR LE SYSTÈME, pas écrits par le modèle
# ─────────────────────────────────────────────────────────────────────────────


def test_la_notice_interdit_de_recopier_les_gestes():
    """Défaut relevé sur le fil le 30/08/2026, dans une réponse réelle.

    Chaque réponse se terminait par « 👍 👎 🔄 ➕ » — parfois avec la légende
    complète — **en plus** des réactions réellement posées dessous. Deux fois la même
    information, dont une inutile, à la fin de chaque message.

    La cause est ma propre notice : « Tu poses toi-même quatre réactions sous chacune
    de tes réponses : 👍 (la réponse convient), 👎 … ». Le modèle l'exécutait
    littéralement — on lui décrivait une action en lui donnant les caractères à écrire.

    C'est `proposer_gestes()` qui les pose, par l'API de réaction. Le modèle n'a rien
    à en faire, et doit l'apprendre explicitement : décrire une capacité sans dire qui
    l'exerce, c'est la lui confier.
    """
    for mode in TOUS_LES_MODES:
        notice = capacites.notice_de_soi(mode)
        for emoji, _ in capacites.GESTES:
            assert emoji not in notice, (
                f"le caractère {emoji} est dans le prompt système (mode {mode}) : "
                f"une interdiction ne suffit pas, le modèle recopie ce qu'on lui donne"
            )
        assert "réaction" in notice.lower(), (
            f"la notice doit tout de même dire que des réactions existent, mode {mode}"
        )


@pytest.mark.parametrize("mode", TOUS_LES_MODES)
def test_la_notice_dit_qui_pose_les_gestes(mode):
    """« Tu poses » invitait à faire ; il faut dire que c'est déjà fait."""
    notice = capacites.notice_de_soi(mode)
    assert "tu poses toi-même" not in notice.lower(), (
        "formulation qui fait écrire les gestes au modèle au lieu de l'informer"
    )
