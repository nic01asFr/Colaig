"""
Registre MCP par workspace.

MCPRegistry maintient une instance MCPClient par serveur déclaré,
scoped par workspace (identifié par son webdav_root path).

Câble automatiquement :
  • sampling_handler → appelle generate(config, ...) pour les requêtes
    sampling/createMessage envoyées par les serveurs MCP
  • roots_provider   → retourne les MCPRoot du workspace courant

Point d'entrée global : get_mcp_registry()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.mcp.client import MCPClient
from app.services.mcp.types import (
    MCPCompletionResult,
    MCPPrompt,
    MCPPromptResult,
    MCPResource,
    MCPResourceContent,
    MCPRoot,
    MCPTool,
    MCPToolResult,
)
from app.services.mcp.workspace_loader import (
    MCPServerConfig,
    load_mcp_servers_from_webdav,
)

logger = logging.getLogger("colaig.mcp.registry")

TOOL_NAME_SEP = "__"


class MCPRegistry:
    """
    Gère les clients MCP pour tous les workspaces actifs.

    Chaque workspace est identifié par son workspace_root (chemin WebDAV).
    Un MCPClient est créé par serveur déclaré dans mcp_servers.json.

    Le registry câble automatiquement les callbacks sampling et roots
    sur chaque client à la création.
    """

    def __init__(self):
        # workspace_root → {server_name: MCPClient}
        self._clients: Dict[str, Dict[str, MCPClient]] = {}
        # Caches d'outils agrégés par workspace
        self._tools_cache: Dict[str, List[MCPTool]] = {}
        self._lock = asyncio.Lock()

        # Callback global pour le sampling — injecté par l'application
        # Signature : async (messages, system_prompt, max_tokens, model_prefs, config) → str
        self._sampling_callback: Optional[Callable] = None
        # Config applicative (pour generate())
        self._app_config: Any = None

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(self, app_config: Any, sampling_callback: Optional[Callable] = None):
        """
        Configure le registre avec la config applicative et le callback de sampling.

        sampling_callback : async fn(messages, system_prompt, max_tokens, model_prefs) → str
            Typiquement un wrapper autour de generate() de core_llm.
        """
        self._app_config = app_config
        self._sampling_callback = sampling_callback

    # ── API tools ─────────────────────────────────────────────────────────────

    async def get_tools(
        self,
        webdav_service,
        workspace_root: str,
        force_refresh: bool = False,
    ) -> List[MCPTool]:
        """Retourne la liste agrégée des outils MCP pour ce workspace."""
        if not force_refresh and workspace_root in self._tools_cache:
            return self._tools_cache[workspace_root]

        async with self._lock:
            if not force_refresh and workspace_root in self._tools_cache:
                return self._tools_cache[workspace_root]

            servers = await load_mcp_servers_from_webdav(webdav_service, workspace_root)
            if not servers:
                self._tools_cache[workspace_root] = []
                return []

            clients = await self._get_or_init_clients(workspace_root, servers)
            all_tools: List[MCPTool] = []

            for name, client in clients.items():
                try:
                    tools = await client.list_tools(force_refresh=force_refresh)
                    all_tools.extend(tools)
                except Exception as e:
                    logger.warning(f"[MCP] Erreur list_tools {name}: {e}")

            self._tools_cache[workspace_root] = all_tools
            logger.info(
                f"[MCP] Workspace {workspace_root!r}: {len(all_tools)} outil(s) total"
            )
            return all_tools

    async def call_tool(
        self,
        workspace_root: str,
        qualified_name: str,
        arguments: Dict[str, Any],
        progress_token: Optional[str] = None,
    ) -> MCPToolResult:
        """Appelle un outil MCP par son nom qualifié 'server__tool'."""
        server_name, tool_name = _split_qualified(qualified_name)
        client = self._get_client(workspace_root, server_name)
        return await client.call_tool(tool_name, arguments, progress_token=progress_token)

    # ── API resources ─────────────────────────────────────────────────────────

    async def get_resources(
        self,
        webdav_service,
        workspace_root: str,
    ) -> List[MCPResource]:
        """Retourne la liste agrégée des ressources MCP pour ce workspace."""
        clients = await self._ensure_clients(webdav_service, workspace_root)
        all_resources: List[MCPResource] = []
        for client in clients.values():
            try:
                resources = await client.list_resources()
                all_resources.extend(resources)
            except Exception as e:
                logger.warning(f"[MCP] Erreur list_resources {client.server_name}: {e}")
        return all_resources

    async def read_resource(
        self,
        workspace_root: str,
        server_name: str,
        uri: str,
    ) -> MCPResourceContent:
        """Lit le contenu d'une ressource MCP."""
        client = self._get_client(workspace_root, server_name)
        return await client.read_resource(uri)

    async def subscribe_resource(
        self, workspace_root: str, server_name: str, uri: str
    ) -> None:
        client = self._get_client(workspace_root, server_name)
        await client.subscribe_resource(uri)

    async def unsubscribe_resource(
        self, workspace_root: str, server_name: str, uri: str
    ) -> None:
        client = self._get_client(workspace_root, server_name)
        await client.unsubscribe_resource(uri)

    # ── API prompts ───────────────────────────────────────────────────────────

    async def get_prompts(
        self,
        webdav_service,
        workspace_root: str,
    ) -> List[MCPPrompt]:
        """Retourne la liste agrégée des prompts MCP pour ce workspace."""
        clients = await self._ensure_clients(webdav_service, workspace_root)
        all_prompts: List[MCPPrompt] = []
        for client in clients.values():
            try:
                prompts = await client.list_prompts()
                all_prompts.extend(prompts)
            except Exception as e:
                logger.warning(f"[MCP] Erreur list_prompts {client.server_name}: {e}")
        return all_prompts

    async def get_prompt(
        self,
        workspace_root: str,
        server_name: str,
        prompt_name: str,
        arguments: Optional[Dict[str, str]] = None,
    ) -> MCPPromptResult:
        client = self._get_client(workspace_root, server_name)
        return await client.get_prompt(prompt_name, arguments)

    # ── API completion ────────────────────────────────────────────────────────

    async def complete(
        self,
        workspace_root: str,
        server_name: str,
        ref_type: str,
        ref_name_or_uri: str,
        argument_name: str,
        argument_value: str,
    ) -> MCPCompletionResult:
        client = self._get_client(workspace_root, server_name)
        return await client.complete(
            ref_type, ref_name_or_uri, argument_name, argument_value
        )

    # ── Gestion des clients ───────────────────────────────────────────────────

    async def _ensure_clients(
        self,
        webdav_service,
        workspace_root: str,
    ) -> Dict[str, MCPClient]:
        """Initialise les clients si nécessaire et retourne le dict."""
        if workspace_root not in self._clients:
            async with self._lock:
                if workspace_root not in self._clients:
                    servers = await load_mcp_servers_from_webdav(
                        webdav_service, workspace_root
                    )
                    await self._get_or_init_clients(workspace_root, servers)
        return self._clients.get(workspace_root, {})

    async def _get_or_init_clients(
        self,
        workspace_root: str,
        servers: List[MCPServerConfig],
    ) -> Dict[str, MCPClient]:
        """Crée ou réutilise les clients pour la liste de serveurs."""
        existing = self._clients.get(workspace_root, {})
        new_clients: Dict[str, MCPClient] = {}

        for cfg in servers:
            if cfg.name in existing:
                new_clients[cfg.name] = existing[cfg.name]
                continue

            client = MCPClient(
                url=cfg.url,
                token=cfg.token,
                timeout=cfg.timeout,
                server_name=cfg.name,
            )

            # Câbler sampling
            if self._sampling_callback:
                app_config = self._app_config
                sampling_cb = self._sampling_callback

                async def _sampling_handler(
                    messages, system_prompt, max_tokens, model_prefs,
                    _cb=sampling_cb, _cfg=app_config,
                ):
                    return await _cb(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        model_prefs=model_prefs,
                        config=_cfg,
                    )

                client.sampling_handler = _sampling_handler

            # Câbler roots (retourne le workspace comme racine)
            _workspace = workspace_root

            def _roots_provider(_wp=_workspace) -> List[MCPRoot]:
                return [MCPRoot(uri=f"webdav://{_wp}", name=_wp or "workspace")]

            client.roots_provider = _roots_provider

            try:
                await client.initialize()
                new_clients[cfg.name] = client
                logger.info(f"[MCP] Client initialisé: {cfg.name} ({cfg.url})")
            except Exception as e:
                logger.warning(f"[MCP] Impossible d'initialiser {cfg.name}: {e}")
                try:
                    await client.close()
                except Exception:
                    pass

        # Fermer les clients qui ne sont plus dans la config
        for name, client in existing.items():
            if name not in new_clients:
                try:
                    await client.close()
                except Exception:
                    pass

        self._clients[workspace_root] = new_clients
        return new_clients

    def _get_client(self, workspace_root: str, server_name: str) -> MCPClient:
        clients = self._clients.get(workspace_root, {})
        client = clients.get(server_name)
        if client is None:
            raise KeyError(
                f"[MCP] Serveur {server_name!r} non initialisé "
                f"pour workspace {workspace_root!r}"
            )
        return client

    # ── Fermeture ─────────────────────────────────────────────────────────────

    async def close_workspace(self, workspace_root: str) -> None:
        async with self._lock:
            clients = self._clients.pop(workspace_root, {})
            self._tools_cache.pop(workspace_root, None)
            for client in clients.values():
                try:
                    await client.close()
                except Exception:
                    pass

    async def close_all(self) -> None:
        async with self._lock:
            for clients in self._clients.values():
                for client in clients.values():
                    try:
                        await client.close()
                    except Exception:
                        pass
            self._clients.clear()
            self._tools_cache.clear()


# ─── Singleton global ─────────────────────────────────────────────────────────

_registry: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


async def close_mcp_registry() -> None:
    global _registry
    if _registry is not None:
        await _registry.close_all()
        _registry = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _split_qualified(qualified: str) -> Tuple[str, str]:
    """'server__tool' → ('server', 'tool'). Si pas de sep → ('', qualified)."""
    if TOOL_NAME_SEP in qualified:
        idx = qualified.index(TOOL_NAME_SEP)
        return qualified[:idx], qualified[idx + len(TOOL_NAME_SEP):]
    return "", qualified
