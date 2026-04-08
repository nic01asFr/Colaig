# SPDX-License-Identifier: MIT
"""
Scoping des outils par workspace (whitelist/blacklist déterministe).

S'applique APRÈS la construction du registre d'outils (build_registry)
et AVANT le filtrage par mots-clés/embeddings.

Principe :
- Première couche déterministe : décide quels outils existent dans ce workspace
- Lit la section `tools` du workspace.yaml (champ enabled/disabled)
- Additif par défaut : un workspace sans yaml hérite de tous les outils

Ordre dans le pipeline :
    build_registry()              # tous les outils candidats
    → apply_workspace_scoping()   # whitelist/blacklist statique (CETTE COUCHE)
    → filter_tools_by_keywords()  # mots-clés (avec enrichissement workspace)
    → filter_tools_by_embeddings  # sémantique si ambigu
    → garantie always_included    # noyau toujours présent
"""
from __future__ import annotations

import fnmatch
from typing import List, Set

from app.matrix_bot.config import logger
from app.agent.tools import ToolRegistry
from app.agent.workspace_config import WorkspaceConfig


def apply_workspace_scoping(
    registry: ToolRegistry,
    ws_config: WorkspaceConfig,
) -> None:
    """Applique le scoping workspace au registre d'outils en place.

    Modifie le registre via filter_in_place. Aucun effet si workspace_config
    est vide ou si tools.enabled/disabled ne sont pas définis.

    Args:
        registry: Registre d'outils à filtrer en place.
        ws_config: Configuration du workspace courant.

    Logique :
    1. Si `enabled` non vide : whitelist stricte (glob supporté).
       Tout outil ne matchant aucun pattern est retiré.
    2. `disabled` : retire ces outils en plus, même s'ils étaient dans enabled.
       Patterns glob supportés.
    3. `always_included` : ces outils sont garantis présents s'ils existent
       dans le registre original (override la blacklist si conflit).
    """
    if ws_config.is_empty():
        return

    all_names = [t.name for t in registry.all_tools]
    if not all_names:
        return

    enabled_patterns = ws_config.tools_enabled
    disabled_patterns = ws_config.tools_disabled
    always_included = set(ws_config.tools_always_included)

    if not enabled_patterns and not disabled_patterns and not always_included:
        return  # rien à faire

    kept: Set[str] = set()

    # 1. Whitelist (si présente)
    if enabled_patterns:
        for name in all_names:
            for pattern in enabled_patterns:
                if _matches_pattern(name, pattern):
                    kept.add(name)
                    break
    else:
        # Pas de whitelist → tous candidats
        kept = set(all_names)

    # 2. Blacklist (toujours appliquée)
    if disabled_patterns:
        to_remove = set()
        for name in kept:
            for pattern in disabled_patterns:
                if _matches_pattern(name, pattern):
                    to_remove.add(name)
                    break
        kept -= to_remove

    # 3. always_included : forcer la présence si l'outil existe dans le registre
    #    Override la blacklist si conflit (la doctrine est : always_included
    #    est volontaire et explicite, blacklist est défensive)
    for name in always_included:
        if name in all_names:
            kept.add(name)

    # Appliquer le filtrage en place
    if kept != set(all_names):
        registry.filter_in_place(kept)
        removed = set(all_names) - kept
        logger.info(
            f"[WS-SCOPING] {len(all_names)} → {len(kept)} outils "
            f"(retirés: {sorted(removed)[:5]}{'...' if len(removed) > 5 else ''})"
        )


def _matches_pattern(name: str, pattern: str) -> bool:
    """Match un nom d'outil contre un pattern glob.

    Examples:
        _matches_pattern("datagouv__search_datasets", "datagouv__*") → True
        _matches_pattern("search_documents", "search_documents") → True
        _matches_pattern("foo", "datagouv__*") → False
    """
    return fnmatch.fnmatchcase(name, pattern)


def merge_keywords_with_workspace(
    base_keywords: dict,
    ws_config: WorkspaceConfig,
) -> dict:
    """Fusionne les mots-clés globaux avec les keywords_extra du workspace.

    Le workspace **étend** (additif), il ne remplace pas. Permet à chaque
    workspace d'enrichir le matching avec son vocabulaire métier sans
    perdre les défauts d'instance.

    Args:
        base_keywords: Mots-clés globaux (depuis agent_config.yaml).
        ws_config: Configuration workspace.

    Returns:
        Nouveau dict {tool_name: [keywords]} fusionné.
    """
    if not ws_config.tools_keywords_extra:
        return base_keywords

    merged = {k: list(v) for k, v in (base_keywords or {}).items()}
    for tool_name, extra in ws_config.tools_keywords_extra.items():
        if tool_name not in merged:
            merged[tool_name] = list(extra)
        else:
            # Ajouter sans doublons
            existing = set(merged[tool_name])
            for kw in extra:
                if kw not in existing:
                    merged[tool_name].append(kw)
                    existing.add(kw)
    return merged


def merge_always_included_with_workspace(
    base_always_included: Set[str],
    ws_config: WorkspaceConfig,
) -> Set[str]:
    """Fusionne le noyau global avec celui du workspace (additif)."""
    return set(base_always_included or set()) | set(ws_config.tools_always_included or [])
