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
    send() et send_typing() loggent un warning.
    on_message() enregistre le handler mais il ne sera jamais appelé.
    """

    def __init__(self) -> None:
        self._handler: Callable | None = None
        logger.info("NoopMessaging actif (MESSAGING_BACKEND=none) — aucun canal de messagerie")

    def on_message(self, handler: Callable) -> None:
        self._handler = handler

    async def connect(self) -> None:
        pass

    async def run(self) -> None:
        # Boucle infinie non-bloquante — laisse les autres tâches tourner
        while True:
            await asyncio.sleep(3600)

    async def send(self, conversation_id: str, text: str, **kwargs) -> None:
        logger.warning("NoopMessaging.send ignoré (conversation=%s, text_chars=%d)", conversation_id, len(text))

    async def send_typing(self, conversation_id: str, **kwargs) -> None:
        pass
