"""
Colaig — WebChatMessaging

Implémente MessagingProtocol via WebSocket FastAPI.
Activé par MESSAGING_BACKEND=webchat.

Le canal est interne à FastAPI : les clients navigateur se connectent
via ws://.../chat/ws/{conv_id}. Aucune connexion sortante.

run() est passif (dort). Le WebSocket est géré par handle_websocket()
appelé depuis web/routes.py.

Multi-onglets : plusieurs connexions WebSocket sur le même conv_id
sont supportées — send() diffuse à toutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable

from colaig.models import ConversationType, IncomingMessage

logger = logging.getLogger(__name__)


class WebChatMessaging:
    """MessagingProtocol via WebSocket FastAPI.

    Architecture :
        - 1 liste de connexions WebSocket par conv_id (multi-onglets)
        - send() diffuse à toutes les connexions actives du conv_id
        - handle_websocket() est appelé par web/routes.py pour chaque connexion
        - run() dort — la boucle d'écoute est gérée par le WebSocket FastAPI
    """

    def __init__(self) -> None:
        self._handler: Callable | None = None
        # conv_id → liste des WebSocket actifs (multi-onglets supportés)
        self._connections: dict[str, list] = defaultdict(list)
        logger.info("WebChatMessaging actif (MESSAGING_BACKEND=webchat)")

    def on_message(self, callback: Callable) -> None:
        self._handler = callback

    async def connect(self) -> None:
        pass

    async def run(self) -> None:
        # Passif — la boucle d'écoute est dans handle_websocket()
        while True:
            await asyncio.sleep(3600)

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        payload = json.dumps(
            {"type": "chat_message", "role": "assistant", "text": text, "is_status": is_status},
            ensure_ascii=False,
        )
        dead = []
        for ws in list(self._connections.get(conversation_id, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[conversation_id].remove(ws)

    async def send_typing(
        self,
        conversation_id: str,
        typing: bool = True,
        timeout: int = 10000,
    ) -> None:
        if not typing:
            return
        payload = json.dumps({"type": "chat_thinking"})
        for ws in list(self._connections.get(conversation_id, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                pass

    async def handle_websocket(self, ws, conv_id: str, user_id: str) -> None:
        """Point d'entrée WebSocket depuis web/routes.py.

        Args:
            ws: WebSocket FastAPI.
            conv_id: Identifiant de la conversation (depuis l'URL).
            user_id: Identifiant utilisateur (depuis query param, libre).
        """
        from fastapi.websockets import WebSocketDisconnect

        await ws.accept()
        self._connections[conv_id].append(ws)

        # Invariant 2 SIGNAL (spec dual-mode) — premier événement à la connexion
        await ws.send_text(json.dumps({"type": "x-mode", "mode": "standalone"}))

        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue

                body = msg.get("body", "").strip()
                if not body:
                    continue

                # uid canonique conforme spec dual-mode Couche 3
                uid = msg.get("user_id", user_id)
                incoming = IncomingMessage(
                    user_id=f"uid:app:webchat:{uid}",
                    conversation_id=conv_id,
                    body=body,
                    conversation_type=ConversationType.DM,
                    platform="webchat",
                )

                if self._handler:
                    await self._handler(incoming)

        except (WebSocketDisconnect, Exception):
            pass
        finally:
            conns = self._connections.get(conv_id, [])
            if ws in conns:
                conns.remove(ws)
