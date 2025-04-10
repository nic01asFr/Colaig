"""
Commande docquery pour interroger les documents indexés.

Ce module contient la commande principale et les fonctions auxiliaires
pour questionner les documents indexés avec le contexte de conversation.
"""

import sys
import time
import json
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
from app.services.context.types import ContextType
from app.services.context.models import SessionContext

from app.commands.registry import register_feature, only_allowed_user, mark_event_processed
from app.commands import get_context_manager, get_unified_session_context
from app.commands.lifecycle import command_lifecycle, CommandLifecycleManager

async def get_session_context(
    config: Config,
    room_id: str, 
    sender: str
) -> SessionContext:
    """
    Récupère ou crée un contexte de session pour un salon et un utilisateur.
    Assure la continuité contextuelle avec les conversations générales.
    """
    # Utiliser la fonction unifiée pour récupérer ou créer le contexte
    session_context = await get_unified_session_context(config, room_id, sender)
    
    # Mettre à jour uniquement l'état de la conversation pour docquery
    # sans toucher à l'historique
    session_context.conversation_state.update({
        "last_command": "docquery",
        "action": "question",
        "command_context": "document"  # Marquer clairement le contexte documentaire
    })
    
    # Mettre à jour le contexte
    session_id = f"{room_id}_{sender}"
    from app.services.context.instance import context_manager
    await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
    
    logger.debug(f"Contexte de session docquery mis à jour, historique: {len(session_context.history)} messages")
    
    return session_context


@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="docquery",
    help="!docquery [question] - Interroge les documents indexés avec une question",
)
@only_allowed_user
@command_lifecycle("docquery")  # Utiliser le nouveau décorateur pour la gestion du cycle de vie
async def doc_query_command(ep: EventParser, matrix_client: MatrixClient):
    """Exécute une requête sur les documents indexés"""
    # AMÉLIORATIONS DE ROBUSTESSE: Ajouter plus de logs et une meilleure gestion des exceptions
    try:
        # Capturer toutes les informations essentielles au début
        logger.info(f"[DOCQUERY DEBUG] ====== DÉMARRAGE DE LA COMMANDE DOCQUERY ======")
        logger.info(f"[DOCQUERY DEBUG] Type d'événement: {type(ep.event).__name__}")
        logger.info(f"[DOCQUERY DEBUG] Event ID: {getattr(ep.event, 'event_id', 'NON DISPONIBLE')}")
        logger.info(f"[DOCQUERY DEBUG] Sender: {ep.sender}")
        logger.info(f"[DOCQUERY DEBUG] Room ID: {ep.room.room_id}")
        
        # Utiliser albert_config si disponible, sinon utiliser la config standard
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        room_id = ep.room.room_id
        sender = ep.sender
        event_id = getattr(ep.event, "event_id", None)
        
        # Log détaillé au début de la fonction
        logger.info(f"[DOCQUERY DEBUG] Début du traitement de la commande docquery")
        
        # Extraire la requête du texte du message
        message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
        command_parts = message_text.split()
        logger.info(f"[DOCQUERY DEBUG] Message reçu: '{message_text}'")
        
        # Si le message est seulement "!docquery" sans arguments
        if len(command_parts) <= 1:
            logger.info(f"[DOCQUERY DEBUG] Message d'aide envoyé (pas d'arguments)")
            await matrix_client.send_markdown_message(
                room_id,
                "❓ **Comment utiliser !docquery**\n\n"
                "Cette commande vous permet d'interroger vos documents.\n"
                "```\n!docquery Votre question sur les documents\n```",
                msgtype="m.notice"
            )
            
            # Retourner des résultats pour le décorateur command_lifecycle
            return {
                "action": "aide affichée",
                "result": "help_message"
            }
        
        # Extraire la requête (tout ce qui suit après le premier mot)
        query = " ".join(command_parts[1:])
        logger.info(f"[DOCQUERY DEBUG] Requête extraite: '{query}'")
        
        # ROBUSTESSE: Utiliser un bloc try/except pour l'envoi du message de confirmation
        try:
            logger.info(f"[DOCQUERY DEBUG] Envoi du message de chargement")
            loading_msg_event = await matrix_client.send_markdown_message(
                room_id,
                f"🔍 Je recherche des informations pour répondre à: *{query}*",
                msgtype="m.notice"
            )
            logger.info(f"[DOCQUERY DEBUG] Message de chargement envoyé: {type(loading_msg_event).__name__}")
        except Exception as msg_err:
            # Capture explicite des erreurs d'envoi de message
            logger.error(f"[DOCQUERY DEBUG] Erreur lors de l'envoi du message de chargement: {str(msg_err)}")
            # Ne pas interrompre le traitement, continuer sans message de chargement
            loading_msg_event = None
        
        # Récupérer ou créer le contexte de session
        logger.info(f"[DOCQUERY DEBUG] Récupération du contexte de session")
        session_context = await get_session_context(config, room_id, sender)
        
        # Référence au gestionnaire de contexte
        logger.info(f"[DOCQUERY DEBUG] Obtention du gestionnaire de contexte")
        ctx_manager = get_context_manager(config)
        
        # Mettre à jour l'activité du salon
        try:
            logger.info(f"[DOCQUERY DEBUG] Mise à jour de l'activité du salon")
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
        logger.info(f"[DOCQUERY DEBUG] Préparation de l'historique de conversation")
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
            # Vérifier si loading_msg_event a un event_id, mais ne pas l'utiliser pour send_markdown_message
            if hasattr(loading_msg_event, 'event_id'):
                # Pour référence future, mais non utilisé actuellement
                event_id = loading_msg_event.event_id
            await matrix_client.send_markdown_message(
                room_id,
                f"🔍 Recherche en cours pour: *{query}*\nAnalyse des documents pertinents...",
                msgtype="m.notice"
            )
        except AttributeError:
            # Fallback si update_message n'est pas disponible
            logger.warning("Impossible de mettre à jour le message, envoi d'un nouveau message")
            await matrix_client.send_markdown_message(
                room_id,
                f"🔍 Recherche en cours pour: *{query}*\nAnalyse des documents pertinents...",
                msgtype="m.notice"
            )
        
        # Initialiser l'index avec timeout explicite
        logger.info(f"[DOCQUERY DEBUG] Initialisation du service d'index")
        try:
            # Utiliser le service d'index global au lieu d'en créer un nouveau
            from app.services.index_service import get_index_service
            index_service = await asyncio.wait_for(
                get_index_service(config), 
                timeout=30.0
            )
            logger.info(f"[DOCQUERY DEBUG] Service d'index global récupéré avec succès")
        except asyncio.TimeoutError:
            # En cas de timeout, envoyer un message d'erreur et terminer
            logger.error(f"[DOCQUERY DEBUG] Timeout lors de l'initialisation du service d'index")
            try:
                # Noter la présence de l'event_id mais ne pas l'utiliser avec send_markdown_message
                if hasattr(loading_msg_event, 'event_id'):
                    event_id = loading_msg_event.event_id  # Pour référence future
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Délai d'attente dépassé lors de l'initialisation du service de recherche. Veuillez réessayer ultérieurement.",
                    msgtype="m.notice"
                )
            except Exception as msg_error:
                # En cas d'erreur, fallback sur un nouveau message
                logger.warning(f"Erreur lors de la mise à jour du message: {str(msg_error)}")
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Délai d'attente dépassé lors de l'initialisation du service de recherche. Veuillez réessayer ultérieurement.",
                    msgtype="m.notice"
                )
            
            # Retourner un résultat d'erreur pour le décorateur command_lifecycle
            return {
                "action": "recherche terminée avec erreur",
                "result": "initialization_timeout",
                "query": query,
                "error": "Timeout lors de l'initialisation"
            }
            
        # Effectuer la recherche avec timeout
        logger.info(f"[DOCQUERY DEBUG] Exécution de la recherche")
        try:
            # Timeout de 20 secondes pour la recherche
            chunks = await asyncio.wait_for(
                index_service.search(
                    query=query,
                    limit=5,
                    index_type="document",
                    search_mode="precise"
                ),
                timeout=20.0
            )
            logger.info(f"[DOCQUERY DEBUG] Recherche terminée avec {len(chunks) if chunks else 0} résultats")
        except asyncio.TimeoutError:
            # En cas de timeout, envoyer un message d'erreur et terminer
            logger.error(f"[DOCQUERY DEBUG] Timeout lors de la recherche")
            try:
                # Noter la présence de l'event_id mais ne pas l'utiliser pour send_markdown_message
                if hasattr(loading_msg_event, 'event_id'):
                    event_id = loading_msg_event.event_id  # Pour référence future
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Délai d'attente dépassé lors de la recherche. Veuillez réessayer avec une question plus simple.",
                    msgtype="m.notice"
                )
            except AttributeError:
                # Fallback si loading_msg_event est invalide
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Délai d'attente dépassé lors de la recherche. Veuillez réessayer avec une question plus simple.",
                    msgtype="m.notice"
                )
            
            # Retourner un résultat d'erreur pour le décorateur command_lifecycle
            return {
                "action": "recherche terminée avec erreur",
                "result": "search_timeout",
                "query": query,
                "error": "Timeout lors de la recherche"
            }
            
        # Si aucun document pertinent n'est trouvé
        if not chunks:
            logger.info(f"[DOCQUERY DEBUG] Aucun document pertinent trouvé pour la requête")
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
                # Noter la présence de l'event_id mais ne pas l'utiliser pour send_markdown_message
                if hasattr(loading_msg_event, 'event_id'):
                    event_id = loading_msg_event.event_id  # Pour référence future
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Je n'ai pas trouvé de documents pertinents pour répondre à votre question.",
                    msgtype="m.notice"
                )
            except AttributeError:
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Je n'ai pas trouvé de documents pertinents pour répondre à votre question.",
                    msgtype="m.notice"
                )
            
            # Retourner un résultat pour le décorateur command_lifecycle
            return {
                "action": "recherche terminée",
                "result": "no_documents_found",
                "query": query
            }
            
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
            logger.info(f"[DOCQUERY DEBUG] Initialisation du client Albert API")
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
            
            logger.info(f"[DOCQUERY DEBUG] Génération de la réponse avec le modèle")
            # Ajouter un timeout pour la génération de la réponse
            response_text = await asyncio.wait_for(
                albert_client.generate(
                    model=config.albert_model,
                    messages=messages,
                    max_tokens=1024,
                    temperature=0.1
                ),
                timeout=30.0  # 30 secondes de timeout
            )
            logger.info(f"[DOCQUERY DEBUG] Réponse générée avec succès ({len(response_text)} caractères)")
        except asyncio.TimeoutError:
            logger.error(f"[DOCQUERY DEBUG] Timeout lors de la génération de la réponse")
            try:
                # Noter la présence de l'event_id mais ne pas l'utiliser pour send_markdown_message
                if hasattr(loading_msg_event, 'event_id'):
                    event_id = loading_msg_event.event_id  # Pour référence future
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Délai d'attente dépassé lors de la génération de la réponse. Veuillez réessayer avec une question plus simple.",
                    msgtype="m.notice"
                )
            except AttributeError:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Délai d'attente dépassé lors de la génération de la réponse. Veuillez réessayer avec une question plus simple.",
                    msgtype="m.notice"
                )
                
            # Retourner un résultat d'erreur pour le décorateur command_lifecycle
            return {
                "action": "recherche terminée avec erreur",
                "result": "generation_timeout",
                "query": query,
                "error": "Timeout lors de la génération de la réponse"
            }
        except Exception as e:
            logger.error(f"Erreur génération réponse: {str(e)}")
            try:
                # Noter la présence de l'event_id mais ne pas l'utiliser pour send_markdown_message
                if hasattr(loading_msg_event, 'event_id'):
                    event_id = loading_msg_event.event_id  # Pour référence future
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Erreur lors de la génération de la réponse: {str(e)}",
                    msgtype="m.notice"
                )
            except AttributeError:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"❌ Erreur lors de la génération de la réponse: {str(e)}",
                    msgtype="m.notice"
                )
                
            # Retourner un résultat d'erreur pour le décorateur command_lifecycle
            return {
                "action": "recherche terminée avec erreur",
                "result": "generation_error",
                "query": query,
                "error": str(e)
            }
            
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
        ctx_manager = get_context_manager(config)
        await ctx_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        
        # Envoyer la réponse
        try:
            # Noter la présence de l'event_id mais ne pas l'utiliser pour send_markdown_message
            if hasattr(loading_msg_event, 'event_id'):
                event_id = loading_msg_event.event_id  # Pour référence future
            await matrix_client.send_markdown_message(
                room_id,
                f"🤖 {response_with_sources}",
                msgtype="m.notice"
            )
        except AttributeError:
            await matrix_client.send_markdown_message(
                room_id,
                f"🤖 {response_with_sources}",
                msgtype="m.notice"
            )
        
        # Retourner les résultats finaux pour le décorateur command_lifecycle
        return {
            "action": "recherche terminée avec succès",
            "result": "response_generated",
            "query": query,
            "sources_count": len(chunks) if chunks else 0
        }
        
    # ROBUSTESSE: Capture globale de toutes les exceptions pour diagnostic
    except Exception as global_err:
        # Log détaillé de l'erreur 
        logger.error(f"[DOCQUERY DEBUG] ====== ERREUR GLOBALE DANS DOCQUERY ======")
        logger.error(f"[DOCQUERY DEBUG] Type d'erreur: {type(global_err).__name__}")
        logger.error(f"[DOCQUERY DEBUG] Message d'erreur: {str(global_err)}")
        logger.error(f"[DOCQUERY DEBUG] Traceback: {traceback.format_exc()}")
        
        # Essayer d'informer l'utilisateur si possible
        try:
            if 'room_id' in locals() and room_id:
                await matrix_client.send_markdown_message(
                    room_id,
                    "❌ Une erreur est survenue lors du traitement de votre demande. L'équipe technique a été notifiée.",
                    msgtype="m.notice"
                )
        except Exception as msg_err:
            logger.error(f"[DOCQUERY DEBUG] Impossible d'envoyer le message d'erreur: {str(msg_err)}")
        
        # Propager l'erreur pour que le décorateur command_lifecycle la gère correctement
        raise