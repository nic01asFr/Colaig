# SPDX-FileCopyrightText: 2021 - 2022 Isaac Beverly <https://github.com/imbev>
# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
#
# SPDX-License-Identifier: MIT
import mimetypes
import os
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import aiofiles.os
import markdown
import requests
from nio import (
    AsyncClient,
    AsyncClientConfig,
    ErrorResponse,
    RemoteProtocolError,
    RoomMessage,
    RoomSendResponse,
    UploadError,
    LoginResponse,
)
from nio.exceptions import OlmUnverifiedDeviceError
from nio.responses import UploadResponse, KeysQueryResponse
from nio.crypto.device import TrustState
from PIL import Image

from .auth import AuthLogin
from .config import bot_lib_config, logger
from .room_utils import room_is_direct_message

import asyncio
import traceback


def check_valid_homeserver(homeserver: str):
    if not (homeserver.startswith("http://") or homeserver.startswith("https://")):
        raise ValueError(
            f"Invalid Homeserver, should start with http:// or https://, is {homeserver} instead"
        )
    matrix_version_url = f"{homeserver}/_matrix/client/versions"
    try:
        response = requests.get(matrix_version_url, timeout=60)
        response.raise_for_status()
    except requests.HTTPError as http_error:
        raise ValueError(
            f"Invalid Homeserver, could not connect to {matrix_version_url}"
        ) from http_error


def extract_text_from_html(html: str) -> str:
    """This is used to get a rough non-HTML fallback version to put in `body`"""

    class HTMLFilter(HTMLParser):
        text = ""

        def handle_data(self, data):
            """Extract and contatenate all text inside and outside of HTML tags, which are ignored"""
            self.text += data

    filter = HTMLFilter()
    filter.feed(html)

    return filter.text


class MatrixClient(AsyncClient):
    """
    A class to interact with the matrix-nio library. Usually used by the Bot class, and sparingly by the bot developer.
    Subclass AsyncClient from nio for ease of use.
    """

    def __init__(self, auth: AuthLogin):
        self.auth = auth
        logger.info("Initialisation MatrixClient avec les identifiants fournis")
        
        try:
            logger.info(f"Vérification du homeserver: {self.auth.credentials.homeserver}")
            check_valid_homeserver(self.auth.credentials.homeserver)
            logger.info("Homeserver valide")
        except Exception as e:
            logger.error(f"Erreur lors de la vérification du homeserver: {str(e)}")
            raise
            
        self.matrix_config = bot_lib_config
        logger.info(f"Configuration Matrix chargée depuis bot_lib_config, store_path={self.matrix_config.store_path}")
        
        try:
            self.matrix_config.store_path.mkdir(mode=0o750, exist_ok=True, parents=True)
            logger.info(f"Répertoire de stockage créé: {self.matrix_config.store_path}")
        except Exception as e:
            logger.error(f"Erreur lors de la création du répertoire de stockage: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise
            
        client_config = AsyncClientConfig(
            max_limit_exceeded=0,
            max_timeouts=0,  # 0 = illimité, pas de backoff exponentiel
            backoff_factor=0,  # pas de sleep entre les retries
            store_sync_tokens=True,
            encryption_enabled=self.matrix_config.encryption_enabled,
            request_timeout=90,  # timeout aiohttp en secondes
        )
        logger.info(f"Configuration AsyncClient créée, encryption_enabled={self.matrix_config.encryption_enabled}")
        
        try:
            logger.info("Initialisation de AsyncClient (parent)")
            super().__init__(
                homeserver=self.auth.credentials.homeserver,
                user=self.auth.credentials.username,
                device_id=self.auth.device_id,
                store_path=str(self.matrix_config.store_path.resolve()),
                config=client_config,
            )
            logger.info("Initialisation AsyncClient réussie")
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation de AsyncClient: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    async def automatic_login(self):
        """Login the client to the homeserver with better error handling"""
        login_successful = False
        
        logger.info("=== DÉBUT TENTATIVE AUTHENTIFICATION MATRIX ===")
        logger.info(f"Vérification de la configuration: homeserver={self.auth.credentials.homeserver}, username={self.auth.credentials.username}, device_id={self.auth.device_id}")
        
        # Ajouter une gestion du timeout global
        try:
            await asyncio.wait_for(
                self._perform_login_attempts(),
                timeout=60  # Timeout de 60 secondes pour l'ensemble du processus de login
            )
            logger.info("=== AUTHENTIFICATION MATRIX RÉUSSIE ===")
            return True
        except asyncio.TimeoutError:
            logger.error("TIMEOUT lors de l'authentification Matrix - processus bloquant détecté")
            logger.warning("Continuation du bot malgré l'échec d'authentification Matrix")
            return False
        except Exception as e:
            logger.error(f"ERREUR FATALE lors de l'authentification Matrix: {str(e)}")
            logger.error(traceback.format_exc())
            logger.warning("Continuation du bot malgré l'erreur critique d'authentification")
            return False
            
    async def _perform_login_attempts(self):
        """Sous-routine pour isoler les tentatives d'authentification"""
        # Tenter d'utiliser le token existant d'abord
        if self.auth.access_token:
            try:
                logger.info(f"Tentative de connexion avec token existant: {self.auth.access_token[:10]}...")
                self.access_token = self.auth.access_token
                who_am_i = await self.whoami()
                if not isinstance(who_am_i, ErrorResponse):
                    # Token valide
                    logger.info(f"Token valide, connecté en tant que {who_am_i.user_id}")
                    self.device_id = self.auth.device_id
                    self.user_id = who_am_i.user_id
                    
                    # Chargement du store de chiffrement si activé
                    if self.matrix_config.encryption_enabled:
                        logger.info("Chargement du store de chiffrement")
                        self.load_store()
                        # Publier les clés Olm du device (obligatoire pour que Synapse accepte le sync)
                        logger.info("Publication des clés Olm (keys_upload)...")
                        keys_resp = await self.keys_upload()
                        logger.info(f"keys_upload: {keys_resp}")
                        # Auto-trust tous les devices connus (pattern bot)
                        self.auto_trust_devices()
                        # Importer les clés E2E si configurées (Megolm session export)
                        await self.import_e2e_keys_if_configured()
                        # Callback pour auto-trust les nouveaux devices après chaque keys_query
                        self.add_response_callback(
                            lambda resp: self.auto_trust_devices() or None,
                            KeysQueryResponse,
                        )

                    logger.info(f"Connecté avec la session existante: {self.user_id} (device_id: {self.auth.device_id})")
                    return True
                else:
                    logger.warning(f"Token existant invalide: {who_am_i.message}")
            except Exception as e:
                logger.warning(f"Erreur utilisation token existant: {str(e)}")
                self.access_token = None
        else:
            logger.info("Pas de token existant, authentification par mot de passe nécessaire")
        
        # Si on arrive ici, le token n'était pas valide, essayer login/password
        try:
            # Nouvelle tentative avec mot de passe
            logger.info("Tentative d'authentification avec login/password...")
            # Modification pour compatibilité avec matrix-nio 0.25.2
            response = await self.login(
                password=self.auth.credentials.password,
                device_name=self.auth.device_name
                # device_id retiré car non supporté par cette version de matrix-nio
            )
            
            if isinstance(response, LoginResponse):
                logger.info(f"Authentification réussie, user_id: {response.user_id}, device_id: {response.device_id}")
                # Charger le store E2E et publier les clés Olm
                if self.matrix_config.encryption_enabled:
                    self.load_store()
                    logger.info("Publication des clés Olm (keys_upload) après login...")
                    keys_resp = await self.keys_upload()
                    logger.info(f"keys_upload: {keys_resp}")
                    self.auto_trust_devices()
                    # Importer les clés E2E si configurées (Megolm session export)
                    await self.import_e2e_keys_if_configured()
                    self.add_response_callback(
                        lambda resp: self.auto_trust_devices() or None,
                        KeysQueryResponse,
                    )
                # Persister la session pour réutiliser le même device au prochain démarrage
                self.auth.device_id = response.device_id
                self.auth.access_token = response.access_token
                self.auth.write_session_file()
                logger.info(f"Session sauvegardée dans {self.auth.session_stored_file_path}")
                return response
            else:
                logger.error(f"Échec de l'authentification: {response}")
                return response
        except Exception as e:
            logger.error(f"Erreur critique lors de l'authentification: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def import_e2e_keys_if_configured(self) -> None:
        """Import Megolm session keys from E2E_KEYS_PATH env var if set."""
        import os as _os
        keys_path = _os.environ.get("E2E_KEYS_PATH", "")
        passphrase = _os.environ.get("E2E_KEYS_PASSPHRASE", "")
        if not keys_path or not passphrase:
            return
        if not _os.path.exists(keys_path):
            import logging as _log
            _log.getLogger("colaig.bot").warning(f"E2E_KEYS_PATH défini mais fichier introuvable: {keys_path}")
            return
        import logging as _log
        _log = _log.getLogger("colaig.bot")
        try:
            _log.info(f"Importation des clés E2E depuis {keys_path}...")
            resp = await self.import_keys(keys_path, passphrase)
            _log.info(f"Clés E2E importées: {resp}")
        except Exception as e:
            _log.error(f"Erreur import clés E2E: {e}")

    def auto_trust_devices(self) -> None:
        """Marque tous les devices non-vérifiés comme user_ignored (pattern bot).
        Le bot ne fait pas de vérification manuelle — tous les devices sont acceptés.
        """
        if self.olm is None:
            return
        count = 0
        for device in self.olm.device_store:
            if device.trust_state == TrustState.unset:
                device.trust_state = TrustState.ignored
                count += 1
        if count:
            import logging as _log
            _log.getLogger("colaig.bot").info(f"auto_trust: {count} device(s) marqués ignored")

    def get_non_private_rooms(self):
        return {
            room_id: room
            for room_id, room in self.rooms.items()
            if not room_is_direct_message(room)
        }

    async def get_display_name(self):
        res = await self.get_displayname(self.user_id)
        if isinstance(res, ErrorResponse):
            return None
        return res.displayname

    async def _send_room(
        self,
        room_id: str,
        content: dict,
        message_type: str = "m.room.message",
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
        ignore_unverified_devices: Optional[bool] = None,
    ):
        """
        Send a custom event in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        content : dict
            The content block of the event to be sent.

        message_type : str, optional
            The type of event to send, default m.room.message.

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.

        ignore_unverified_devices : bool, optional
            Whether to ignore that devices are not verified and send the
            message to them regardless on a per-message basis.
            If None is specified, the global config value is used.
        """

        if thread_root:
            content.setdefault("m.relates_to", {})["event_id"] = thread_root
            content["m.relates_to"]["rel_type"] = "m.thread"

        if reply_to:
            content.setdefault("m.relates_to", {}).setdefault("m.in_reply_to", {})["event_id"] = (
                reply_to
            )

        if thread_root and reply_to:
            content["m.relates_to"]["is_falling_back"] = True

        try:
            res = await self.room_send(
                room_id=room_id,
                message_type=message_type,
                content=content,
                ignore_unverified_devices=ignore_unverified_devices
                or self.matrix_config.ignore_unverified_devices,
            )
            if isinstance(res, RoomSendResponse):
                return res.event_id
        except OlmUnverifiedDeviceError:
            logger.info(
                "Message could not be sent. "
                "Set ignore_unverified_devices = True to allow sending to unverified devices."
            )
            logger.info("Automatically blacklisting the following devices:")
            for user in self.rooms[room_id].users:
                unverified: list[str] = []
                assert self.olm
                for device_id, device in self.olm.device_store[user].items():
                    if not (
                        self.olm.is_device_verified(device)
                        or self.olm.is_device_blacklisted(device)
                    ):
                        self.olm.blacklist_device(device)
                        unverified.append(device_id)
                if len(unverified) > 0:
                    logger.info(f"\tUser {user}: {', '.join(unverified)}")

            res = await self.room_send(
                room_id=room_id,
                message_type=message_type,
                content=content,
                ignore_unverified_devices=ignore_unverified_devices
                or self.matrix_config.ignore_unverified_devices,
            )
            if isinstance(res, RoomSendResponse):
                return res.event_id

        return None

    async def send_text_message(
        self,
        room_id: str,
        message: str,
        msgtype: str = "m.text",
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send a text message in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        message : str
            The content of the message to be sent.

        msgtype : str, optional
            The type of message to send: m.text (default), m.notice, etc

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """

        return await self._send_room(
            room_id=room_id,
            content={"msgtype": msgtype, "body": message},
            reply_to=reply_to,
            thread_root=thread_root,
        )

    async def send_html_message(
        self,
        room_id: str,
        message: str,
        msgtype: str = "m.text",
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send an HTML message in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        message : str
            The content of the message to be sent.

        msgtype : str, optional
            The type of message to send: m.text (default), m.notice, etc

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """
        return await self._send_room(
            room_id=room_id,
            content={
                "msgtype": msgtype,
                "body": extract_text_from_html(message),
                "format": "org.matrix.custom.html",
                "formatted_body": message,
            },
            reply_to=reply_to,
            thread_root=thread_root,
        )

    async def send_markdown_message(
        self,
        room_id: str,
        message: str,
        msgtype: str = "m.text",
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send a markdown message in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        message : str
            The content of the message to be sent.

        msgtype : str, optional
            The type of message to send: m.text (default), m.notice, etc

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """
        # Prétraitement pour éviter les interprétations incorrectes de listes numérotées
        import re
        
        # Fonction pour prétraiter le texte Markdown
        def preprocess_markdown(text):
            lines = text.split('\n')
            processed_lines = []
            
            # Pattern pour détecter les titres potentiels qui pourraient être interprétés comme des listes
            title_pattern = r'^([A-Za-z]+\s?[A-Za-z]*)\s?:'
            
            # Modèle pour détecter les lignes qui commencent par un nombre suivi d'un point
            number_start_pattern = r'^(\d+)\.\s'
            
            for i, line in enumerate(lines):
                # Si c'est une ligne qui ressemble à un titre (mot suivi de ":")
                if re.match(title_pattern, line):
                    # Si c'est un titre potentiel et qu'il n'est pas déjà en gras, le mettre en gras
                    if i == 0 or lines[i-1].strip() == "":
                        if not line.startswith('**') and not line.endswith('**'):
                            line = f"**{line}**"
                
                # Si c'est une ligne qui commence par un nombre suivi d'un point (numéro potentiel de liste)
                if re.match(number_start_pattern, line):
                    # Si la ligne précédente ne finit pas par ":" ou n'est pas vide, c'est
                    # probablement une phrase normale qui commence par un chiffre
                    if i > 0:
                        prev_line = lines[i-1].strip()
                        if not prev_line.endswith(':') and prev_line != "":
                            # Remplacer par une puce
                            line = re.sub(number_start_pattern, '- ', line)
                
                processed_lines.append(line)
            
            return '\n'.join(processed_lines)
        
        # Appliquer le prétraitement
        message = preprocess_markdown(message)
        
        # Conversion markdown vers HTML
        message = markdown.markdown(message, extensions=["fenced_code", "nl2br"])
        
        # Post-traitement pour corriger les listes numérotées inappropriées dans le HTML généré
        def fix_html_lists(html_content):
            # Détection des structures HTML qui correspondent à des listes mal formées ou inappropriées
            # Cas spécifique: <ol><li>Titre :</li></ol> - liste qui démarre avec un titre suivi d'un ":"
            import re
            
            # Cas 1: Corriger les listes commençant par un titre/descriptif
            # Remplacer <ol><li>Titre :</li> par <p><strong>Titre :</strong></p>
            html_content = re.sub(
                r'<ol>\s*<li>([^<:]+):</li>',
                r'<p><strong>\1:</strong></p>',
                html_content
            )
            
            # Cas 2: Si une liste contient seulement un ou deux éléments et que ces éléments semblent être des phrases complètes
            # plutôt que des éléments d'une vraie liste, convertir en paragraphes
            # Remplacer <ol><li>Phrase complète qui n'est pas un vrai élément de liste.</li></ol> par des paragraphes
            content_with_no_single_item_lists = re.sub(
                r'<ol>\s*<li>([A-Z][^<.]+\.)</li>\s*</ol>',
                r'<p>\1</p>',
                html_content
            )
            
            # Si le remplacement a fonctionné, utiliser cette version
            if content_with_no_single_item_lists != html_content:
                html_content = content_with_no_single_item_lists
            
            # Cas 3: Convertir les listes numérotées en listes à puces dans des cas spécifiques
            # comme pour les explications de fonctionnalités ou des caractéristiques
            # Remplacer <ol> par <ul> pour certains motifs
            content_with_bullets = re.sub(
                r'<ol>\s*<li>([^<]*?(?:prix|classe|type|référenc|fonction|caractéristique)[^<]*?)</li>',
                r'<ul><li>\1</li>',
                html_content
            )
            
            # Si ce remplacement a fonctionné, remplacer aussi </ol> par </ul>
            if content_with_bullets != html_content:
                html_content = content_with_bullets.replace('</ol>', '</ul>')
            
            # Cas 4: Problème spécifique observé - liste numérotée après un paragraphe contenant un titre en gras se terminant par ":"
            # Par exemple "<p><strong>Relevé topographique :</strong></p><ol>..."
            # Remplacer toute <ol> qui suit un titre en gras avec ":" par <ul>
            html_content = re.sub(
                r'<p><strong>([^<:]+):</strong></p>\s*<ol>',
                r'<p><strong>\1:</strong></p><ul>',
                html_content
            )
            # Remplacer les éventuelles balises </ol> correspondantes par </ul>
            if '<ul>' in html_content and '</ol>' in html_content:
                # Compter le nombre de <ul> et </ul>
                ul_open_count = html_content.count('<ul>')
                ul_close_count = html_content.count('</ul>')
                
                # Si on a plus de balises ouvrantes <ul> que de fermantes </ul>,
                # remplacer des </ol> par </ul> pour équilibrer
                if ul_open_count > ul_close_count:
                    # Combien de </ol> à remplacer
                    to_replace = min(ul_open_count - ul_close_count, html_content.count('</ol>'))
                    
                    # Remplacer progressivement
                    for _ in range(to_replace):
                        html_content = html_content.replace('</ol>', '</ul>', 1)
            
            return html_content
        
        # Appliquer le post-traitement au HTML généré
        message = fix_html_lists(message)
        
        if bot_lib_config.message_prefix:
            message = bot_lib_config.message_prefix + "\n\n" + message

        return await self.send_html_message(
            room_id=room_id,
            message=message,
            msgtype=msgtype,
            reply_to=reply_to,
            thread_root=thread_root,
        )

    async def send_reaction(self, room_id: str, event: RoomMessage, key: str):
        """
        Send a reaction to a message in a Matrix room.

        Parameters
        ----------
        room_id : str
            The room id of the destination of the message.

        event :
            The event object you want to react to.

        key: str
            The content of the reaction. This is usually an emoji, but may technically be any text.
        """

        return await self._send_room(
            room_id=room_id,
            content={
                "m.relates_to": {
                    "event_id": event.event_id,
                    "key": key,
                    "rel_type": "m.annotation",
                }
            },
            message_type="m.reaction",
        )

    async def _upload_file(
        self,
        file_path: Union[str, Path],
        mime_type: Optional[str] = None,
        filename: Optional[str] = None,
        encrypt: bool = False,
    ) -> Tuple[Union[UploadResponse, UploadError], os.stat_result, str, Optional[Dict[str, Any]]]:
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        if not filename:
            filename = Path(file_path).name
        file_stat = await aiofiles.os.stat(file_path)
        async with aiofiles.open(file_path, "r+b") as file:
            uploaded_file, maybe_keys = await self.upload(
                file,
                content_type=mime_type,
                filename=filename,
                filesize=file_stat.st_size,
                encrypt=encrypt,
            )
        if not isinstance(uploaded_file, UploadResponse):
            logger.error(f"Failed Upload Response: {uploaded_file}")
        return uploaded_file, file_stat, mime_type, maybe_keys

    async def send_image_message(
        self,
        room_id: str,
        image_filepath: str,
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send an image message in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        image_filepath : str
            The path to the image on your machine.

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """
        encrypt = room_id in self.encrypted_rooms

        uploaded_file, file_stat, mime_type, maybe_keys = await self._upload_file(
            image_filepath, encrypt=encrypt
        )
        if isinstance(uploaded_file, ErrorResponse):
            return None

        image = Image.open(image_filepath)
        (width, height) = image.size

        content = {
            "body": os.path.basename(image_filepath),
            "info": {
                "size": file_stat.st_size,
                "mimetype": mime_type,
                "thumbnail_info": None,
                "w": width,
                "h": height,
                "thumbnail_url": None,
            },
            "msgtype": "m.image",
        }

        if encrypt and maybe_keys:
            content["file"] = maybe_keys
            content["file"]["url"] = uploaded_file.content_uri
        else:
            content["url"] = uploaded_file.content_uri

        try:
            return await self._send_room(
                room_id=room_id, content=content, reply_to=reply_to, thread_root=thread_root
            )
        except Exception:
            logger.error(f"Failed to send image file {image_filepath}")

    async def send_video_message(
        self,
        room_id: str,
        video_filepath: str,
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send a video message in a Matrix room.

        Parameters
        ----------
        room_id : str
            The room id of the destination of the message.

        video_filepath : str
            The path to the video on your machine.

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """
        encrypt = room_id in self.encrypted_rooms

        uploaded_file, file_stat, mime_type, maybe_keys = await self._upload_file(
            video_filepath, encrypt=encrypt
        )
        if isinstance(uploaded_file, ErrorResponse):
            return None

        content = {
            "body": os.path.basename(video_filepath),
            "info": {
                "size": file_stat.st_size,
                "mimetype": mime_type,
                "thumbnail_info": None,
            },
            "msgtype": "m.video",
        }

        if encrypt and maybe_keys:
            content["file"] = maybe_keys
            content["file"]["url"] = uploaded_file.content_uri
        else:
            content["url"] = uploaded_file.content_uri

        try:
            return await self._send_room(
                room_id=room_id, content=content, reply_to=reply_to, thread_root=thread_root
            )
        except Exception:
            logger.error(f"Failed to send video file {video_filepath}")

    async def send_file_message(
        self,
        room_id: str,
        filepath: str,
        mime_type: Optional[str] = None,
        filename: Optional[str] = None,
        reply_to: Optional[str] = None,
        thread_root: Optional[str] = None,
    ):
        """
        Send a file message in a Matrix room.

        Parameters
        -----------
        room_id : str
            The room id of the destination of the message.

        filepath : str
            The path to the image on your machine.

        mime_type : str, optional
            The MIME type of the file. If not specified it will try to
            autodetect it.

        filename (str, optional): The file's original name.

        reply_to : str, optional
            The event id of the message to reply to.

        thread_root : str, optional
            The event id of the message acting as a thread root for the message.
        """
        encrypt = room_id in self.encrypted_rooms

        uploaded_file, file_stat, mime_type, maybe_keys = await self._upload_file(
            filepath, mime_type, filename, encrypt
        )
        if isinstance(uploaded_file, ErrorResponse):
            return None

        if not filename:
            filename = os.path.basename(filepath)

        content = {
            "body": filename,
            "info": {
                "size": file_stat.st_size,
                "mimetype": mime_type,
            },
            "msgtype": "m.file",
        }

        if encrypt and maybe_keys:
            content["file"] = maybe_keys
            content["file"]["url"] = uploaded_file.content_uri
        else:
            content["url"] = uploaded_file.content_uri

        try:
            return await self._send_room(
                room_id=room_id, content=content, reply_to=reply_to, thread_root=thread_root
            )
        except Exception:
            logger.error(f"Failed to send file {filepath}")
