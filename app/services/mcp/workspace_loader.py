"""
Chargement de la configuration MCP depuis les workspaces WebDAV.

Format du fichier `.albert/config/mcp_servers.json` dans chaque workspace :

    {
      "servers": [
        {
          "name": "mon-serveur",
          "url": "https://example.com/mcp",
          "token": "bearer-token-optionnel",
          "description": "Description libre",
          "enabled": true,
          "timeout": 30
        }
      ]
    }

Chaque workspace déclare ses propres serveurs MCP — la résolution reste
toujours scoped au workspace (room_id / webdav_context).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("colaig.mcp")

# Chemin dans l'arborescence WebDAV du workspace
MCP_CONFIG_PATH = ".albert/config/mcp_servers.json"


@dataclass
class MCPServerConfig:
    """Configuration d'un serveur MCP déclaré dans un workspace."""
    name: str
    url: str
    token: Optional[str] = None
    description: str = ""
    enabled: bool = True
    timeout: float = 30.0

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        return cls(
            name=data["name"],
            url=data["url"],
            token=data.get("token") or data.get("auth_token"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            timeout=float(data.get("timeout", 30.0)),
        )


async def load_mcp_servers_from_webdav(
    webdav_service,
    workspace_root: str = "",
) -> List[MCPServerConfig]:
    """
    Lit `.albert/config/mcp_servers.json` depuis le WebDAV du workspace.

    Args:
        webdav_service: instance WebDAVService (doit avoir download_file() et exists())
        workspace_root: chemin racine du workspace sur le WebDAV (peut être vide)

    Returns:
        Liste de MCPServerConfig actifs (enabled=True). Vide si fichier absent.
    """
    # Construction du chemin absolu selon la racine du workspace
    if workspace_root and not workspace_root.endswith("/"):
        workspace_root += "/"
    config_path = f"{workspace_root}{MCP_CONFIG_PATH}"

    try:
        exists = await webdav_service.exists(config_path)
        if not exists:
            logger.debug(f"[MCP] Pas de config MCP dans ce workspace: {config_path}")
            return []

        raw_bytes = await webdav_service.download_file(config_path)
        raw_text = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        data = json.loads(raw_text)

        servers_raw = data.get("servers", [])
        servers = [
            MCPServerConfig.from_dict(s)
            for s in servers_raw
            if isinstance(s, dict) and s.get("name") and s.get("url")
        ]
        active = [s for s in servers if s.enabled]

        logger.info(
            f"[MCP] Workspace {workspace_root!r}: "
            f"{len(active)}/{len(servers)} serveur(s) MCP actif(s)"
        )
        return active

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"[MCP] Fichier mcp_servers.json malformé dans {config_path}: {e}")
        return []
    except Exception as e:
        logger.warning(f"[MCP] Impossible de charger la config MCP depuis {config_path}: {e}")
        return []
