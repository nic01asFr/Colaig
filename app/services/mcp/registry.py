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
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
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
    ServerCapabilities,
)
from app.services.mcp.workspace_loader import (
    MCPServerConfig,
    build_default_servers,
    load_mcp_servers_from_webdav,
)

logger = logging.getLogger("colaig.mcp.registry")

TOOL_NAME_SEP = "__"


@dataclass
class _CacheEntry:
    """Entrée du cache d'appels MCP : valeur + timestamp + scope d'origine."""
    value: str
    ts: float
    workspace_root: str  # workspace concerné (vide si scope=server)


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
        # Configuration de chaque serveur initialisé : workspace_root → {server_name: MCPServerConfig}
        # Permet de retrouver le cache_scope et les instructions sans relire les fichiers.
        self._server_configs: Dict[str, Dict[str, MCPServerConfig]] = {}
        # Cache des résultats d'appels d'outils MCP. Clé construite selon
        # le cache_scope du serveur (server : partagée, workspace : isolée).
        # Cette structure remplace l'ancien singleton app/agent/cache.py.
        self._call_cache: Dict[str, "_CacheEntry"] = {}
        self._lock = asyncio.Lock()

        # Callback global pour le sampling — injecté par l'application
        # Signature : async (messages, system_prompt, max_tokens, model_prefs, config) → str
        self._sampling_callback: Optional[Callable] = None
        # Config applicative (pour generate())
        self._app_config: Any = None
        # URL de base WebDAV pour construire les roots MCP scopées au workspace
        # Ex: "https://webdav.host/documents" → root = "{base}/{workspace_root}"
        self._webdav_base_url: str = ""
        # Pool de serveurs MCP par défaut de l'instance (depuis Config.mcp_default_servers)
        self._default_servers: List[MCPServerConfig] = []
        # Paramètres du cache d'appels (modifiables via configure())
        self._call_cache_ttl: float = 300.0
        self._call_cache_max: int = 200
        # Stats globales (hits/misses)
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(
        self,
        app_config: Any,
        sampling_callback: Optional[Callable] = None,
        webdav_base_url: str = "",
    ):
        """
        Configure le registre avec la config applicative et le callback de sampling.

        sampling_callback : async fn(messages, system_prompt, max_tokens, model_prefs) → str
            Typiquement un wrapper autour de generate() de core_llm.

        webdav_base_url : URL de base WebDAV complète (ex: "https://webdav.host/documents")
            Utilisée pour construire les URIs de roots MCP scopées au workspace.
            Format final : "{webdav_base_url}/{workspace_root}"
            Permet aux serveurs MCP de référencer des ressources fichier du workspace.
        """
        self._app_config = app_config
        self._sampling_callback = sampling_callback
        self._webdav_base_url = webdav_base_url.rstrip("/")
        # Construire le pool de défauts depuis la config applicative
        raw_defaults = getattr(app_config, "mcp_default_servers", []) or []
        self._default_servers = build_default_servers(raw_defaults)

        # Lire les paramètres du cache depuis le YAML agent (optionnel).
        # Ne dépend pas du loader pour rester sans dépendance circulaire.
        try:
            from app.agent.config_loader import get_cache_config
            cache_cfg = get_cache_config() or {}
            self._call_cache_ttl = float(cache_cfg.get("ttl_seconds", 300))
            self._call_cache_max = int(cache_cfg.get("max_size", 200))
        except Exception:
            # config_loader peut être indisponible (tests, etc.) — on garde les défauts
            pass

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

            servers = await load_mcp_servers_from_webdav(
                webdav_service, workspace_root,
                default_servers=self._default_servers,
            )
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

    def get_server_instructions(self, workspace_root: str) -> Dict[str, str]:
        """Retourne les instructions serveur pour chaque serveur initialisé.

        Source unique de vérité pour les hints injectés au LLM.
        Priorité (du plus fort au plus faible) :
          1. Instructions natives du serveur (champ initialize.instructions)
          2. Champ instructions du MCPServerConfig (déclaré localement)
          3. Pas d'entrée si aucune des deux

        Returns:
            Dict[server_name, instructions]
        """
        result: Dict[str, str] = {}
        clients = self._clients.get(workspace_root, {})
        configs = self._server_configs.get(workspace_root, {})

        for name, client in clients.items():
            # 1. Natif depuis le handshake initialize
            caps = client.server_capabilities
            if caps and caps.instructions:
                result[name] = caps.instructions
                continue
            # 2. Fallback : instructions déclarées dans MCPServerConfig
            cfg = configs.get(name)
            if cfg and cfg.instructions:
                result[name] = cfg.instructions
        return result

    # ── Cache d'appels d'outils (scopé par cache_scope du serveur) ───────────

    def _build_cache_key(
        self,
        server_name: str,
        workspace_root: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """Construit la clé de cache selon le cache_scope du serveur.

        Returns:
            Clé string si le serveur est cacheable, None si cache_scope='none'.
        """
        # Trouver la config du serveur (peut être dans n'importe quel workspace
        # initialisé, on cherche dans le workspace courant en priorité)
        cfg = self._server_configs.get(workspace_root, {}).get(server_name)
        if cfg is None:
            for ws_configs in self._server_configs.values():
                if server_name in ws_configs:
                    cfg = ws_configs[server_name]
                    break
        if cfg is None:
            # Inconnu : pas de cache
            return None

        scope = (cfg.cache_scope or "workspace").lower()
        if scope == "none":
            return None

        try:
            args_str = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(arguments)

        if scope == "server":
            # Partagé entre tous les workspaces
            return f"server::{server_name}::{tool_name}::{args_str}"
        # Défaut : workspace
        return f"ws::{workspace_root}::{server_name}::{tool_name}::{args_str}"

    def _cache_get(self, key: str) -> Optional[str]:
        entry = self._call_cache.get(key)
        if entry is None:
            self._cache_misses += 1
            return None
        if time.time() - entry.ts > self._call_cache_ttl:
            del self._call_cache[key]
            self._cache_misses += 1
            return None
        # LRU : remettre en fin
        if isinstance(self._call_cache, OrderedDict):
            self._call_cache.move_to_end(key)
        self._cache_hits += 1
        return entry.value

    def _cache_set(self, key: str, value: str, workspace_root: str) -> None:
        # Migrer vers OrderedDict à la première écriture pour bénéficier du LRU
        if not isinstance(self._call_cache, OrderedDict):
            self._call_cache = OrderedDict(self._call_cache)
        self._call_cache[key] = _CacheEntry(
            value=value, ts=time.time(), workspace_root=workspace_root,
        )
        self._call_cache.move_to_end(key)
        # Éviction LRU
        while len(self._call_cache) > self._call_cache_max:
            self._call_cache.popitem(last=False)

    def cache_stats(self) -> dict:
        """Retourne les statistiques d'usage du cache d'appels."""
        total = self._cache_hits + self._cache_misses
        ratio = (self._cache_hits / total) if total > 0 else 0.0
        return {
            "size": len(self._call_cache),
            "max_size": self._call_cache_max,
            "ttl_seconds": self._call_cache_ttl,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_ratio": round(ratio, 3),
        }

    async def call_tool(
        self,
        workspace_root: str,
        qualified_name: str,
        arguments: Dict[str, Any],
        progress_token: Optional[str] = None,
        on_progress: Optional[Callable] = None,
        use_cache: bool = True,
    ) -> MCPToolResult:
        """Appelle un outil MCP par son nom qualifié 'server__tool'.

        Args:
            workspace_root: Workspace courant (clé du registry de clients).
            qualified_name: 'server__tool'.
            arguments: Arguments de l'outil.
            progress_token: ID de progression optionnel.
            on_progress: Callback optionnel installé le temps de l'appel.
            use_cache: Si True, utilise le cache d'appels selon le cache_scope
                du serveur (défaut : True). Le scope est défini dans
                MCPServerConfig.cache_scope.
        """
        server_name, tool_name = _split_qualified(qualified_name)
        client = self._get_client(workspace_root, server_name)

        # Cache hit ?
        cache_key = None
        if use_cache:
            cache_key = self._build_cache_key(
                server_name, workspace_root, tool_name, arguments,
            )
            if cache_key is not None:
                cached = self._cache_get(cache_key)
                if cached is not None:
                    logger.info(f"[MCP-CACHE] hit: {qualified_name}")
                    return MCPToolResult.from_dict(
                        tool_name,
                        {"content": [{"type": "text", "text": cached}], "isError": False},
                    )

        # Appel réel
        prev_progress = client.on_progress
        if on_progress:
            client.on_progress = on_progress
        try:
            result = await client.call_tool(
                tool_name, arguments, progress_token=progress_token,
            )
        finally:
            client.on_progress = prev_progress

        # Cache set si applicable et pas une erreur
        if cache_key is not None and not result.is_error:
            self._cache_set(cache_key, result.text or "", workspace_root)

        return result

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
                        webdav_service, workspace_root,
                        default_servers=self._default_servers,
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

            # Câbler roots — URI WebDAV complète scopée au workspace
            # Si webdav_base_url est configuré : "https://host/documents/workspace_root"
            # Sinon fallback sur le chemin relatif pour ne pas bloquer
            _workspace = workspace_root
            _base = self._webdav_base_url

            def _roots_provider(_wp=_workspace, _base=_base) -> List[MCPRoot]:
                if _base:
                    # URI complète et opérable par un serveur MCP accédant aux fichiers
                    wp_clean = _wp.strip("/")
                    uri = f"{_base}/{wp_clean}" if wp_clean else _base
                else:
                    # Fallback : chemin relatif (serveur MCP ne peut pas l'utiliser
                    # pour accéder aux fichiers mais identifie le scope du workspace)
                    uri = f"webdav://{_wp}"
                name = _wp.rstrip("/").rsplit("/", 1)[-1] or "workspace"
                return [MCPRoot(uri=uri, name=name)]

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

        # Mémoriser les configs des serveurs initialisés (pour résolution
        # cache_scope et instructions sans relire les fichiers).
        self._server_configs[workspace_root] = {
            cfg.name: cfg for cfg in servers if cfg.name in new_clients
        }

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
        """Ferme un workspace : ferme ses clients, vide ses caches scopés workspace.

        Le cache d'appels avec scope='server' (donnée publique partagée) est
        préservé car il est valide pour les autres workspaces actifs.
        """
        async with self._lock:
            clients = self._clients.pop(workspace_root, {})
            self._tools_cache.pop(workspace_root, None)
            self._server_configs.pop(workspace_root, None)
            for client in clients.values():
                try:
                    await client.close()
                except Exception:
                    pass

            # Purger les entrées du cache d'appels scopées à ce workspace.
            # Les entrées scope='server' (donnée partagée) sont conservées.
            keys_to_remove = [
                k for k, v in self._call_cache.items()
                if v.workspace_root == workspace_root
            ]
            for k in keys_to_remove:
                del self._call_cache[k]
            if keys_to_remove:
                logger.info(
                    f"[MCP] close_workspace {workspace_root!r}: "
                    f"{len(keys_to_remove)} entrée(s) de cache purgée(s)"
                )

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
            self._server_configs.clear()
            self._call_cache.clear()


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
