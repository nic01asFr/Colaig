"""
Commande docquery pour interroger les documents indexés.

Ce module contient la commande principale et les fonctions auxiliaires
pour questionner les documents indexés avec le contexte de conversation.
"""

import asyncio
import traceback
from typing import Dict, Any, Optional, List
from urllib.parse import unquote

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import RoomMessageText

from app.bot_msg import AlbertMsg
from app.config import Config
from app.core_llm import AlbertApiClient

from app.services.index_service import IndexService
from app.services.webdav import WebDAVService
from app.services.context.manager import ContextManager
from app.services.context.types import ContextType
from app.services.context.models import SessionContext

from app.commands.registry import register_feature, only_allowed_user
from app.commands import get_context_manager

async def get_session_context(
    config: Config,
    room_id: str, 
    sender: str
) -> SessionContext:
    """Récupère ou crée un contexte de session pour un salon et un utilisateur"""
    # Créer un gestionnaire de contexte
    ctx_manager = ContextManager(config)
    await ctx_manager.initialize()
    
    # Générer un ID de session unique pour cette conversation
    session_id = f"{room_id}_{sender}"
    
    # Récupérer le contexte de session existant, ou en créer un nouveau
    session_context = await ctx_manager.get_context(session_id, ContextType.SESSION)
    
    if not session_context:
        # Créer un nouveau contexte de session
        session_context = await ctx_manager.create_context(
            session_id, 
            ContextType.SESSION,
            {
                "session_id": session_id,
                "room_id": room_id,
                "user_id": sender,
                "conversation_state": {
                    "last_command": "docquery",
                    "current_action": "question"
                }
            }
        )
    else:
        # Mettre à jour l'état de la conversation
        session_context.conversation_state = {
            "last_command": "docquery",
            "current_action": "question"
        }
        # Mettre à jour le contexte
        await ctx_manager.update_context(
            session_id, 
            ContextType.SESSION, 
            session_context.to_dict()
        )
    
    return session_context


@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="docquery",
    help="!docquery [question] - Interroge les documents indexés avec une question",
)
@only_allowed_user
async def doc_query_command(ep: EventParser, matrix_client: MatrixClient):
    """Exécute une requête sur les documents indexés"""
    # Utiliser albert_config si disponible, sinon utiliser la config standard
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire la requête du texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split()
    
    # Si le message est seulement "!docquery" sans arguments
    if len(command_parts) <= 1:
        await matrix_client.send_markdown_message(
            room_id,
            "❓ **Comment utiliser !docquery**\n\n"
            "Cette commande vous permet d'interroger vos documents.\n"
            "```\n!docquery Votre question sur les documents\n```",
            msgtype="m.notice"
        )
        return
    
    # Extraire la requête (tout ce qui suit après le premier mot)
    query = " ".join(command_parts[1:])
    
    # Envoyer un message de confirmation
    loading_msg_event = await matrix_client.send_markdown_message(
        room_id,
        f"🔍 Je recherche des informations pour répondre à: *{query}*",
        msgtype="m.notice"
    )
    
    try:
        # Récupérer ou créer le contexte de session
        session_context = await get_session_context(config, room_id, sender)
        
        # Référence au gestionnaire de contexte
        ctx_manager = get_context_manager(config)
        
        # Mettre à jour l'activité du salon
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
        
        # Convertir l'historique de conversation en texte pour le prompt
        history_text = ""
        if session_context.history:
            # Limiter à 5 derniers échanges pour éviter token overflow
            recent_history = session_context.history[-10:]
            history_pairs = []
            
            for i in range(0, len(recent_history), 2):
                if i+1 < len(recent_history):
                    user_msg = recent_history[i]["content"]
                    bot_msg = recent_history[i+1]["content"]
                    history_pairs.append(f"Utilisateur: {user_msg}\nAssistant: {bot_msg}")
            
            history_text = "\n\n".join(history_pairs)
                
        # Mettre à jour le message de chargement
        try:
            await matrix_client.send_markdown_message(
                room_id,
                f"🔍 Recherche en cours pour: *{query}*\nAnalyse des documents pertinents...",
                msgtype="m.notice",
                message_id=loading_msg_event.event_id
            )
        except AttributeError:
            # Fallback si update_message n'est pas disponible
            logger.warning("Impossible de mettre à jour le message, envoi d'un nouveau message")
            await matrix_client.send_markdown_message(
                room_id,
                f"🔍 Recherche en cours pour: *{query}*\nAnalyse des documents pertinents...",
                msgtype="m.notice"
            )
        
        # Initialiser l'index
        index_service = IndexService(config)
        await index_service.initialize(init_document_index=True)
        
        # Effectuer la recherche
        chunks = await index_service.search(
            query=query,
            limit=5,
            index_type="document",
            search_mode="precise"
        )
        
        # Si aucun document pertinent n'est trouvé
        if not chunks:
            # Mettre à jour l'historique de conversation
            user_message = {"role": "user", "content": query}
            bot_message = {"role": "assistant", "content": "Je n'ai pas trouvé de documents pertinents pour répondre à votre question."}
            
            session_context.history.append(user_message)
            session_context.history.append(bot_message)
            
            # Mettre à jour le contexte
            session_id = f"{room_id}_{sender}"
            await ctx_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
            
            # Informer l'utilisateur
            try:
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Je n'ai pas trouvé de documents pertinents pour répondre à votre question.",
                    msgtype="m.notice",
                    message_id=loading_msg_event.event_id
                )
            except AttributeError:
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Je n'ai pas trouvé de documents pertinents pour répondre à votre question.",
                    msgtype="m.notice"
                )
            return
            
        # Construire le prompt avec les documents et l'historique
        # Préparer la chaîne de documents en dehors de la f-string pour éviter les problèmes d'échappement
        documents_text = "\n".join(chunk.get('content', '') for chunk in chunks)
        
        # Préparer la partie historique
        history_part = ""
        if history_text:
            history_part = f"Historique de la conversation:\n{history_text}\n"
        
        prompt = f"""En tant qu'assistant, répondez à la question suivante en vous basant uniquement sur les documents fournis.
{history_part}Question actuelle: {query}

Documents pertinents:
{documents_text}

Réponse:"""
        
        # Générer la réponse avec le client Albert
        response_text = ""
        try:
            albert_client = AlbertApiClient(
                base_url=config.albert_api_url,
                api_key=config.albert_api_token
            )
            
            # Convertir le prompt en format de messages
            messages = [
                {
                    "role": "system",
                    "content": "Vous êtes Albert, l'assistant de l'État français. Votre rôle est d'aider les utilisateurs en fournissant des réponses précises et détaillées basées sur les documents fournis."
                },
                {
                    "role": "user",
                    "content": f"Je recherche des informations sur la question suivante : {query}\n\nContexte des documents pertinents :\n{documents_text}\n\n{history_part}Merci de me répondre de manière claire et précise, en citant vos sources."
                }
            ]
            
            response_text = await albert_client.generate(
                model=config.albert_model,
                messages=messages,
                max_tokens=1024,
                temperature=0.1
            )
        except Exception as e:
            logger.error(f"Erreur génération réponse: {str(e)}")
            try:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Erreur lors de la génération de la réponse: {str(e)}",
                    msgtype="m.notice",
                    message_id=loading_msg_event.event_id
                )
            except AttributeError:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Erreur lors de la génération de la réponse: {str(e)}",
                    msgtype="m.notice"
                )
            return
            
        # Ajouter les informations sur les sources
        sources = []
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            # Décoder les caractères URL-encodés dans le nom du document
            document_name = metadata.get('document_name', 'Document inconnu')
            if document_name:
                # Utiliser urllib.parse.unquote pour décoder correctement tous les caractères URL-encodés
                document_name = unquote(document_name)
            
            source_parts = [f"📄 {document_name}"]
            
            if metadata.get("section_title"):
                section_title = metadata["section_title"]
                # Utiliser urllib.parse.unquote pour le titre de section également
                section_title = unquote(section_title)
                source_parts.append(f"   Section: {section_title}")
                
            sources.append("\n".join(source_parts))
            
        response_with_sources = response_text
        if sources:
            response_with_sources += "\n\n💡 Sources :\n" + "\n".join(sources)
            
        # Mettre à jour l'historique de conversation
        user_message = {"role": "user", "content": query}
        bot_message = {"role": "assistant", "content": response_with_sources}
        
        session_context.history.append(user_message)
        session_context.history.append(bot_message)
        
        # Mettre à jour le contexte
        session_id = f"{room_id}_{sender}"
        await ctx_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        
        # Envoyer la réponse
        try:
            await matrix_client.send_markdown_message(
                room_id,
                f"🤖 {response_with_sources}",
                msgtype="m.notice",
                message_id=loading_msg_event.event_id
            )
        except AttributeError:
            await matrix_client.send_markdown_message(
                room_id,
                f"🤖 {response_with_sources}",
                msgtype="m.notice"
            )
        
    except Exception as e:
        logger.error(f"Erreur dans docquery: {str(e)}\n{traceback.format_exc()}")
        try:
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Une erreur est survenue: {str(e)}",
                msgtype="m.notice",
                message_id=loading_msg_event.event_id
            )
        except (AttributeError, NameError):
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Une erreur est survenue: {str(e)}",
                msgtype="m.notice"
            )