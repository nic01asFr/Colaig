"""
Commandes de base du bot Albert.

Ce module contient les commandes fondamentales comme l'aide.
"""

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import RoomMessageText, RoomMemberEvent

from bot_msg import AlbertMsg
from config import Config

from .registry import register_feature, only_allowed_user, command_registry

# Commande d'aide
@register_feature(
    group="basic",
    onEvent=RoomMessageText,
    command="aide",
    aliases=["help", "aiuto"],
    help=AlbertMsg.shorts["help"],
)
@only_allowed_user
async def help(ep: EventParser, matrix_client: MatrixClient):
    """Affiche l'aide du bot"""
    # Utiliser la configuration Albert si disponible, sinon utiliser la config Matrix
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    
    # Extraire les arguments depuis le texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split()
    
    # Si le message contient plus qu'un mot et que le deuxième est "all", on affiche l'aide détaillée
    verbose = len(command_parts) > 1 and command_parts[1] == "all"
    
    # Activer l'indicateur de frappe
    await matrix_client.room_typing(room_id, typing_state=True)
    
    try:
        # Utiliser le registre importé directement
        cmd_registry = command_registry
        
        # Générer le message d'aide
        help_msg = cmd_registry.get_help(config, verbose)
        
        # Envoyer le message d'aide
        await matrix_client.send_markdown_message(
            room_id,
            help_msg,
            msgtype="m.notice",
            reply_to=event_id
        )
        
        # Si l'utilisateur a demandé l'aide détaillée, on lui montre comment
        # retourner à l'aide simplifiée
        if verbose:
            await matrix_client.send_markdown_message(
                room_id,
                "Tapez !aide pour afficher l'aide simplifiée.",
                msgtype="m.notice"
            )
    finally:
        # Désactiver l'indicateur de frappe
        await matrix_client.room_typing(room_id, typing_state=False) 