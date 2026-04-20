"""Tests pour colaig/context/layers.py — Construction des couches contextuelles."""

import pytest

from colaig.context.layers import (
    build_context,
    _extract_domain,
    load_conversation_history,
    save_conversation_history,
)
from colaig.models import ContextMode, ConversationType, IncomingMessage, WorkspaceConfig


@pytest.fixture
def workspace() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id="test",
        name="Test",
        storage_path="/test/",
        system_prompt="Tu es un assistant de test.",
        tone="professional",
        tools_enabled=["search", "summarize"],
    )


@pytest.fixture
def message() -> IncomingMessage:
    return IncomingMessage(
        user_id="@jean.dupont-education.gouv.fr:agent.tchap.gouv.fr",
        conversation_id="!room:test.local",
        body="Bonjour",
        conversation_type=ConversationType.PRIVATE,
        display_name="Jean Dupont",
    )


class TestBuildContext:
    """Tests de construction du contexte."""

    def test_assistant_mode(self, workspace, message):
        ctx = build_context(workspace, message, ContextMode.ASSISTANT)
        assert ctx.mode == ContextMode.ASSISTANT
        assert ctx.workspace == workspace
        assert "assistant de test" in ctx.system_prompt

    def test_chatbot_mode_no_workspace(self, message):
        ctx = build_context(None, message, ContextMode.CHATBOT)
        assert ctx.mode == ContextMode.CHATBOT
        assert "Colaig" in ctx.system_prompt

    def test_personal_mode(self, message):
        ctx = build_context(None, message, ContextMode.PERSONAL)
        assert ctx.mode == ContextMode.PERSONAL
        assert "personnel" in ctx.system_prompt

    def test_tools_from_workspace(self, workspace, message):
        ctx = build_context(workspace, message, ContextMode.ASSISTANT)
        assert "search" in ctx.available_tools

    def test_no_tools_without_workspace(self, message):
        ctx = build_context(None, message, ContextMode.CHATBOT)
        assert ctx.available_tools == []

    def test_conversation_history_truncated(self, workspace, message):
        history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        ctx = build_context(workspace, message, ContextMode.ASSISTANT, conversation_history=history)
        assert len(ctx.conversation_history) == 10  # DEFAULT_HISTORY_LENGTH

    def test_user_profile_extracted(self, workspace, message):
        ctx = build_context(workspace, message, ContextMode.ASSISTANT)
        assert ctx.user_display_name == "Jean Dupont"
        assert ctx.user_domain == "education.gouv.fr"

    def test_tone_casual(self, message):
        ws = WorkspaceConfig(
            workspace_id="t", name="T", storage_path="/t/",
            system_prompt="Base prompt.", tone="casual",
        )
        ctx = build_context(ws, message, ContextMode.ASSISTANT)
        assert "décontracté" in ctx.system_prompt

    def test_tone_professional_no_extra(self, workspace, message):
        ctx = build_context(workspace, message, ContextMode.ASSISTANT)
        # tone=professional → pas d'instruction supplémentaire
        assert "décontracté" not in ctx.system_prompt


class TestExtractDomain:
    """Tests de l'extraction de domaine depuis user_id Matrix."""

    def test_tchap_domain(self):
        assert _extract_domain("@jean.dupont-education.gouv.fr:agent.tchap.gouv.fr") == "education.gouv.fr"

    def test_tchap_domain_multi_segments(self):
        assert _extract_domain("@jean.marie-dupont-interieur.gouv.fr:agent.tchap.gouv.fr") == "interieur.gouv.fr"

    def test_simple_matrix_domain(self):
        assert _extract_domain("@user:matrix.org") == "matrix.org"

    def test_email_format(self):
        assert _extract_domain("user@example.com") == "example.com"

    def test_no_colon_no_at(self):
        assert _extract_domain("invalid") == ""

    def test_no_dash_in_localpart(self):
        # Pas de domaine encodé dans le localpart → retourne le domaine Matrix
        assert _extract_domain("@jean.dupont:agent.tchap.gouv.fr") == "agent.tchap.gouv.fr"


class TestConversationHistory:
    """Tests du chargement/sauvegarde de l'historique."""

    async def test_load_empty_history(self, mock_storage):
        """Pas d'historique → liste vide."""
        result = await load_conversation_history(mock_storage, "/ws/", "!room:test")
        assert result == []

    async def test_load_existing_history(self, mock_storage):
        """Charge un historique existant."""
        import json
        history = [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Bonjour !"},
        ]
        mock_storage.add_file(
            "/ws/.colaig/conversations/_room_test.json",
            json.dumps(history).encode("utf-8"),
        )
        result = await load_conversation_history(mock_storage, "/ws/", "!room:test")
        assert len(result) == 2
        assert result[0]["role"] == "user"

    async def test_load_history_truncated(self, mock_storage):
        """L'historique est tronqué au max_messages."""
        import json
        history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        mock_storage.add_file(
            "/ws/.colaig/conversations/_room_test.json",
            json.dumps(history).encode("utf-8"),
        )
        result = await load_conversation_history(mock_storage, "/ws/", "!room:test", max_messages=5)
        assert len(result) == 5

    async def test_load_history_no_workspace_path(self, mock_storage):
        """Pas de workspace_path → liste vide."""
        result = await load_conversation_history(mock_storage, "", "!room:test")
        assert result == []

    async def test_save_and_load_roundtrip(self, mock_storage):
        """Save puis load → données identiques."""
        history = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Réponse"},
        ]
        await save_conversation_history(mock_storage, "/ws/", "!room:test", history)
        result = await load_conversation_history(mock_storage, "/ws/", "!room:test")
        assert result == history
