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

from app.bot_msg import AlbertMsg
from app.services.context.models import ConversationStateKeys

from .client import MatrixClient
from .config import bot_lib_config, logger
from .eventparser import (
    EventNotConcerned,
    EventParser,
    MessageEventParser,
)


def properly_fail(client):
    """
    un decorateur pour gérer les erreurs dans un callback.
    On affiche les erreurs et éviter l'arrêt du thread principal.
    """
    
    def wrapper(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except asyncio.CancelledError:
                # Ne pas intercepter les annulations
                raise
            except Exception as e:
                error_msg = f"Erreur dans le callback {func.__name__}: {e}"
                logger.error(error_msg)
                try:
                    room = args[0]  # typiquement le 1er paramètre est la salle (MatrixRoom)
                    # Journaliser l'erreur complète pour le débogage
                    tb = traceback.format_exc()
                    logger.error(f"Détails de l'erreur: \n{tb}")

                    # Si possible, informer le salon qu'une erreur s'est produite
                    if hasattr(room, 'room_id'):
                        try:
                            await client.send_text_message(
                                room.room_id,
                                f"❌ Une erreur s'est produite lors du traitement de votre demande. " 
                                f"Veuillez réessayer ou contacter l'administrateur.",
                                msgtype="m.notice",
                            )
                        except Exception as send_error:
                            logger.error(f"Impossible d'envoyer le message d'erreur: {send_error}")
                except Exception:
                    # En cas d'erreur lors de la gestion de l'erreur, simplement journaliser
                    pass
                    
                # Ne pas propager l'exception pour éviter d'interrompre la boucle principale
                
        return wrapped
    return wrapper


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
            
            # S'assurer que les services sont initialisés avant d'exécuter la commande
            try:
                from app.services.initialization import initialize_services
                # Initialiser les services si nécessaire
                await initialize_services(self.matrix_client.config)
                logger.debug(f"Services initialisés pour la commande {feature.get('name', 'unknown')}")
            except Exception as e:
                logger.error(f"Erreur lors de l'initialisation des services: {str(e)}")
                # Continuer malgré l'erreur
            
            # MÉCANISME ANTI-BLOCAGE: Utiliser asyncio.wait_for avec timeout
            COMMAND_TIMEOUT = 180.0  # Timeout de 180 secondes (agent loop + WebDAV + LLM)
            
            cmd_name = feature.get('name', 'unknown')
            
            # Liste des commandes spéciales qui nécessitent un temps d'exécution plus long
            LONG_RUNNING_COMMANDS = [
                ("index", "rebuild"),  # Commande index avec action rebuild
                ("index", "clean"),    # Commande index avec action clean
            ]
            
            # Vérifier si nous sommes dans le cas d'une commande longue
            is_long_running = False
            if hasattr(ep.event, 'body'):
                message_parts = ep.event.body.strip().split()
                if len(message_parts) >= 2:
                    command = message_parts[0].lstrip('!')
                    action = message_parts[1]
                    if (command, action) in LONG_RUNNING_COMMANDS:
                        is_long_running = True
                        logger.info(f"[TIMEOUT DEBUG] Commande longue détectée: {command} {action} - pas de timeout global")
            
            try:
                # Appliquer ou non le timeout selon le type de commande
                if is_long_running:
                    logger.info(f"[TIMEOUT DEBUG] Exécution de la commande {cmd_name} sans timeout global")
                    # Exécution sans timeout
                    await func(ep=ep, matrix_client=self.matrix_client)
                    logger.info(f"[TIMEOUT DEBUG] Commande longue {cmd_name} terminée avec succès")
                else:
                    # Comportement standard avec timeout
                    logger.info(f"[TIMEOUT DEBUG] Démarrage de la commande {cmd_name} avec timeout de {COMMAND_TIMEOUT}s")
                    try:
                        # Exécuter la commande avec un timeout global
                        result = await asyncio.wait_for(
                            func(ep=ep, matrix_client=self.matrix_client),
                            timeout=COMMAND_TIMEOUT
                        )
                        # Envoyer le résultat si la commande retourne une chaîne
                        if isinstance(result, str) and result.strip():
                            await self.matrix_client.send_markdown_message(
                                room.room_id, result, msgtype="m.notice"
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
                            from app.services.context.instance import get_context_manager
                            
                            # Récupérer la configuration
                            config = getattr(self.matrix_client, "albert_config", self.matrix_client.config)
                            
                            # Récupérer et nettoyer le contexte
                            session_context = await get_unified_session_context(config, room.room_id, event.sender)
                            
                            if hasattr(session_context, "conversation_state"):
                                # Nettoyer les drapeaux bloquants
                                session_context.conversation_state.pop(ConversationStateKeys.IN_COMMAND_THREAD, None)
                                session_context.conversation_state[ConversationStateKeys.COMMAND_COMPLETED] = True
                                session_context.conversation_state[ConversationStateKeys.TIMEOUT_OCCURRED] = True
                                session_context.conversation_state[ConversationStateKeys.TIMEOUT_COMMAND] = cmd_name
                                
                                # Mettre à jour le contexte
                                session_id = f"{room.room_id}_{event.sender}"
                                context_manager = await get_context_manager()
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
                # Désactiver l'indicateur "en train d'écrire" après le traitement
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
            "⚠️ Je ne peux pas déchiffrer votre message. "
            "Pour résoudre ce problème, allez dans mon profil → Appareils → faites confiance à mon appareil, "
            "ou créez un nouveau salon avec moi.",
            msgtype="m.notice",
        )
