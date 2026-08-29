"""
Colaig — ce que Colaig sait faire, déclaré une seule fois.

STATUT: COMPLET
VERSION: 2026-08-29 - v1.0

Pourquoi ce module existe
---------------------------
La campagne d'usage du 29/08/2026 a relevé deux symptômes d'une seule cause.

**Le texte d'aide annonçait une commande inopérante.** `!aide` affichait « Pour lier ce
salon à un espace : `colaig lier <identifiant>` » — dans tous les modes. Or
`_handle_onboarding_command` n'est atteint que derrière la porte
`mode == ContextMode.CHATBOT`, tandis que `_repondre_commande` répond partout. En
conversation directe, l'aide envoyait donc l'utilisateur taper une commande que le
pipeline traiterait comme une phrase ordinaire.

**Et le modèle ne savait rien de tout cela.** Interrogé sur la même question, il a
répondu « il n'existe pas de commande native » — plus juste que le texte d'aide — puis
a comblé le silence en inventant une procédure (Notion, Confluence). Le seul outil que
le prompt système nommait, `ask_workspace`, est justement celui qu'il a mis en avant :
**il récitait ce qu'on lui avait dit, et devinait le reste.**

Une source, deux lecteurs
---------------------------
Ce module déclare les capacités. `!aide` les affiche, le prompt système les porte. Les
deux textes ne peuvent plus diverger, et surtout : **ils disent la vérité du mode**.

C'est le même principe que `paths.py` pour les chemins — un seul endroit produit, tout
le reste consomme.

Ce que ce module ne fait pas
------------------------------
Il ne décrit **que ce qui est implémenté**. `!space link` / `unlink` en mode ASSISTANT
sont nommés par D59 §6 comme le prochain lot ; tant qu'ils n'existent pas, rien ici ne
les annonce. Un module de capacités qui anticipe est un module qui ment.
"""

from __future__ import annotations

from colaig.models import ContextMode

# ─────────────────────────────────────────────────────────────────────────────
# Les gestes que Colaig pose sous chacune de ses réponses (L3.3)
#
# Ils vivent ICI plutôt que dans `messaging/retours.py` pour une raison que la campagne
# a rendue concrète : le code émettait 🔁 (U+1F501) quand toute la documentation
# annonçait 🔄 (U+1F504). Deux définitions, deux vérités. Le mécanisme lit désormais
# la même source que le texte qui l'explique.
# ─────────────────────────────────────────────────────────────────────────────

POUCE = "\N{THUMBS UP SIGN}"                                              # 👍
POUCE_BAS = "\N{THUMBS DOWN SIGN}"                                        # 👎
REJOUER = "\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}"    # 🔄
GARDER = "\N{HEAVY PLUS SIGN}"                                            # ➕

GESTES: tuple[tuple[str, str], ...] = (
    (POUCE, "la réponse convient"),
    (POUCE_BAS, "la réponse ne convient pas"),
    (REJOUER, "reformuler la réponse"),
    (GARDER, "verser la réponse dans les notes de l'espace"),
)

# ─────────────────────────────────────────────────────────────────────────────
# Les commandes, par portée réelle
# ─────────────────────────────────────────────────────────────────────────────

# Interceptées dans TOUS les modes, avant le pipeline (L3.7). Toutes des lectures.
COMMANDES: tuple[tuple[str, str], ...] = (
    ("!aide", "cette liste"),
    ("!space", "l'espace auquel ce salon est lié"),
    ("!index", "l'état de l'index documentaire"),
    ("!classer", "où les documents ont été rangés"),
    ("!skills", "les procédures déposées dans l'espace"),
)

# Interceptées UNIQUEMENT en mode CHATBOT — salon sans espace lié.
# Les annoncer ailleurs est le défaut que ce module corrige.
COMMANDES_DE_LIAISON: tuple[tuple[str, str], ...] = (
    ("colaig créer <nom>", "créer un espace et y lier ce salon"),
    ("colaig lier <identifiant>", "lier ce salon à un espace existant"),
)

# Ce que Colaig peut dire de la liaison, selon le mode. Le mode CHATBOT est le seul où
# une commande existe ; ailleurs, le dire franchement vaut mieux qu'orienter à faux.
_LIAISON_PAR_MODE = {
    ContextMode.CHATBOT: None,  # les commandes de liaison sont listées à la place
    ContextMode.ASSISTANT: (
        "Ce salon est déjà lié à un espace documentaire. Le relier à un autre espace "
        "ne se fait pas encore depuis Tchap : cela passe par la configuration de "
        "l'espace."
    ),
    ContextMode.PERSONAL: (
        "Cette conversation directe utilise votre espace personnel. Il n'y a pas de "
        "commande pour la lier à un autre espace."
    ),
}


def _liste(entrees: tuple[tuple[str, str], ...], puce: str = "- ") -> str:
    return "\n".join(f"{puce}`{nom}` — {quoi}" for nom, quoi in entrees)


def texte_aide(mode: ContextMode) -> str:
    """Le texte que `!aide` affiche — vrai pour ce mode, et pour lui seul."""
    blocs = [
        "Commandes disponibles :",
        "",
        _liste(COMMANDES),
    ]

    if mode == ContextMode.CHATBOT:
        blocs += [
            "",
            "Pour donner un espace documentaire à ce salon :",
            "",
            _liste(COMMANDES_DE_LIAISON),
        ]
    else:
        blocs += ["", _LIAISON_PAR_MODE[mode]]

    blocs += [
        "",
        "Sous chacune de mes réponses, je pose quatre réactions ; tapotez-en une :",
        "",
        "\n".join(f"- {emoji} {quoi}" for emoji, quoi in GESTES),
        "",
        f"({REJOUER} et {GARDER} n'agissent que sur mes réponses récentes.)",
        "Toutes ces commandes lisent — aucune ne modifie l'espace.",
    ]
    return "\n".join(blocs)


def notice_de_soi(mode: ContextMode) -> str:
    """Le bloc ajouté au prompt système, pour que le modèle sache ce qu'il offre.

    Le même contenu que `texte_aide`, adressé au modèle plutôt qu'à l'utilisateur.
    La dernière phrase n'est pas un ornement : sans elle, le modèle a inventé une
    procédure de configuration complète plutôt que d'admettre son ignorance.
    """
    blocs = [
        "Ce que tu offres réellement dans ce salon — n'annonce rien d'autre :",
        "",
        _liste(COMMANDES),
    ]

    if mode == ContextMode.CHATBOT:
        blocs += ["", _liste(COMMANDES_DE_LIAISON)]
    else:
        blocs += ["", _LIAISON_PAR_MODE[mode]]

    blocs += [
        "",
        "Tu poses toi-même quatre réactions sous chacune de tes réponses : "
        + ", ".join(f"{emoji} ({quoi})" for emoji, quoi in GESTES)
        + ".",
        "",
        "Si l'on t'interroge sur ta configuration ou tes commandes, réponds à partir "
        "de cette liste. N'invente jamais une commande, un outil ou une procédure qui "
        "n'y figure pas : dis que tu ne sais pas et renvoie vers `!aide`.",
    ]
    return "\n".join(blocs)
