"""
Module de gestion de l'index documentaire FAISS.

Commandes : !index [status|verify|rebuild|clean]
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
from app.matrix_bot.typing import typing_indicator
from nio import Event, RoomMessageText, RoomMessageFile

from app.bot_msg import AlbertMsg
from app.config import Config
from app.services.document_index import DocumentIndex
from app.services.index_service import get_index_service
from app.services.context import ContextManager, ContextType
from app.commands.registry import register_feature, only_allowed_user
from app.commands import get_context_manager, get_unified_session_context
from app.commands.lifecycle import command_lifecycle
import app.commands as cmd_module


VALID_ACTIONS = ("status", "verify", "rebuild", "clean")


@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="index",
    help="!index [status|verify|rebuild|clean] - Gérer l'index documentaire"
)
@only_allowed_user
@command_lifecycle("index", timeout=None)
async def faiss_index_command(ep: EventParser, matrix_client: MatrixClient):
    """Commande pour gérer l'index FAISS."""
    if isinstance(ep.event, RoomMessageFile):
        return
    if not hasattr(ep.event, 'body') or not ep.event.body.strip().startswith("!index"):
        return

    message_text = ep.event.body.strip()
    command_parts = message_text.split()
    action = command_parts[1].lower() if len(command_parts) > 1 else "status"
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    sender = ep.sender

    if action not in VALID_ACTIONS:
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ **Action invalide** — Actions disponibles : `{'`, `'.join(VALID_ACTIONS)}`",
            msgtype="m.notice", reply_to=event_id,
        )
        return

    config = getattr(matrix_client, "albert_config", matrix_client.config)

    async with typing_indicator(matrix_client, room_id):
        await matrix_client.send_markdown_message(
            room_id, f"🔄 `!index {action}` en cours...", msgtype="m.notice",
        )

        try:
            context_manager = await get_context_manager(config)
            session_context = await get_unified_session_context(config, room_id, sender)
            session_id = f"{room_id}_{sender}"

            if not hasattr(session_context, "conversation_state"):
                session_context.conversation_state = {}
            session_context.conversation_state.update({
                "command_type": "index", "last_command": "index", "action": action,
            })
            await context_manager.update_context(
                session_id, ContextType.SESSION, session_context.to_dict(),
            )
            await context_manager.update_room_activity(room_id, sender)

            index_service = await get_index_service(config)
            response = await _run_action(action, index_service, matrix_client, room_id, event_id)

            # Historique
            session_context.add_message("user", f"!index {action}")
            session_context.add_message("assistant", response)
            await context_manager.update_context(
                session_id, ContextType.SESSION, session_context.to_dict(),
            )

        except Exception as e:
            logger.error(f"[INDEX] Erreur !index {action}: {e}\n{traceback.format_exc()}")
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ **Erreur index** — {e}",
                msgtype="m.notice", reply_to=event_id,
            )


async def _run_action(
    action: str,
    index_service,
    matrix_client: MatrixClient,
    room_id: str,
    event_id: str,
) -> str:
    """Exécute l'action et envoie le résultat. Retourne le texte de la réponse."""

    if action == "status":
        response = await _action_status(index_service)
    elif action == "verify":
        response = await _action_verify(index_service)
    elif action == "rebuild":
        response = await _action_rebuild(index_service, matrix_client, room_id, event_id)
    elif action == "clean":
        await index_service.clean()
        response = "✅ Index et cache nettoyés avec succès."
    else:
        response = "❌ Action inconnue."

    await matrix_client.send_markdown_message(
        room_id, response, msgtype="m.notice", reply_to=event_id,
    )
    return response


async def _action_status(index_service) -> str:
    if not hasattr(index_service, 'document_index') or index_service.document_index is None:
        return "❌ **Index non initialisé** — Vérifiez la configuration WebDAV."

    status = await index_service.get_status()
    lines = [
        "ℹ️ **Statut de l'index**",
        f"- Documents indexés : {status.get('total_documents', 0)}",
        f"- Chunks : {status.get('total_chunks', 0)}",
        f"- Dimension : {status.get('embedding_dimension', 'N/A')}",
        f"- Modèle : {status.get('embedding_model', 'N/A')}",
    ]
    if 'embedding_cache' in status:
        c = status['embedding_cache']
        lines.append(f"- Cache embeddings : {c.get('size', 0)}/{c.get('max_size', 'N/A')}")
    lines.append(f"- À jour : {'✅' if status.get('is_fresh') else '❌'}")
    if status.get('last_update'):
        lines.append(f"- Dernière MAJ : {status['last_update'].strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


async def _action_verify(index_service) -> str:
    is_fresh = await index_service.verify()
    if is_fresh:
        return "✅ L'index est à jour."
    return "⚠️ L'index n'est pas à jour. Utilisez `!index rebuild` pour le reconstruire."


async def _action_rebuild(index_service, matrix_client, room_id, event_id) -> str:
    import asyncio

    async def _do_rebuild():
        try:
            try:
                await index_service.webdav_service.create_directory("documents")
            except Exception:
                pass
            await index_service.rebuild()
            await matrix_client.send_markdown_message(
                room_id, "✅ Index reconstruit avec succès.", msgtype="m.notice",
            )
        except Exception as e:
            await matrix_client.send_markdown_message(
                room_id, f"❌ Erreur rebuild : {e}", msgtype="m.notice",
            )

    # Lancer en tâche de fond pour ne pas bloquer le bot
    asyncio.create_task(_do_rebuild())
    return "🔄 Reconstruction lancée en arrière-plan. Vous serez notifié à la fin."
