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

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser, EventNotConcerned
from nio import RoomMessageText

from app.core_llm import AlbertApiClient, generate
from app.services.context.manager import ContextManager
from app.services.context.types import ContextType
from app.services.context.models import SessionContext
from app.config import COMMAND_PREFIX

from app.commands.registry import register_feature, only_allowed_user, command_registry, is_event_processed
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
    event_id = getattr(ep.event, "event_id", None)
    
    # Extraire le texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Ignorer les messages vides
    if not message_text:
        return
    
    # Log du message reçu
    logger.info(f"Message reçu: '{message_text}'")
    
    # Vérification supplémentaire pour éviter le double traitement
    # Vérifie si ce message a déjà été traité dans une synchronisation précédente
    if event_id:
        processed, handler, completed = is_event_processed(event_id)
        if processed and handler != "handle_conversation":
            # Si ce message a déjà été traité par un handler de commande dans une synchronisation précédente,
            # nous devons vérifier si nous sommes dans une nouvelle synchronisation ou dans le même tour
            
            # Vérifier si nous sommes au début d'une nouvelle synchronisation
            # (ceci est une heuristique basée sur le fait que l'état de synchronisation est réinitialisé)
            # Nous pouvons vérifier si le thread de commande est actif maintenant
            is_in_thread, _ = await is_in_active_command_thread(room_id, sender, config)
            
            # Si la commande est terminée (thread inactif) mais que le message a déjà été traité,
            # alors c'est probablement une nouvelle synchronisation et nous permettons le traitement
            if not is_in_thread:
                logger.info(f"Nouvelle synchronisation détectée, reprise du message '{message_text}' en conversation normale")
                # Continuer pour traitement
            else:
                # Même tour de synchronisation, éviter le double traitement
                logger.info(f"Message '{message_text}' déjà traité par {handler}, ignoré par handle_conversation")
                return
    
    # Étape 1: Vérifier si l'utilisateur est dans un thread de commande actif
    from app.commands.registry import is_in_active_command_thread
    
    is_in_thread, thread_command = await is_in_active_command_thread(room_id, sender, config)
    
    if is_in_thread:
        logger.info(f"Message ignoré par handle_conversation: utilisateur dans un thread de commande '{thread_command}'")
        return
    
    # Étape 2: Vérifier si le message est une commande connue (commençant par !)
    # Si c'est le cas, ne pas le traiter ici mais enregistrer quand même dans l'historique
    is_command = False
    command_parts = message_text.split()
    first_word = command_parts[0] if command_parts else ""
    
    # Si le premier mot commence par le préfixe de commande (!)
    if first_word.startswith(COMMAND_PREFIX):
        # Extraire la commande sans le préfixe
        command = first_word[len(COMMAND_PREFIX):]
        # Vérifier si c'est une commande valide
        if command_registry.is_valid_command(command):
            logger.info(f"Message '{message_text}' identifié comme commande valide '{command}', enregistrement dans l'historique uniquement")
            # Mettre à jour l'historique avec le message utilisateur, mais ne pas générer de réponse
            await update_conversation_history(
                config, room_id, sender, user_message=message_text
            )
            return

    # Étape 3: Ce n'est ni un thread de commande, ni une commande - traiter comme conversation générale
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
            final_status = conversation_state.get("final_status", "")
            error_status = conversation_state.get("error_status", "")
            
            # Détermine le statut de la dernière commande de manière plus précise
            status_description = ""
            if error_status:
                status_description = f"a rencontré une erreur ({error_status}) lors du traitement"
            elif final_status == "success":
                status_description = "a traité avec succès"
            elif last_action in ["classé", "classer_terminé"]:
                status_description = "a classé avec succès"
            else:
                status_description = last_action or "a traité"
            
            # Log détaillé pour le débogage de la reprise de conversation
            logger.debug(f"[CONVERSATION DEBUG] Reprise après commande : last_command={last_command}, last_file={last_file}, last_action={last_action}, statut={status_description}")
            
            # Construction d'un prompt plus détaillé selon la commande
            cmd_context = ""
            if last_command == "pj" and last_file:
                cmd_context = f"L'utilisateur vient de terminer la commande de classement de pièce jointe (!pj) où il {status_description} le fichier \"{last_file}\""
                if last_path:
                    cmd_context += f" dans le dossier \"{last_path}\""
                cmd_context += "."
            elif last_command == "docquery":
                query = conversation_state.get("query", "")
                cmd_context = f"L'utilisateur vient de faire une recherche documentaire (!docquery) sur : \"{query}\""
                if final_status == "success":
                    cmd_context += " et a obtenu des résultats pertinents."
                elif error_status:
                    cmd_context += " mais la recherche a rencontré des problèmes."
                else:
                    cmd_context += "."
            else:
                cmd_context = f"L'utilisateur vient de terminer une commande \"{last_command}\" où il {status_description}"
                if last_file:
                    cmd_context += f" le fichier \"{last_file}\""
                if last_path:
                    cmd_context += f" dans \"{last_path}\""
                cmd_context += "."
            
            # Construire un message système enrichi avec un contexte plus détaillé
            context_prompt = f"""Tu es Albert, l'assistant de l'État français. Ton rôle est d'aider les utilisateurs en fournissant des réponses précises et pertinentes.

Information contextuelle importante : {cmd_context}

Ce message suivant semble être une réaction ou une nouvelle question après cette action. Si l'utilisateur fait référence à cette action précédente, réponds en tenant compte de ce contexte. Si c'est un simple remerciement, tu peux confirmer que l'action précédente s'est bien déroulée et proposer ton aide pour la suite."""
            
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