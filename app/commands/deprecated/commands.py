from typing import List, Dict, Any, Optional, Set

# ================================================================================================
# ATTENTION: Ce fichier est considéré comme DÉPRÉCIÉ.
# Les nouvelles commandes doivent être ajoutées dans les modules dédiés:
#  - app/commands/basic_commands.py
#  - app/commands/document_commands/
#  - etc.
#
# Ce fichier est conservé uniquement pour référence et sera supprimé dans une version future.
# ================================================================================================

# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import asyncio
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
import os
from datetime import datetime
from enum import Enum
import io

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventNotConcerned, EventParser
from nio import Event, RoomEncryptedFile, RoomMemberEvent, RoomMessageText

from bot_msg import AlbertMsg
from config import COMMAND_PREFIX, Config
from core_llm import (
    flush_collections_with_name,
    get_all_public_collections,
    get_or_create_collection_with_name,
    get_or_not_collection_with_name,
    get_documents,
    generate,
    get_available_models,
    get_available_modes,
    upload_file,
    AlbertApiClient,
)
from iam import TchapIam
from tchap_utils import (
    get_cleanup_body, 
    get_decrypted_file,
    get_previous_messages, 
    get_thread_messages, 
    isa_reply_to,
    room_is_direct_message
)
from actions.rag_action import AskAlbertRagAction
from actions.standard_rag_action import StandardMessageRagAction
from services.webdav import WebDAVService
from services.document_index import DocumentIndex
from services.index_service import IndexService
from services.context import ContextManager, ContextType
from services.context.models import BaseContext, UserContext  # Import des classes de contexte
from services.context.instance import context_manager  # Import du gestionnaire global

# Import de l'instance unique du registre de commandes
from app.commands.registry import command_registry

@dataclass
class CommandRegistry:
    function_register: dict
    activated_functions: set[str]

    def add_command(
        self,
        name: str,
        group: str,
        onEvent: Event,
        command: str | None,
        aliases: list[str] | None,
        prefix: str | None,
        help_message: str | None,
        for_geek: bool,
        func,
    ):
        commands = [command] if command else None
        if aliases:
            commands += aliases

        self.function_register[name] = {
            "name": name,
            "group": group,
            "onEvent": onEvent,
            "commands": commands,
            "prefix": prefix,
            "help": help_message,
            "for_geek": for_geek,
            "func": func,
        }

    def activate_and_retrieve_group(self, group_name: str) -> list:
        features = []
        for name, feature in self.function_register.items():
            if feature["group"] == group_name:
                self.activated_functions |= {name}
                features.append(feature)
        return features

    def is_valid_command(self, command) -> bool:
        valid_commands = []
        for name, feature in self.function_register.items():
            if name in self.activated_functions:
                if feature.get("commands"):
                    valid_commands += feature["commands"]
        return command in valid_commands

    def get_help(self, config: Config, verbose: bool = False) -> str:
        cmds = self._get_cmds(config, verbose)
        model_url = f"https://huggingface.co/{config.albert_model}"
        model_short_name = config.albert_model.split("/")[-1]
        return AlbertMsg.help(model_url, model_short_name, cmds)

    def show_commands(self, config: Config) -> str:
        cmds = self._get_cmds(config)
        return AlbertMsg.commands(cmds)

    def _get_cmds(self, config: Config, verbose: bool = False) -> list[str]:
        cmds = set(
            feature["help"]
            for name, feature in self.function_register.items()
            if name in self.activated_functions
            and feature["help"]
            and (not feature["for_geek"] or verbose)
            and not ("sources" in feature.get("commands") and config.albert_mode == "norag")
        )
        return sorted(list(cmds))


# ================================================================================
# Globals lifespan
# ================================================================================

command_registry = CommandRegistry({}, set())
user_configs: dict[str, Config] = defaultdict(lambda: Config())
tiam = TchapIam(Config())


async def log_not_allowed(msg: str, ep: EventParser, matrix_client: MatrixClient):
    """Send feedback message for unauthorized user"""
    config = user_configs[ep.sender]
    await matrix_client.send_markdown_message(ep.room.room_id, msg, msgtype="m.notice")

    # If user is new to the pending list, send a notification for a new pending user
    if await tiam.add_pending_user(config, ep.sender):
        if config.errors_room_id:
            try:
                await matrix_client.send_markdown_message(
                    config.errors_room_id,
                    f"\u26a0\ufe0f **New Albert Tchap user access request**\n\n{ep.sender}\n\nMatrix server: {config.matrix_home_server}",
                )
            except:
                print("Failed to find error room ?!")


# ================================================================================
# Decorators
# ================================================================================


def register_feature(
    group: str,
    onEvent: Event,
    command: str | None = None,
    aliases: list[str] | None = None,
    prefix: str = COMMAND_PREFIX,
    help: str | None = None,
    for_geek: bool = False,
):
    def decorator(func):
        command_registry.add_command(
            name=func.__name__,
            group=group,
            onEvent=onEvent,
            command=command,
            aliases=aliases,
            prefix=prefix,
            help_message=help,
            for_geek=for_geek,
            func=func,
        )
        return func

    return decorator


def only_allowed_user(func):
    """decorator to use with async function using EventParser"""

    @wraps(func)
    async def wrapper(ep: EventParser, matrix_client: MatrixClient):
        ep.do_not_accept_own_message()  # avoid infinite loop
        ep.only_on_direct_message()  # Only in direct room for now (need a spec for "saloon" conversation)

        config = user_configs[ep.sender]
        is_allowed, msg = await tiam.is_user_allowed(config, ep.sender, refresh=True)
        if not is_allowed:
            if not msg or ep.is_command(COMMAND_PREFIX):
                # Only send back the message for the generic albert_answer method
                # ignoring other callbacks.
                raise EventNotConcerned

            await log_not_allowed(msg, ep, matrix_client)
            return

        await func(ep, matrix_client)
        await matrix_client.room_typing(ep.room.room_id, typing_state=False)

    return wrapper


# ================================================================================
# Bot commands
# ================================================================================


@register_feature(
    group="basic",
    onEvent=RoomMessageText,
    command="aide",
    aliases=["help", "aiuto"],
    help=AlbertMsg.shorts["help"],
)
@only_allowed_user
# COMMENTÉ: Cette fonction a été migrée vers un module dédié
# async def help(ep: EventParser, matrix_client: MatrixClient):
#     config = user_configs[ep.sender]
# 
#     commands = ep.get_command()
#     verbose = False
#     if len(commands) > 1 and commands[1] in ["-v", "--verbose", "--more", "-a", "--all"]:
#         verbose = True
#     await matrix_client.send_markdown_message(ep.room.room_id, command_registry.get_help(config, verbose))  # fmt: off
# 

@register_feature(
    group="albert",
    # @DEBUG: RoomCreateEvent is not captured ?
    onEvent=RoomMemberEvent,
    help=None,
)
@only_allowed_user
async def albert_welcome(ep: EventParser, matrix_client: MatrixClient):
    """
    Receive the join/invite event and send the welcome/help message
    """
    config = user_configs[ep.sender]

    ep.only_on_join()

    config.update_last_activity()
    await matrix_client.room_typing(ep.room.room_id)
    await asyncio.sleep(
        3
    )  # wait for the room to be ready - otherwise the encryption seems to be not ready
    await matrix_client.send_markdown_message(ep.room.room_id, command_registry.get_help(config))


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="reset",
    help=AlbertMsg.shorts["reset"],
)
@only_allowed_user
async def albert_reset(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    if config.albert_with_history:
        config.update_last_activity()
        config.albert_history_lookup = 0
        
        # Suppression du contexte existant
        context_id = f"{ep.room.room_id}_{ep.sender}"
        await context_manager.delete_context(context_id)
        
        # Création d'un nouveau contexte
        await context_manager.create_context(
            ep.room.room_id,
            ep.sender,
            config=config.dict()
        )
        
        reset_message = AlbertMsg.reset
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            reset_message,
            msgtype="m.notice"
        )
        
        message = AlbertMsg.flush_start
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            message,
            msgtype="m.notice"
        )
        
        await matrix_client.room_typing(ep.room.room_id)
        flush_collections_with_name(config, ep.room.room_id)
        config.albert_collections_by_id = {}
        
        message = AlbertMsg.flush_end
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            message,
            msgtype="m.notice"
        )
    else:
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "Le mode conversation n'est pas activé. tapez !conversation pour l'activer.",
            msgtype="m.notice",
        )


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="conversation",
    help=AlbertMsg.shorts["conversation"],
    for_geek=True,
)
@only_allowed_user
async def albert_conversation(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    context_id = f"{ep.room.room_id}_{ep.sender}"
    
    if config.albert_with_history:
        config.albert_with_history = False
        config.albert_history_lookup = 0
        
        # Suppression du contexte existant
        await context_manager.delete_context(context_id)
        
        message = "Le mode conversation est désactivé."
    else:
        config.update_last_activity()
        config.albert_with_history = True
        
        # Création d'un nouveau contexte
        await context_manager.create_context(
            ep.room.room_id,
            ep.sender,
            config=config.dict()
        )
        
        message = "Le mode conversation est activé."
        
    await matrix_client.send_markdown_message(
        ep.room.room_id,
        message,
        msgtype="m.notice"
    )


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="debug",
    help=AlbertMsg.shorts["debug"],
    for_geek=True,
)
@only_allowed_user
async def albert_debug(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    debug_message = AlbertMsg.debug(config)
    await matrix_client.send_markdown_message(ep.room.room_id, debug_message, msgtype="m.notice")


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="model",
    aliases=["models"],
    help=AlbertMsg.shorts["model"],
    for_geek=True,
)
@only_allowed_user
async def albert_model(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    await matrix_client.room_typing(ep.room.room_id)
    command = ep.get_command()
    # Get all available models
    all_models = list(get_available_models(config))
    models_list = "\n\n- " + "\n- ".join(
        map(lambda x: x + (" *" if x == config.albert_model else ""), all_models)
    )
    if len(command) <= 1:
        message = "La commande !model nécessite de donner un modèle parmi :" + models_list
        message += "\n\nExemple: `!model " + all_models[-1] + "`"
    else:
        model = command[1]
        if model not in all_models:
            message = "La commande !model nécessite de donner un modèle parmi :" + models_list
            message += "\n\nExemple: `!model " + all_models[-1] + "`"
        else:
            previous_model = config.albert_model
            config.albert_model = model
            message = f"Le modèle a été modifié : {previous_model} -> {model}"
    await matrix_client.send_markdown_message(ep.room.room_id, message, msgtype="m.notice")


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="mode",
    aliases=["modes"],
    help=AlbertMsg.shorts["mode"],
    for_geek=True,
)
@only_allowed_user
async def albert_mode(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    command = ep.get_command()
    # Get all available mode for the current model
    all_modes = get_available_modes(config)
    mode_list = "\n\n- " + "\n- ".join(
        map(lambda x: x + (" *" if x == config.albert_mode else ""), all_modes)
    )
    if len(command) <= 1:
        message = "La commande !mode nécessite de donner un mode parmi :" + mode_list
        message += "\n\nExemple: `!mode " + all_modes[-1] + "`"
    else:
        mode = command[1]
        if mode not in all_modes:
            message = "La commande !mode nécessite de donner un mode parmi :" + mode_list
            message += "\n\nExemple: `!mode " + all_modes[-1] + "`"
        else:
            old_mode = config.albert_mode
            config.albert_mode = mode
            message = f"Le mode a été modifié : {old_mode} -> {mode}"
        
    await matrix_client.send_markdown_message(ep.room.room_id, message, msgtype="m.notice")

    if mode == "norag":
        message = AlbertMsg.flush_start
        await matrix_client.send_markdown_message(ep.room.room_id, message, msgtype="m.notice")  
        await matrix_client.room_typing(ep.room.room_id)
        flush_collections_with_name(config, ep.room.room_id)
        config.albert_collections_by_id = {}
        message = AlbertMsg.flush_end
        await matrix_client.send_markdown_message(ep.room.room_id, message, msgtype="m.notice")  


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="sources",
    help=AlbertMsg.shorts["sources"],
)
@only_allowed_user
async def albert_sources(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]

    try:
        if config.last_rag_chunks:
            await matrix_client.room_typing(ep.room.room_id)
            sources_msg = ""
            for chunk in config.last_rag_chunks[:max(30, len(config.last_rag_chunks))]:
                sources_msg += f'________________________________________\n'
                sources_msg += f'####{chunk["metadata"]["document_name"]}\n'
                sources_msg += f'{chunk["content"]}\n'
        else:
            sources_msg = "Aucune source trouvée, veuillez me poser une question d'abord."
    except Exception:
        traceback.print_exc()
        await matrix_client.send_markdown_message(ep.room.room_id, AlbertMsg.failed, msgtype="m.notice")  # fmt: off
        return

    await matrix_client.send_markdown_message(ep.room.room_id, sources_msg)


@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="collections",
    help=AlbertMsg.shorts["collections"],
)
@only_allowed_user
async def albert_collection(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]
    await matrix_client.room_typing(ep.room.room_id)
    command = ep.get_command()
    if len(command) <= 1:
        message = f"La commande !collections nécessite de donner list/use/unuse/info puis éventuellement <nom_de_collection>/{config.albert_all_public_command} :"
        message += "\n\nExemple: `!collections use decisions-adlc`"
    elif command[1] != 'list' and len(command) <= 2:
        if command[1] not in ['use', 'unuse']:
            message = f"La commande !collections {command[1]} n'est pas reconnue, seul list/use/unuse sont autorisés"
        else:
            message = f"La commande !collections {command[1]} nécessite de donner en plus COLLECTION_NAME/{config.albert_all_public_command} :"
            message += "\n\nExemple: `!collections use decisions-adlc`"
    else:
        method = command[1]
        if method == 'list':
            collections = config.albert_collections_by_id.values()
            collection_display_names = [c['name'] if c['name'] != ep.room.room_id else config.albert_my_private_collection_name for c in collections]
            collection_ids = [c['id'] for c in collections]
            collection_infos = '\n - ' + '\n - '.join([f"{display_name}" for display_name, collection_id in zip(collection_display_names, collection_ids)])
            if not collections:
                message = "Vous n'avez pas de collections enregistrées pour le moment qui pourraient m'aider à répondre à vos questions."
            else:
                message = (
                    "Les collections :\n"
                    f"{collection_infos}\n\n"
                    "sont prises en compte pour m'aider à répondre à vos questions."
                )
            collections = get_all_public_collections(config)
            message += "\n\nNotez que les collections publiques à votre disposition sont:\n"
            message += '\n - ' + '\n - '.join([f"{c['name']}" for c in collections])
            message += f"\n\nVous pouvez toutes les ajouter d'un coup en utilisant la commande `!collections use {config.albert_all_public_command}`"
        elif method == 'info':
            collection_name = command[2] if command[2] != config.albert_my_private_collection_name else ep.room.room_id
            collection = get_or_not_collection_with_name(config, collection_name)
            if not collection:
                message = f"La collection {collection_name} n'existe pas."
            else:
                document_infos = [f"{d['name']} ({d['id']})" for d in get_documents(config, collection['id'])]
                if not document_infos:
                    message = (
                        f"Collection '{command[2]}' ({collection['id']}) : \n\n"
                        f"Aucun document n'est présent dans cette collection ({collection['id']})."
                    )
                else:
                    document_infos_message = '\n - ' + '\n - '.join(document_infos)
                    message = (
                        f"Collection '{command[2]}' ({collection['id']}) : \n\n"
                        "Voici les documents actuellement présents dans la collection : \n\n"
                        f"{document_infos_message}"
                        "\n\n"
                    )
        elif method == 'use':
            if command[2] == config.albert_all_public_command:
                collections = get_all_public_collections(config)
            else:
                collection = get_or_not_collection_with_name(config, command[2])
                if not collection:
                    message = f"La collection {command[2]} n'existe pas."
                    collections = []
                else:
                    collections = [collection]
            if collections:
                collection_names = ','.join([c['name'] for c in collections])
                for collection in collections:
                    config.albert_collections_by_id[collection["id"]] = collection
                collection_infos = '\n - ' + '\n - '.join([f"{c['name']}" for c in config.albert_collections_by_id.values()])
                message = (
                    f"Les collections {collection_names} sont ajoutées à vos collections.\n\n" if len(collections) > 1 else f"La collection {command[2]} est ajoutée à vos collections.\n\n"
                    "Maintenant, les collections :\n"
                    f"{collection_infos}\n\n"
                    "sont disponibles pour m'aider à répondre à vos questions."
                )
        else:
            collections = config.albert_collections_by_id.values()
            collection_names = ','.join([c['name'] for c in collections])
            config.albert_collections_by_id = {}
            if not collections:
                message = "Il n'y avait pas de collections à retirer."
            else:
                message = f"Les collections {collection_names} sont retirées de vos collections."
    await matrix_client.send_markdown_message(ep.room.room_id, message, msgtype="m.notice")
    
@register_feature(
    group="albert",
    onEvent=RoomEncryptedFile,
    help=None
)
@only_allowed_user
async def albert_document(ep: EventParser, matrix_client: MatrixClient):
    config = user_configs[ep.sender]

    try:
        await matrix_client.room_typing(ep.room.room_id)
        if ep.event.mimetype in ['application/json', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
            config.update_last_activity()       
            config.albert_mode = "rag"
            collection = get_or_create_collection_with_name(config, ep.room.room_id)
            config.albert_collections_by_id[collection['id']] = collection
            file = await get_decrypted_file(ep)
            upload_file(config, file, collection['id'])
            private_document_infos = [d['name'] for d in get_documents(config, collection['id'])]
            private_document_infos_message = '\n - ' + '\n - '.join(private_document_infos)
            response = (
                "Votre document : \n\n"
                f"\"{file.name}\"\n\n"
                "a été chargé dans votre collection privée.\n\n"
                "Voici les documents actuellement présents dans votre collection privée : \n\n"
                f"{private_document_infos_message}"
                "\n\n"
                "Je tiendrai compte de tous ces documents pour répondre. \n\n"
                "Vous pouvez taper \"!mode norag\" pour vider votre collection privée de tous ces documents."
            )
        else:
            response = (
                f"J'ai détecté que vous avez téléchargé un fichier {ep.event.mimetype}. "
                "Ce fichier n'est pris en charge par Albert. "
                "Veuillez téléverser un fichier PDF, DOCX ou JSON."
            )
        await matrix_client.send_markdown_message(ep.room.room_id, response, msgtype="m.notice")
    
    except Exception as albert_err:
        logger.error(f"{albert_err}")
        traceback.print_exc()
        await matrix_client.send_markdown_message(ep.room.room_id, AlbertMsg.failed, msgtype="m.notice")
        if config.errors_room_id:
            try:
                await matrix_client.send_markdown_message(config.errors_room_id, AlbertMsg.error_debug(albert_err, config))
            except:
                print("Failed to find error room ?!")

@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    help="Traite une requête utilisateur"
)
async def process_request(ep: EventParser, matrix_client: MatrixClient):
    """Traite une requête utilisateur standard (non-commande)"""
    # Ajouter un log pour voir si la fonction est appelée
    logger.info(f"process_request appelé pour le message: '{ep.event.body}'")
    
    # Ignorer les commandes et les messages du bot
    if ep.is_command(COMMAND_PREFIX) or ep.sender == matrix_client.user_id:
        logger.info(f"Message ignoré: est_commande={ep.is_command(COMMAND_PREFIX)}, envoyé_par_bot={ep.sender == matrix_client.user_id}")
        return
    
    # Vérifier si l'utilisateur est autorisé
    from iam import TchapIam
    tiam = TchapIam(Config())
    is_allowed = await tiam.is_allowed_user(ep.sender)
    if not is_allowed:
        logger.warning(f"Utilisateur {ep.sender} non autorisé à utiliser process_request")
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "🔒 Je suis désolé, mais vous n'avez pas l'autorisation requise pour interagir avec moi.",
            msgtype="m.notice"
        )
        return
        
    logger.info(f"Utilisateur {ep.sender} autorisé, traitement de la requête...")
    config = user_configs[ep.sender]
    
    try:
        # Informer l'utilisateur que sa requête est en cours de traitement
        typing_message = await matrix_client.send_markdown_message(
            ep.room.room_id,
            "⌛ Traitement de votre requête en cours...",
            msgtype="m.notice"
        )
        
        # Récupérer ou créer le contexte du salon
        try:
            room_context = await context_manager.get_or_create_room_context(
                room_id=ep.room.room_id,
                room_name=ep.room.name,
                is_direct=room_is_direct_message(ep.room)
            )
            
            # Ajouter/mettre à jour le participant
            await context_manager.add_room_participant(ep.room.room_id, ep.sender)
            await context_manager.update_room_activity(ep.room.room_id, ep.sender)
        except Exception as room_ctx_err:
            logger.warning(f"Erreur lors de la gestion du contexte de salon: {str(room_ctx_err)}")
            # Créer un objet de contexte de salon minimal pour permettre de continuer
            room_context = RoomContext(
                room_id=ep.room.room_id,
                name=ep.room.name or "Salon inconnu",
                is_direct=room_is_direct_message(ep.room)
            )
        
        # Création ou récupération du contexte de session
        session_id = f"{ep.room.room_id}_{ep.sender}_{datetime.now().strftime('%Y%m%d')}"
        try:
            session_context = await context_manager.get_context(session_id, ContextType.SESSION)
            
            if not session_context:
                # Créer le contexte de session
                session_context = await context_manager.create_context(
                    session_id,
                    ContextType.SESSION,
                    {
                        "session_id": session_id,
                        "room_id": ep.room.room_id,
                        "user_id": ep.sender,
                        "conversation_state": {},
                        "history": []
                    }
                )
        except Exception as session_ctx_err:
            logger.warning(f"Erreur lors de la gestion du contexte de session: {str(session_ctx_err)}")
            # Créer un objet de contexte de session minimal pour permettre de continuer
            session_context = SessionContext(
                session_id=session_id,
                room_id=ep.room.room_id,
                user_id=ep.sender
            )
            
        # Mise à jour du contexte de requête
        try:
            request_context = await context_manager.create_context(
                f"{session_id}_req_{datetime.now().timestamp()}",
                ContextType.REQUEST,
                {
                    "query": ep.event.body,
                    "raw_input": ep.event.source,
                    "event_timestamp": datetime.now()
                }
            )
        except Exception as req_ctx_err:
            logger.warning(f"Erreur lors de la création du contexte de requête: {str(req_ctx_err)}")
            # Non critique, on peut continuer sans contexte de requête

        # Récupération des messages précédents pour le contexte
        message_events = []
        is_reply_to = isa_reply_to(ep.event)
        
        try:
            if is_reply_to:
                message_events = await get_thread_messages(
                    config, ep, max_rewind=config.albert_max_rewind
                )
            else:
                config.albert_history_lookup += 1
                message_events = await get_previous_messages(
                    config,
                    ep,
                    history_lookup=config.albert_history_lookup,
                    max_rewind=config.albert_max_rewind,
                )
        except Exception as msg_hist_err:
            logger.warning(f"Erreur lors de la récupération de l'historique des messages: {str(msg_hist_err)}")
            # Continuer même sans historique des messages
            
        # Mise à jour de l'historique dans le contexte de session
        try:
            for event in message_events:
                event_timestamp = datetime.fromtimestamp(event.server_timestamp / 1000)
                session_context.add_message(
                    role="user" if event.source["sender"] == ep.sender else "assistant",
                    content=get_cleanup_body(event),
                    timestamp=event_timestamp
                )
                
            # Sauvegarder l'historique mis à jour
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict(),
                immediate_save=True
            )
        except Exception as hist_update_err:
            logger.warning(f"Erreur lors de la mise à jour de l'historique de session: {str(hist_update_err)}")
            # Non critique, on continue même en cas d'erreur

        # Générer une réponse
        response = ""
        try:
            # Créer et initialiser l'action RAG standard
            rag_action = StandardMessageRagAction(config)
            await rag_action.initialize()

            # Récupérer les chunks avec contexte en mode rapide
            chunks = await rag_action.retrieve_relevant_chunks(
                query=ep.event.body,
                session_context=session_context,
                room_context=room_context,
                search_mode="fast"  # Utiliser le mode rapide
            )

            if chunks:
                # Générer la réponse avec contexte
                response = await rag_action.generate_response(
                    query=ep.event.body,
                    chunks=chunks,
                    session_context=session_context
                )
            else:
                # Fallback en mode norag
                messages = [
                    {
                        "role": "system",
                        "content": "Vous êtes Colaig, l'assistant de l'État français."
                    },
                    {
                        "role": "user",
                        "content": ep.event.body
                    }
                ]
                response = await generate(config, messages)
        except Exception as generate_err:
            logger.error(f"Erreur lors de la génération de la réponse: {str(generate_err)}")
            # Réponse de secours en cas d'échec
            response = "Je suis désolé, je ne peux pas traiter votre demande pour le moment. Le service est temporairement indisponible ou surchargé. Veuillez réessayer ultérieurement."

        try:
            # Mettre à jour l'historique de session avant l'envoi
            session_context.add_message("user", ep.event.body)
            session_context.add_message("assistant", response)
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
        except Exception as update_err:
            logger.warning(f"Erreur lors de la mise à jour du contexte de session finale: {str(update_err)}")
            # Non critique pour l'envoi de la réponse

        try:
            # Supprimer le message de chargement
            if typing_message:
                await matrix_client.redact_message(
                    ep.room.room_id,
                    typing_message
                )
        except Exception as redact_err:
            logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
            # Non critique, continuer malgré l'erreur

        # Envoyer la réponse
        reply_to = ep.event.event_id if is_reply_to else None
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            response,
            reply_to=reply_to
        )

        try:
            # Mise à jour finale de l'activité de session
            session_context.last_activity = datetime.now()
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict(),
                immediate_save=True
            )
        except Exception as final_update_err:
            logger.warning(f"Erreur lors de la mise à jour finale de l'activité de session: {str(final_update_err)}")
            # Non critique
        
    except Exception as e:
        logger.error(f"Erreur traitement requête: {str(e)}")
        # Message d'erreur en cas d'échec complet
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "Je suis désolé, une erreur est survenue lors du traitement de votre demande. Veuillez réessayer ou contacter l'administrateur si le problème persiste.",
            msgtype="m.notice"
        )

@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="docquery",
    help="Interroge Albert en utilisant la documentation comme contexte",
)
@only_allowed_user
# COMMENTÉ: Cette fonction a été migrée vers un module dédié
# async def doc_query_command(ep: EventParser, matrix_client: MatrixClient):
#     """Gère la commande !docquery"""
#     try:
#         # Extraire la requête du message
#         query = ep.event.body.split("!docquery", 1)[1].strip()
#         if not query:
#             await matrix_client.send_markdown_message(
#                 ep.room.room_id,
#                 "❌ Veuillez spécifier une requête après !docquery",
#                 msgtype="m.notice"
#             )
#             return
# 
#         # Informer l'utilisateur que la requête est en cours de traitement
#         loading_message = await matrix_client.send_markdown_message(
#             ep.room.room_id,
#             "🔄 Traitement de votre requête en cours...",
#             msgtype="m.notice"
#         )
# 
#         # Utiliser la configuration de l'utilisateur
#         config = user_configs[ep.sender]
#         
#         # Récupérer le gestionnaire de contexte
#         context_manager = get_context_manager(config)
#         
#         # Récupérer ou créer le contexte de session
#         session_id = f"{ep.room.room_id}_{ep.sender}_{config.session_id}"
#         session_context = await context_manager.get_context(session_id, ContextType.SESSION)
#         
#         if not session_context:
#             # Créer un nouveau contexte de session
#             session_context = await context_manager.create_context(
#                 session_id,
#                 ContextType.SESSION,
#                 {
#                     "session_id": config.session_id,
#                     "room_id": ep.room.room_id,
#                     "user_id": ep.sender,
#                     "history": [],
#                     "conversation_state": {
#                         "last_command": "docquery",
#                         "current_query": query
#                     }
#                 }
#             )
#         else:
#             # Mettre à jour l'état de la conversation
#             session_context.conversation_state.update({
#                 "last_command": "docquery",
#                 "current_query": query
#             })
# 
#         # Récupérer ou créer le contexte de salon
#         room_context = await context_manager.get_or_create_room_context(
#             ep.room.room_id,
#             ep.room.name,
#             is_direct=True  # Supposer un salon direct, ajuster selon la logique
#         )
#         
#         # Mettre à jour l'activité du participant
#         await context_manager.update_room_activity(ep.room.room_id, ep.sender)
#         
#         # Initialiser les services
#         try:
#             # Utiliser un contexte async pour garantir le nettoyage
#             async with IndexService(config=config) as index_service:
#                 try:
#                     # Initialiser uniquement l'index documentaire
#                     await index_service.initialize(
#                         init_document_index=True,
#                         init_behavior_manager=False
#                     )
#                     
#                     # Effectuer la recherche en mode précis
#                     chunks = await index_service.search(
#                         query=query,
#                         index_type="document",
#                         limit=5,
#                         search_mode="precise"
#                     )
# 
#                     if not chunks:
#                         # Aucun document trouvé, essayer avec une recherche moins précise
#                         logger.info("Aucun document trouvé en mode précis, essai en mode rapide")
#                         chunks = await index_service.search(
#                             query=query,
#                             index_type="document",
#                             limit=5,
#                             search_mode="fast"
#                         )
#                         
#                     if not chunks:
#                         # Supprimer le message de chargement
#                         try:
#                             await matrix_client.redact_message(
#                                 ep.room.room_id,
#                                 loading_message
#                             )
#                         except Exception as redact_err:
#                             logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                             
#                         # Enregistrer la requête sans résultat dans l'historique
#                         session_context.add_message("user", f"!docquery {query}")
#                         session_context.add_message("assistant", "Aucun document pertinent trouvé.")
#                         await context_manager.update_context(
#                             session_id,
#                             ContextType.SESSION,
#                             session_context.to_dict(),
#                             immediate_save=True
#                         )
#                         
#                         await matrix_client.send_markdown_message(
#                             ep.room.room_id,
#                             "❌ Aucun document pertinent trouvé pour votre requête. Essayez de reformuler ou utilisez des mots-clés différents.",
#                             msgtype="m.notice"
#                         )
#                         return
# 
#                     # Préparer le prompt pour la génération
#                     # Vérifier s'il y a un historique de conversation à inclure
#                     conversation_history = []
#                     if session_context.history and len(session_context.history) > 0:
#                         # Extraire les derniers messages (jusqu'à 5 échanges) pour le contexte
#                         recent_history = session_context.history[-10:]  # 5 échanges = 10 messages max
#                         for msg in recent_history:
#                             if msg["role"] == "user":
#                                 conversation_history.append(f"Question précédente: {msg['content']}")
#                             elif msg["role"] == "assistant":
#                                 conversation_history.append(f"Réponse précédente: {msg['content']}")
#                     
#                     history_text = "\n".join(conversation_history) if conversation_history else ""
#                     
#                     # Ajouter l'historique de conversation au prompt si disponible
#                     prompt = f"""En tant qu'assistant, répondez à la question suivante en vous basant uniquement sur les documents fournis.
# {"Historique de la conversation:" + chr(10) + history_text if history_text else ""}
# Question actuelle: {query}
# 
# Documents pertinents:
# """
#                     # Ajouter les chunks de manière sécurisée
#                     for chunk in chunks:
#                         prompt += chunk.get('content', '') + chr(10)
#                     
#                     prompt += "\nRéponse:"
#                     
#                     # Générer la réponse avec le client Albert
#                     response_text = ""
#                     try:
#                         albert_client = AlbertApiClient(
#                             base_url=config.albert_api_url,
#                             api_key=config.albert_api_token
#                         )
# 
#                         messages = [
#                             {
#                                 "role": "system",
#                                 "content": "Vous êtes un assistant expert qui répond aux questions en se basant uniquement sur les documents fournis. Répondez de manière directe et concise. Si la question fait référence à des questions précédentes, prenez en compte ce contexte."
#                             }
#                         ]
#                         
#                         # Ajouter les messages de l'historique récent au format ChatML
#                         if conversation_history:
#                             for i, msg in enumerate(recent_history):
#                                 role = msg["role"]
#                                 if role in ["user", "assistant"]:
#                                     messages.append({
#                                         "role": role,
#                                         "content": msg["content"]
#                                     })
#                         
#                         # Ajouter la requête actuelle
#                         messages.append({
#                             "role": "user",
#                             "content": prompt
#                         })
# 
#                         response = await albert_client.generate(
#                             model=config.albert_model,
#                             messages=messages
#                         )
# 
#                         # Formater la réponse
#                         response_text = f"🤖 {response.strip()}"
#                     except Exception as albert_err:
#                         logger.error(f"Erreur API Albert: {str(albert_err)}")
#                         
#                         # Essayer avec l'API standard generate
#                         try:
#                             logger.info("Tentative avec l'API standard generate")
#                             response = await generate(config, messages)
#                             response_text = f"🤖 {response.strip()}"
#                         except Exception as gen_err:
#                             logger.error(f"Erreur avec generate: {str(gen_err)}")
#                             # Réponse de secours en cas d'échec total de génération
#                             response_text = "❌ Je n'ai pas pu générer une réponse avec l'API. Voici les documents pertinents trouvés:\n\n"
#                             for i, chunk in enumerate(chunks[:3], 1):
#                                 response_text += f"**Document {i}:**\n{chunk.get('content', '').strip()[:500]}...\n\n"
# 
#                     # Ajouter les sources si configuré
#                     try:
#                         if config.colaig_show_sources:
#                             sources = []
#                             for chunk in chunks:
#                                 metadata = chunk.get("metadata", {})
#                                 source_parts = [f"📄 {metadata.get('document_name', 'Document inconnu')}"]
#                                 if metadata.get("section_title"):
#                                     source_parts.append(f"   Section: {metadata['section_title']}")
#                                 sources.append("\n".join(source_parts))
#                             
#                             if sources:
#                                 response_text += "\n\n💡 Sources :\n" + "\n".join(sources)
#                     except Exception as sources_err:
#                         logger.warning(f"Erreur lors de l'ajout des sources: {str(sources_err)}")
#                         # Non critique, continuer sans les sources
#                     
#                     # Supprimer le message de chargement
#                     try:
#                         await matrix_client.redact_message(
#                             ep.room.room_id,
#                             loading_message
#                         )
#                     except Exception as redact_err:
#                         logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
# 
#                     # Enregistrer l'échange dans le contexte de session
#                     session_context.add_message("user", f"!docquery {query}")
#                     session_context.add_message("assistant", response_text)
#                     
#                     # Mettre à jour le contexte de session
#                     await context_manager.update_context(
#                         session_id,
#                         ContextType.SESSION,
#                         session_context.to_dict()
#                     )
# 
#                     # Envoyer la réponse
#                     await matrix_client.send_markdown_message(
#                         ep.room.room_id,
#                         response_text,
#                         msgtype="m.text"
#                     )
# 
#                 except Exception as search_err:
#                     logger.error(f"Erreur lors de la recherche: {str(search_err)}")
#                     error_msg = str(search_err)
#                     
#                     # Supprimer le message de chargement
#                     try:
#                         await matrix_client.redact_message(
#                             ep.room.room_id,
#                             loading_message
#                         )
#                     except Exception as redact_err:
#                         logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                         
#                     if "Index obsolète" in error_msg or "Dimension de l'index incompatible" in error_msg:
#                         await matrix_client.send_markdown_message(
#                             ep.room.room_id,
#                             "❌ L'index est corrompu ou obsolète. Veuillez utiliser la commande `!index rebuild` pour le reconstruire.",
#                             msgtype="m.notice"
#                         )
#                     else:
#                         await matrix_client.send_markdown_message(
#                             ep.room.room_id,
#                             f"❌ Une erreur est survenue lors de la recherche: {error_msg}",
#                             msgtype="m.notice"
#                         )
#         except Exception as init_err:
#             logger.error(f"Erreur lors de l'initialisation des services: {str(init_err)}")
#             
#             # Supprimer le message de chargement
#             try:
#                 await matrix_client.redact_message(
#                     ep.room.room_id,
#                     loading_message
#                 )
#             except Exception as redact_err:
#                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                 
#             await matrix_client.send_markdown_message(
#                 ep.room.room_id,
#                 f"❌ Erreur lors de l'initialisation des services nécessaires: {str(init_err)}",
#                 msgtype="m.notice"
#             )
#             
#     except Exception as e:
#         logger.error(f"Erreur lors de l'exécution de docquery: {str(e)}")
#         await matrix_client.send_markdown_message(
#             ep.room.room_id,
#             f"❌ Une erreur est survenue lors du traitement de votre requête: {str(e)}",
#             msgtype="m.notice"
#         )

@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="index",
    help="Gestion de l'index FAISS (status/verify/rebuild/clean)",
)
@only_allowed_user
# COMMENTÉ: Cette fonction a été migrée vers un module dédié
# async def faiss_index_command(ep: EventParser, matrix_client: MatrixClient):
#     """Commande pour gérer l'index FAISS"""
#     # Récupérer l'action demandée
#     command = ep.get_command()
#     action = command[1] if len(command) > 1 else "status"
#     
#     if action not in ["status", "verify", "rebuild", "clean"]:
#         await matrix_client.send_markdown_message(
#             ep.room.room_id,
#             "❌ Action invalide. Actions disponibles: status, verify, rebuild, clean",
#             msgtype="m.notice"
#         )
#         return
#     
#     try:
#         # Informer l'utilisateur que l'action est en cours
#         loading_message = await matrix_client.send_markdown_message(
#             ep.room.room_id,
#             f"🔄 Action '{action}' en cours...",
#             msgtype="m.notice"
#         )
#         
#         # Utiliser la configuration de l'utilisateur
#         config = user_configs[ep.sender]
#         
#         # Récupérer le gestionnaire de contexte
#         context_manager = get_context_manager(config)
#         
#         # Récupérer ou créer le contexte de session
#         session_id = f"{ep.room.room_id}_{ep.sender}_{config.session_id}"
#         session_context = await context_manager.get_context(session_id, ContextType.SESSION)
#         
#         if not session_context:
#             # Créer un nouveau contexte de session
#             session_context = await context_manager.create_context(
#                 session_id,
#                 ContextType.SESSION,
#                 {
#                     "session_id": config.session_id,
#                     "room_id": ep.room.room_id,
#                     "user_id": ep.sender,
#                     "history": [],
#                     "conversation_state": {
#                         "last_command": "index",
#                         "current_action": action
#                     }
#                 }
#             )
#         else:
#             # Mettre à jour l'état de la conversation
#             session_context.conversation_state.update({
#                 "last_command": "index",
#                 "current_action": action
#             })
#             await context_manager.update_context(
#                 session_id,
#                 ContextType.SESSION,
#                 session_context.to_dict()
#             )
#             
#         # Mettre à jour l'activité du participant
#         await context_manager.update_room_activity(ep.room.room_id, ep.sender)
#         
#         # Initialiser le service d'indexation
#         try:
#             async with IndexService(config=config) as index_service:
#                 try:
#                     # Initialiser uniquement l'index documentaire
#                     await index_service.initialize(
#                         init_document_index=True,
#                         init_behavior_manager=False
#                     )
#                     
#                     if action == "status":
#                         try:
#                             # Obtenir le statut de l'index
#                             status = await index_service.get_status()
#                             
#                             # Formater le message de statut
#                             status_msg = [
#                                 "📊 **Statut de l'index**",
#                                 f"- Documents indexés: {status.get('total_documents', 0)}",
#                                 f"- Chunks total: {status.get('total_chunks', 0)}",
#                                 f"- Dimension des embeddings: {status.get('embedding_dimension', 'N/A')}",
#                                 f"- Modèle d'embedding: {status.get('embedding_model', 'N/A')}",
#                             ]
#                             
#                             # Ajouter les infos de cache si disponibles
#                             if 'embedding_cache' in status:
#                                 cache_info = status['embedding_cache']
#                                 status_msg.append(f"- Cache des embeddings: {cache_info.get('size', 0)}/{cache_info.get('max_size', 'N/A')}")
#                             
#                             # Ajouter l'information de fraîcheur
#                             is_fresh = status.get('is_fresh', False)
#                             status_msg.append(f"- Index à jour: {'✅' if is_fresh else '❌'}")
#                             
#                             # Ajouter la date de dernière mise à jour si disponible
#                             if status.get('last_update'):
#                                 status_msg.append(f"- Dernière mise à jour: {status['last_update'].strftime('%Y-%m-%d %H:%M:%S')}")
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                             
#                             response = "\n".join(status_msg)
#                             
#                             # Enregistrer dans l'historique de session
#                             session_context.add_message("user", f"!index {action}")
#                             session_context.add_message("assistant", response)
#                             await context_manager.update_context(
#                                 session_id,
#                                 ContextType.SESSION,
#                                 session_context.to_dict()
#                             )
#                                 
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 response,
#                                 msgtype="m.notice"
#                             )
#                         except Exception as status_err:
#                             logger.error(f"Erreur lors de la récupération du statut: {str(status_err)}")
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                             # Tenter de récupérer des informations minimales
#                             minimal_status = "⚠️ **Statut partiel de l'index**\n\n"
#                             try:
#                                 if hasattr(index_service, 'document_index') and index_service.document_index:
#                                     if hasattr(index_service.document_index, 'faiss_index') and index_service.document_index.faiss_index:
#                                         minimal_status += f"- Documents indexés: {len(index_service.document_index.document_map) if hasattr(index_service.document_index, 'document_map') else 'N/A'}\n"
#                                         minimal_status += f"- Index existant: {'✅' if index_service.document_index.faiss_index.index else '❌'}\n"
#                                 minimal_status += "\n⚠️ Certaines informations n'ont pas pu être récupérées."
#                                 
#                                 # Enregistrer dans l'historique de session
#                                 session_context.add_message("user", f"!index {action}")
#                                 session_context.add_message("assistant", minimal_status)
#                                 await context_manager.update_context(
#                                     session_id,
#                                     ContextType.SESSION,
#                                     session_context.to_dict()
#                                 )
#                                 
#                                 await matrix_client.send_markdown_message(
#                                     ep.room.room_id,
#                                     minimal_status,
#                                     msgtype="m.notice"
#                                 )
#                             except Exception:
#                                 await matrix_client.send_markdown_message(
#                                     ep.room.room_id,
#                                     f"❌ Impossible de récupérer le statut de l'index: {str(status_err)}",
#                                     msgtype="m.notice"
#                                 )
#                         
#                     elif action == "verify":
#                         try:
#                             # Vérifier la fraîcheur de l'index
#                             is_fresh = await index_service.verify()
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                             
#                             if is_fresh:
#                                 response = "✅ L'index est à jour!"
#                             else:
#                                 response = "⚠️ L'index n'est pas à jour. Utilisez `!index rebuild` pour le reconstruire."
#                                 
#                             # Enregistrer dans l'historique de session
#                             session_context.add_message("user", f"!index {action}")
#                             session_context.add_message("assistant", response)
#                             await context_manager.update_context(
#                                 session_id, 
#                                 ContextType.SESSION,
#                                 session_context.to_dict()
#                             )
#                                 
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 response,
#                                 msgtype="m.notice"
#                             )
#                         except Exception as verify_err:
#                             logger.error(f"Erreur lors de la vérification: {str(verify_err)}")
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 f"❌ Erreur lors de la vérification de l'index: {str(verify_err)}",
#                                 msgtype="m.notice"
#                             )
#                             
#                     elif action == "rebuild":
#                         try:
#                             # Mettre à jour le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                                 
#                                 loading_message = await matrix_client.send_markdown_message(
#                                     ep.room.room_id,
#                                     "🔄 Début de la reconstruction de l'index...\nCette opération peut prendre plusieurs minutes.",
#                                     msgtype="m.notice"
#                                 )
#                             except Exception as update_msg_err:
#                                 logger.warning(f"Erreur lors de la mise à jour du message de chargement: {str(update_msg_err)}")
#                                 # Non critique, continuer
#                             
#                             # Vérifier d'abord l'existence du répertoire documents
#                             try:
#                                 await index_service.webdav_service.create_directory("documents")
#                             except Exception as mkdir_err:
#                                 logger.warning(f"Erreur lors de la vérification/création du répertoire documents: {str(mkdir_err)}")
#                                 # Non critique, le répertoire existe peut-être déjà
#                             
#                             # Reconstruire l'index
#                             try:
#                                 await index_service.rebuild()
#                                 
#                                 # Supprimer le message de chargement
#                                 try:
#                                     await matrix_client.redact_message(
#                                         ep.room.room_id,
#                                         loading_message
#                                     )
#                                 except Exception as redact_err:
#                                     logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                     
#                                 response = "✅ Index reconstruit avec succès!"
#                                 
#                                 # Enregistrer dans l'historique de session
#                                 session_context.add_message("user", f"!index {action}")
#                                 session_context.add_message("assistant", response)
#                                 await context_manager.update_context(
#                                     session_id,
#                                     ContextType.SESSION,
#                                     session_context.to_dict()
#                                 )
#                                 
#                                 await matrix_client.send_markdown_message(
#                                     ep.room.room_id,
#                                     response,
#                                     msgtype="m.notice"
#                                 )
#                             except Exception as rebuild_err:
#                                 logger.error(f"Erreur lors de la reconstruction: {str(rebuild_err)}")
#                                 
#                                 # Supprimer le message de chargement
#                                 try:
#                                     await matrix_client.redact_message(
#                                         ep.room.room_id,
#                                         loading_message
#                                     )
#                                 except Exception as redact_err:
#                                     logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                                 error_msg = f"❌ Erreur lors de la reconstruction de l'index: {str(rebuild_err)}"
#                                 
#                                 # Enregistrer dans l'historique de session
#                                 session_context.add_message("user", f"!index {action}")
#                                 session_context.add_message("assistant", error_msg)
#                                 await context_manager.update_context(
#                                     session_id,
#                                     ContextType.SESSION,
#                                     session_context.to_dict()
#                                 )
#                                 
#                                 await matrix_client.send_markdown_message(
#                                     ep.room.room_id,
#                                     error_msg,
#                                     msgtype="m.notice"
#                                 )
#                         except Exception as rebuild_err:
#                             logger.error(f"Erreur lors de la reconstruction: {str(rebuild_err)}")
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                             error_msg = f"❌ Erreur lors de la reconstruction de l'index: {str(rebuild_err)}"
#                             
#                             # Enregistrer dans l'historique de session
#                             session_context.add_message("user", f"!index {action}")
#                             session_context.add_message("assistant", error_msg)
#                             await context_manager.update_context(
#                                 session_id,
#                                 ContextType.SESSION,
#                                 session_context.to_dict()
#                             )
#                             
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 error_msg,
#                                 msgtype="m.notice"
#                             )
#                             
#                     elif action == "clean":
#                         try:
#                             # Nettoyer l'index
#                             await index_service.clean()
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                             response = "✅ Index et cache nettoyés avec succès!"
#                             
#                             # Enregistrer dans l'historique de session
#                             session_context.add_message("user", f"!index {action}")
#                             session_context.add_message("assistant", response)
#                             await context_manager.update_context(
#                                 session_id,
#                                 ContextType.SESSION,
#                                 session_context.to_dict()
#                             )
#                             
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 response,
#                                 msgtype="m.notice"
#                             )
#                         except Exception as clean_err:
#                             logger.error(f"Erreur lors du nettoyage: {str(clean_err)}")
#                             
#                             # Supprimer le message de chargement
#                             try:
#                                 await matrix_client.redact_message(
#                                     ep.room.room_id,
#                                     loading_message
#                                 )
#                             except Exception as redact_err:
#                                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                                 
#                             error_msg = f"❌ Erreur lors du nettoyage de l'index: {str(clean_err)}"
#                             
#                             # Enregistrer dans l'historique de session
#                             session_context.add_message("user", f"!index {action}")
#                             session_context.add_message("assistant", error_msg)
#                             await context_manager.update_context(
#                                 session_id,
#                                 ContextType.SESSION,
#                                 session_context.to_dict()
#                             )
#                             
#                             await matrix_client.send_markdown_message(
#                                 ep.room.room_id,
#                                 error_msg,
#                                 msgtype="m.notice"
#                             )
#                 except Exception as index_err:
#                     logger.error(f"Erreur lors de l'utilisation du service d'index: {str(index_err)}")
#                     
#                     # Supprimer le message de chargement
#                     try:
#                         await matrix_client.redact_message(
#                             ep.room.room_id,
#                             loading_message
#                         )
#                     except Exception as redact_err:
#                         logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                         
#                     error_msg = f"❌ Une erreur est survenue lors de l'utilisation du service d'index: {str(index_err)}"
#                     
#                     # Enregistrer dans l'historique de session
#                     session_context.add_message("user", f"!index {action}")
#                     session_context.add_message("assistant", error_msg)
#                     await context_manager.update_context(
#                         session_id,
#                         ContextType.SESSION,
#                         session_context.to_dict()
#                     )
#                     
#                     await matrix_client.send_markdown_message(
#                         ep.room.room_id,
#                         error_msg,
#                         msgtype="m.notice"
#                     )
#         except Exception as init_err:
#             logger.error(f"Erreur lors de l'initialisation des services: {str(init_err)}")
#             
#             # Supprimer le message de chargement
#             try:
#                 await matrix_client.redact_message(
#                     ep.room.room_id,
#                     loading_message
#                 )
#             except Exception as redact_err:
#                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                 
#             error_msg = f"❌ Erreur lors de l'initialisation des services nécessaires: {str(init_err)}"
#             
#             # Enregistrer dans l'historique de session
#             if session_context:
#                 session_context.add_message("user", f"!index {action}")
#                 session_context.add_message("assistant", error_msg)
#                 await context_manager.update_context(
#                     session_id,
#                     ContextType.SESSION,
#                     session_context.to_dict()
#                 )
#                 
#             await matrix_client.send_markdown_message(
#                 ep.room.room_id,
#                 error_msg,
#                 msgtype="m.notice"
#             )
#             
#     except Exception as e:
#         logger.error(f"Erreur lors de l'exécution de la commande index: {str(e)}")
#         
#         # Tenter de supprimer le message de chargement si défini
#         if 'loading_message' in locals():
#             try:
#                 await matrix_client.redact_message(
#                     ep.room.room_id,
#                     loading_message
#                 )
#             except Exception as redact_err:
#                 logger.warning(f"Erreur lors de la suppression du message de chargement: {str(redact_err)}")
#                 
#         await matrix_client.send_markdown_message(
#             ep.room.room_id,
#             f"❌ Une erreur inattendue est survenue: {str(e)}",
#             msgtype="m.notice"
#         )

@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    command="pj",
    help="Récupère et classe les pièces jointes d'un message référencé"
)
@only_allowed_user
# COMMENTÉ: Cette fonction a été migrée vers un module dédié
# DÉPRÉCIÉ: Ne pas utiliser cette fonction, mais plutôt document_commands/attachment.py:handle_attachments_command
# async def handle_attachments_command(ep: EventParser, matrix_client: MatrixClient):
#     """Gère la commande !pj"""
@register_feature(
    group="albert",
    onEvent=RoomMessageText,
    help=None
)
@only_allowed_user
# COMMENTÉ: Cette fonction a été migrée vers un module dédié
# DÉPRÉCIÉ: Ne pas utiliser cette fonction, mais plutôt document_commands/attachment.py:handle_attachments_response
# async def handle_attachments_response(ep: EventParser, matrix_client: MatrixClient):
#     """Gère la réponse de l'utilisateur pour le classement du fichier"""

# La commande bnum a été complètement supprimée car elle est dépréciée
# L'accès aux fonctionnalités du Bureau Numérique est maintenant géré directement via WebDAVService

async def _save_context(
    self,
    context_id: str,
    context_type: ContextType,
    context: Any
) -> None:
    """Sauvegarde un contexte"""
    try:
        # Convertir le contexte en dictionnaire
        context_data = context.to_dict() if hasattr(context, 'to_dict') else context.__dict__
        
        # Ajouter les métadonnées de type
        context_data['_type'] = context_type.value
        
        # Sauvegarder dans le cache
        self._context_cache[context_id] = context
        
        # Sauvegarder sur WebDAV
        await self._save_to_webdav(context_id, context_data)
        
    except Exception as e:
        logger.error(f"Erreur sauvegarde contexte {context_id}: {str(e)}")
        raise

async def get_context(
    self,
    context_id: str,
    context_type: ContextType
) -> Optional[Any]:
    """Récupère un contexte"""
    try:
        # Vérifier le cache
        if context_id in self._context_cache:
            return self._context_cache[context_id]
            
        # Charger depuis WebDAV
        context_data = await self._load_from_webdav(context_id)
        if not context_data:
            return None
            
        # Vérifier le type
        stored_type = context_data.get('_type')
        if stored_type != context_type.value:
            return None
            
        # Créer une nouvelle instance
        return await self.create_context(context_id, context_type, context_data)
        
    except Exception as e:
        logger.error(f"Erreur récupération contexte {context_id}: {str(e)}")
        return None
