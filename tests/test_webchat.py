"""
Tests pour colaig/messaging/webchat.py — WebChatMessaging.

Stratégie : mock WebSocket FastAPI pour tester la logique applicative
sans serveur HTTP réel. Tests unitaires purs, pas de réseau.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from colaig.messaging.webchat import WebChatMessaging
from colaig.models import ConversationType, IncomingMessage


# =============================================================================
# Helper factories
# =============================================================================


class _MockWS:
    """Mock WebSocket minimal — évite les interférences AsyncMock."""

    def __init__(self, messages: list[str] | None = None):
        from fastapi.websockets import WebSocketDisconnect
        self._payloads = list(messages or []) + [None]
        self._idx = 0
        self._WSD = WebSocketDisconnect
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent.append(data)

    async def receive_text(self) -> str:
        val = self._payloads[min(self._idx, len(self._payloads) - 1)]
        self._idx += 1
        if val is None:
            raise self._WSD(code=1000)
        return val

    async def close(self, code: int = 1000):
        pass


def _make_ws(messages: list[str] | None = None) -> _MockWS:
    """Crée un mock WebSocket.

    Args:
        messages: Messages à retourner séquentiellement. Déconnexion après le dernier.
    """
    return _MockWS(messages)


def _make_wc() -> WebChatMessaging:
    return WebChatMessaging()


# =============================================================================
# Construction / on_message
# =============================================================================


class TestConstruction:
    def test_instanciation(self):
        wc = _make_wc()
        assert wc._handler is None
        assert len(wc._connections) == 0

    def test_on_message_registers_handler(self):
        wc = _make_wc()
        cb = AsyncMock()
        wc.on_message(cb)
        assert wc._handler is cb


# =============================================================================
# connect / run
# =============================================================================


class TestConnectRun:
    @pytest.mark.asyncio
    async def test_connect_is_noop(self):
        wc = _make_wc()
        await wc.connect()  # ne doit pas lever

    @pytest.mark.asyncio
    async def test_run_is_passive(self):
        """run() ne bloque pas indéfiniment — on vérifie qu'il dort."""
        wc = _make_wc()
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError
            with pytest.raises(asyncio.CancelledError):
                await wc.run()
            mock_sleep.assert_called_once_with(3600)


# =============================================================================
# send
# =============================================================================


class TestSend:
    @pytest.mark.asyncio
    async def test_send_to_active_connection(self):
        wc = _make_wc()
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        wc._connections["conv-1"].append(ws)

        await wc.send("conv-1", "Bonjour !")

        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "chat_message"
        assert payload["role"] == "assistant"
        assert payload["text"] == "Bonjour !"
        assert payload["is_status"] is False

    @pytest.mark.asyncio
    async def test_send_status_message(self):
        wc = _make_wc()
        ws = AsyncMock()
        wc._connections["conv-1"].append(ws)

        await wc.send("conv-1", "indexation en cours…", is_status=True)

        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["is_status"] is True

    @pytest.mark.asyncio
    async def test_send_to_unknown_conv_is_noop(self):
        """send() sur un conv_id sans connexion active ne lève pas."""
        wc = _make_wc()
        await wc.send("conv-inexistant", "message")  # ne doit pas lever

    @pytest.mark.asyncio
    async def test_send_multicast_to_multiple_tabs(self):
        """Deux onglets sur le même conv_id reçoivent le même message."""
        wc = _make_wc()
        ws1, ws2 = AsyncMock(), AsyncMock()
        wc._connections["conv-1"].extend([ws1, ws2])

        await wc.send("conv-1", "hello")

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_removes_dead_connections(self):
        """Une connexion morte (exception) est retirée de la liste."""
        wc = _make_wc()
        dead_ws = AsyncMock()
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        wc._connections["conv-1"].append(dead_ws)

        await wc.send("conv-1", "test")

        assert dead_ws not in wc._connections["conv-1"]

    @pytest.mark.asyncio
    async def test_send_dead_connection_does_not_block_others(self):
        """Une connexion morte ne bloque pas l'envoi aux autres."""
        wc = _make_wc()
        dead_ws = AsyncMock()
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        live_ws = AsyncMock()
        wc._connections["conv-1"].extend([dead_ws, live_ws])

        await wc.send("conv-1", "test")

        live_ws.send_text.assert_called_once()


# =============================================================================
# send_typing
# =============================================================================


class TestSendTyping:
    @pytest.mark.asyncio
    async def test_send_typing_true_pushes_event(self):
        wc = _make_wc()
        ws = AsyncMock()
        wc._connections["conv-1"].append(ws)

        await wc.send_typing("conv-1", typing=True)

        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "chat_thinking"

    @pytest.mark.asyncio
    async def test_send_typing_false_is_noop(self):
        wc = _make_wc()
        ws = AsyncMock()
        wc._connections["conv-1"].append(ws)

        await wc.send_typing("conv-1", typing=False)

        ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_typing_unknown_conv_is_noop(self):
        wc = _make_wc()
        await wc.send_typing("conv-inexistant")  # ne doit pas lever


# =============================================================================
# handle_websocket — connexion / déconnexion
# =============================================================================


class TestHandleWebsocketLifecycle:
    @pytest.mark.asyncio
    async def test_accept_called_on_connect(self):
        wc = _make_wc()
        ws = _make_ws()

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert ws.accepted is True

    @pytest.mark.asyncio
    async def test_x_mode_signal_sent_on_connect(self):
        """Premier message envoyé = x-mode standalone (invariant 2 spec dual-mode)."""
        wc = _make_wc()
        ws = _make_ws()

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert len(ws.sent) >= 1
        payload = json.loads(ws.sent[0])
        assert payload["type"] == "x-mode"
        assert payload["mode"] == "standalone"

    @pytest.mark.asyncio
    async def test_connection_registered_then_cleaned_up(self):
        wc = _make_wc()
        ws = _make_ws()

        # Pendant le handle, la connexion est présente
        registered_during = []

        async def _spy_handler(msg):
            registered_during.append(wc._connections.get("conv-1", []))

        wc.on_message(_spy_handler)

        msg = json.dumps({"body": "hello", "user_id": "alice"})
        ws = _make_ws(messages=[msg])
        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        # Après déconnexion, la connexion est retirée
        assert ws not in wc._connections.get("conv-1", [])

    @pytest.mark.asyncio
    async def test_disconnect_cleans_connection(self):
        wc = _make_wc()
        ws = _make_ws()  # aucun message → déconnexion immédiate

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert ws not in wc._connections.get("conv-1", [])


# =============================================================================
# handle_websocket — traitement des messages
# =============================================================================


class TestHandleWebsocketMessages:
    @pytest.mark.asyncio
    async def test_valid_message_triggers_handler(self):
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg: IncomingMessage):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "Bonjour Colaig", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-42", user_id="alice")

        assert len(received) == 1
        assert received[0].body == "Bonjour Colaig"
        assert received[0].conversation_id == "conv-42"

    @pytest.mark.asyncio
    async def test_uid_namespace_canonical(self):
        """user_id est préfixé uid:app:webchat:."""
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg: IncomingMessage):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "test", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert received[0].user_id == "uid:app:webchat:alice"

    @pytest.mark.asyncio
    async def test_platform_field_is_webchat(self):
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg: IncomingMessage):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "test", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert received[0].platform == "webchat"

    @pytest.mark.asyncio
    async def test_conversation_type_is_direct(self):
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg: IncomingMessage):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "test", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert received[0].conversation_type == ConversationType.DM

    @pytest.mark.asyncio
    async def test_empty_body_ignored(self):
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "   ", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")
        await asyncio.sleep(0.05)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_invalid_json_ignored(self):
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg):
            received.append(msg)

        wc.on_message(_handler)
        ws = _make_ws(messages=["pas du json {{{"])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")
        await asyncio.sleep(0.05)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_no_handler_registered_does_not_crash(self):
        """Un message reçu sans handler enregistré ne lève pas."""
        wc = _make_wc()
        payload = json.dumps({"body": "test", "user_id": "alice"})
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")  # ne doit pas lever

    @pytest.mark.asyncio
    async def test_user_id_fallback_to_url_param(self):
        """Si body ne contient pas user_id, on utilise le paramètre URL."""
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg):
            received.append(msg)

        wc.on_message(_handler)
        payload = json.dumps({"body": "hello"})  # pas de user_id dans le body
        ws = _make_ws(messages=[payload])

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="bob")

        assert received[0].user_id == "uid:app:webchat:bob"

    @pytest.mark.asyncio
    async def test_multiple_messages_same_connection(self):
        """Plusieurs messages sur la même connexion → handler appelé N fois."""
        wc = _make_wc()
        received: list[IncomingMessage] = []

        async def _handler(msg):
            received.append(msg)

        wc.on_message(_handler)
        msgs = [
            json.dumps({"body": f"message {i}", "user_id": "alice"})
            for i in range(3)
        ]
        ws = _make_ws(messages=msgs)

        await wc.handle_websocket(ws, conv_id="conv-1", user_id="alice")

        assert len(received) == 3


# =============================================================================
# Intégration avec create_app — routes WebChat montées
# =============================================================================


class TestCreateAppIntegration:
    def test_webchat_routes_mounted_when_messaging_is_webchat(self):
        from colaig.web.routes import create_app

        wc = WebChatMessaging()
        app = create_app(messaging=wc)
        paths = {r.path for r in app.routes}

        assert "/chat" in paths
        assert "/chat/{conv_id}" in paths
        assert "/chat/ws/{conv_id}" in paths

    def test_no_chat_routes_without_webchat(self):
        from colaig.web.routes import create_app

        app = create_app(messaging=None)
        paths = {r.path for r in app.routes}

        assert "/chat" not in paths
        assert "/chat/{conv_id}" not in paths
        assert "/chat/ws/{conv_id}" not in paths

    def test_noop_messaging_does_not_mount_chat_routes(self):
        from colaig.messaging.noop import NoopMessaging
        from colaig.web.routes import create_app

        app = create_app(messaging=NoopMessaging())
        paths = {r.path for r in app.routes}

        assert "/chat" not in paths
