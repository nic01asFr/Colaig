"""
Transport Streamable HTTP pour MCP (spec 2025-03-26).

Gère les deux flux du protocole :
  • POST  → requêtes et notifications client→serveur
  • GET SSE → flux persistant serveur→client (sampling, roots, notifications)

Le stream SSE est ouvert en tâche background via start_sse_listener().
Les messages entrants sont dispatchés via le dictionnaire _handlers.

Usage :
    transport = StreamableHTTPTransport(url, token)
    await transport.start(sse=True)          # POST initialize + ouvre SSE
    result = await transport.request("tools/list", {})
    await transport.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger("colaig.mcp.transport")

MCP_PROTOCOL_VERSION = "2025-03-26"
_DEFAULT_TIMEOUT = 30.0
_SSE_RECONNECT_DELAY = 5.0   # secondes avant reconnexion SSE


class StreamableHTTPTransport:
    """
    Couche transport bas-niveau : POST JSON-RPC + GET SSE bidirectionnel.

    Callbacks disponibles (setter properties) :
        on_server_request(method, id, params) → Any
            Appelé pour chaque requête serveur→client (sampling, roots/list).
            Doit retourner le résultat à renvoyer. Peut être une coroutine.

        on_notification(method, params) → None
            Appelé pour chaque notification serveur→client (fire-and-forget).
    """

    def __init__(
        self,
        url: str,
        token: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

        self._session_id: Optional[str] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._sse_task: Optional[asyncio.Task] = None
        self._sse_active = False
        self._req_counter = 0

        # Callbacks — remplis par MCPClient
        self._on_server_request: Optional[Callable] = None
        self._on_notification: Optional[Callable] = None

    # ── Callbacks ─────────────────────────────────────────────────────────────

    @property
    def on_server_request(self) -> Optional[Callable]:
        return self._on_server_request

    @on_server_request.setter
    def on_server_request(self, fn: Callable):
        self._on_server_request = fn

    @property
    def on_notification(self) -> Optional[Callable]:
        return self._on_notification

    @on_notification.setter
    def on_notification(self, fn: Callable):
        self._on_notification = fn

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    async def start(self, open_sse: bool = True) -> None:
        """Ouvre le client HTTP et, optionnellement, la connexion SSE."""
        self._http = httpx.AsyncClient(timeout=self.timeout)
        if open_sse:
            await self._launch_sse_listener()

    async def stop(self) -> None:
        """Ferme le stream SSE et le client HTTP."""
        self._sse_active = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Envoi POST ────────────────────────────────────────────────────────────

    async def request(
        self,
        method: str,
        params: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Envoie une requête JSON-RPC et attend la réponse."""
        req_id = str(self._next_id())
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        return await self._post(payload, timeout=timeout or self.timeout)

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        """Envoie une notification JSON-RPC (sans id, pas de réponse attendue)."""
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._post(payload, is_notification=True)

    async def respond(self, req_id: Any, result: Any) -> None:
        """
        Répond à une requête reçue depuis le serveur (ex: sampling, roots/list).
        Envoyé via POST selon la spec Streamable HTTP.
        """
        payload = {"jsonrpc": "2.0", "id": req_id, "result": result}
        await self._post(payload, is_notification=True)  # la réponse est acceptée sans corps

    async def respond_error(self, req_id: Any, code: int, message: str) -> None:
        """Répond avec une erreur JSON-RPC à une requête serveur."""
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
        await self._post(payload, is_notification=True)

    # ── Transport interne POST ────────────────────────────────────────────────

    async def _post(
        self,
        payload: Dict[str, Any],
        is_notification: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self._http is None:
            raise RuntimeError("Transport non démarré (appeler start() d'abord)")

        headers = self._build_headers()
        try:
            response = await self._http.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=timeout or self.timeout,
            )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"[MCP] Timeout POST {payload.get('method', '?')}") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"[MCP] Erreur réseau: {e}") from e

        # Extraire le session_id si présent
        sid = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        if is_notification:
            # 202 Accepted ou 200 sans corps — pas de résultat à parser
            return {}

        if response.status_code == 202:
            return {}

        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return await self._consume_sse_response(response)
        else:
            return self._parse_jsonrpc_response(response.json())

    def _build_headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_jsonrpc_response(data: Dict[str, Any]) -> Dict[str, Any]:
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"[MCP] Erreur JSON-RPC {err.get('code')}: {err.get('message')}"
            )
        return data.get("result", data)

    async def _consume_sse_response(self, response: httpx.Response) -> Dict[str, Any]:
        """
        Consomme un stream SSE en réponse à un POST.
        Accumule les événements jusqu'à recevoir le résultat correspondant.
        """
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                event_raw, buffer = buffer.split("\n\n", 1)
                msg = self._parse_sse_event(event_raw)
                if msg is None:
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(
                        f"[MCP] Erreur JSON-RPC SSE {err.get('code')}: {err.get('message')}"
                    )
                if "result" in msg:
                    return msg["result"]
        return {}

    # ── SSE listener background ───────────────────────────────────────────────

    async def _launch_sse_listener(self) -> None:
        """Lance la tâche background d'écoute du stream SSE serveur→client."""
        self._sse_active = True
        self._sse_task = asyncio.create_task(self._sse_listener_loop())

    async def _sse_listener_loop(self) -> None:
        """
        Boucle de reconnexion du stream SSE serveur→client.
        Gère la reprise avec Last-Event-ID.
        """
        last_event_id: Optional[str] = None
        while self._sse_active:
            try:
                await self._sse_listen(last_event_id=last_event_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._sse_active:
                    break
                logger.warning(
                    f"[MCP SSE] Déconnexion ({e}), reconnexion dans {_SSE_RECONNECT_DELAY}s"
                )
                await asyncio.sleep(_SSE_RECONNECT_DELAY)

    async def _sse_listen(self, last_event_id: Optional[str] = None) -> None:
        """Ouvre le stream SSE GET et traite les événements entrants."""
        if self._http is None:
            return

        headers: Dict[str, str] = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id

        # Timeout très long pour SSE — on veut garder la connexion ouverte
        sse_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0))
        try:
            async with sse_client.stream("GET", self.url, headers=headers) as response:
                if response.status_code == 405:
                    # Le serveur ne supporte pas GET SSE — fonctionnement POST-only
                    logger.info("[MCP SSE] Serveur sans stream SSE (405), mode POST-only")
                    return

                response.raise_for_status()
                logger.info(f"[MCP SSE] Stream ouvert vers {self.url}")

                buffer = ""
                current_event_id: Optional[str] = None

                async for chunk in response.aiter_text():
                    if not self._sse_active:
                        break
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_raw, buffer = buffer.split("\n\n", 1)
                        eid, msg = self._parse_sse_event_with_id(event_raw)
                        if eid:
                            current_event_id = eid
                            last_event_id = eid
                        if msg is not None:
                            await self._dispatch_server_message(msg)
        finally:
            await sse_client.aclose()

    async def _dispatch_server_message(self, msg: Dict[str, Any]) -> None:
        """
        Dispatche un message JSON-RPC reçu du serveur via SSE.

        - Requête (id + method) → appelle on_server_request, renvoie la réponse
        - Notification (method sans id) → appelle on_notification
        """
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if msg_id is not None:
            # C'est une requête — le client doit répondre
            logger.info(f"[MCP SSE] Requête serveur reçue: {method} (id={msg_id})")
            if self._on_server_request:
                try:
                    result = self._on_server_request(method, msg_id, params)
                    if asyncio.iscoroutine(result):
                        result = await result
                    await self.respond(msg_id, result)
                except Exception as e:
                    logger.error(f"[MCP SSE] Erreur traitement requête {method}: {e}")
                    await self.respond_error(msg_id, -32603, str(e))
            else:
                # Pas de handler → réponse d'erreur par défaut
                await self.respond_error(msg_id, -32601, f"Method not supported: {method}")
        else:
            # C'est une notification
            logger.debug(f"[MCP SSE] Notification serveur: {method}")
            if self._on_notification:
                try:
                    result = self._on_notification(method, params)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning(f"[MCP SSE] Erreur notification {method}: {e}")

    # ── Parsing SSE ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_sse_event(raw: str) -> Optional[Dict[str, Any]]:
        """Parse un bloc SSE brut et retourne le JSON-RPC ou None."""
        for line in raw.strip().splitlines():
            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                if data_str:
                    try:
                        return json.loads(data_str)
                    except json.JSONDecodeError:
                        pass
        return None

    @staticmethod
    def _parse_sse_event_with_id(raw: str):
        """Parse un bloc SSE et retourne (event_id, json_msg)."""
        event_id = None
        msg = None
        for line in raw.strip().splitlines():
            if line.startswith("id:"):
                event_id = line[3:].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                if data_str:
                    try:
                        msg = json.loads(data_str)
                    except json.JSONDecodeError:
                        pass
        return event_id, msg

    def _next_id(self) -> int:
        self._req_counter += 1
        return self._req_counter
