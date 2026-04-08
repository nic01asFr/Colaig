# SPDX-License-Identifier: MIT
"""
Filtrage des outils exposés au LLM selon le contenu du message.

Stratégie heuristique par mots-clés :
- Définition de mots-clés (FR + EN) par catégorie d'outil.
- Au runtime, on extrait les mots du message et on garde uniquement
  les outils dont au moins un mot-clé matche.
- Un noyau d'outils est toujours présent (search_documents, internes).
- Si aucun outil ne matche : on garde tout (failsafe).

Cela réduit drastiquement la taille du prompt système pour les conversations
qui n'ont pas besoin d'outils spécialisés.
"""
from __future__ import annotations

import re
from typing import List, Set

from app.matrix_bot.config import logger


def _get_keywords() -> dict:
    """Charge les mots-clés depuis le YAML config (dynamique)."""
    from app.agent.config_loader import get_tool_keywords
    return get_tool_keywords()


def _get_always_included() -> Set[str]:
    """Charge le noyau d'outils toujours présents depuis le YAML config."""
    from app.agent.config_loader import get_filtering_config
    return set(get_filtering_config().get("always_included", []))


def _normalize(text: str) -> str:
    """Lowercase + suppression accents pour matching robuste."""
    text = text.lower()
    repl = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return text.translate(repl)


# Marqueurs de salutation/politesse pure : si le message ne contient *que*
# ces marqueurs (pas de question concrète), on n'expose aucun outil métier.
_GREETING_MARKERS = {
    "bonjour", "bonsoir", "salut", "hello", "coucou", "hey", "hi",
    "merci", "thanks", "thx",
    "ca va", "ça va", "comment vas tu", "comment allez vous",
    "comment vas-tu", "comment ca va", "comment ça va",
    "qui es tu", "qui es-tu", "qui êtes vous", "qui etes vous",
    "tu peux faire quoi", "que peux tu faire", "tes capacites",
    "au revoir", "bye", "a bientot", "à bientôt",
}


def is_pure_greeting(message: str) -> bool:
    """True si le message est une salutation/politesse sans intent métier.

    Détecte les phrases comme :
      "Bonjour, comment vas-tu ?"
      "Salut, qui es-tu ?"
      "Merci !"

    Le critère : présence d'au moins un marqueur ET absence de mots techniques
    (document, donnees, dataset, recherche, etc.)
    """
    norm = _normalize(message).strip()
    if not norm or len(norm) > 100:
        return False

    # Marqueurs techniques qui invalident la détection "salutation pure"
    technical_markers = {
        "document", "fichier", "donnee", "dataset", "data",
        "recherch", "trouve", "chercher", "cherche",
        "synthes", "rapport", "url", "lien", "indexe",
        "api", "service", "ressource",
    }
    if any(m in norm for m in technical_markers):
        return False

    # Si au moins un marqueur de salutation présent → c'est une salutation
    return any(m in norm for m in _GREETING_MARKERS)


def _tokenize(text: str) -> Set[str]:
    """Extrait les tokens significatifs du message."""
    return set(re.findall(r"\b[\w-]{2,}\b", _normalize(text)))


def filter_tools_by_keywords(
    message: str,
    tool_names: List[str],
    workspace_config=None,
) -> List[str]:
    """Filtre une liste de noms d'outils par pertinence avec le message.

    Charge la config (mots-clés + noyau) depuis YAML à chaque appel pour
    permettre la modification à chaud sans restart.

    Si `workspace_config` est fourni, ses `keywords_extra` sont fusionnés
    avec les mots-clés globaux (additif), et son `tools_always_included`
    étend le noyau global. Permet à un workspace d'enrichir le matching
    avec son vocabulaire métier sans toucher aux défauts d'instance.

    Args:
        message: Texte utilisateur courant.
        tool_names: Liste exhaustive des outils disponibles.
        workspace_config: Optionnel, WorkspaceConfig pour extension par workspace.

    Returns:
        Liste filtrée. Si le message est une salutation pure, ne retourne que
        le noyau (pas d'outils métier). Sinon, applique le matching mots-clés
        avec failsafe (tout retourné si aucun match).
    """
    if not message or not tool_names:
        return tool_names

    always_included = _get_always_included()
    keywords_map = _get_keywords()

    # Phase 2 : enrichir avec workspace.yaml si présent
    if workspace_config is not None:
        try:
            from app.agent.tool_scoping import (
                merge_keywords_with_workspace,
                merge_always_included_with_workspace,
            )
            keywords_map = merge_keywords_with_workspace(keywords_map, workspace_config)
            always_included = merge_always_included_with_workspace(
                always_included, workspace_config,
            )
        except Exception as e:
            logger.debug(f"[FILTER] workspace enrichment failed: {e}")

    # Détection prioritaire : salutation/politesse pure
    # → on n'expose que le noyau, le LLM répondra directement.
    if is_pure_greeting(message):
        return [n for n in tool_names if n in always_included]

    msg_normalized = _normalize(message)
    matched: List[str] = []

    for name in tool_names:
        if name in always_included:
            matched.append(name)
            continue

        keywords = keywords_map.get(name)
        if not keywords:
            # Outil sans config → on l'expose (prudence)
            matched.append(name)
            continue

        # Match si un mot-clé apparaît dans le message normalisé
        for kw in keywords:
            if _normalize(kw) in msg_normalized:
                matched.append(name)
                break

    # Failsafe : si seul le noyau matche, garder tout pour ne pas brider l'agent
    if len(matched) <= len(always_included) and len(tool_names) > len(matched):
        if len(message.strip()) < 30 and not any(
            w in msg_normalized for w in ("comment", "pourquoi", "quoi", "quel", "que")
        ):
            return matched
        return tool_names

    return matched
