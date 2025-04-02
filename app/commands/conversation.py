"""
Module de gestion des conversations générales pour Albert.

Ce module contient la logique permettant à Albert de répondre à tous les messages
qui ne correspondent pas à des commandes spécifiques, en utilisant le modèle LLM
et le contexte de la conversation.
"""

import asyncio
import traceback
from typing import Dict, Any, Optional, List
import re

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser, EventNotConcerned
from nio import RoomMessageText

from app.core_llm import AlbertApiClient, generate
from app.services.context.manager import ContextManager
from app.services.context.types import ContextType
from app.services.context.models import SessionContext
from app.config import COMMAND_PREFIX

from app.commands.registry import register_feature, only_allowed_user, command_registry
from app.commands import get_context_manager, get_unified_session_context, update_conversation_history


# NOTE: Cette fonction est conservée pour compatibilité avec l'ancien code
# mais devrait être remplacée progressivement par get_unified_session_context
async def get_session_context(
    config, 
    room_id: str, 
    sender: str
) -> SessionContext:
    """Récupère ou crée un contexte de session pour la conversation."""
    return await get_unified_session_context(config, room_id, sender)


@register_feature(
    group="conversation",
    onEvent=RoomMessageText,
    command=None,  # Pas de commande spécifique, traite tous les messages
    help="Conversation générale avec Albert",
)
@only_allowed_user
async def handle_conversation(ep: EventParser, matrix_client: MatrixClient):
    """Gestionnaire de conversation générale qui traite tous les messages non-commandes."""
    # Utiliser albert_config si disponible, sinon utiliser la config standard
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire le texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Ignorer les messages vides
    if not message_text:
        return
    
    # Log du message reçu
    logger.info(f"Message reçu: '{message_text}'")
    
    # AMÉLIORATION: Vérifier si l'utilisateur est dans un thread de commande actif
    from app.commands.registry import is_in_active_command_thread
    
    is_in_thread, thread_command = await is_in_active_command_thread(room_id, sender, config)
    
    if is_in_thread:
        logger.info(f"Message ignoré par handle_conversation: utilisateur dans un thread de commande '{thread_command}'")
        return
    
    # Vérifier si le message est une commande connue
    is_command = False
    command_parts = message_text.split()
    first_word = command_parts[0] if command_parts else ""
    
    # Si le premier mot commence par le préfixe de commande (généralement '!')
    if first_word.startswith(COMMAND_PREFIX):
        # Extraire la commande sans le préfixe
        command = first_word[len(COMMAND_PREFIX):]
        # Vérifier si c'est une commande valide
        if command_registry.is_valid_command(command):
            # MODIFICATION: Ne pas rejeter les commandes connues
            # On continue le traitement pour conserver l'historique de contexte,
            # mais on ne générera pas de réponse ici car elle sera fournie par le handler spécifique
            is_command = True
            # Mettre à jour l'historique avec le message utilisateur, mais ne pas générer de réponse
            await update_conversation_history(
                config, room_id, sender, user_message=message_text
            )
            return

    # Nous sommes certains que ce n'est pas une commande et que l'utilisateur n'est pas dans un thread actif
    # On peut traiter ce message comme une conversation générale
    try:
        # Indiquer que le bot est en train d'écrire
        await matrix_client.room_typing(room_id, typing_state=True)
        
        # Récupérer le contexte de session et mettre à jour l'historique avec le message utilisateur
        session_context = await update_conversation_history(
            config, room_id, sender, user_message=message_text
        )
        
        # Mettre à jour l'activité du salon
        ctx_manager = get_context_manager(config)
        try:
            room_context = await ctx_manager.get_or_create_room_context(
                room_id=room_id,
                room_name=ep.room.display_name if hasattr(ep.room, 'display_name') else "Salon inconnu",
                is_direct=False
            )
            await ctx_manager.update_room_activity(room_id, sender)
        except Exception as e:
            logger.warning(f"Erreur lors de la mise à jour du contexte de salon: {str(e)}")
            # Continuer malgré l'erreur
        
        # Construire la liste des messages pour préserver le contexte de la conversation
        messages = []
        
        # MODIFICATION: Augmenter la limite de messages d'historique de 10 à 20
        history = session_context.history[-20:] if session_context.history else []
        
        for msg in history:
            if "role" in msg and "content" in msg:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Si aucun message d'historique n'a été ajouté, ajouter un message système
        if len(messages) <= 1:
            messages = [
                {
                    "role": "system",
                    "content": "Tu es Albert, l'assistant de l'État français. Ton rôle est d'aider les utilisateurs en fournissant des réponses précises et pertinentes. Sois cordial, professionnel et concis."
                }
            ] + messages
        
        # Vérifier si nous reprenons après une commande terminée
        conversation_state = session_context.conversation_state if hasattr(session_context, 'conversation_state') else {}
        is_resumable_command = conversation_state.get("command_completed", False)
        
        # Ajouter du contexte supplémentaire si nous reprenons après une commande terminée
        if is_resumable_command:
            # Contexte spécifique en fonction de la dernière commande
            last_command = conversation_state.get("last_command", "")
            last_file = conversation_state.get("last_file_processed", "")
            last_path = conversation_state.get("last_target_path", "")
            last_action = conversation_state.get("last_action", "")
            
            # Construire un message système enrichi
            context_prompt = f"""Tu es Albert, l'assistant de l'État français. Ton rôle est d'aider les utilisateurs en fournissant des réponses précises et pertinentes.

Information contextuelle importante : L'utilisateur vient de terminer une commande "{last_command}" où il a {last_action} le fichier "{last_file}" dans le dossier "{last_path}". 

Ce message est une nouvelle question après cette action, prends en compte ce contexte récent sans y faire référence explicitement sauf si c'est pertinent pour la réponse."""
            
            # Remplacer le message système par notre message enrichi
            for i, msg in enumerate(messages):
                if msg["role"] == "system":
                    messages[i]["content"] = context_prompt
                    break
            else:
                # Aucun message système trouvé, ajouter le nôtre
                messages = [{"role": "system", "content": context_prompt}] + messages
        
        # Générer la réponse
        response = await generate(config, messages)
        
        # Mettre à jour l'historique avec la réponse du bot
        await update_conversation_history(
            config, room_id, sender, bot_response=response
        )
        
        # Envoyer la réponse
        await matrix_client.send_markdown_message(
            room_id,
            response,
            msgtype="m.notice"
        )
        
    except Exception as e:
        logger.error(f"Erreur dans la gestion de conversation: {str(e)}\n{traceback.format_exc()}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Désolé, une erreur est survenue lors du traitement de votre message: {str(e)}",
            msgtype="m.notice"
        )
    finally:
        # Toujours désactiver l'indicateur de frappe
        await matrix_client.room_typing(room_id, typing_state=False) 