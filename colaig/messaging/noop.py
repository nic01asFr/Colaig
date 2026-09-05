"""
Colaig — NoopMessaging

Implémentation MessagingProtocol vide (no-op).
Utilisée avec MESSAGING_BACKEND=none pour le mode MCP-only / dev
(serveur web + MCP exposés sans canal de messagerie actif).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class NoopMessaging:
    """MessagingProtocol vide — ne connecte à aucun canal.

    connect() et run() ne font rien.
    send() logge un warning, send_typing() ne fait rien.
    on_message() enregistre le handler mais il ne sera jamais appelé.
    """

    def __init__(self) -> None:
        self._handler: Callable | None = None
        logger.info("NoopMessaging actif (MESSAGING_BACKEND=none) — aucun canal de messagerie")

    def on_message(self, callback: Callable) -> None:
        self._handler = callback

    async def connect(self) -> None:
        pass

    async def run(self) -> None:
        # Boucle infinie non-bloquante — laisse les autres tâches tourner
        while True:
            await asyncio.sleep(3600)

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        logger.warning("NoopMessaging.send ignoré (conversation=%s, text_chars=%d)", conversation_id, len(text))

    async def send_typing(
        self, conversation_id: str, typing: bool = True, timeout: int = 10000
    ) -> None:
        # Signature complète du Protocol. Elle était `(conversation_id, **kwargs)` :
        # `**kwargs` n'absorbe que les mots-clés, donc `send_typing(conv, True)` levait
        # un TypeError — sur ce backend uniquement. Les appelants actuels passent tous
        # `typing=` en mot-clé, le piège n'était donc pas déclenché ; il attendait.
        pass
