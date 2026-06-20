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
    logger.info(f"[CONVERSATION] entrée: room={room_id} text={message_text[:60]!r}")
    
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
            from app.commands.registry import is_in_active_command_thread
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
        # NOTE: On ne charge PAS les behaviors ici (ensure_behaviors_loaded)
        # car _orchestrate_response utilise l'agent loop avec workspace_config/skills
        # et n'a pas besoin du behavior_index (qui fait ~40 requêtes WebDAV).
        from app.commands import get_behavior_manager_for_context
        behavior_manager = await get_behavior_manager_for_context(config, room_id=room_id, user_id=sender)
        
        # Récupérer l'historique de conversation
        history = session_context.history
        
        # Indicateur de frappe (désactivé dans le finally)
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
        
        # Historique récent : 6 derniers messages (3 échanges user/assistant).
        # Les réponses assistant sont tronquées pour éviter que le LLM ne
        # les recopie dans sa prochaine réponse.
        history = session_context.history[-6:] if session_context.history else []

        for msg in history:
            if "role" in msg and "content" in msg:
                content = msg["content"]
                # Tronquer les longues réponses assistant pour économiser le contexte
                # et éviter la re-citation par le LLM
                if msg["role"] == "assistant" and len(content) > 300:
                    content = content[:300] + "…"
                messages.append({
                    "role": msg["role"],
                    "content": content,
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
        from app.agent.result import AgentResult
        from app.matrix_bot.response_formatter import format_agent_response

        agent_result = await _orchestrate_response(
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

        # Formater la réponse avec sources et références
        if isinstance(agent_result, AgentResult):
            # Injecter les skills activées pour le bloc références
            formatted_response = format_agent_response(
                agent_result,
                active_skills=getattr(agent_result, '_active_skills', None),
            )
            raw_text = agent_result.text
        else:
            # Fallback si _orchestrate_response retourne un str (compatibilité)
            formatted_response = str(agent_result)
            raw_text = formatted_response

        # Mettre à jour l'historique avec le texte brut (pas les métadonnées)
        await update_conversation_history(
            config, room_id, sender, role="assistant", user_message=raw_text
        )

        # Obtenir le thread_id pour la réponse depuis le contexte Tchap
        thread_root = await ep.get_response_thread_id()

        # Envoyer la réponse formatée — msgtype m.text pour les réponses
        # conversationnelles (notifiantes), m.notice pour les statuts système.
        await matrix_client.send_markdown_message(
            room_id,
            formatted_response,
            msgtype="m.text",
            thread_root=thread_root,
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
    Orchestre la réponse via la boucle agent.

    Le LLM décide lui-même quels outils utiliser (RAG, synthèse, MCP, ou aucun).
    La boucle itère jusqu'à ce que le LLM réponde sans appel d'outil.
    """
    logger.info(f"[AGENT] _orchestrate_response appelé pour: {message_text[:80]!r}")

    from app.agent.loop import agent_loop
    from app.agent.tools import build_registry
    from app.agent.prompt import (
        build_system_prompt,
        build_mcp_tools_description,
        gather_behaviors_summary,
        gather_workspace_info,
    )

    # Préparer les contextes sérialisables
    session_dict = session_context.to_dict() if hasattr(session_context, "to_dict") else {}
    room_dict = room_context.to_dict() if room_context and hasattr(room_context, "to_dict") else {}

    # ── Résolution du workspace ──
    _wdav_ctx = room_dict.get("webdav_context")
    if isinstance(_wdav_ctx, str):
        workspace_root = _wdav_ctx
    elif isinstance(_wdav_ctx, dict):
        workspace_root = _wdav_ctx.get("webdav_root", "") or _wdav_ctx.get("path", "")
    else:
        workspace_root = ""

    has_doc_index = workspace_root is not None
    logger.info(
        f"[AGENT] workspace_root={workspace_root!r} has_doc_index={has_doc_index}"
    )

    # ── Résoudre le service WebDAV scopé au workspace ──
    # Utilisé pour charger MCP, workspace.yaml, behaviors.
    # Grâce au fix d'isolation phase 0, ce service propage workspace_path/BEHAVIOR_DIR.
    webdav_svc = None
    try:
        from app.services.webdav_context_manager import get_webdav_context_manager
        webdav_manager = await get_webdav_context_manager(config)
        webdav_svc = await webdav_manager.get_webdav_for_context(room_id=room_id)
    except Exception as e:
        logger.warning(f"[AGENT] WebDAV indisponible pour ce contexte: {e}")

    # ── Charger la config workspace (workspace.yaml) ──
    from app.agent.workspace_config import load_workspace_config, WorkspaceConfig
    ws_config = WorkspaceConfig.empty()
    if webdav_svc is not None and workspace_root is not None:
        try:
            ws_config = await load_workspace_config(webdav_svc, workspace_root)
        except Exception as e:
            logger.warning(f"[AGENT] workspace.yaml indisponible: {e}")

    # ── Charger les skills déclaratives du workspace ──
    # .albert/skills/*.md activées par triggers regex sur le message courant.
    from app.agent.skills import load_workspace_skills, find_matching_skills
    workspace_skills = []
    active_skills = []
    if webdav_svc is not None and workspace_root is not None:
        try:
            workspace_skills = await load_workspace_skills(webdav_svc, workspace_root)
            active_skills = find_matching_skills(workspace_skills, message_text)
            if active_skills:
                logger.info(
                    f"[AGENT] Skills actives ({len(active_skills)}/{len(workspace_skills)}) : "
                    f"{[s.name for s in active_skills]}"
                )
        except Exception as e:
            logger.warning(f"[AGENT] Skills indisponibles: {e}")

    # ── Charger les outils MCP (même sans workspace_root pour les defaults globaux) ──
    mcp_tools = []
    mcp_registry = None
    mcp_instructions = {}
    try:
        from app.services.mcp.registry import get_mcp_registry
        mcp_registry = get_mcp_registry()
        if webdav_svc:
            # workspace_root vide = chargement des defaults globaux uniquement
            mcp_tools = await mcp_registry.get_tools(webdav_svc, workspace_root or "")
            # get_server_instructions est la SSOT : natif > config > vide
            mcp_instructions = mcp_registry.get_server_instructions(workspace_root or "")

            logger.info(
                f"[AGENT] MCP {len(mcp_tools)} outils, "
                f"{len(mcp_instructions)} instructions"
            )
    except Exception as e:
        logger.warning(f"[AGENT] MCP non disponible: {e}")

    # ── Construire le registre d'outils ──
    registry = build_registry(mcp_tools=mcp_tools, has_doc_index=has_doc_index)

    # ── Phase 2 : Scoping workspace (whitelist/blacklist déterministe) ──
    # S'applique APRÈS build_registry et AVANT le filtrage par mots-clés.
    # Permet à un workspace de retirer ou whitelister des outils via workspace.yaml.
    from app.agent.tool_scoping import apply_workspace_scoping
    apply_workspace_scoping(registry, ws_config)

    # ── Phase 3 : Skills actives → prefers_tools garantis ──
    # Quand une skill matche, ses outils préférés doivent rester dans le registre
    # (override le filtrage à venir). On les ajoute au noyau implicite.
    skills_preferred_tools: set = set()
    if active_skills:
        for s in active_skills:
            for t in s.prefers_tools:
                skills_preferred_tools.add(t)

    # ── Phase 4 : Pipeline hybride de filtrage en 2 niveaux ──
    # Niveau 1 : déterministe et gratuit (mots-clés + détection salutation)
    # Niveau 2 : sémantique unifié via WorkspaceResourceIndex (si ambigu)
    #
    # Le WorkspaceResourceIndex unifie les descriptions d'outils + skills
    # dans un seul index sémantique par workspace, partagé avec les skills
    # déjà chargées plus haut. Remplace l'ancien tool_filter_embed.py.
    from app.agent.tool_filter import filter_tools_by_keywords
    from app.agent.workspace_resources import populate_index

    all_tool_names = [t.name for t in registry.all_tools]
    kw_kept = set(filter_tools_by_keywords(
        message_text, all_tool_names,
        workspace_config=ws_config,
    ))

    if len(kw_kept) > 6:
        # Trop d'outils : on tranche par sémantique unifiée
        try:
            # Construire/maj l'index unifié du workspace
            ws_idx = populate_index(
                workspace_root or "_global",
                internal_tools=[t for t in registry.all_tools if t.name in kw_kept],
                mcp_tools=None,  # déjà inclus via internal_tools (registre filtré)
                skills=workspace_skills,  # pour boost sémantique des skills
            )
            results = await ws_idx.search(
                query=message_text,
                config=config,
                top_k=6,
                kinds=["tool_internal", "tool_mcp"],  # on cherche des outils ici
            )
            embed_kept = {entry.name for entry, _ in results}
            # Garder uniquement les outils qui étaient déjà candidats (intersect kw_kept)
            kept_names = embed_kept & kw_kept if embed_kept else kw_kept
            logger.info(
                f"[AGENT] Filtrage outils: {len(all_tool_names)} "
                f"→ kw={len(kw_kept)} → ws-res={len(kept_names)}"
            )
        except Exception as e:
            # Fallback gracieux sur l'ancien tool_filter_embed (compat)
            logger.warning(f"[AGENT] WS-RES échoué ({e}), fallback tool_filter_embed")
            try:
                from app.agent.tool_filter_embed import filter_tools_by_embeddings
                kw_kept_tools = [t for t in registry.all_tools if t.name in kw_kept]
                embed_kept = await filter_tools_by_embeddings(
                    message_text, kw_kept_tools, config, top_k=6,
                )
                kept_names = set(embed_kept)
            except Exception as e2:
                logger.warning(f"[AGENT] tool_filter_embed aussi échoué ({e2})")
                kept_names = kw_kept
    else:
        kept_names = kw_kept
        logger.info(
            f"[AGENT] Filtrage outils: {len(all_tool_names)} → {len(kept_names)} (kw)"
        )

    # Phase 2b : réinjecter les outils always_included du noyau
    # (le filtrage sémantique top_k peut les avoir retirés)
    from app.agent.tool_filter import _get_always_included
    _always = _get_always_included()
    for tool in _always:
        if tool in set(all_tool_names):
            kept_names.add(tool)

    # Phase 3 : forcer la présence des prefers_tools des skills actives
    if skills_preferred_tools:
        existing_in_reg = set(all_tool_names)
        for tool in skills_preferred_tools:
            if tool in existing_in_reg:
                kept_names.add(tool)

    if len(kept_names) < len(all_tool_names):
        registry.filter_in_place(kept_names)

    # ── Construire le system prompt ──
    behaviors_summary = await gather_behaviors_summary(behavior_manager)
    workspace_info = await gather_workspace_info(config, behavior_manager) if has_doc_index else {}

    system_prompt = build_system_prompt(
        registry,
        mcp_instructions=mcp_instructions,
        mcp_tools_desc=build_mcp_tools_description(mcp_tools),
        workspace_info=workspace_info,
        behaviors_summary=behaviors_summary,
        persona_override=ws_config.persona_override,
        active_skills=active_skills,
    )

    # ── Contexte partagé pour les handlers d'outils ──
    tool_context = {
        "config": config,
        "behavior_manager": behavior_manager,
        "session_dict": session_dict,
        "room_dict": room_dict,
        "session_context_obj": session_context,
        "room_context_obj": room_context,
        "mcp_registry": mcp_registry,
        "workspace_root": workspace_root,
        # Question utilisateur originale, pour orienter le résumé LLM des listings
        "_user_query": message_text,
    }

    # ── Callbacks de progression ──
    async def on_tool_start(tool_name):
        logger.info(f"[AGENT] Exécution : {tool_name}")

    async def on_tool_end(tool_name, preview):
        logger.info(f"[AGENT] Résultat {tool_name}: {preview[:80]}...")

    # ── Lancer la boucle agent ──
    logger.info(
        f"[AGENT] Démarrage — workspace={workspace_root!r}, "
        f"outils={len(registry.all_tools)}, mcp={len(mcp_tools)}"
    )

    result = await agent_loop(
        message=message_text,
        history=messages,
        system_prompt=system_prompt,
        registry=registry,
        config=config,
        tool_context=tool_context,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
    )

    # Enrichir le résultat avec les skills activées (pour le formateur)
    if active_skills:
        result.skills_used = [s.name for s in active_skills]
        result._active_skills = active_skills  # référence complète pour format_skills_ref

    return result