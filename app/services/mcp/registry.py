"""
Registre MCP par workspace.

MCPRegistry maintient une instance MCPHTTPClient par serveur déclaré,
scoped par workspace (identifié par son webdav_root path).

Point d'entrée : get_mcp_registry() → singleton global.

Usage dans l'orchestrateur :
    registry = get_mcp_registry()
    tools = await registry.get_tools(webdav_service, workspace_root)
    result = await registry.call_tool(workspace_root, "server__tool_name", args)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from app.services.mcp.client import MCPHTTPClient, MCPTool, MCPToolResult
from app.services.mcp.workspace_loader import (
    MCPServerConfig,
    load_mcp_servers_from_webdav,
)

logger = logging.getLogger("colaig.mcp")

# Séparateur entre nom de serveur et nom d'outil dans le nom qualifié
TOOL_NAME_SEP = "__"


class MCPRegistry:
    """
    Gère les clients MCP pour tous les workspaces actifs.

    - Un client HTTP par serveur MCP déclaré, initialisé à la demande (lazy).
    - Les configs sont rechargées depuis WebDAV si le workspace n'est pas encore connu.
    - Le cache des outils est conservé en mémoire ; un `force_refresh=True` recharge.
    """

    def __init__(self):
        # workspace_root → {server_name: MCPHTTPClient}
        self._clients: Dict[str, Dict[str, MCPHTTPClient]] = {}
        # workspace_root → [MCPTool]  (cache des outils agrégés)
        self._tools_cache: Dict[str, List[MCPTool]] = {}
        self._lock = asyncio.Lock()

    # ── API publique ──────────────────────────────────────────────────────────

    async def get_tools(
        self,
        webdav_service,
        workspace_root: str,
        force_refresh: bool = False,
    ) -> List[MCPTool]:
        """
        Retourne la liste agrégée de tous les outils MCP disponibles
        pour ce workspace.

        Si le workspace n'a pas de config MCP, retourne [].
        """
        if not force_refresh and workspace_root in self._tools_cache:
            return self._tools_cache[workspace_root]

        async with self._lock:
            # Double-check après acquisition du verrou
            if not force_refresh and workspace_root in self._tools_cache:
                return self._tools_cache[workspace_root]

            servers = await load_mcp_servers_from_webdav(webdav_service, workspace_root)
            if not servers:
                self._tools_cache[workspace_root] = []
                return []

            # Initialiser/mettre à jour les clients pour ce workspace
            clients = await self._init_clients(workspace_root, servers)

            # Agréger les outils de tous les serveurs
            all_tools: List[MCPTool] = []
            for server_name, client in clients.items():
                try:
                    tools = await client.list_tools(force_refresh=force_refresh)
                    all_tools.extend(tools)
                except Exception as e:
                    logger.warning(
                        f"[MCP] Impossible de lister les outils de {server_name}: {e}"
                    )

            self._tools_cache[workspace_root] = all_tools
            logger.info(
                f"[MCP] Workspace {workspace_root!r}: "
                f"{len(all_tools)} outil(s) MCP total"
            )
            return all_tools

    async def call_tool(
        self,
        workspace_root: str,
        qualified_tool_name: str,
        arguments: Dict,
    ) -> MCPToolResult:
        """
        Appelle un outil MCP identifié par son nom qualifié `server__tool`.

        Args:
            workspace_root: racine WebDAV du workspace
            qualified_tool_name: "server_name__tool_name" (séparateur __)
            arguments: arguments de l'outil

        Raises:
            KeyError si le serveur ou l'outil est inconnu
        """
        server_name, tool_name = self._split_qualified_name(qualified_tool_name)

        clients = self._clients.get(workspace_root, {})
        client = clients.get(server_name)
        if client is None:
            raise KeyError(
                f"[MCP] Serveur {server_name!r} non initialisé pour workspace {workspace_root!r}"
            )

        return await client.call_tool(tool_name, arguments)

    async def close_workspace(self, workspace_root: str) -> None:
        """Ferme tous les clients MCP d'un workspace."""
        async with self._lock:
            clients = self._clients.pop(workspace_root, {})
            self._tools_cache.pop(workspace_root, None)
            for client in clients.values():
                try:
                    await client.close()
                except Exception:
                    pass

    async def close_all(self) -> None:
        """Ferme tous les clients MCP (appelé à l'arrêt du bot)."""
        async with self._lock:
            for workspace_root in list(self._clients.keys()):
                clients = self._clients.pop(workspace_root, {})
                for client in clients.values():
                    try:
                        await client.close()
                    except Exception:
                        pass
            self._tools_cache.clear()

    # ── Helpers internes ──────────────────────────────────────────────────────

    async def _init_clients(
        self,
        workspace_root: str,
        servers: List[MCPServerConfig],
    ) -> Dict[str, MCPHTTPClient]:
        """
        Crée ou réutilise les clients MCP pour la liste de serveurs donnée.
        Les clients existants dont la config n'a pas changé sont conservés.
        """
        existing = self._clients.get(workspace_root, {})
        new_clients: Dict[str, MCPHTTPClient] = {}

        for cfg in servers:
            if cfg.name in existing:
                # Réutiliser le client existant
                new_clients[cfg.name] = existing[cfg.name]
                continue

            # Créer et initialiser un nouveau client
            client = MCPHTTPClient(
                url=cfg.url,
                token=cfg.token,
                timeout=cfg.timeout,
                server_name=cfg.name,
            )
            try:
                await client.initialize()
                new_clients[cfg.name] = client
                logger.info(f"[MCP] Client initialisé: {cfg.name} ({cfg.url})")
            except Exception as e:
                logger.warning(
                    f"[MCP] Impossible d'initialiser {cfg.name} ({cfg.url}): {e}"
                )

        # Fermer les clients qui ne sont plus dans la config
        for name, client in existing.items():
            if name not in new_clients:
                try:
                    await client.close()
                except Exception:
                    pass

        self._clients[workspace_root] = new_clients
        return new_clients

    @staticmethod
    def _split_qualified_name(qualified: str) -> Tuple[str, str]:
        """
        Découpe "server_name__tool_name" en (server_name, tool_name).
        Si pas de séparateur, retourne ("", qualified).
        """
        if TOOL_NAME_SEP in qualified:
            idx = qualified.index(TOOL_NAME_SEP)
            return qualified[:idx], qualified[idx + len(TOOL_NAME_SEP):]
        return "", qualified


# ─── Singleton global ─────────────────────────────────────────────────────────

_registry: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    """Retourne le singleton MCPRegistry global (crée à la première demande)."""
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


async def close_mcp_registry() -> None:
    """Ferme proprement le registre MCP (à appeler à l'arrêt du bot)."""
    global _registry
    if _registry is not None:
        await _registry.close_all()
        _registry = None
