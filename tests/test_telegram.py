"""
Tests pour colaig/integrations/messaging/telegram.py — TelegramMessaging.

Stratégie : mock de _api (niveau Bot API) pour isoler la logique applicative
sans appel réseau réel.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from colaig.exceptions import MessagingError
from colaig.integrations.messaging.telegram import (
    TelegramMessaging,
    _chat_type_to_conversation_type,
)
from colaig.models import ConversationType, IncomingMessage


# =============================================================================
# Helper factories
# =============================================================================


def _make_bot(webhook_url: str = "") -> TelegramMessaging:
    return TelegramMessaging(
        bot_token="123:BOT_TOKEN_TEST",
        webhook_url=webhook_url,
        polling_interval=0,
    )


def _make_update(
    update_id: int = 1,
    text: str = "Hello",
    chat_type: str = "private",
    chat_id: int = 42,
    user_id: int = 99,
    first_name: str = "Alice",
    message_id: int = 10,
    date: int | None = None,
    reply_to: dict | None = None,
) -> dict:
    if date is None:
        date = int(time.time())
    msg: dict = {
        "message_id": message_id,
        "date": date,
        "text": text,
        "from": {"id": user_id, "first_name": first_name, "last_name": ""},
        "chat": {"id": chat_id, "type": chat_type},
    }
    if reply_to:
        msg["reply_to_message"] = reply_to
    return {"update_id": update_id, "message": msg}


# =============================================================================
# Construction
# =============================================================================


class TestConstruction:
    def test_raises_on_empty_token(self):
        with pytest.raises(MessagingError, match="TELEGRAM_BOT_TOKEN"):
            TelegramMessaging(bot_token="")

    def test_base_url_built(self):
        bot = _make_bot()
        assert "123:BOT_TOKEN_TEST" in bot._base_url


# =============================================================================
# connect
# =============================================================================


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_polling_mode(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={"username": "ColBot"})

        await bot.connect()

        assert bot._bot_username == "ColBot"
        # En mode polling, deleteWebhook appelé
        calls = [c[0][0] for c in bot._api.call_args_list]
        assert "getMe" in calls
        assert "deleteWebhook" in calls

    @pytest.mark.asyncio
    async def test_connect_webhook_mode(self):
        bot = _make_bot(webhook_url="https://example.com/hook")
        bot._api = AsyncMock(return_value={"username": "ColBot"})

        await bot.connect()

        calls = [c[0][0] for c in bot._api.call_args_list]
        assert "getMe" in calls
        assert "setWebhook" in calls
        assert "deleteWebhook" not in calls


# =============================================================================
# send
# =============================================================================


class TestSend:
    @pytest.mark.asyncio
    async def test_send_plain_text(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={})

        await bot.send("42", "Bonjour !")

        bot._api.assert_called_once_with(
            "sendMessage",
            json={"chat_id": 42, "text": "Bonjour !"},
        )

    @pytest.mark.asyncio
    async def test_send_formatted_html(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={})

        await bot.send("42", "plain", formatted="<b>bold</b>")

        call_kwargs = bot._api.call_args[1]["json"]
        assert call_kwargs["text"] == "<b>bold</b>"
        assert call_kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_truncates_long_message(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={})

        long_text = "x" * 5000
        await bot.send("42", long_text)

        sent_text = bot._api.call_args[1]["json"]["text"]
        assert len(sent_text) <= 4096
        assert sent_text.endswith("...")


# =============================================================================
# send_typing
# =============================================================================


class TestSendTyping:
    @pytest.mark.asyncio
    async def test_send_typing_true(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={})

        await bot.send_typing("42")

        bot._api.assert_called_once_with(
            "sendChatAction",
            json={"chat_id": 42, "action": "typing"},
        )

    @pytest.mark.asyncio
    async def test_send_typing_false_skips_api(self):
        bot = _make_bot()
        bot._api = AsyncMock(return_value={})

        await bot.send_typing("42", typing=False)

        bot._api.assert_not_called()


# =============================================================================
# on_message / callback registration
# =============================================================================


class TestOnMessage:
    def test_callback_registered(self):
        bot = _make_bot()
        cb = AsyncMock()
        bot.on_message(cb)
        assert cb in bot._message_callbacks

    def test_multiple_callbacks(self):
        bot = _make_bot()
        cb1, cb2 = AsyncMock(), AsyncMock()
        bot.on_message(cb1)
        bot.on_message(cb2)
        assert len(bot._message_callbacks) == 2


# =============================================================================
# _process_update — logique de traitement des messages
# =============================================================================


class TestProcessUpdate:
    @pytest.fixture
    def bot(self) -> TelegramMessaging:
        bot = _make_bot()
        bot._start_time = time.time() - 10  # démarré il y a 10s
        bot._bot_username = "ColBot"
        return bot

    @pytest.mark.asyncio
    async def test_private_dm_triggers_callback(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(text="Hello", chat_type="private")

        await bot._process_update(update)

        cb.assert_called_once()
        msg: IncomingMessage = cb.call_args[0][0]
        assert msg.body == "Hello"
        assert msg.conversation_type == ConversationType.DM
        assert msg.user_id == "99"

    @pytest.mark.asyncio
    async def test_stale_message_ignored(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        stale_date = int(time.time()) - 400  # > 300s de stale
        bot._start_time = time.time()
        update = _make_update(text="old msg", date=stale_date)

        await bot._process_update(update)

        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(text="")

        await bot._process_update(update)

        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_message_key_ignored(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)

        await bot._process_update({"update_id": 1})

        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_message_without_mention_ignored(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(
            text="anyone there?",
            chat_type="group",
        )

        await bot._process_update(update)

        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_message_with_mention_processed(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(
            text="@ColBot what is this?",
            chat_type="group",
        )

        await bot._process_update(update)

        cb.assert_called_once()
        msg: IncomingMessage = cb.call_args[0][0]
        # La mention doit être nettoyée
        assert "@ColBot" not in msg.body
        assert msg.conversation_type == ConversationType.PRIVATE

    @pytest.mark.asyncio
    async def test_group_slash_command_processed(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(text="/colaig help", chat_type="group")

        await bot._process_update(update)

        cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_group_reply_processed(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(
            text="thanks",
            chat_type="group",
            reply_to={"message_id": 5},
        )

        await bot._process_update(update)

        cb.assert_called_once()
        msg: IncomingMessage = cb.call_args[0][0]
        assert msg.is_reply is True
        assert msg.reply_to == "5"

    @pytest.mark.asyncio
    async def test_channel_message_is_public_type(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = _make_update(
            text="@ColBot broadcast this",
            chat_type="channel",
        )

        await bot._process_update(update)

        cb.assert_called_once()
        msg: IncomingMessage = cb.call_args[0][0]
        assert msg.conversation_type == ConversationType.PUBLIC

    @pytest.mark.asyncio
    async def test_display_name_built_from_first_last(self, bot):
        cb = AsyncMock()
        bot.on_message(cb)
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "text": "hello",
                "from": {"id": 1, "first_name": "Jean", "last_name": "Dupont", "username": "jdupont"},
                "chat": {"id": 1, "type": "private"},
            },
        }

        await bot._process_update(update)

        msg: IncomingMessage = cb.call_args[0][0]
        assert msg.display_name == "Jean Dupont"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self, bot):
        """Une exception dans un callback ne doit pas interrompre le traitement."""
        bad_cb = AsyncMock(side_effect=ValueError("boom"))
        bot.on_message(bad_cb)
        update = _make_update(text="Hello")

        # Ne doit pas lever
        await bot._process_update(update)


# =============================================================================
# handle_webhook_update
# =============================================================================


class TestWebhookUpdate:
    @pytest.mark.asyncio
    async def test_delegates_to_process_update(self):
        bot = _make_bot(webhook_url="https://example.com/hook")
        bot._start_time = time.time() - 10
        bot._bot_username = "ColBot"

        cb = AsyncMock()
        bot.on_message(cb)

        update = _make_update(text="Hello via webhook")
        await bot.handle_webhook_update(update)

        cb.assert_called_once()


# =============================================================================
# _chat_type_to_conversation_type helper
# =============================================================================


class TestChatTypeConversion:
    def test_private_is_dm(self):
        assert _chat_type_to_conversation_type("private") == ConversationType.DM

    def test_channel_is_public(self):
        assert _chat_type_to_conversation_type("channel") == ConversationType.PUBLIC

    def test_group_is_private(self):
        assert _chat_type_to_conversation_type("group") == ConversationType.PRIVATE

    def test_supergroup_is_private(self):
        assert _chat_type_to_conversation_type("supergroup") == ConversationType.PRIVATE


# =============================================================================
# _api — error handling
# =============================================================================


class TestApiMethod:
    @pytest.mark.asyncio
    async def test_raises_messaging_error_when_not_connected(self):
        bot = _make_bot()
        # _http est None → pas de connect()

        with pytest.raises(MessagingError, match="connect\\(\\)"):
            await bot._api("getMe")

    @pytest.mark.asyncio
    async def test_raises_on_api_error_response(self):
        bot = _make_bot()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error_code": 400, "description": "Bad Request"}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.is_closed = False
        bot._http = mock_http

        with pytest.raises(MessagingError, match="Bad Request"):
            await bot._api("sendMessage")

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        bot = _make_bot()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": {"id": 123}}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.is_closed = False
        bot._http = mock_http

        result = await bot._api("getMe")
        assert result == {"id": 123}


# =============================================================================
# close
# =============================================================================


class TestClose:
    @pytest.mark.asyncio
    async def test_close_when_connected(self):
        bot = _make_bot()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        bot._http = mock_http

        await bot.close()

        mock_http.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_when_not_connected(self):
        bot = _make_bot()
        # Pas d'exception si _http est None
        await bot.close()
