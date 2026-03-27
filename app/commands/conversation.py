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
from app.services.context.models import SessionContext, ConversationStateKeys
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
    """
    Gère les conversations générales avec Albert.
    
    Cette fonction traite tous les messages qui ne correspondent pas
    à une commande spécifique.
    """
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    event_id = getattr(ep.event, "event_id", None)
    message_text = ep.event.body.strip() if hasattr(ep.event, "body") else ""
    
    # ===== VÉRIFICATION DU CONTEXTE TCHAP =====
    # Vérifier si on doit répondre selon le contexte Tchap (DM, salon avec mention, thread participatif)
    if not await ep.should_respond_in_context():
        logger.info(f"[CONVERSATION] Message ignoré selon le contexte Tchap: '{message_text}'")
        return
    
    # Obtenir le contexte pour le formatage et les logs
    context = await ep.get_tchap_context()
    logger.info(f"[CONVERSATION] Contexte résolu: {context.context_type.value}, "
                f"mentionné: {context.is_mentioned}, "
                f"participe au thread: {context.is_bot_participating_in_thread}")
    
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
                config, room_id, sender, role="user", user_message=message_text
            )
            return

    # Étape 3: Ce n'est ni un thread de commande, ni une commande - traiter comme conversation générale
    try:
        # Récupérer le contexte de session après la mise à jour
        session_context = await get_unified_session_context(config, room_id, sender)
        
        # Mettre à jour l'activité du salon
        ctx_manager = await get_context_manager(config)
        try:
            await ctx_manager.update_room_activity(room_id, sender)
        except Exception as e:
            logger.warning(f"Erreur mise à jour activité salon: {str(e)}")
        
        # Récupérer le BehaviorManager spécifique au contexte
        from app.commands import get_behavior_manager_for_context
        behavior_manager = await get_behavior_manager_for_context(config, room_id=room_id, user_id=sender)
        
        # S'assurer que les comportements sont chargés (important en mode lazy loading)
        await behavior_manager.ensure_behaviors_loaded()
        
        # Récupérer l'historique de conversation
        history = session_context.history
        
        # Indiquer que le bot est en train d'écrire
        await matrix_client.room_typing(room_id, typing_state=True)
        
        # Récupérer le contexte de session après la mise à jour
        session_context = await get_unified_session_context(config, room_id, sender)
        
        # Mettre à jour l'activité du salon
        ctx_manager = await get_context_manager(config)
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
        is_resumable_command = conversation_state.get(ConversationStateKeys.COMMAND_COMPLETED, False)

        # Ajouter du contexte supplémentaire si nous reprenons après une commande terminée
        if is_resumable_command:
            # Contexte spécifique en fonction de la dernière commande
            last_command = conversation_state.get(ConversationStateKeys.LAST_COMMAND, "")
            last_file = conversation_state.get(ConversationStateKeys.LAST_FILE_PROCESSED, "")
            last_path = conversation_state.get(ConversationStateKeys.LAST_TARGET_PATH, "")
            last_action = conversation_state.get(ConversationStateKeys.LAST_ACTION, "")
            final_status = conversation_state.get(ConversationStateKeys.FINAL_STATUS, "")
            error_status = conversation_state.get(ConversationStateKeys.ERROR_STATUS, "")
            
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
                query = conversation_state.get(ConversationStateKeys.QUERY, "")
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
        
        # Analyser l'intention et orchestrer les outils disponibles
        response = await _orchestrate_response(
            message_text=message_text,
            messages=messages,
            behavior_manager=behavior_manager,
            session_context=session_context,
            room_context=room_context,
            config=config,
            room_id=room_id,
            matrix_client=matrix_client,
            ep=ep,
        )
        
        # Mettre à jour l'historique avec la réponse du bot
        await update_conversation_history(
            config, room_id, sender, role="assistant", user_message=response
        )
        
        # Obtenir le thread_id pour la réponse depuis le contexte Tchap
        thread_root = await ep.get_response_thread_id()
        
        # Envoyer la réponse dans le thread approprié
        await matrix_client.send_markdown_message(
            room_id,
            response,
            msgtype="m.notice",
            thread_root=thread_root  # Utiliser le thread_root du contexte Tchap
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


async def _orchestrate_response(
    message_text: str,
    messages: list,
    behavior_manager,
    session_context,
    room_context,
    config,
    room_id: str,
    matrix_client,
    ep,
) -> str:
    """
    Orchestre la réponse en fonction de l'intention détectée et des ressources
    disponibles dans le workspace.

    Flux :
    1. Analyse de l'intention via BehaviorManager.detect_intent()
    2. Sélection de la stratégie selon l'intent + ressources disponibles
    3. Exécution de l'action appropriée (RAG, synthèse, web, LLM direct)
    4. Fallback sur generate() direct si aucune action ne correspond

    Returns:
        Réponse textuelle finale
    """
    # Préparer les contextes sérialisables
    session_dict = session_context.to_dict() if hasattr(session_context, "to_dict") else {}
    room_dict = room_context.to_dict() if room_context and hasattr(room_context, "to_dict") else {}

    # Étape 1 — Détecter l'intention
    try:
        has_intent, intent_name, confidence = await behavior_manager.detect_intent(
            message_text,
            session_context=session_dict,
            room_context=room_dict,
        )
        logger.info(
            f"[ORCHESTRATOR] Intent détecté: '{intent_name}' "
            f"(has_intent={has_intent}, confidence={confidence:.2f})"
        )
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Erreur detect_intent, fallback LLM direct: {e}")
        return await generate(config, messages)

    # Étape 2 — Vérifier les ressources disponibles du workspace
    has_doc_index = bool(room_dict.get("webdav_context"))
    has_web_links = False  # TODO: vérifier via web_links_manager quand disponible

    # Vérifier et charger les outils MCP disponibles pour ce workspace
    mcp_tools = []
    mcp_registry = None
    webdav_svc = None
    # webdav_context est stocké comme string (chemin WebDAV du workspace)
    # ex: "/documents/room-123" — c'est directement le workspace_root
    _wdav_ctx = room_dict.get("webdav_context")
    if isinstance(_wdav_ctx, str):
        workspace_root = _wdav_ctx
    elif isinstance(_wdav_ctx, dict):
        workspace_root = _wdav_ctx.get("webdav_root", "") or _wdav_ctx.get("path", "")
    else:
        workspace_root = ""
    if workspace_root:
        try:
            from app.services.mcp.registry import get_mcp_registry
            from app.services.webdav_context_manager import WebDAVContextManager
            mcp_registry = get_mcp_registry()
            webdav_manager = WebDAVContextManager(config)
            webdav_svc = await webdav_manager.get_webdav_for_context(room_id=room_id)
            if webdav_svc:
                mcp_tools = await mcp_registry.get_tools(webdav_svc, workspace_root)
        except Exception as e:
            logger.debug(f"[ORCHESTRATOR] MCP tools non disponibles: {e}")

    has_mcp_tools = bool(mcp_tools)
    if has_mcp_tools:
        logger.info(f"[ORCHESTRATOR] {len(mcp_tools)} outil(s) MCP disponible(s) pour ce workspace")

    # Étape 3 — Orchestration selon l'intent et les ressources
    if has_intent and intent_name in ("standard_rag", "rag") and has_doc_index:
        logger.info("[ORCHESTRATOR] Routage vers RAG documentaire")
        try:
            result = await behavior_manager.execute_action(
                intent_name=intent_name,
                query=message_text,
                context={**session_dict, **room_dict},
            )
            if result.get("success") and result.get("response"):
                return result["response"]
            # Résultat insuffisant → fallback LLM avec contexte enrichi
            logger.info("[ORCHESTRATOR] Résultat RAG insuffisant, fallback LLM")
        except Exception as e:
            logger.warning(f"[ORCHESTRATOR] Erreur execute_action RAG: {e}")

    elif has_intent and intent_name == "synthesis" and has_doc_index:
        logger.info("[ORCHESTRATOR] Routage vers synthèse documentaire")
        try:
            result = await behavior_manager.execute_action(
                intent_name=intent_name,
                query=message_text,
                context={**session_dict, **room_dict},
            )
            if result.get("success") and result.get("response"):
                return result["response"]
            logger.info("[ORCHESTRATOR] Résultat synthèse insuffisant, fallback LLM")
        except Exception as e:
            logger.warning(f"[ORCHESTRATOR] Erreur execute_action synthèse: {e}")

    # Étape 4 — Outils MCP : injecter les descriptions dans le prompt et laisser le LLM décider
    if has_mcp_tools and mcp_registry is not None:
        logger.info("[ORCHESTRATOR] Injection des outils MCP dans le contexte LLM")
        try:
            response = await _orchestrate_with_mcp(
                message_text=message_text,
                messages=messages,
                mcp_tools=mcp_tools,
                workspace_root=workspace_root,
                mcp_registry=mcp_registry,
                config=config,
            )
            if response:
                return response
        except Exception as e:
            logger.warning(f"[ORCHESTRATOR] Erreur orchestration MCP, fallback LLM: {e}")

    # Étape 5 — Fallback : LLM direct avec historique
    logger.info(
        f"[ORCHESTRATOR] Fallback LLM direct "
        f"(intent={intent_name}, has_doc_index={has_doc_index}, mcp={has_mcp_tools})"
    )
    return await generate(config, messages)


async def _orchestrate_with_mcp(
    message_text: str,
    messages: list,
    mcp_tools: list,
    workspace_root: str,
    mcp_registry,
    config,
) -> str:
    """
    Délègue au LLM la sélection d'un outil MCP et l'exécute.

    Stratégie simple (1 tour) :
    1. Injecter les descriptions d'outils dans le prompt système
    2. Demander au LLM si un outil est pertinent (réponse JSON structurée)
    3. Si un outil est sélectionné : l'appeler, puis reformuler avec le résultat
    4. Si aucun outil n'est pertinent : retourner None → fallback LLM direct
    """
    from app.core_llm import generate
    import json as _json

    # Construire la liste des outils pour le prompt
    tools_desc = "\n".join(
        f"- `{t.server_name}__{t.name}` : {t.description}"
        for t in mcp_tools
    )

    selection_messages = [
        {
            "role": "system",
            "content": (
                "Tu es un assistant qui doit sélectionner l'outil le plus pertinent "
                "parmi ceux disponibles pour répondre à la requête.\n\n"
                f"Outils disponibles :\n{tools_desc}\n\n"
                "Réponds UNIQUEMENT avec un objet JSON valide :\n"
                '{"use_tool": true, "tool": "server__name", "arguments": {...}}\n'
                "ou\n"
                '{"use_tool": false}\n'
                "Ne génère aucun autre texte."
            ),
        },
        {"role": "user", "content": message_text},
    ]

    raw_decision = await generate(config, selection_messages)

    # Parser la décision
    try:
        # Extraire le JSON même si le LLM a ajouté du texte autour
        import re as _re
        match = _re.search(r"\{.*\}", raw_decision, _re.DOTALL)
        if not match:
            return None
        decision = _json.loads(match.group())
    except (_json.JSONDecodeError, AttributeError):
        logger.debug(f"[MCP] Décision LLM non parseable: {raw_decision!r}")
        return None

    if not decision.get("use_tool"):
        logger.info("[MCP] LLM a décidé de ne pas utiliser d'outil MCP")
        return None

    qualified_name = decision.get("tool", "")
    arguments = decision.get("arguments", {})

    if not qualified_name:
        return None

    logger.info(f"[MCP] LLM sélectionne l'outil: {qualified_name}")

    tool_result = await mcp_registry.call_tool(workspace_root, qualified_name, arguments)

    if tool_result.is_error:
        logger.warning(f"[MCP] Outil {qualified_name} a retourné une erreur: {tool_result.content}")
        return None

    # Reformuler la réponse finale avec le résultat de l'outil
    synthesis_messages = messages + [
        {
            "role": "system",
            "content": (
                f"L'outil `{qualified_name}` a retourné le résultat suivant :\n\n"
                f"{tool_result.content}\n\n"
                "Utilise ce résultat pour répondre à la question de l'utilisateur "
                "de manière claire et synthétique."
            ),
        },
    ]

    return await generate(config, synthesis_messages)