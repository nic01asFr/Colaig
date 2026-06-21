"""
Colaig — Canal et progression du pipeline (Phase 6)

Fournit :
- resolve_channel() : fonction pure — résout ChannelFormat selon la plateforme
- ProgressReporter : envoie des messages intermédiaires formatés selon le canal
"""

from __future__ import annotations

import logging

from colaig.models import ChannelFormat, ConversationType
from colaig.protocols import MessagingProtocol

logger = logging.getLogger(__name__)


def resolve_channel(
    platform: str,
    conv_type: ConversationType = ConversationType.UNKNOWN,
) -> ChannelFormat:
    """Retourne le ChannelFormat adapté à la plateforme et au type de conversation.

    Fonction pure, sans état — appelée une fois avant le pipeline dans handlers.py.

    Args:
        platform: Identifiant de la plateforme (matrix, telegram, mcp, slack, ...).
        conv_type: Type de conversation (dm, public, channel, ...).

    Returns:
        ChannelFormat avec les capacités réelles du canal.
    """
    match platform.lower():
        case "matrix" | "tchap":
            return ChannelFormat(
                supports_html=True,
                supports_markdown=True,
                supports_tables=True,        # Matrix HTML supporte <table>
                supports_streaming=True,
                reply_style="conversational",
            )
        case "telegram":
            return ChannelFormat(
                supports_html=False,
                supports_markdown=True,
                supports_tables=False,       # MarkdownV2 Telegram ne supporte pas les tables
                max_length=4096,
                supports_streaming=False,
                reply_style="conversational",
            )
        case "mcp":
            return ChannelFormat(
                supports_html=False,
                supports_markdown=True,      # MCP clients rendent CommonMark nativement
                supports_tables=True,        # CommonMark supporte les tables GFM
                supports_streaming=True,
                reply_style="json",
            )
        case "slack":
            return ChannelFormat(
                supports_html=False,
                supports_markdown=True,
                max_length=4000,
                supports_streaming=False,
                reply_style="conversational",
            )
        case _:
            # Défaut conservateur : pas de streaming, markdown basique
            return ChannelFormat()


# Templates de messages intermédiaires par phase
_PHASE_MESSAGES: dict[str, str] = {
    "thinking":     "*Analyse de votre demande...*",
    "retrieving":   "*Recherche dans les documents...*",
    "executing":    "*Exécution en cours...*",
    "synthesizing": "*Rédaction de la réponse...*",
}


class ProgressReporter:
    """Envoie des messages intermédiaires formatés pendant le pipeline.

    Adapte le format selon ChannelFormat (markdown, plain text, longueur max).
    Si messaging est None (mode test ou pipeline synchrone), les appels sont silencieux.

    Args:
        messaging: Backend de messagerie (None = mode silencieux).
        conversation_id: Identifiant de la conversation cible.
        channel_format: Format et capacités du canal.
    """

    def __init__(
        self,
        messaging: MessagingProtocol | None,
        conversation_id: str,
        channel_format: ChannelFormat,
    ) -> None:
        self._messaging = messaging
        self._conv_id = conversation_id
        self._fmt = channel_format

    async def report(self, phase: str, message: str = "") -> None:
        """Envoie un message intermédiaire formaté pour le canal.

        Args:
            phase: Phase du pipeline ("thinking", "retrieving", "executing", "synthesizing").
            message: Message custom — si vide, utilise le template par défaut.
        """
        if not self._messaging:
            return

        text = message or _PHASE_MESSAGES.get(phase, f"[{phase}]")

        if not self._fmt.supports_markdown:
            text = text.replace("**", "").replace("*", "").replace("_", "")

        if self._fmt.max_length > 0 and len(text) > self._fmt.max_length:
            text = text[: self._fmt.max_length - 3] + "..."

        try:
            await self._messaging.send(self._conv_id, text, is_status=True)
        except Exception:
            logger.debug("ProgressReporter.report: échec envoi (ignoré)")

    async def report_tool_use(self, tool_name: str, detail: str = "") -> None:
        """Notifie l'utilisation d'un outil dans la boucle agentique.

        Args:
            tool_name: Nom de l'outil appelé.
            detail: Détail humain-lisible (ex: nom du document consulté).
        """
        if not self._messaging:
            return

        if detail:
            text = f"*{detail}...*" if self._fmt.supports_markdown else f"{detail}..."
        else:
            text = f"*{tool_name}*" if self._fmt.supports_markdown else tool_name

        if self._fmt.max_length > 0 and len(text) > self._fmt.max_length:
            text = text[: self._fmt.max_length - 3] + "..."

        try:
            await self._messaging.send(self._conv_id, text)
        except Exception:
            logger.debug("ProgressReporter.report_tool_use: échec envoi (ignoré)")
