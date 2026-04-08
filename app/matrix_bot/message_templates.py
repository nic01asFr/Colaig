# SPDX-License-Identifier: MIT
"""
Templates standardisés pour les messages Colaig.

Toutes les commandes doivent utiliser ces fonctions pour garantir
un style visuel cohérent dans Tchap.
"""


def fmt_error(title: str, detail: str = "", suggestion: str = "") -> str:
    """Message d'erreur bloquante."""
    msg = f"❌ **{title}**"
    if detail:
        msg += f" — {detail}"
    if suggestion:
        msg += f"\n\n{suggestion}"
    return msg


def fmt_warning(message: str) -> str:
    """Avertissement non bloquant (saisie invalide, dégradation)."""
    return f"⚠️ {message}"


def fmt_success(message: str) -> str:
    """Confirmation de succès."""
    return f"✅ {message}"


def fmt_progress(message: str) -> str:
    """Opération en cours. Court, sans bold."""
    return f"🔄 {message}"


def fmt_info(title: str, body: str = "") -> str:
    """Information, résultat, détail."""
    msg = f"ℹ️ **{title}**"
    if body:
        msg += f"\n\n{body}"
    return msg


def fmt_help(command: str, description: str, usage: str,
             examples: list = None) -> str:
    """Aide standardisée pour une commande.

    Args:
        command: Nom de la commande avec le préfixe (ex: "!webhook")
        description: Description courte (1 ligne)
        usage: Syntaxe d'usage (sera mis dans un bloc code)
        examples: Liste de tuples (commande, description)
    """
    msg = f"💡 **{command}** — {description}\n\n"
    msg += f"**Usage :**\n`{usage}`"
    if examples:
        msg += "\n\n**Exemples :**"
        for cmd, desc in examples:
            msg += f"\n- `{cmd}` — {desc}"
    return msg


def fmt_result(title: str, body: str) -> str:
    """Résultat d'une commande (recherche, liste, détails)."""
    return f"**{title}**\n\n{body}"


def fmt_empty(entity: str, suggestion: str = "") -> str:
    """Liste vide / aucun résultat."""
    msg = f"🔍 Aucun {entity} trouvé."
    if suggestion:
        msg += f"\n\n{suggestion}"
    return msg
