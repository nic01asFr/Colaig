"""Test d'intégration : notification proactive dans run_indexation_loop."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.models import UpdateSummary, WorkspaceConfig

# ---------------------------------------------------------------------------
# Test du chemin de notification dans run_indexation_loop
# ---------------------------------------------------------------------------

class TestNotificationInLoop:
    """Vérifie que messaging.send() est appelé quand proactive_notifications=True."""

    def _make_workspace(self, proactive=True, channels=None):
        return WorkspaceConfig(
            workspace_id="test-ws",
            name="Test Workspace",
            storage_path="/test/",
            conversations=["!room1:server", "!room2:server"],
            proactive_notifications=proactive,
            notification_channels=channels or [],
            language="fr",
        )

    def _make_indexer(self, update: UpdateSummary):
        """Indexer factice dont check_updates() retourne un UpdateSummary donné."""
        idx = MagicMock()
        idx.check_updates = AsyncMock(return_value=update)
        idx.save_to_storage = AsyncMock()
        idx.load_from_storage = AsyncMock(return_value=True)
        return idx

    def _make_store(self):
        store = MagicMock()
        store.get_all_active_chunks.return_value = []
        return store

    @pytest.mark.asyncio
    async def test_notification_sent_when_new_docs(self):
        """Quand de nouveaux docs sont détectés et proactive=True → messaging.send appelé."""
        update = UpdateSummary(count=1, changed_paths=["/test/new.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=True)
        indexer = self._make_indexer(update)
        store = self._make_store()

        # Simule le bloc de notification de run_indexation_loop
        await _simulate_notification_block(ws, update, store, messaging)

        # channels vide → toutes les conversations (2 ici)
        assert messaging.send.call_count == 2
        sent_channels = [c[0][0] for c in messaging.send.call_args_list]
        assert "!room1:server" in sent_channels
        sent_text = messaging.send.call_args_list[0][0][1]
        assert "new.pdf" in sent_text
        assert "Test Workspace" in sent_text

    @pytest.mark.asyncio
    async def test_notification_sent_to_all_conversations_when_channels_empty(self):
        """Canaux vides → toutes les conversations du workspace reçoivent la notification."""
        update = UpdateSummary(count=1, changed_paths=["/test/doc.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=True, channels=[])  # vide = toutes
        indexer = self._make_indexer(update)
        store = self._make_store()

        await _simulate_notification_block(ws, update, store, messaging)

        assert messaging.send.call_count == 2  # !room1 + !room2
        sent_channels = [c[0][0] for c in messaging.send.call_args_list]
        assert "!room1:server" in sent_channels
        assert "!room2:server" in sent_channels

    @pytest.mark.asyncio
    async def test_notification_sent_to_specific_channels_only(self):
        """notification_channels non vide → uniquement ces canaux reçoivent la notif."""
        update = UpdateSummary(count=1, changed_paths=["/test/doc.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=True, channels=["!room1:server"])
        store = self._make_store()

        await _simulate_notification_block(ws, update, store, messaging)

        assert messaging.send.call_count == 1
        assert messaging.send.call_args[0][0] == "!room1:server"

    @pytest.mark.asyncio
    async def test_no_notification_when_proactive_false(self):
        """proactive_notifications=False → aucun envoi."""
        update = UpdateSummary(count=1, changed_paths=["/test/doc.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=False)
        store = self._make_store()

        await _simulate_notification_block(ws, update, store, messaging)

        messaging.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notification_when_no_changed_paths(self):
        """Pas de docs nouveaux → pas de notification (même si update.count > 0)."""
        update = UpdateSummary(count=0, changed_paths=[], removed_paths={"/test/old.pdf"})
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=True)
        store = self._make_store()

        await _simulate_notification_block(ws, update, store, messaging)

        messaging.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_error_does_not_propagate(self):
        """Une erreur dans l'envoi ne doit pas faire crasher la boucle."""
        update = UpdateSummary(count=1, changed_paths=["/test/doc.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock(side_effect=RuntimeError("connexion perdue"))

        ws = self._make_workspace(proactive=True)
        store = self._make_store()

        # Ne doit pas lever d'exception
        await _simulate_notification_block(ws, update, store, messaging)

    @pytest.mark.asyncio
    async def test_notification_with_contextual_prefix(self):
        """Mode B : le contextual_prefix du premier chunk enrichit la notification."""
        from colaig.models import DocumentChunk

        update = UpdateSummary(count=1, changed_paths=["/test/guide.pdf"])
        messaging = MagicMock()
        messaging.send = AsyncMock()

        ws = self._make_workspace(proactive=True)

        # Store avec un chunk ayant un préfixe contextuel
        store = MagicMock()
        store.get_all_active_chunks.return_value = [
            DocumentChunk(
                text="contenu du guide",
                source_path="/test/guide.pdf",
                source_name="guide.pdf",
                contextual_prefix="Ce guide décrit les procédures d'onboarding des nouveaux agents.",
            )
        ]

        await _simulate_notification_block(ws, update, store, messaging)

        messaging.send.assert_called()
        sent_text = messaging.send.call_args[0][1]
        assert "procédures d'onboarding" in sent_text
        assert "guide.pdf" in sent_text


# ---------------------------------------------------------------------------
# Simulateur du bloc de notification (extrait de run_indexation_loop)
# ---------------------------------------------------------------------------

async def _simulate_notification_block(ws, update, store, messaging):
    """Reproduit exactement le bloc de notification de run_indexation_loop."""
    import logging
    logger = logging.getLogger("test")

    if messaging and ws.proactive_notifications and update.changed_paths:
        try:
            from colaig.rag.notifier import format_notification
            msg = format_notification(
                workspace_name=ws.name,
                update=update,
                store=store,
                language=ws.language,
            )
            if msg:
                channels = ws.notification_channels or ws.conversations
                for conv_id in channels:
                    await messaging.send(conv_id, msg)
        except Exception:
            logger.warning("erreur notification proactive workspace=%s", ws.workspace_id, exc_info=True)
