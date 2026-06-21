"""
Colaig — TelegramMessaging

Implémente MessagingProtocol pour Telegram via Bot API.
Utilise httpx pour tous les appels HTTP (pas de bibliothèque python-telegram-bot).

Deux modes :
  - Polling (défaut, TELEGRAM_WEBHOOK_URL vide) : boucle getUpdates en long-polling
  - Webhook : reçoit les updates via un endpoint FastAPI POST /telegram/webhook

Conversation types :
  - Chat privé avec le bot → ConversationType.DM
  - Groupe ou supergroupe  → ConversationType.PRIVATE (membres connus)
  - Channel Telegram       → ConversationType.PUBLIC

Dépendance : httpx (déjà dans le projet)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from colaig.exceptions import MessagingError
from colaig.models import Attachment, ConversationType, IncomingMessage

logger = logging.getLogger(__name__)

_BOT_API_BASE = "https://api.telegram.org/bot{token}"
# Messages de plus de 5 minutes ignorés au démarrage
_STALE_MESSAGE_SECONDS = 300
# Timeout long-polling getUpdates (secondes)
_POLLING_TIMEOUT = 30


class TelegramMessaging:
    """Client Telegram Bot pour Colaig.

    Implémente MessagingProtocol.

    Args:
        bot_token: Token du bot Telegram (obtenu via @BotFather).
        webhook_url: URL webhook HTTPS (vide = mode polling).
        polling_interval: Intervalle entre les polls en secondes (mode polling).
    """

    def __init__(
        self,
        bot_token: str,
        webhook_url: str = "",
        polling_interval: float = 0.5,
    ) -> None:
        if not bot_token:
            raise MessagingError("TELEGRAM_BOT_TOKEN manquant")

        self._token = bot_token
        self._webhook_url = webhook_url
        self._polling_interval = polling_interval
        self._base_url = _BOT_API_BASE.format(token=bot_token)

        self._http: httpx.AsyncClient | None = None
        self._message_callbacks: list[Callable] = []
        self._last_update_id: int = 0
        self._start_time: float = 0.0
        self._bot_username: str = ""

    # ------------------------------------------------------------------
    # MessagingProtocol

    async def connect(self) -> None:
        """Initialise le client HTTP et récupère l'identité du bot."""
        self._http = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self._start_time = time.time()

        # Vérifier le token et récupérer le username du bot
        me = await self._api("getMe")
        self._bot_username = me.get("username", "")
        logger.info("telegram bot connecté: @%s", self._bot_username)

        if self._webhook_url:
            await self._set_webhook()
        else:
            await self._clear_webhook()

    async def run(self) -> None:
        """Boucle d'écoute des messages (polling ou webhook)."""
        if self._webhook_url:
            # En mode webhook, run() attend indéfiniment (le webhook est géré ailleurs)
            logger.info("telegram webhook actif sur %s — en attente", self._webhook_url)
            await asyncio.Event().wait()
        else:
            logger.info("telegram polling démarré (@%s)", self._bot_username)
            await self._polling_loop()

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        """Envoie un message dans une conversation Telegram.

        Args:
            conversation_id: chat_id Telegram (str ou int converti en str).
            text: Texte du message.
            formatted: HTML formaté (utilise parse_mode=HTML si fourni).
        """
        payload: dict = {
            "chat_id": int(conversation_id),
            "text": formatted or text,
        }
        if formatted:
            payload["parse_mode"] = "HTML"

        # Telegram limite à 4096 chars par message — tronquer si nécessaire
        max_len = 4096
        for key in ("text",):
            if key in payload and len(str(payload[key])) > max_len:
                payload[key] = str(payload[key])[:max_len - 3] + "..."

        await self._api("sendMessage", json=payload)

    async def send_typing(
        self,
        conversation_id: str,
        typing: bool = True,
        timeout: int = 5000,
    ) -> None:
        """Envoie l'action 'typing' dans une conversation."""
        if not typing:
            return  # Telegram ne supporte pas l'annulation de typing
        await self._api(
            "sendChatAction",
            json={"chat_id": int(conversation_id), "action": "typing"},
        )

    def on_message(self, callback: Callable) -> None:
        """Enregistre un callback appelé pour chaque message reçu."""
        self._message_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Polling interne

    async def _polling_loop(self) -> None:
        """Boucle long-polling getUpdates."""
        while True:
            try:
                updates = await self._api(
                    "getUpdates",
                    json={
                        "offset": self._last_update_id + 1,
                        "timeout": _POLLING_TIMEOUT,
                        "allowed_updates": ["message"],
                    },
                    timeout=_POLLING_TIMEOUT + 5,
                )
                for update in updates:
                    await self._process_update(update)
                    self._last_update_id = update["update_id"]
            except asyncio.CancelledError:
                logger.info("telegram polling arrêté")
                break
            except Exception:
                logger.exception("telegram polling erreur — retry dans %ds", 5)
                await asyncio.sleep(5)

            if self._polling_interval > 0:
                await asyncio.sleep(self._polling_interval)

    async def _process_update(self, update: dict) -> None:
        """Traite un update Telegram brut."""
        msg = update.get("message")
        if not msg:
            return

        # Ignorer les messages trop vieux (replay au démarrage)
        msg_date = msg.get("date", 0)
        if msg_date < self._start_time - _STALE_MESSAGE_SECONDS:
            return

        text = msg.get("text", "").strip()

        # Détection des messages vocaux (voice = vocal Telegram, audio = fichier audio)
        voice_data = msg.get("voice") or msg.get("audio")
        if not text and not voice_data:
            return  # Ignorer les messages sans texte ni audio (photos, stickers…)

        from_user = msg.get("from", {})
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")  # private | group | supergroup | channel

        conv_type = _chat_type_to_conversation_type(chat_type)

        # En groupe/supergroupe, ignorer si le bot n'est pas mentionné
        if conv_type in (ConversationType.PRIVATE, ConversationType.PUBLIC):
            if self._bot_username and f"@{self._bot_username}" not in text:
                # Accepter quand même les commandes /colaig ou messages en réponse au bot
                if not text.startswith("/") and not msg.get("reply_to_message"):
                    return
            # Nettoyer la mention du bot du texte
            if self._bot_username:
                text = text.replace(f"@{self._bot_username}", "").strip()

        user_id = str(from_user.get("id", ""))
        first = from_user.get("first_name", "")
        last = from_user.get("last_name", "")
        display_name = f"{first} {last}".strip() or from_user.get("username", user_id)

        # Détection des réponses
        reply_to_msg = msg.get("reply_to_message", {})
        is_reply = bool(reply_to_msg)
        reply_to = str(reply_to_msg.get("message_id", "")) if is_reply else ""

        # Télécharger le fichier audio si présent
        audio_attachment: Attachment | None = None
        if voice_data:
            file_id = voice_data.get("file_id", "")
            mime_type = voice_data.get("mime_type", "audio/ogg")
            if file_id:
                try:
                    audio_bytes = await self._download_file(file_id)
                    if audio_bytes:
                        ext = _mime_to_ext(mime_type)
                        audio_attachment = Attachment(
                            filename=f"voice{ext}",
                            content_type=mime_type,
                            size=len(audio_bytes),
                            content=audio_bytes,
                        )
                except Exception:
                    logger.warning("impossible de télécharger le vocal telegram file_id=%s", file_id, exc_info=True)

        incoming = IncomingMessage(
            user_id=user_id,
            conversation_id=chat_id,
            body=text,  # Vide si vocal pur — handlers.py transcrit
            conversation_type=conv_type,
            message_id=str(msg.get("message_id", "")),
            display_name=display_name,
            is_reply=is_reply,
            reply_to=reply_to,
            attachments=[audio_attachment] if audio_attachment else [],
        )

        for callback in self._message_callbacks:
            try:
                await callback(incoming)
            except Exception:
                logger.exception("erreur handler message telegram chat_id=%s", chat_id)

    # ------------------------------------------------------------------
    # Webhook support

    async def _set_webhook(self) -> None:
        """Configure le webhook sur Telegram."""
        await self._api("setWebhook", json={"url": self._webhook_url})
        logger.info("telegram webhook configuré: %s", self._webhook_url)

    async def _clear_webhook(self) -> None:
        """Supprime le webhook (nécessaire avant de passer en polling)."""
        await self._api("deleteWebhook")

    async def handle_webhook_update(self, update: dict) -> None:
        """Traite un update reçu via webhook (appelé par FastAPI route)."""
        await self._process_update(update)

    # ------------------------------------------------------------------
    # API Telegram générique

    async def _api(
        self,
        method: str,
        json: dict | None = None,
        timeout: float | None = None,
    ) -> dict | list:
        """Appelle une méthode Bot API Telegram.

        Args:
            method: Nom de la méthode (ex: "sendMessage").
            json: Paramètres de la requête.
            timeout: Timeout HTTP (None = défaut).

        Returns:
            Champ "result" de la réponse JSON.

        Raises:
            MessagingError: Si l'API retourne ok=False ou une erreur HTTP.
        """
        if self._http is None:
            raise MessagingError("connect() doit être appelé avant toute opération")

        url = f"{self._base_url}/{method}"
        kwargs: dict = {}
        if json is not None:
            kwargs["json"] = json
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            resp = await self._http.post(url, **kwargs)
        except httpx.RequestError as e:
            raise MessagingError(f"Telegram connexion impossible: {e}") from e

        try:
            data = resp.json()
        except Exception as e:
            raise MessagingError(f"Telegram réponse invalide: {resp.text}") from e

        if not data.get("ok"):
            code = data.get("error_code", resp.status_code)
            desc = data.get("description", "erreur inconnue")
            raise MessagingError(f"Telegram API {method} erreur [{code}]: {desc}")

        return data.get("result", {})

    async def _download_file(self, file_id: str) -> bytes | None:
        """Télécharge un fichier depuis Telegram via getFile + CDN.

        Args:
            file_id: Identifiant du fichier Telegram.

        Returns:
            Bytes du fichier, ou None en cas d'erreur.
        """
        if self._http is None:
            return None
        # 1. Obtenir le file_path via getFile
        file_info = await self._api("getFile", json={"file_id": file_id})
        file_path = file_info.get("file_path", "")
        if not file_path:
            return None
        # 2. Télécharger le fichier depuis le CDN Telegram
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        resp = await self._http.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content

    async def close(self) -> None:
        """Ferme le client HTTP."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()


# =============================================================================
# Helpers
# =============================================================================

def _mime_to_ext(mime_type: str) -> str:
    """Retourne l'extension de fichier pour un MIME type audio."""
    _ext_map = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "audio/webm": ".webm",
        "audio/aac": ".aac",
    }
    return _ext_map.get(mime_type, ".ogg")


def _chat_type_to_conversation_type(chat_type: str) -> ConversationType:
    """Convertit le type de chat Telegram en ConversationType Colaig."""
    if chat_type == "private":
        return ConversationType.DM
    if chat_type == "channel":
        return ConversationType.PUBLIC
    # group, supergroup
    return ConversationType.PRIVATE
