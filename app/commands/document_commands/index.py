"""
Module de gestion de l'index documentaire FAISS.

Ce module contient les commandes pour gérer l'index FAISS:
- status: Affiche le statut actuel de l'index
- verify: Vérifie l'intégrité de l'index
- rebuild: Reconstruit l'index 
- clean: Nettoie l'index
"""

from typing import List, Dict, Any, Optional

import asyncio
import traceback
from datetime import datetime
import os
import io

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser
from nio import Event, RoomMessageText, RoomMessageFile

from app.bot_msg import AlbertMsg
from app.config import Config
from app.services.document_index import DocumentIndex
from app.services.index_service import get_index_service
from app.services.context import ContextManager, ContextType
from app.commands.registry import register_feature, only_allowed_user
from app.commands import get_context_manager, get_unified_session_context
from app.commands.lifecycle import command_lifecycle
import app.commands as cmd_module  # Import du module pour accéder à user_configs

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="index",
    help="!index [status|verify|rebuild|clean] - Gérer l'index documentaire"
)
@only_allowed_user
@command_lifecycle("index", timeout=None)
async def faiss_index_command(ep: EventParser, matrix_client: MatrixClient):
    """Commande pour gérer l'index FAISS"""
    # Log détaillé pour le débogage
    logger.debug(f"Déclenchement faiss_index_command - Event: {type(ep.event).__name__}, Message: {getattr(ep.event, 'body', 'N/A')[:50]}")
    
    # Ignorer explicitement les événements de type fichier
    if isinstance(ep.event, RoomMessageFile):
        logger.debug(f"Événement RoomMessageFile ignoré par faiss_index_command")
        return
        
    # Vérification explicite que nous sommes bien dans le cas d'une commande !index
    if not hasattr(ep.event, 'body') or not ep.event.body.strip().startswith("!index"):
        logger.debug(f"Événement ignoré pour !index: le message ne commence pas par !index")
        return
        
    # Récupérer l'action demandée à partir du texte de l'événement
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split()
    action = "status"  # Par défaut
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    sender = ep.sender
    
    # Si la commande a des arguments, le deuxième élément est l'action
    if len(command_parts) > 1:
        action = command_parts[1].lower()
    
    if action not in ["status", "verify", "rebuild", "clean"]:
        await matrix_client.send_markdown_message(
            room_id,
            "❌ Action invalide. Actions disponibles: status, verify, rebuild, clean",
            msgtype="m.notice",
            reply_to=event_id
        )
        return
    
    # Activer l'indicateur de frappe
    await matrix_client.room_typing(room_id, typing_state=True)
    
    try:
        # Informer l'utilisateur que l'action est en cours
        await matrix_client.send_markdown_message(
            room_id,
            f"🔄 Action '{action}' en cours...",
            msgtype="m.notice"
        )
        
        # Désactiver l'indicateur de frappe après le message informatif
        await matrix_client.room_typing(room_id, typing_state=False)
        
        # Utiliser albert_config si disponible, sinon utiliser la config standard
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        
        # Récupérer le gestionnaire de contexte
        context_manager = get_context_manager(config)
        
        # Récupérer ou créer le contexte de session
        logger.info(f"[INDEX DEBUG] Récupération du contexte de session")
        session_context = await get_unified_session_context(config, room_id, sender)
        session_id = f"{room_id}_{sender}"
        
        # S'assurer que conversation_state existe
        if not hasattr(session_context, "conversation_state"):
            session_context.conversation_state = {}
            
        # Mettre à jour l'état de conversation pour la commande index sans écraser les marqueurs de thread
        session_context.conversation_state.update({
            "command_type": "index",
            "last_command": "index",
            "action": action
        })
        
        # Mettre à jour le contexte
        logger.info(f"[INDEX DEBUG] Mise à jour du contexte de session")
        await context_manager.update_context(
            session_id,
            ContextType.SESSION,
            session_context.to_dict()
        )
        
        # Mettre à jour l'activité du participant
        logger.info(f"[INDEX DEBUG] Mise à jour de l'activité du salon")
        await context_manager.update_room_activity(room_id, sender)
        
        try:
            # Utiliser le service d'index global au lieu d'en créer un nouveau
            logger.info(f"[INDEX DEBUG] Récupération du service d'index global")
            index_service = await get_index_service(config)
            
            if action == "status":
                try:
                    # Vérifier si l'index est correctement initialisé
                    if not hasattr(index_service, 'document_index') or index_service.document_index is None:
                        await matrix_client.send_markdown_message(
                            room_id,
                            "❌ L'index documentaire n'est pas correctement initialisé. Veuillez vérifier la configuration WebDAV.",
                            msgtype="m.notice",
                            reply_to=event_id
                        )
                        return
                        
                    # Obtenir le statut de l'index
                    logger.info(f"[INDEX DEBUG] Récupération du statut de l'index")
                    status = await index_service.get_status()
                    
                    # Formater le message de statut
                    status_msg = [
                        "📊 **Statut de l'index**",
                        f"- Documents indexés: {status.get('total_documents', 0)}",
                        f"- Chunks total: {status.get('total_chunks', 0)}",
                        f"- Dimension des embeddings: {status.get('embedding_dimension', 'N/A')}",
                        f"- Modèle d'embedding: {status.get('embedding_model', 'N/A')}",
                    ]
                    
                    # Ajouter les infos de cache si disponibles
                    if 'embedding_cache' in status:
                        cache_info = status['embedding_cache']
                        status_msg.append(f"- Cache des embeddings: {cache_info.get('size', 0)}/{cache_info.get('max_size', 'N/A')}")
                    
                    # Ajouter l'information de fraîcheur
                    is_fresh = status.get('is_fresh', False)
                    status_msg.append(f"- Index à jour: {'✅' if is_fresh else '❌'}")
                    
                    # Ajouter la date de dernière mise à jour si disponible
                    if status.get('last_update'):
                        status_msg.append(f"- Dernière mise à jour: {status['last_update'].strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    # Activer l'indicateur de frappe avant l'envoi de la réponse finale
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    # Envoyer le message de statut
                    logger.info(f"[INDEX DEBUG] Envoi du message de statut")
                    await matrix_client.send_markdown_message(
                        room_id,
                        "\n".join(status_msg),
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi de la réponse
                    await matrix_client.room_typing(room_id, typing_state=False)
                    
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", "\n".join(status_msg))
                    await context_manager.update_context(
                        session_id,
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                except Exception as status_err:
                    logger.error(f"[INDEX DEBUG] Erreur lors de la récupération du statut: {str(status_err)}")
                    
                    # Activer l'indicateur de frappe avant l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"❌ Erreur lors de la récupération du statut: {str(status_err)}",
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=False)
                
            elif action == "verify":
                try:
                    # Vérifier la fraîcheur de l'index
                    logger.info(f"[INDEX DEBUG] Vérification de la fraîcheur de l'index")
                    is_fresh = await index_service.verify()
                    
                    if is_fresh:
                        response = "✅ L'index est à jour!"
                    else:
                        response = "⚠️ L'index n'est pas à jour. Utilisez `!index rebuild` pour le reconstruire."
                        
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", response)
                    await context_manager.update_context(
                        session_id, 
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                    # Activer l'indicateur de frappe avant l'envoi de la réponse finale
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    logger.info(f"[INDEX DEBUG] Envoi du résultat de vérification")
                    await matrix_client.send_markdown_message(
                        room_id,
                        response,
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi de la réponse
                    await matrix_client.room_typing(room_id, typing_state=False)
                except Exception as verify_err:
                    logger.error(f"[INDEX DEBUG] Erreur lors de la vérification: {str(verify_err)}")
                    
                    # Activer l'indicateur de frappe avant l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"❌ Erreur lors de la vérification de l'index: {str(verify_err)}",
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=False)
                
            elif action == "rebuild":
                try:
                    # Mettre à jour le message de chargement
                    await matrix_client.send_markdown_message(
                        room_id,
                        "🔄 Début de la reconstruction de l'index...\nCette opération peut prendre plusieurs minutes.",
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Vérifier d'abord l'existence du répertoire documents
                    try:
                        await index_service.webdav_service.create_directory("documents")
                    except Exception as mkdir_err:
                        logger.warning(f"Erreur lors de la vérification/création du répertoire documents: {str(mkdir_err)}")
                    
                    # Reconstruire l'index
                    logger.info(f"[INDEX DEBUG] Début de la reconstruction de l'index")
                    await index_service.rebuild()
                    
                    response = "✅ Index reconstruit avec succès!"
                    
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", response)
                    await context_manager.update_context(
                        session_id,
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                    # Activer l'indicateur de frappe avant l'envoi de la réponse finale
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    logger.info(f"[INDEX DEBUG] Index reconstruit avec succès")
                    await matrix_client.send_markdown_message(
                        room_id,
                        response,
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi de la réponse
                    await matrix_client.room_typing(room_id, typing_state=False)
                except Exception as rebuild_err:
                    logger.error(f"[INDEX DEBUG] Erreur lors de la reconstruction: {str(rebuild_err)}")
                    
                    error_msg = f"❌ Erreur lors de la reconstruction de l'index: {str(rebuild_err)}"
                    
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", error_msg)
                    await context_manager.update_context(
                        session_id,
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                    # Activer l'indicateur de frappe avant l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    await matrix_client.send_markdown_message(
                        room_id,
                        error_msg,
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=False)
                
            elif action == "clean":
                try:
                    # Nettoyer l'index
                    logger.info(f"[INDEX DEBUG] Nettoyage de l'index")
                    await index_service.clean()
                    
                    response = "✅ Index et cache nettoyés avec succès!"
                    
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", response)
                    await context_manager.update_context(
                        session_id,
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                    # Activer l'indicateur de frappe avant l'envoi de la réponse finale
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    logger.info(f"[INDEX DEBUG] Index nettoyé avec succès")
                    await matrix_client.send_markdown_message(
                        room_id,
                        response,
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi de la réponse
                    await matrix_client.room_typing(room_id, typing_state=False)
                except Exception as clean_err:
                    logger.error(f"[INDEX DEBUG] Erreur lors du nettoyage: {str(clean_err)}")
                    
                    error_msg = f"❌ Erreur lors du nettoyage de l'index: {str(clean_err)}"
                    
                    # Enregistrer dans l'historique de session
                    session_context.add_message("user", f"!index {action}")
                    session_context.add_message("assistant", error_msg)
                    await context_manager.update_context(
                        session_id,
                        ContextType.SESSION,
                        session_context.to_dict()
                    )
                    
                    # Activer l'indicateur de frappe avant l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=True)
                    
                    await matrix_client.send_markdown_message(
                        room_id,
                        error_msg,
                        msgtype="m.notice",
                        reply_to=event_id
                    )
                    
                    # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
                    await matrix_client.room_typing(room_id, typing_state=False)
                
        except Exception as index_err:
            logger.error(f"[INDEX DEBUG] Erreur lors de l'utilisation du service d'index: {str(index_err)}")
            
            error_msg = f"❌ Une erreur est survenue lors de l'utilisation du service d'index: {str(index_err)}"
            
            # Enregistrer dans l'historique de session
            session_context.add_message("user", f"!index {action}")
            session_context.add_message("assistant", error_msg)
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            
            # Activer l'indicateur de frappe avant l'envoi du message d'erreur
            await matrix_client.room_typing(room_id, typing_state=True)
            
            await matrix_client.send_markdown_message(
                room_id,
                error_msg,
                msgtype="m.notice",
                reply_to=event_id
            )
            
            # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
            await matrix_client.room_typing(room_id, typing_state=False)
        
    except Exception as e:
        logger.error(f"[INDEX DEBUG] Erreur lors du traitement de la commande !index: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Activer l'indicateur de frappe avant l'envoi du message d'erreur
        await matrix_client.room_typing(room_id, typing_state=True)
        
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Une erreur est survenue lors du traitement de la commande: {str(e)}",
            msgtype="m.notice",
            reply_to=event_id
        )
        
        # Désactiver l'indicateur de frappe après l'envoi du message d'erreur
        await matrix_client.room_typing(room_id, typing_state=False)
        return
    finally:
        # Ne plus désactiver l'indicateur de frappe ici car il est géré individuellement
        pass 