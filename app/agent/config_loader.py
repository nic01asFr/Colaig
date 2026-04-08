# SPDX-License-Identifier: MIT
"""
Chargement de la configuration agent depuis YAML.

Le fichier `app/agent/config/agent_config.yaml` est rechargé à la demande,
permettant de modifier le comportement de l'agent sans rebuild Docker
(le code est monté en volume).

Cache simple : on garde la dernière config chargée + son mtime, on recharge
seulement si le fichier a été modifié.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger("colaig.agent.config")

_CONFIG_PATH = Path(__file__).parent / "config" / "agent_config.yaml"

_cached_config: Optional[Dict[str, Any]] = None
_cached_mtime: float = 0.0


def load_agent_config() -> Dict[str, Any]:
    """Charge la config agent depuis YAML, avec cache invalidé sur mtime.

    Returns:
        Dict de configuration. Retourne un dict vide si erreur ou fichier absent.
    """
    global _cached_config, _cached_mtime

    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        logger.warning(f"[CONFIG] Fichier introuvable: {_CONFIG_PATH}")
        return _cached_config or {}

    if _cached_config is not None and mtime == _cached_mtime:
        return _cached_config

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _cached_config = data
        _cached_mtime = mtime
        logger.info(f"[CONFIG] Rechargé depuis {_CONFIG_PATH}")
        return data
    except Exception as e:
        logger.error(f"[CONFIG] Erreur lecture YAML: {e}")
        return _cached_config or {}


# Helpers pour accès typés ────────────────────────────────────────────────────

def get_filtering_config() -> Dict[str, Any]:
    return load_agent_config().get("filtering", {})


def get_tool_keywords() -> Dict[str, list]:
    return load_agent_config().get("tool_keywords", {})


def get_server_hints() -> Dict[str, str]:
    return load_agent_config().get("server_hints", {})


def get_compaction_config() -> Dict[str, Any]:
    return load_agent_config().get("compaction", {})


def get_cache_config() -> Dict[str, Any]:
    return load_agent_config().get("cache", {})


def get_loop_config() -> Dict[str, Any]:
    return load_agent_config().get("loop", {})
