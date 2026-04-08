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

Les serveurs par défaut (DEFAULT_MCP_SERVERS) sont toujours inclus, sauf
si le workspace déclare un serveur portant le même nom (override explicite).
Un workspace peut désactiver un serveur par défaut en le déclarant avec
``"enabled": false``.
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
    """Configuration d'un serveur MCP déclaré dans un workspace ou par défaut.

    Champs étendus :
    - instructions : hints à injecter au LLM si le serveur ne fournit pas
      d'instructions natives via initialize. Permet de documenter le workflow
      recommandé là où le serveur est déclaré (cohérent avec la déclaration).
    - cache_scope : portée du cache des résultats d'outils :
        * "server" : cache partagé entre tous les workspaces (donnée publique
          stateless, ex: data.gouv.fr). Une seule entrée par (server, args).
        * "workspace" (défaut) : cache isolé par workspace_root. Pour les
          serveurs qui peuvent rendre des résultats différents selon le
          contexte workspace (via roots, tokens spécifiques, etc.).
        * "none" : pas de cache du tout (résultats jamais mémorisés).
    """
    name: str
    url: str
    token: Optional[str] = None
    description: str = ""
    enabled: bool = True
    timeout: float = 30.0
    instructions: str = ""
    cache_scope: str = "workspace"

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        return cls(
            name=data["name"],
            url=data["url"],
            token=data.get("token") or data.get("auth_token"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            timeout=float(data.get("timeout", 30.0)),
            instructions=data.get("instructions", ""),
            cache_scope=data.get("cache_scope", "workspace"),
        )


def build_default_servers(raw_list: List[dict]) -> List[MCPServerConfig]:
    """Convertit la liste brute de dicts (depuis Config.mcp_default_servers) en MCPServerConfig."""
    servers = []
    for entry in raw_list:
        if isinstance(entry, dict) and entry.get("name") and entry.get("url"):
            servers.append(MCPServerConfig.from_dict(entry))
    return servers


async def load_mcp_servers_from_webdav(
    webdav_service,
    workspace_root: str = "",
    default_servers: Optional[List[MCPServerConfig]] = None,
) -> List[MCPServerConfig]:
    """
    Charge les serveurs MCP pour un workspace.

    Fusionne les serveurs par défaut (``default_servers``, issus de
    ``Config.mcp_default_servers``) avec ceux déclarés dans
    ``.albert/config/mcp_servers.json`` du workspace WebDAV.  Si le
    workspace déclare un serveur portant le même ``name`` qu'un serveur par
    défaut, la déclaration workspace l'emporte (override).  Un workspace peut
    désactiver un défaut en le déclarant avec ``"enabled": false``.

    Args:
        webdav_service: instance WebDAVService (doit avoir download_file() et exists())
        workspace_root: chemin racine du workspace sur le WebDAV (peut être vide)
        default_servers: pool de serveurs par défaut de l'instance (peut être vide)

    Returns:
        Liste de MCPServerConfig actifs (enabled=True).
    """
    defaults = default_servers or []

    # Charger la config workspace
    workspace_servers = await _load_workspace_config(webdav_service, workspace_root)

    # Fusionner : defaults + workspace (workspace gagne en cas de doublon)
    by_name = {s.name: s for s in defaults}
    for s in workspace_servers:
        by_name[s.name] = s

    active = [s for s in by_name.values() if s.enabled]

    logger.info(
        f"[MCP] Workspace {workspace_root!r}: "
        f"{len(active)} serveur(s) MCP actif(s) "
        f"({len(defaults)} défaut + {len(workspace_servers)} workspace)"
    )
    return active


async def _load_workspace_config(
    webdav_service,
    workspace_root: str,
) -> List[MCPServerConfig]:
    """Lit .albert/config/mcp_servers.json depuis le WebDAV. Vide si absent."""
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
        return [
            MCPServerConfig.from_dict(s)
            for s in servers_raw
            if isinstance(s, dict) and s.get("name") and s.get("url")
        ]

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"[MCP] Fichier mcp_servers.json malformé dans {config_path}: {e}")
        return []
    except Exception as e:
        logger.warning(f"[MCP] Impossible de charger la config MCP depuis {config_path}: {e}")
        return []
