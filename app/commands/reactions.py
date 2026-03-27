"""
Gestion des réactions emoji sur les messages du bot.

Emojis pris en charge :
  👍  Feedback positif — mémorisé dans le contexte utilisateur
  👎  Feedback négatif — mémorisé + proposition de reformulation
  🔄  Reformuler — redemande une version reformulée du dernier message bot
  ➕  Sauvegarder — enregistre le message dans les notes du workspace (WebDAV)

Prérequis : MatrixClient._sent_messages_cache doit être alimenté par _send_room().
"""
from __future__ import annotations

import traceback
from typing import Optional

from nio import MatrixRoom, Event

from app.matrix_bot.config import logger


# ─── Émojis reconnus ──────────────────────────────────────────────────────────

EMOJI_POSITIVE   = "👍"
EMOJI_NEGATIVE   = "👎"
EMOJI_REPHRASE   = "🔄"
EMOJI_SAVE       = "➕"

HANDLED_EMOJIS = {EMOJI_POSITIVE, EMOJI_NEGATIVE, EMOJI_REPHRASE, EMOJI_SAVE}


# ─── Handler principal ─────────────────────────────────────────────────────────

async def handle_reaction(room: MatrixRoom, event: Event, emoji: str, matrix_client):
    """
    Point d'entrée unique pour toutes les réactions emoji.
    Appelé depuis callbacks.register_on_reaction_event().
    """
    if emoji not in HANDLED_EMOJIS:
        return

    # Ignorer les réactions du bot lui-même
    if event.sender == matrix_client.user_id:
        return

    room_id = room.room_id
    sender = event.sender

    # Récupérer l'event_id du message réagi via m.relates_to
    try:
        relates_to = event.source.get("content", {}).get("m.relates_to", {})
        target_event_id = relates_to.get("event_id")
    except Exception:
        target_event_id = None

    if not target_event_id:
        logger.warning(f"[REACTION] Réaction {emoji} sans event_id cible, ignorée")
        return

    # Vérifier si c'est bien une réaction à un message du bot
    cached = matrix_client._sent_messages_cache.get(target_event_id)
    if not cached:
        # Réaction sur un message non-bot, pas notre affaire
        logger.debug(f"[REACTION] event_id {target_event_id} non trouvé dans le cache bot")
        return

    original_body = cached.get("formatted_body") or cached.get("body", "")
    thread_root = cached.get("thread_root")

    logger.info(f"[REACTION] {sender} a réagi avec {emoji} sur le message {target_event_id}")

    try:
        if emoji == EMOJI_POSITIVE:
            await _handle_positive(room_id, sender, target_event_id, matrix_client)

        elif emoji == EMOJI_NEGATIVE:
            await _handle_negative(room_id, sender, target_event_id, matrix_client, thread_root)

        elif emoji == EMOJI_REPHRASE:
            await _handle_rephrase(
                room_id, sender, original_body, matrix_client, thread_root
            )

        elif emoji == EMOJI_SAVE:
            await _handle_save(
                room_id, sender, original_body, matrix_client, thread_root
            )

    except Exception as e:
        logger.error(f"[REACTION] Erreur traitement {emoji}: {e}\n{traceback.format_exc()}")


# ─── Handlers spécifiques ─────────────────────────────────────────────────────

async def _handle_positive(room_id: str, sender: str, event_id: str, matrix_client):
    """Enregistre un feedback positif dans le contexte utilisateur."""
    try:
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        await _save_feedback(config, room_id, sender, event_id, positive=True)
    except Exception as e:
        logger.warning(f"[REACTION] Impossible de sauvegarder feedback 👍 : {e}")

    # Accusé de réception discret : réagir avec ✅ sur le même message
    try:
        # Créer un faux objet event avec l'event_id pour send_reaction
        class _FakeEvent:
            pass
        fake = _FakeEvent()
        fake.event_id = event_id
        await matrix_client.send_reaction(room_id, fake, "✅")
    except Exception as e:
        logger.debug(f"[REACTION] Impossible d'envoyer ✅ : {e}")


async def _handle_negative(
    room_id: str, sender: str, event_id: str, matrix_client, thread_root: Optional[str]
):
    """Enregistre un feedback négatif et propose une reformulation."""
    try:
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        await _save_feedback(config, room_id, sender, event_id, positive=False)
    except Exception as e:
        logger.warning(f"[REACTION] Impossible de sauvegarder feedback 👎 : {e}")

    await matrix_client.send_markdown_message(
        room_id,
        "Je prends note que cette réponse ne vous a pas satisfait. "
        "Si vous souhaitez une reformulation, utilisez la réaction 🔄, "
        "ou précisez votre question directement.",
        msgtype="m.notice",
        thread_root=thread_root,
    )


async def _handle_rephrase(
    room_id: str, sender: str, original_body: str, matrix_client, thread_root: Optional[str]
):
    """Reformule le dernier message du bot via le LLM."""
    try:
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        from app.core_llm import generate

        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es Albert, l'assistant de l'État français. "
                    "L'utilisateur te demande de reformuler ta réponse précédente "
                    "de manière plus claire, concise et pédagogique."
                ),
            },
            {
                "role": "assistant",
                "content": original_body,
            },
            {
                "role": "user",
                "content": "Peux-tu reformuler ta réponse précédente de façon plus claire ?",
            },
        ]

        await matrix_client.room_typing(room_id, typing_state=True)
        try:
            response = await generate(config, messages)
        finally:
            await matrix_client.room_typing(room_id, typing_state=False)

        await matrix_client.send_markdown_message(
            room_id,
            f"🔄 **Reformulation :**\n\n{response}",
            msgtype="m.notice",
            thread_root=thread_root,
        )

    except Exception as e:
        logger.error(f"[REACTION] Erreur reformulation: {e}")
        await matrix_client.send_markdown_message(
            room_id,
            "❌ Impossible de reformuler la réponse pour le moment.",
            msgtype="m.notice",
            thread_root=thread_root,
        )


async def _handle_save(
    room_id: str, sender: str, original_body: str, matrix_client, thread_root: Optional[str]
):
    """Sauvegarde le message dans les notes du workspace (WebDAV)."""
    try:
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        saved = await _save_to_workspace_notes(config, room_id, sender, original_body)
    except Exception as e:
        logger.warning(f"[REACTION] Impossible de sauvegarder dans le workspace: {e}")
        saved = False

    if saved:
        await matrix_client.send_markdown_message(
            room_id,
            "➕ Message sauvegardé dans vos notes de workspace.",
            msgtype="m.notice",
            thread_root=thread_root,
        )
    else:
        await matrix_client.send_markdown_message(
            room_id,
            "➕ Impossible de sauvegarder : aucun workspace configuré pour ce salon.",
            msgtype="m.notice",
            thread_root=thread_root,
        )


# ─── Helpers persistance ──────────────────────────────────────────────────────

async def _save_feedback(config, room_id: str, sender: str, event_id: str, positive: bool):
    """Persiste le feedback dans le UserContext."""
    try:
        from app.commands import get_unified_session_context
        session = await get_unified_session_context(config, room_id, sender)
        if not hasattr(session, "conversation_state"):
            return
        feedback_key = "feedback_positive" if positive else "feedback_negative"
        existing = session.conversation_state.get(feedback_key, [])
        existing.append(event_id)
        # Limiter à 50 entrées
        session.conversation_state[feedback_key] = existing[-50:]
        logger.info(f"[REACTION] Feedback {'👍' if positive else '👎'} sauvegardé pour {sender}")
    except Exception as e:
        logger.warning(f"[REACTION] _save_feedback: {e}")


async def _save_to_workspace_notes(
    config, room_id: str, sender: str, content: str
) -> bool:
    """
    Tente de sauvegarder le contenu dans un fichier notes.md sur WebDAV.
    Retourne True si succès.
    """
    try:
        from app.commands import get_context_manager
        ctx_manager = await get_context_manager(config)
        room_context = await ctx_manager.get_room_context(room_id)
        if not room_context or not room_context.webdav_context:
            return False

        webdav_ctx = room_context.webdav_context
        webdav_url = webdav_ctx.get("url") or webdav_ctx.get("webdav_url", "")
        if not webdav_url:
            return False

        # Importer le client WebDAV
        from app.services.webdav_client import WebDAVClient
        import datetime

        client = WebDAVClient(
            url=webdav_url,
            username=webdav_ctx.get("username", ""),
            password=webdav_ctx.get("password", ""),
        )

        notes_path = webdav_ctx.get("path", "/") + "notes.md"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n\n---\n*Sauvegardé le {timestamp} par {sender}*\n\n{content}"

        # Lire le fichier existant ou créer
        try:
            existing = await client.download_file(notes_path)
            new_content = existing + entry
        except Exception:
            new_content = f"# Notes Colaig\n{entry}"

        await client.upload_file(notes_path, new_content.encode("utf-8"))
        return True

    except Exception as e:
        logger.warning(f"[REACTION] _save_to_workspace_notes: {e}")
        return False
