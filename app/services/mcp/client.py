"""
Client MCP complet — spec 2025-03-26.

Implémente toutes les capacités standard d'un client MCP :

  Client → Serveur :
    • tools/list, tools/call (avec progressToken)
    • resources/list, resources/read
    • resources/subscribe, resources/unsubscribe
    • prompts/list, prompts/get
    • completion/complete
    • logging/setLevel
    • ping

  Serveur → Client (via SSE) :
    • sampling/createMessage  → délégué à sampling_handler
    • roots/list              → délégué à roots_provider
    • notifications/tools/list_changed
    • notifications/resources/list_changed
    • notifications/resources/updated
    • notifications/prompts/list_changed
    • notifications/message   (logging)
    • notifications/progress  → délégué à progress_handler
    • notifications/cancelled

Usage :
    async with MCPClient(url, token, server_name="mon-serveur") as client:
        client.sampling_handler = my_async_fn       # pour sampling
        client.roots_provider   = my_roots_fn       # pour roots/list
        tools = await client.list_tools()
        result = await client.call_tool("name", {"arg": "val"})
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from app.services.mcp.transport import StreamableHTTPTransport
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

logger = logging.getLogger("colaig.mcp.client")


class MCPClient:
    """
    Client MCP haut-niveau.

    Paramètres de construction :
        url         : endpoint HTTP du serveur MCP
        token       : Bearer token optionnel
        timeout     : timeout en secondes pour les appels
        server_name : nom logique du serveur (pour nommer les outils qualifiés)

    Callbacks configurables (assigner avant d'appeler initialize) :
        sampling_handler(messages, system_prompt, max_tokens, model_prefs) → str
            Appelé quand le serveur demande une génération LLM.

        roots_provider() → List[MCPRoot]
            Appelé quand le serveur demande la liste des racines workspace.

        on_progress(token, progress, total, message) → None
            Appelé à chaque notification de progression.

        on_resource_updated(uri) → None
            Appelé quand une ressource souscrite a changé.

        on_log(level, logger_name, data) → None
            Appelé pour les messages de log du serveur.
    """

    def __init__(
        self,
        url: str,
        token: Optional[str] = None,
        timeout: float = 30.0,
        server_name: str = "",
    ):
        self.server_name = server_name
        self._transport = StreamableHTTPTransport(url=url, token=token, timeout=timeout)

        # Capacités négociées à l'init
        self._server_caps: Optional[ServerCapabilities] = None
        self._initialized = False

        # Caches
        self._tools_cache: Optional[List[MCPTool]] = None
        self._resources_cache: Optional[List[MCPResource]] = None
        self._prompts_cache: Optional[List[MCPPrompt]] = None

        # Callbacks
        self.sampling_handler: Optional[Callable] = None
        self.roots_provider: Optional[Callable] = None
        self.on_progress: Optional[Callable] = None
        self.on_resource_updated: Optional[Callable] = None
        self.on_log: Optional[Callable] = None

        # Wirer les handlers du transport
        self._transport.on_server_request = self._handle_server_request
        self._transport.on_notification = self._handle_notification

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    async def __aenter__(self) -> "MCPClient":
        await self.initialize()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def initialize(self) -> ServerCapabilities:
        """
        Handshake MCP complet :
          1. Démarre le transport (ouvre le client HTTP + SSE listener)
          2. Envoie initialize avec toutes les capacités client supportées
          3. Envoie notifications/initialized
          4. Stocke et retourne les capacités serveur négociées
        """
        if self._initialized:
            return self._server_caps

        await self._transport.start(open_sse=True)

        result = await self._transport.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "colaig",
                "version": "1.0",
            },
        })

        self._server_caps = ServerCapabilities.from_init_result(result)
        self._initialized = True

        await self._transport.notify("notifications/initialized", {})

        logger.info(
            f"[MCP] {self.server_name!r} initialisé — "
            f"tools={self._server_caps.has_tools}, "
            f"resources={self._server_caps.has_resources}, "
            f"prompts={self._server_caps.has_prompts}, "
            f"sampling={bool(self.sampling_handler)}"
        )
        return self._server_caps

    async def close(self) -> None:
        """Ferme proprement la connexion."""
        if self._initialized:
            try:
                await self._transport.notify(
                    "notifications/cancelled",
                    {"requestId": "session", "reason": "client closing"},
                )
            except Exception:
                pass
        await self._transport.stop()
        self._initialized = False

    @property
    def server_capabilities(self) -> Optional[ServerCapabilities]:
        return self._server_caps

    # ── Ping ──────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Vérifie que le serveur est vivant. Retourne True si réponse reçue."""
        try:
            await self._transport.request("ping", {})
            return True
        except Exception as e:
            logger.warning(f"[MCP] Ping {self.server_name} échoué: {e}")
            return False

    # ── Tools ─────────────────────────────────────────────────────────────────

    async def list_tools(self, force_refresh: bool = False) -> List[MCPTool]:
        """Liste les outils disponibles (mise en cache automatique)."""
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        if self._server_caps and not self._server_caps.has_tools:
            return []

        tools: List[MCPTool] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._transport.request("tools/list", params)
            tools.extend(
                MCPTool.from_dict(t, server_name=self.server_name)
                for t in result.get("tools", [])
            )
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self._tools_cache = tools
        logger.info(f"[MCP] {self.server_name}: {len(tools)} outil(s)")
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        progress_token: Optional[str] = None,
    ) -> MCPToolResult:
        """
        Appelle un outil MCP.

        progress_token : si fourni, le serveur enverra des notifications/progress
                         que le client redirigera vers on_progress.
        """
        params: Dict[str, Any] = {"name": tool_name, "arguments": arguments}
        if progress_token:
            params["_meta"] = {"progressToken": progress_token}

        logger.info(f"[MCP] call_tool {self.server_name}/{tool_name}")
        result = await self._transport.request(
            "tools/call", params, timeout=60.0
        )
        return MCPToolResult.from_dict(tool_name, result)

    # ── Resources ─────────────────────────────────────────────────────────────

    async def list_resources(self, force_refresh: bool = False) -> List[MCPResource]:
        """Liste les ressources disponibles (mise en cache automatique)."""
        if self._resources_cache is not None and not force_refresh:
            return self._resources_cache

        if self._server_caps and not self._server_caps.has_resources:
            return []

        resources: List[MCPResource] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._transport.request("resources/list", params)
            resources.extend(
                MCPResource.from_dict(r, server_name=self.server_name)
                for r in result.get("resources", [])
            )
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self._resources_cache = resources
        return resources

    async def read_resource(self, uri: str) -> MCPResourceContent:
        """Lit le contenu d'une ressource par son URI."""
        result = await self._transport.request("resources/read", {"uri": uri})
        return MCPResourceContent.from_dict(result)

    async def subscribe_resource(self, uri: str) -> None:
        """Souscrit aux mises à jour d'une ressource (notifications/resources/updated)."""
        if self._server_caps and not self._server_caps.resources_subscribe:
            logger.debug(f"[MCP] {self.server_name} ne supporte pas resources/subscribe")
            return
        await self._transport.request("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: str) -> None:
        """Annule la souscription aux mises à jour d'une ressource."""
        await self._transport.request("resources/unsubscribe", {"uri": uri})

    # ── Prompts ───────────────────────────────────────────────────────────────

    async def list_prompts(self, force_refresh: bool = False) -> List[MCPPrompt]:
        """Liste les prompts disponibles (mise en cache automatique)."""
        if self._prompts_cache is not None and not force_refresh:
            return self._prompts_cache

        if self._server_caps and not self._server_caps.has_prompts:
            return []

        prompts: List[MCPPrompt] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._transport.request("prompts/list", params)
            prompts.extend(
                MCPPrompt.from_dict(p, server_name=self.server_name)
                for p in result.get("prompts", [])
            )
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self._prompts_cache = prompts
        return prompts

    async def get_prompt(
        self,
        name: str,
        arguments: Optional[Dict[str, str]] = None,
    ) -> MCPPromptResult:
        """Récupère un prompt instancié avec ses arguments."""
        params: Dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        result = await self._transport.request("prompts/get", params)
        return MCPPromptResult.from_dict(result)

    # ── Completion ────────────────────────────────────────────────────────────

    async def complete(
        self,
        ref_type: str,          # "ref/prompt" | "ref/resource"
        ref_name_or_uri: str,
        argument_name: str,
        argument_value: str,
    ) -> MCPCompletionResult:
        """
        Demande une autocomplétion pour un argument de prompt ou de resource.

        ref_type          : "ref/prompt" ou "ref/resource"
        ref_name_or_uri   : nom du prompt ou URI de la ressource template
        argument_name     : nom de l'argument à compléter
        argument_value    : valeur partielle saisie
        """
        if self._server_caps and not self._server_caps.has_completions:
            return MCPCompletionResult()

        ref: Dict[str, Any] = {"type": ref_type}
        if ref_type == "ref/prompt":
            ref["name"] = ref_name_or_uri
        else:
            ref["uri"] = ref_name_or_uri

        result = await self._transport.request("completion/complete", {
            "ref": ref,
            "argument": {"name": argument_name, "value": argument_value},
        })
        return MCPCompletionResult.from_dict(result)

    # ── Logging ───────────────────────────────────────────────────────────────

    async def set_log_level(self, level: str) -> None:
        """
        Demande au serveur de filtrer ses notifications de log au niveau donné.
        Niveaux : debug, info, notice, warning, error, critical, alert, emergency
        """
        if self._server_caps and not self._server_caps.has_logging:
            return
        await self._transport.request("logging/setLevel", {"level": level})

    # ── Handlers serveur→client ───────────────────────────────────────────────

    async def _handle_server_request(
        self,
        method: str,
        req_id: Any,
        params: Dict[str, Any],
    ) -> Any:
        """
        Dispatche les requêtes serveur→client reçues via SSE.
        Doit retourner le résultat à renvoyer au serveur.
        """
        if method == "sampling/createMessage":
            return await self._on_sampling(params)

        elif method == "roots/list":
            return await self._on_roots_list()

        else:
            raise NotImplementedError(f"Méthode serveur non supportée: {method}")

    async def _handle_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Dispatche les notifications serveur→client reçues via SSE."""

        if method == "notifications/tools/list_changed":
            logger.info(f"[MCP] {self.server_name}: liste d'outils modifiée")
            self._tools_cache = None  # invalide le cache

        elif method == "notifications/resources/list_changed":
            logger.info(f"[MCP] {self.server_name}: liste de ressources modifiée")
            self._resources_cache = None

        elif method == "notifications/resources/updated":
            uri = params.get("uri", "")
            logger.info(f"[MCP] {self.server_name}: ressource mise à jour: {uri}")
            if self.on_resource_updated:
                try:
                    r = self.on_resource_updated(uri)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:
                    logger.warning(f"[MCP] on_resource_updated erreur: {e}")

        elif method == "notifications/prompts/list_changed":
            logger.info(f"[MCP] {self.server_name}: liste de prompts modifiée")
            self._prompts_cache = None

        elif method == "notifications/progress":
            token = params.get("progressToken")
            progress = params.get("progress", 0)
            total = params.get("total")
            message = params.get("message", "")
            logger.debug(f"[MCP] Progress {token}: {progress}/{total} — {message}")
            if self.on_progress:
                try:
                    r = self.on_progress(token, progress, total, message)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:
                    logger.warning(f"[MCP] on_progress erreur: {e}")

        elif method == "notifications/message":
            level = params.get("level", "info")
            logger_name = params.get("logger", "")
            data = params.get("data", {})
            logger.debug(f"[MCP] Log serveur [{level}] {logger_name}: {data}")
            if self.on_log:
                try:
                    r = self.on_log(level, logger_name, data)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:
                    logger.warning(f"[MCP] on_log erreur: {e}")

        elif method == "notifications/cancelled":
            req_id = params.get("requestId")
            reason = params.get("reason", "")
            logger.info(f"[MCP] Annulation reçue pour requestId={req_id}: {reason}")

        else:
            logger.debug(f"[MCP] Notification inconnue ignorée: {method}")

    # ── Sampling (requête serveur→client) ─────────────────────────────────────

    async def _on_sampling(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une requête sampling/createMessage du serveur.
        Délègue à self.sampling_handler si défini.

        Le handler doit avoir la signature :
            async def handler(messages, system_prompt, max_tokens, model_prefs) -> str
        """
        if not self.sampling_handler:
            raise RuntimeError(
                "sampling/createMessage reçu mais aucun sampling_handler configuré"
            )

        messages = params.get("messages", [])
        system_prompt = params.get("systemPrompt")
        max_tokens = params.get("maxTokens", 1024)
        model_prefs = params.get("modelPreferences", {})

        response_text = await self.sampling_handler(
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            model_prefs=model_prefs,
        )

        return {
            "role": "assistant",
            "content": {"type": "text", "text": response_text},
            "model": "colaig/albert",
            "stopReason": "endTurn",
        }

    # ── Roots (requête serveur→client) ────────────────────────────────────────

    async def _on_roots_list(self) -> Dict[str, Any]:
        """
        Traite une requête roots/list du serveur.
        Délègue à self.roots_provider si défini.

        Le provider doit avoir la signature :
            def/async def provider() -> List[MCPRoot]
        """
        roots: List[MCPRoot] = []
        if self.roots_provider:
            try:
                result = self.roots_provider()
                if asyncio.iscoroutine(result):
                    result = await result
                roots = result or []
            except Exception as e:
                logger.warning(f"[MCP] roots_provider erreur: {e}")

        return {"roots": [r.to_dict() for r in roots]}
