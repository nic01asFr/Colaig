# SPDX-FileCopyrightText: 2021 - 2022 Isaac Beverly <https://github.com/imbev>
# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
#
# SPDX-License-Identifier: MIT
import traceback
from functools import wraps
import asyncio

from nio import (
    Event,
    InviteMemberEvent,
    MatrixRoom,
    MegolmEvent,
    RoomMessageText,
    ToDeviceEvent,
    UnknownEvent,
)

from bot_msg import AlbertMsg

from .client import MatrixClient
from .config import bot_lib_config, logger
from .eventparser import (
    EventNotConcerned,
    EventParser,
    MessageEventParser,
)


def properly_fail(matrix_client, error_msg=AlbertMsg.failed):
    """use this decorator so that your async callback never crash,
    log the error and return a message to the room"""

    def decorator(func):
        @wraps(func)
        async def wrapper(room, event):
            try:
                return await func(room, event)
            except Exception as unexpected_exception:
                await matrix_client.send_text_message(room.room_id, error_msg, msgtype="m.notice")
                logger.warning(f"command failed with exception: {unexpected_exception}")
                traceback.print_exc()
            # Ne pas désactiver room_typing ici car c'est géré dans register_on_custom_event

        return wrapper

    return decorator


def ignore_when_not_concerned(func):
    """decorator to use with async function using EventParser"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except EventNotConcerned:
            return

    return wrapper


class Callbacks:
    """A class for handling callbacks."""

    def __init__(self, matrix_client: MatrixClient):
        self.matrix_client = matrix_client
        self.startup: list = []
        self.client_callback: list = []

    def register_on_custom_event(self, func, onEvent: Event, feature: dict):
        @properly_fail(self.matrix_client)
        @ignore_when_not_concerned
        async def wrapped_func(room, event):
            # Log détaillé pour le débogage des types d'événements
            logger.debug(f"Processing event for feature {feature.get('name', 'unknown')}: {type(event).__name__}, OnEvent type: {onEvent.__name__}")
            
            # Ignorer les messages envoyés par le bot lui-même
            if hasattr(event, 'sender') and event.sender == self.matrix_client.user_id:
                logger.debug(f"Ignoring own message from {event.sender}")
                return

            if not isinstance(event, onEvent):
                logger.debug(f"Event type mismatch for {feature.get('name', 'unknown')}: {type(event).__name__} != {onEvent.__name__}")
                raise EventNotConcerned

            if onEvent == RoomMessageText:
                ep = MessageEventParser(
                    room=room, event=event, matrix_client=self.matrix_client, log_usage=True
                )
                
                # Vérifier si cette fonction devrait traiter cette commande
                if feature.get("commands"):
                    try:
                        # Vérifier strictement que la commande correspond avant de l'appeler
                        body = event.body.strip()
                        user_command = body.split()[0] if body else ""
                        
                        # Vérifie si cette fonction devrait traiter cette commande
                        found_match = False
                        prefix = feature["prefix"]
                        
                        # Chercher une correspondance exacte avec les commandes de cette fonction
                        for cmd in feature["commands"]:
                            cmd_with_prefix = f"{prefix}{cmd}"
                            if user_command == cmd_with_prefix:
                                found_match = True
                                logger.info(f"Commande correspondante trouvée: {user_command} -> fonction {feature['name']}")
                                break
                                
                        if not found_match:
                            # Ce n'est pas la commande correcte pour cette fonction
                            logger.debug(f"Commande non correspondante pour {feature['name']}: {user_command}")
                            raise EventNotConcerned
                            
                        # Si on arrive ici, c'est bien la bonne commande pour cette fonction spécifique
                        ep.parse_command(feature["commands"], prefix=feature["prefix"])
                    except (IndexError, EventNotConcerned):
                        # Pas la bonne commande ou format incorrect
                        raise EventNotConcerned
            else:
                ep = EventParser(
                    room=room, event=event, matrix_client=self.matrix_client, log_usage=True
                )

            # Activer l'indicateur "en train d'écrire" avant de traiter la commande
            await self.matrix_client.room_typing(room.room_id, typing_state=True)
            
            # MÉCANISME ANTI-BLOCAGE: Utiliser asyncio.wait_for avec timeout
            COMMAND_TIMEOUT = 60.0  # Timeout de 60 secondes pour toutes les commandes
            
            cmd_name = feature.get('name', 'unknown')
            logger.info(f"[TIMEOUT DEBUG] Démarrage de la commande {cmd_name} avec timeout de {COMMAND_TIMEOUT}s")
            
            try:
                # Exécuter la commande avec un timeout global
                try:
                    await asyncio.wait_for(
                        func(ep=ep, matrix_client=self.matrix_client),
                        timeout=COMMAND_TIMEOUT
                    )
                    logger.info(f"[TIMEOUT DEBUG] Commande {cmd_name} terminée avec succès dans le délai imparti")
                except asyncio.TimeoutError:
                    logger.error(f"[TIMEOUT DEBUG] TIMEOUT GLOBAL pour la commande {cmd_name} après {COMMAND_TIMEOUT}s!")
                    
                    # Informer l'utilisateur du timeout
                    try:
                        await self.matrix_client.send_markdown_message(
                            room.room_id,
                            f"❌ **Délai d'attente dépassé**\n\nLa commande a pris trop de temps à s'exécuter et a été annulée. Veuillez réessayer ultérieurement.",
                            msgtype="m.notice"
                        )
                    except Exception as msg_err:
                        logger.error(f"[TIMEOUT DEBUG] Impossible d'envoyer le message de timeout: {str(msg_err)}")
                        
                    # Essayer de nettoyer l'état
                    try:
                        # Nettoyer le contexte de conversation
                        from app.commands import get_unified_session_context
                        from app.services.context.types import ContextType
                        from app.services.context.instance import context_manager
                        
                        # Récupérer la configuration
                        config = getattr(self.matrix_client, "albert_config", self.matrix_client.config)
                        
                        # Récupérer et nettoyer le contexte
                        session_context = await get_unified_session_context(config, room.room_id, event.sender)
                        
                        if hasattr(session_context, "conversation_state"):
                            # Nettoyer les drapeaux bloquants
                            session_context.conversation_state.pop("in_command_thread", None)
                            session_context.conversation_state["command_completed"] = True
                            session_context.conversation_state["timeout_occurred"] = True
                            session_context.conversation_state["timeout_command"] = cmd_name
                            
                            # Mettre à jour le contexte
                            session_id = f"{room.room_id}_{event.sender}"
                            await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
                            logger.info(f"[TIMEOUT DEBUG] Contexte nettoyé avec succès après timeout")
                    except Exception as ctx_err:
                        logger.error(f"[TIMEOUT DEBUG] Erreur lors du nettoyage du contexte: {str(ctx_err)}")
                    
                    # Marquer l'événement comme traité malgré l'erreur
                    if hasattr(event, 'event_id'):
                        try:
                            from app.commands.registry import mark_event_processed
                            mark_event_processed(event.event_id, cmd_name, command_completed=True)
                            logger.info(f"[TIMEOUT DEBUG] Événement marqué comme traité après timeout")
                        except Exception as mark_err:
                            logger.error(f"[TIMEOUT DEBUG] Erreur lors du marquage de l'événement: {str(mark_err)}")
                    
            except Exception as e:
                # Capture explicite de toutes les exceptions pour assurer qu'elles sont bien loggées
                logger.error(f"ERREUR CRITIQUE lors de l'exécution de {feature.get('name', 'unknown')}: {str(e)}")
                logger.error(f"Détails de l'erreur: {traceback.format_exc()}")
                
                # Tentative d'envoi d'un message d'erreur à l'utilisateur
                try:
                    await self.matrix_client.send_markdown_message(
                        room.room_id,
                        "❌ Une erreur inattendue s'est produite lors du traitement de votre commande. Les administrateurs ont été notifiés.",
                        msgtype="m.notice"
                    )
                except Exception as msg_err:
                    logger.error(f"Impossible d'envoyer le message d'erreur: {str(msg_err)}")
            finally:
                # S'assurer que l'indicateur est désactivé, que la commande réussisse ou non
                await self.matrix_client.room_typing(room.room_id, typing_state=False)
                
        self.client_callback.append((wrapped_func, onEvent))

    def register_on_reaction_event(self, func):
        @properly_fail(self.matrix_client)
        @ignore_when_not_concerned
        async def wrapped_func(room: MatrixRoom, event: Event):
            if event.type == "m.reaction":
                await func(room, event, event.source["content"]["m.relates_to"]["key"])

        self.client_callback.append((wrapped_func, UnknownEvent))

    def register_on_startup(self, func):
        self.startup.append(func)

    async def setup_callbacks(self):
        """Add callbacks to async_client"""
        if bot_lib_config.join_on_invite:
            self.matrix_client.add_event_callback(self.invite_callback, InviteMemberEvent)

        self.matrix_client.add_event_callback(self.decryption_failure, MegolmEvent)
        for function, event in self.client_callback:
            if issubclass(event, ToDeviceEvent):
                self.matrix_client.add_to_device_callback(function, event)
            else:
                self.matrix_client.add_event_callback(function, event)

    async def invite_callback(self, room: MatrixRoom, event: InviteMemberEvent):
        """Callback for handling invites."""
        if not event.membership == "invite":
            return

        try:
            await self.matrix_client.join(room.room_id)
            logger.info(f"Joined {room.room_id}")
        except Exception as join_room_exception:
            logger.info(f"Failed to join {room.room_id}", join_room_exceptions=join_room_exception)

    async def decryption_failure(self, room: MatrixRoom, event: MegolmEvent):
        """Callback for handling decryption errors."""
        if not isinstance(event, MegolmEvent):
            return

        logger.error(
            f"Failed to decrypt message: {event.event_id} from {event.sender} in {room.room_id}. "
            "If this error persists despite verification, reset the crypto session by deleting "
            f"{self.matrix_client.matrix_config.store_path} "
            f"and {self.matrix_client.auth.credentials.session_stored_file_path}. "
            "You will have to verify any verified devices anew."
        )
        await self.matrix_client.send_text_message(
            room.room_id,
            "Failed to decrypt your message. Make sure encryption is enabled in my config and "
            "either enable sending messages to unverified devices or verify me if possible.",
            msgtype="m.notice",
        )
