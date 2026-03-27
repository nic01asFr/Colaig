"""
Client MCP conforme à la spec Streamable HTTP (2025-03-26).

Transport : POST JSON-RPC vers un endpoint HTTP unique.
  - Réponse JSON simple  → Content-Type: application/json
  - Réponse streamée SSE → Content-Type: text/event-stream

Session : le serveur peut retourner un header `Mcp-Session-Id` ; le client
l'inclut dans toutes les requêtes suivantes.

Authentification : Bearer token statique (suffisant pour les services internes).
OAuth 2.1 non implémenté ici (à ajouter si besoin de serveurs publics).

Référence spec : https://modelcontextprotocol.io/specification/2025-03-26/
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger("colaig.mcp")

# Version de protocole supportée
MCP_PROTOCOL_VERSION = "2025-03-26"
# Timeout par défaut pour les appels d'outils (secondes)
DEFAULT_TOOL_TIMEOUT = 30.0


# ─── Modèles de données ───────────────────────────────────────────────────────

@dataclass
class MCPTool:
    """Décrit un outil exposé par un serveur MCP."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_name: str = ""  # ajouté par le registre pour identifier la source

    def to_llm_description(self) -> Dict[str, Any]:
        """Formate l'outil pour l'inclure dans un message système LLM."""
        return {
            "name": f"{self.server_name}__{self.name}" if self.server_name else self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


@dataclass
class MCPToolResult:
    """Résultat de l'appel d'un outil MCP."""
    tool_name: str
    content: str          # Texte consolidé issu des content blocks
    is_error: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, tool_name: str, result: Dict[str, Any]) -> "MCPToolResult":
        is_error = result.get("isError", False)
        content_blocks = result.get("content", [])
        # Consolidation des blocs texte/image/resource
        parts: List[str] = []
        for block in content_blocks:
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "image":
                parts.append(f"[image: {block.get('mimeType', 'unknown')}]")
            elif btype == "resource":
                uri = block.get("resource", {}).get("uri", "?")
                parts.append(f"[resource: {uri}]")
        return cls(
            tool_name=tool_name,
            content="\n".join(parts),
            is_error=is_error,
            raw=result,
        )


# ─── Client HTTP MCP ──────────────────────────────────────────────────────────

class MCPHTTPClient:
    """
    Client MCP Streamable HTTP.

    Cycle de vie :
        async with MCPHTTPClient(url, token) as client:
            tools = await client.list_tools()
            result = await client.call_tool("name", {"arg": "val"})

    Il est possible de le réutiliser sur plusieurs appels sans rouvrir
    une session : le session_id est conservé entre les appels.
    """

    def __init__(
        self,
        url: str,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TOOL_TIMEOUT,
        server_name: str = "",
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.server_name = server_name

        self._session_id: Optional[str] = None
        self._initialized: bool = False
        self._tools_cache: Optional[List[MCPTool]] = None
        self._http: Optional[httpx.AsyncClient] = None

    # ── Gestion du contexte async ─────────────────────────────────────────────

    async def __aenter__(self) -> "MCPHTTPClient":
        self._http = httpx.AsyncClient(timeout=self.timeout)
        await self.initialize()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def close(self) -> None:
        """Ferme la session HTTP et notifie le serveur si possible."""
        if self._http and self._initialized:
            try:
                # Notification de fin de session (best-effort, pas de réponse attendue)
                await self._post_jsonrpc(
                    "notifications/cancelled",
                    {"requestId": "session", "reason": "client closing"},
                    notification=True,
                )
            except Exception:
                pass
        if self._http:
            await self._http.aclose()
            self._http = None
        self._initialized = False

    # ── API publique ──────────────────────────────────────────────────────────

    async def initialize(self) -> Dict[str, Any]:
        """
        Handshake MCP : envoie `initialize` puis `notifications/initialized`.
        Doit être appelé une fois avant list_tools / call_tool.
        """
        if self._initialized:
            return {}

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)

        result = await self._post_jsonrpc("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "roots": {"listChanged": False},
            },
            "clientInfo": {
                "name": "colaig",
                "version": "1.0",
            },
        })

        # Notification (fire-and-forget, pas d'id)
        await self._post_jsonrpc(
            "notifications/initialized", {}, notification=True
        )

        self._initialized = True
        logger.info(
            f"[MCP] Initialisé avec {self.server_name!r} ({self.url}) "
            f"— session_id={self._session_id}"
        )
        return result

    async def list_tools(self, force_refresh: bool = False) -> List[MCPTool]:
        """Liste les outils disponibles sur ce serveur (mis en cache)."""
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        result = await self._post_jsonrpc("tools/list", {})
        tools_raw = result.get("tools", [])
        tools = [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.server_name,
            )
            for t in tools_raw
        ]
        self._tools_cache = tools
        logger.info(f"[MCP] {self.server_name}: {len(tools)} outil(s) disponible(s)")
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> MCPToolResult:
        """Appelle un outil MCP et retourne le résultat consolidé."""
        logger.info(f"[MCP] call_tool {self.server_name}/{tool_name} args={list(arguments.keys())}")
        result = await self._post_jsonrpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return MCPToolResult.from_result(tool_name, result)

    # ── Transport interne ─────────────────────────────────────────────────────

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post_jsonrpc(
        self,
        method: str,
        params: Dict[str, Any],
        notification: bool = False,
    ) -> Dict[str, Any]:
        """
        Envoie une requête JSON-RPC 2.0.

        Si notification=True, pas d'id → pas de réponse attendue.
        Gère les deux types de réponses : JSON direct ou SSE streamé.
        """
        if self._http is None:
            raise RuntimeError("MCPHTTPClient non initialisé (appeler initialize() ou utiliser async with)")

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if not notification:
            payload["id"] = str(uuid4())

        headers = self._build_headers()

        try:
            response = await self._http.post(
                self.url,
                json=payload,
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"[MCP] Timeout sur {self.server_name}/{method}") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"[MCP] Erreur réseau sur {self.server_name}: {e}") from e

        # Récupérer le session_id si présent
        if "mcp-session-id" in response.headers:
            self._session_id = response.headers["mcp-session-id"]
        elif "Mcp-Session-Id" in response.headers:
            self._session_id = response.headers["Mcp-Session-Id"]

        if notification:
            return {}

        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            return await self._parse_sse_response(response)
        else:
            return self._parse_json_response(response)

    def _parse_json_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Parse une réponse JSON-RPC directe."""
        data = response.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"[MCP] Erreur JSON-RPC {err.get('code')}: {err.get('message')}"
            )
        return data.get("result", data)

    async def _parse_sse_response(self, response: httpx.Response) -> Dict[str, Any]:
        """
        Parse une réponse SSE streamée.

        Chaque événement est de la forme :
            data: <json-rpc-message>

        On accumule jusqu'à recevoir le message de résultat (id correspondant).
        """
        result: Dict[str, Any] = {}
        buffer = ""

        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                lines = event_str.strip().splitlines()
                data_lines = [
                    l[len("data:"):].strip()
                    for l in lines
                    if l.startswith("data:")
                ]
                for data_str in data_lines:
                    if not data_str:
                        continue
                    try:
                        msg = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if "error" in msg:
                        err = msg["error"]
                        raise RuntimeError(
                            f"[MCP] Erreur JSON-RPC SSE {err.get('code')}: {err.get('message')}"
                        )
                    if "result" in msg:
                        result = msg["result"]
                        return result

        return result
