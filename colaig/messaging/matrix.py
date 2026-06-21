"""
Colaig — MatrixMessaging (Tchap)

Implémente MessagingProtocol pour Matrix/Tchap.
Utilise matrix-nio (AsyncClient) pour la connexion au homeserver.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from nio import (
    AsyncClient,
    AsyncClientConfig,
    DownloadError,
    InviteMemberEvent,
    JoinError,
    KeysQueryResponse,
    LoginResponse,
    RoomEncryptedAudio,
    RoomMessageAudio,
    RoomMessageText,
    SyncError,
)
from nio.crypto.device import TrustState

from colaig.exceptions import MessagingError
from colaig.models import Attachment, ConversationType, IncomingMessage

logger = logging.getLogger(__name__)

# Messages de plus de 5 minutes ignorés au démarrage
_STALE_MESSAGE_SECONDS = 300


# ── Helpers Markdown → HTML (SPEC-48 : Matrix utilise HTML sanitisé) ─────────

def _escape_html(text: str) -> str:
    """Échappe les caractères spéciaux HTML (&, <, >)."""
    return _html.escape(text, quote=False)


def _inline_markdown(text: str) -> str:
    """Transformations Markdown inline → HTML (gras, italique, code)."""
    text = _escape_html(text)
    # Code inline — avant gras/italique pour éviter les conflits de symboles
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Gras ** ou __
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # Italique * (mais pas **) ou _ entouré de non-alphanum
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?!_)(.+?)(?<!_)_(?!\w)", r"<em>\1</em>", text)
    return text


def _markdown_to_html(text: str) -> str:
    """Convertit Markdown CommonMark → HTML sanitisé pour Matrix/Tchap.

    Couvre le sous-ensemble produit par le Synthétiseur :
    titres, listes, blocs de code, gras/italique, paragraphes.
    (SPEC-48 : Matrix a choisi HTML plutôt que Markdown natif.)
    """
    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_buf: list[str] = []
    in_ul = False
    in_ol = False

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            result.append("</ul>")
            in_ul = False
        if in_ol:
            result.append("</ol>")
            in_ol = False

    for line in lines:
        # ── Blocs de code (``` ... ```) ────────────────────────────────────────
        if line.startswith("```"):
            if not in_code:
                _close_lists()
                in_code = True
                code_buf = []
            else:
                in_code = False
                code_content = "\n".join(_escape_html(l) for l in code_buf)
                result.append(f"<pre><code>{code_content}</code></pre>")
                code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue

        # ── Titres (#, ##, ###, ...) ───────────────────────────────────────────
        h = re.match(r"^(#{1,6})\s+(.+)$", line)
        if h:
            _close_lists()
            n = len(h.group(1))
            result.append(f"<h{n}>{_inline_markdown(h.group(2))}</h{n}>")
            continue

        # ── Règle horizontale (---, ***, ___) ─────────────────────────────────
        if re.match(r"^[-*_]{3,}\s*$", line):
            _close_lists()
            result.append("<hr/>")
            continue

        # ── Listes non-ordonnées (- item, * item, + item) ─────────────────────
        ul = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if ul:
            if in_ol:
                result.append("</ol>")
                in_ol = False
            if not in_ul:
                result.append("<ul>")
                in_ul = True
            result.append(f"<li>{_inline_markdown(ul.group(1))}</li>")
            continue

        # ── Listes ordonnées (1. item) ─────────────────────────────────────────
        ol = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if ol:
            if in_ul:
                result.append("</ul>")
                in_ul = False
            if not in_ol:
                result.append("<ol>")
                in_ol = True
            result.append(f"<li>{_inline_markdown(ol.group(1))}</li>")
            continue

        # ── Ligne vide → séparateur ────────────────────────────────────────────
        if not line.strip():
            _close_lists()
            result.append("")
            continue

        # ── Texte normal ───────────────────────────────────────────────────────
        _close_lists()
        result.append(_inline_markdown(line))

    _close_lists()
    # Fermer un bloc de code non terminé (robustesse)
    if in_code and code_buf:
        code_content = "\n".join(_escape_html(l) for l in code_buf)
        result.append(f"<pre><code>{code_content}</code></pre>")

    return "\n".join(result)


def _strip_markdown(text: str) -> str:
    """Supprime les marqueurs Markdown pour obtenir du texte brut (champ body Matrix)."""
    # Blocs de code → garder le contenu
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    # Code inline
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Titres
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Gras
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Italique
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # Règles horizontales
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


class MatrixMessaging:
    """Client Matrix/Tchap pour Colaig.

    Implémente MessagingProtocol.

    Args:
        homeserver: URL du homeserver Matrix.
        username: Identifiant Matrix (ex: @colaig:agent.tchap.gouv.fr).
        password: Mot de passe.
    """

    def __init__(
        self,
        homeserver: str,
        username: str,
        password: str,
        token_store: Path | None = None,
    ) -> None:
        self._homeserver = homeserver
        self._username = username
        self._password = password
        self._client: AsyncClient | None = None
        self._message_callbacks: list[Callable] = []
        self._start_time: float = 0.0
        # Fichier de persistance du token (évite de créer une nouvelle session à chaque démarrage)
        self._token_store = token_store or Path.home() / ".colaig" / "matrix_token.json"
        # Répertoire du crypto store E2E (clés Olm/Megolm — nécessite matrix-nio[e2e])
        self._store_path = self._token_store.parent / "e2e_store"

    async def connect(self) -> None:
        """Connexion au homeserver Matrix (login ou réutilisation du token existant)."""
        # Charger le device_id sauvegardé pour passer au constructeur (évite de créer un nouveau device)
        saved_device_id = ""
        if self._token_store.exists():
            try:
                saved_device_id = json.loads(self._token_store.read_text()).get("device_id", "")
            except Exception:
                pass

        # Créer le répertoire du store E2E (clés Olm/Megolm — requis par Tchap E2E)
        self._store_path.mkdir(parents=True, exist_ok=True)

        # E2E activé — Tchap chiffre tous les salons par défaut
        client_config = AsyncClientConfig(
            max_timeouts=10,
            max_limit_exceeded=0,
            store_sync_tokens=True,
            encryption_enabled=True,
        )
        self._client = AsyncClient(
            self._homeserver,
            self._username,
            device_id=saved_device_id,
            store_path=str(self._store_path),
            config=client_config,
        )
        self._start_time = time.time()

        # Réutiliser le token existant via restore_login() qui initialise aussi le store Olm
        if self._token_store.exists():
            try:
                data = json.loads(self._token_store.read_text())
                self._client.restore_login(
                    user_id=data.get("user_id", self._username),
                    device_id=data["device_id"],
                    access_token=data["access_token"],
                )
                logger.info(
                    "matrix token chargé depuis %s (device_id=%s)",
                    self._token_store, data["device_id"],
                )
                # Valider le token immédiatement avec whoami() (pattern Colaig_26072025)
                resp = await self._client.whoami()
                if not hasattr(resp, "user_id"):
                    raise ValueError(f"token rejeté par le serveur: {resp}")
                logger.info("token validé (user_id=%s)", resp.user_id)
            except Exception as e:
                logger.warning("token Matrix invalide (%s), re-login...", e)
                self._token_store.unlink(missing_ok=True)
                await self._do_login()
        else:
            await self._do_login()

        logger.info(
            "matrix connecté à %s en tant que %s",
            self._homeserver, self._username,
        )

        # Upload des clés E2E — obligatoire pour que Synapse accepte ce device dans le sync
        # (sans clés, le sync worker peut bloquer sur la distribution des clés de session)
        try:
            if self._client.should_upload_keys:
                resp = await self._client.keys_upload()
                logger.info("clés E2E uploadées: %s", type(resp).__name__)
            else:
                logger.info("clés E2E déjà uploadées pour ce device")
        except Exception as exc:
            logger.warning("upload clés E2E échoué (%s)", exc)

        # Auto-trust : marque les devices inconnus comme user_ignored après chaque keys_query.
        # Pattern bot — le bot n'effectue pas de vérification manuelle des devices.
        self._client.add_response_callback(self._auto_trust_devices, KeysQueryResponse)
        # Applique immédiatement sur les devices déjà connus (re-login depuis token)
        self._do_auto_trust()

        # Enregistre les callbacks
        self._client.add_event_callback(self._on_invite, InviteMemberEvent)
        self._client.add_event_callback(self._on_room_message, RoomMessageText)
        # Audio : non-chiffré (rare) ET chiffré E2E (Tchap = RoomEncryptedAudio)
        self._client.add_event_callback(self._on_room_audio, RoomMessageAudio)
        self._client.add_event_callback(self._on_room_audio, RoomEncryptedAudio)

    def _do_auto_trust(self) -> None:
        """Marque tous les devices non-vérifiés comme user_ignored (pattern bot).

        device_store itère directement sur des OlmDevice (pas sur des user_ids).
        """
        if self._client is None or self._client.olm is None:
            return
        count = 0
        for device in self._client.olm.device_store:
            if device.trust_state == TrustState.unset:
                device.trust_state = TrustState.ignored
                count += 1
        if count:
            logger.debug("auto-trust: %d device(s) marqués user_ignored", count)

    async def _auto_trust_devices(self, response: KeysQueryResponse) -> None:
        """Callback KeysQueryResponse — auto-trust des nouveaux devices."""
        self._do_auto_trust()

    async def _do_login(self) -> None:
        """Login Matrix et persistance du token."""
        if self._client is None:
            return
        response = await self._client.login(self._password, device_name="Colaig")
        if not isinstance(response, LoginResponse):
            raise MessagingError(f"Login Matrix échoué: {response}")

        # Persister le token pour les prochains démarrages
        self._token_store.parent.mkdir(parents=True, exist_ok=True)
        self._token_store.write_text(json.dumps({
            "access_token": response.access_token,
            "device_id": response.device_id,
            "user_id": response.user_id,
        }))
        logger.info("matrix token sauvegardé (device_id=%s)", response.device_id)

    async def _handle_sync_failure(self) -> None:
        """Après une exception dans sync_forever : whoami() pour valider le token.

        Si le token est invalide → re-login. Sinon → sleep 30s et réessayer.
        """
        import asyncio
        if self._client is None:
            return
        try:
            resp = await self._client.whoami()
            if not hasattr(resp, "user_id"):
                raise ValueError(f"token rejeté: {resp}")
            # Token encore valide — simple pause réseau
            logger.info("token valide (user_id=%s) — retry dans 30s", resp.user_id)
            await asyncio.sleep(30)
        except Exception:
            logger.warning("token invalide ou whoami échoué — re-login")
            try:
                self._token_store.unlink(missing_ok=True)
                await self._do_login()
                logger.info("re-login réussi")
            except Exception as login_exc:
                logger.error("re-login échoué (%s) — retry dans 60s", login_exc)
                await asyncio.sleep(60)

    async def run(self) -> None:
        """Boucle d'écoute infinie des événements.

        Pattern Colaig_26072025 :
        1. sync initial pour charger l'état des salons
        2. sync_forever(timeout=3000) — long-poll continu
        3. Si exception → vérifier le token via whoami(), re-login si nécessaire
        """
        if self._client is None:
            raise MessagingError("connect() doit être appelé avant run()")
        logger.info("matrix boucle d'écoute démarrée")

        # Filtre Synapse : lazy_load_members évite le calcul des clés Megolm pour tous
        # les membres à chaque sync (cause de timeouts sur Tchap avec salons chiffrés).
        # Ce filtre s'applique en inline (pas de server-side filter registration).
        _SYNC_FILTER = {
            "room": {
                "state": {"lazy_load_members": True},
                "timeline": {"limit": 50},
            },
            "presence": {"not_types": ["*"]},
        }

        # Sync initial : charge l'état des salons sans historique de messages.
        # Les messages anciens seront filtrés par timestamp dans _on_room_message.
        try:
            sync_resp = await self._client.sync(
                timeout=30000, full_state=True, sync_filter=_SYNC_FILTER,
            )
            if isinstance(sync_resp, SyncError):
                logger.warning("sync initial SyncError — %s", sync_resp)
            else:
                rooms_obj = getattr(sync_resp, "rooms", None)
                if rooms_obj is not None:
                    rooms_joined = len(getattr(rooms_obj, "join", {}))
                else:
                    rooms_joined = 0
                logger.info(
                    "sync initial OK — next_batch=%s rooms_joined=%d rooms_loaded=%d",
                    self._client.next_batch, rooms_joined, len(self._client.rooms),
                )
        except Exception as exc:
            logger.warning("sync initial échoué (%s) — on continue quand même", exc)

        # sync_forever : pattern de référence — simple et efficace quand le token est valide.
        # En cas d'exception (max_timeouts=10 dépassé ou erreur réseau), on vérifie le token
        # et on re-login si nécessaire avant de relancer.
        while True:
            try:
                await self._client.sync_forever(
                    timeout=3000, full_state=False, sync_filter=_SYNC_FILTER,
                )
            except Exception as exc:
                logger.warning("sync_forever interrompu (%s) — vérification token...", exc)
                await self._handle_sync_failure()

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        """Envoie un message dans une conversation."""
        if self._client is None:
            raise MessagingError("Bot non connecté")

        # Auto-trust des nouveaux devices avant chaque envoi (le store se peuple après le sync)
        self._do_auto_trust()

        content: dict = {
            "msgtype": "m.notice" if is_status else "m.text",
            "body": _strip_markdown(text),           # Texte brut (fallback clients sans HTML)
            "format": "org.matrix.custom.html",      # SPEC-48 : HTML sanitisé
            "formatted_body": formatted or _markdown_to_html(text),  # Rendu riche
        }

        await self._client.room_send(
            room_id=conversation_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )

    async def send_typing(
        self,
        conversation_id: str,
        typing: bool = True,
        timeout: int = 10000,
    ) -> None:
        """Envoie/arrête l'indicateur de frappe."""
        if self._client is None:
            return
        await self._client.room_typing(conversation_id, typing, timeout)

    def on_message(self, callback: Callable) -> None:
        """Enregistre un callback appelé pour chaque message reçu."""
        self._message_callbacks.append(callback)

    # Alias de rétrocompatibilité
    async def send_message(self, room_id: str, text: str, formatted: str | None = None) -> None:
        """Alias pour send() — rétrocompatibilité."""
        await self.send(room_id, text, formatted)

    # ── Callbacks internes ───────────────────────────────────────────

    async def _on_invite(self, room, event: InviteMemberEvent) -> None:
        """Auto-join quand invité dans un salon."""
        if self._client is None:
            return
        if event.state_key != self._username:
            return

        result = await self._client.join(room.room_id)
        if isinstance(result, JoinError):
            logger.error("impossible de rejoindre %s: %s", room.room_id, result)
        else:
            logger.info("salon rejoint: %s", room.room_id)

    async def _on_room_message(self, room, event: RoomMessageText) -> None:
        """Traite un message reçu dans un salon."""
        if self._client is None:
            return

        logger.info("message reçu: sender=%s room=%s body_chars=%d", event.sender, room.room_id, len(event.body))

        # Ignorer ses propres messages
        if event.sender == self._username:
            logger.debug("ignoré: propre message")
            return

        # Ignorer les messages trop vieux (replay au démarrage)
        event_ts = event.server_timestamp / 1000  # ms → s
        if event_ts < self._start_time - _STALE_MESSAGE_SECONDS:
            logger.debug("ignoré: message trop vieux (ts=%s start=%s)", event_ts, self._start_time)
            return

        # Déterminer le type de conversation
        conversation_type = await self._resolve_conversation_type(room.room_id)

        # En salon (non-DM), ignorer si pas mentionné
        if conversation_type not in (ConversationType.DM, ConversationType.UNKNOWN):
            bot_display = self._username.split(":")[0].lstrip("@")
            if bot_display not in event.body and self._username not in event.body:
                return

        # Détecter les réponses
        is_reply = False
        reply_to = ""
        if hasattr(event, "source") and isinstance(event.source, dict):
            relates = event.source.get("content", {}).get("m.relates_to", {})
            in_reply = relates.get("m.in_reply_to", {})
            if in_reply.get("event_id"):
                is_reply = True
                reply_to = in_reply["event_id"]

        # Construire l'IncomingMessage (noms provider-agnostic)
        message = IncomingMessage(
            user_id=event.sender,
            conversation_id=room.room_id,
            body=event.body,
            conversation_type=conversation_type,
            message_id=event.event_id,
            display_name=room.user_name(event.sender) or event.sender,
            is_reply=is_reply,
            reply_to=reply_to,
            platform="matrix",
        )

        # Appeler les handlers enregistrés
        for callback in self._message_callbacks:
            try:
                await callback(message)
            except Exception:
                logger.exception("erreur dans handler message pour %s", room.room_id)

    async def _on_room_audio(self, room, event: RoomMessageAudio) -> None:
        """Traite un message vocal reçu dans un salon."""
        if self._client is None:
            return

        # Ignorer ses propres messages
        if event.sender == self._username:
            return

        # Ignorer les messages trop vieux (replay au démarrage)
        event_ts = event.server_timestamp / 1000
        if event_ts < self._start_time - _STALE_MESSAGE_SECONDS:
            logger.info("ignoré: vocal trop vieux (ts=%s start=%s)", event_ts, self._start_time)
            return

        logger.info("vocal reçu: sender=%s room=%s", event.sender, room.room_id)

        # Télécharger l'audio (gère chiffrement E2E si disponible)
        audio_bytes = await self._download_audio(event)
        if not audio_bytes:
            logger.warning("impossible de télécharger l'audio: %s", event.event_id)
            return

        filename = getattr(event, "body", None) or "voice.ogg"
        # Déterminer le MIME type depuis l'événement ou défaut ogg/opus (Tchap)
        content = event.source.get("content", {}) if hasattr(event, "source") else {}
        info = content.get("info", {})
        mime_type = info.get("mimetype", "audio/ogg")

        attachment = Attachment(
            filename=filename,
            content_type=mime_type,
            size=len(audio_bytes),
            content=audio_bytes,
        )

        conversation_type = await self._resolve_conversation_type(room.room_id)

        message = IncomingMessage(
            user_id=event.sender,
            conversation_id=room.room_id,
            body="",  # Transcription faite dans handlers.py
            conversation_type=conversation_type,
            message_id=event.event_id,
            display_name=room.user_name(event.sender) or event.sender,
            platform="matrix",
            attachments=[attachment],
        )

        for callback in self._message_callbacks:
            try:
                await callback(message)
            except Exception:
                logger.exception("erreur handler audio pour %s", room.room_id)

    async def _download_audio(self, event: RoomMessageAudio) -> bytes | None:
        """Télécharge (et décrypte si E2E) l'audio d'un événement Matrix.

        Gère deux cas :
        - Message non chiffré : ``event.url`` est un mxc:// direct.
        - Message E2E chiffré : ``event.source["content"]["file"]`` contient
          l'url mxc + la clé AES-CTR (format Matrix v2 encrypted file).
        """
        if self._client is None:
            return None

        mxc_url: str | None = None
        file_info: dict | None = None

        # Cas 1 — RoomEncryptedAudio : attributs directs url/key/iv/hashes
        if isinstance(event, RoomEncryptedAudio):
            mxc_url = getattr(event, "url", None)
            if mxc_url and getattr(event, "key", None):
                file_info = {
                    "url": mxc_url,
                    "key": event.key,
                    "iv": event.iv,
                    "hashes": event.hashes,
                }
        # Cas 2 — RoomMessageAudio non chiffré : url directe
        elif getattr(event, "url", None):
            mxc_url = event.url
        else:
            # Cas 3 — fallback source dict (format legacy)
            content = event.source.get("content", {}) if hasattr(event, "source") else {}
            file_info = content.get("file")
            if file_info:
                mxc_url = file_info.get("url")

        if not mxc_url:
            return None

        try:
            resp = await self._client.download(mxc=mxc_url)
            if isinstance(resp, DownloadError):
                logger.warning("download audio Matrix échoué: %s", resp)
                return None

            audio_bytes: bytes = resp.body

            # Décryptage E2E si les informations de clé sont présentes
            if file_info and file_info.get("key"):
                try:
                    from nio.crypto.attachments import decrypt_attachment
                    key = file_info["key"]["k"]
                    iv = file_info["iv"]
                    sha256 = file_info["hashes"]["sha256"]
                    audio_bytes = decrypt_attachment(audio_bytes, key, sha256, iv)
                except Exception as exc:
                    logger.warning("décryptage audio E2E échoué (%s)", exc, exc_info=True)
                    return None  # Ne pas envoyer des bytes chiffrés à Albert

            return audio_bytes

        except Exception as exc:
            logger.warning("téléchargement audio Matrix échoué: %s", exc)
            return None

    async def _resolve_conversation_type(self, room_id: str) -> ConversationType:
        """Détermine le type de conversation (DM, public, privé)."""
        if self._client is None:
            return ConversationType.UNKNOWN

        try:
            response = await self._client.joined_members(room_id)
            if hasattr(response, "members"):
                if len(response.members) == 2:
                    return ConversationType.DM
        except Exception:
            pass

        # Vérifier si le salon est public
        room = self._client.rooms.get(room_id)
        if room:
            if hasattr(room, "join_rule") and room.join_rule == "public":
                return ConversationType.PUBLIC
            return ConversationType.PRIVATE

        return ConversationType.UNKNOWN


# Alias de rétrocompatibilité
MatrixBot = MatrixMessaging
