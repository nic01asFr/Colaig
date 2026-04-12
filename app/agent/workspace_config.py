# SPDX-License-Identifier: MIT
"""
Loader de configuration workspace pour Colaig.

Lit `{workspace}/.albert/config/workspace.yaml` depuis WebDAV avec un cache
mtime opportuniste **par workspace_root**.

Format minimal :

    identity:
      persona_override: |
        Tu es l'assistant IA de la préfecture de Mayenne.
        Tu réponds en priorité aux agents publics du département.

    tools:
      enabled: ["search_documents", "datagouv__*"]   # whitelist (glob)
      disabled: ["datagouv__get_metrics"]            # blacklist
      always_included: ["search_documents"]          # noyau
      keywords_extra:                                # mots-clés additionnels
        search_documents: ["OPAH", "PLU", "DPE"]

    limits:
      max_turns: 5

Le fichier est :
- Optionnel : si absent, on tombe sur les défauts d'instance (agent_config.yaml)
- Additif : les valeurs définies overrident, les valeurs absentes héritent
- Hot-reload : rechargé seulement si mtime change (cache par workspace_root)
- Per-workspace : strictement isolé, jamais partagé entre rooms

Cohérent avec mcp_servers.json (même répertoire, même mécanique de lecture).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.matrix_bot.config import logger

WORKSPACE_CONFIG_PATH = ".albert/config/workspace.yaml"

# TTL minimal entre deux re-vérifications WebDAV (anti-spam PROPFIND)
_REFRESH_INTERVAL_SECONDS = 30.0


@dataclass
class WorkspaceConfig:
    """Configuration d'un workspace, lue depuis workspace.yaml.

    Tous les champs sont optionnels : un workspace sans yaml hérite
    intégralement des défauts d'instance.
    """
    # Identité / persona
    persona_override: str = ""

    # Scoping des outils
    tools_enabled: List[str] = field(default_factory=list)        # whitelist (glob)
    tools_disabled: List[str] = field(default_factory=list)       # blacklist
    tools_always_included: List[str] = field(default_factory=list)
    tools_keywords_extra: Dict[str, List[str]] = field(default_factory=dict)

    # Limites
    max_turns: Optional[int] = None

    @classmethod
    def empty(cls) -> "WorkspaceConfig":
        """Retourne une config vide (workspace sans yaml)."""
        return cls()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkspaceConfig":
        """Construit une instance depuis le YAML parsé."""
        if not isinstance(data, dict):
            return cls.empty()

        identity = data.get("identity") or {}
        tools = data.get("tools") or {}
        limits = data.get("limits") or {}

        return cls(
            persona_override=str(identity.get("persona_override", "") or "").strip(),
            tools_enabled=list(tools.get("enabled") or []),
            tools_disabled=list(tools.get("disabled") or []),
            tools_always_included=list(tools.get("always_included") or []),
            tools_keywords_extra={
                str(k): list(v or [])
                for k, v in (tools.get("keywords_extra") or {}).items()
            },
            max_turns=limits.get("max_turns"),
        )

    def is_empty(self) -> bool:
        """True si aucun override n'est défini (config inutile)."""
        return (
            not self.persona_override
            and not self.tools_enabled
            and not self.tools_disabled
            and not self.tools_always_included
            and not self.tools_keywords_extra
            and self.max_turns is None
        )


# ─── Cache par workspace_root ────────────────────────────────────────────────
# Structure : workspace_root → (last_check_ts, last_etag_or_mtime, config)
# Le re-check WebDAV n'est fait qu'au-delà de _REFRESH_INTERVAL_SECONDS,
# pour éviter un PROPFIND par message dans une conversation rapide.

_cache: Dict[str, "_CacheEntry"] = {}


@dataclass
class _CacheEntry:
    last_check_ts: float
    last_etag: str  # mtime/etag du fichier au dernier chargement
    config: WorkspaceConfig
    exists: bool


async def load_workspace_config(
    webdav_service,
    workspace_root: str,
    force_refresh: bool = False,
) -> WorkspaceConfig:
    """Charge la config d'un workspace avec cache mtime opportuniste.

    Args:
        webdav_service: WebDAVService (peut être global ou scopé workspace).
        workspace_root: chemin du workspace (ex: 'rooms/!ROOMa').
        force_refresh: bypasse le cache.

    Returns:
        WorkspaceConfig (vide si yaml absent).
    """
    if workspace_root is None:
        return WorkspaceConfig.empty()

    # 1. Vérifier si on a un cache récent
    now = time.time()
    entry = _cache.get(workspace_root)
    if entry and not force_refresh:
        if (now - entry.last_check_ts) < _REFRESH_INTERVAL_SECONDS:
            return entry.config

    # 2. Construire le chemin complet
    ws = workspace_root.rstrip("/")
    config_path = f"{ws}/{WORKSPACE_CONFIG_PATH}" if ws else WORKSPACE_CONFIG_PATH

    # 3. Vérifier l'existence
    try:
        exists = await webdav_service.exists(config_path)
    except Exception as e:
        logger.debug(f"[WS-CONFIG] exists() échoué pour {config_path}: {e}")
        exists = False

    if not exists:
        # Mémoriser l'absence pour ne pas re-checker à chaque message
        config = WorkspaceConfig.empty()
        _cache[workspace_root] = _CacheEntry(
            last_check_ts=now, last_etag="", config=config, exists=False,
        )
        return config

    # 4. Lire le contenu (peut-être inchangé : on utilise un etag/hash simple
    #    sur le contenu brut pour invalider le cache si modifié)
    try:
        raw_bytes = await webdav_service.download_file(config_path)
        raw_text = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        new_etag = _content_etag(raw_text)
    except Exception as e:
        logger.warning(f"[WS-CONFIG] Lecture impossible {config_path}: {e}")
        # On garde l'ancien cache si présent, sinon vide
        if entry:
            entry.last_check_ts = now
            return entry.config
        return WorkspaceConfig.empty()

    # 5. Si etag inchangé, juste rafraîchir le timestamp et retourner le cache
    if entry and entry.last_etag == new_etag:
        entry.last_check_ts = now
        return entry.config

    # 6. Sinon parser le YAML et mettre à jour le cache
    try:
        import yaml
        data = yaml.safe_load(raw_text) or {}
        config = WorkspaceConfig.from_dict(data)
    except Exception as e:
        logger.warning(f"[WS-CONFIG] YAML invalide {config_path}: {e}")
        config = WorkspaceConfig.empty()

    _cache[workspace_root] = _CacheEntry(
        last_check_ts=now, last_etag=new_etag, config=config, exists=True,
    )

    if not config.is_empty():
        logger.info(
            f"[WS-CONFIG] {workspace_root!r}: chargé "
            f"(persona={'oui' if config.persona_override else 'non'}, "
            f"tools_enabled={len(config.tools_enabled)}, "
            f"tools_disabled={len(config.tools_disabled)})"
        )

    return config


def _content_etag(text: str) -> str:
    """Etag déterministe basé sur le hash du contenu (rapide, sans dépendance)."""
    import hashlib
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()


def invalidate_workspace_cache(workspace_root: Optional[str] = None) -> None:
    """Invalide le cache d'un workspace (ou tout le cache si None).

    À appeler depuis close_workspace pour ne pas garder de cache mort.
    """
    if workspace_root is None:
        _cache.clear()
        logger.info("[WS-CONFIG] Cache global vidé")
    else:
        _cache.pop(workspace_root, None)
        logger.debug(f"[WS-CONFIG] Cache vidé pour {workspace_root!r}")


def get_cache_stats() -> dict:
    """Stats d'observabilité (debug/métrologie)."""
    return {
        "size": len(_cache),
        "workspaces": list(_cache.keys()),
        "with_config": sum(1 for e in _cache.values() if e.exists),
    }
