"""Tests pour colaig/context/resolver.py — Context Resolver."""

import pytest

from colaig.context.resolver import ContextResolver
from colaig.models import (
    ContextMode,
    ConversationType,
    IncomingMessage,
    StorageFile,
)


@pytest.fixture
def resolver_with_workspace(mock_storage_with_workspace):
    """Resolver avec un workspace pré-configuré dans le storage."""
    return ContextResolver(mock_storage_with_workspace, cache_ttl=0)


class TestResolve:
    """Tests de résolution de contexte."""

    async def test_resolve_mapped_conversation(self, resolver_with_workspace, test_message):
        """Un conversation_id mappé à un workspace → mode ASSISTANT."""
        ctx = await resolver_with_workspace.resolve(test_message)
        assert ctx.mode == ContextMode.ASSISTANT
        assert ctx.workspace is not None
        assert ctx.workspace.workspace_id == "test-workspace"

    async def test_resolve_dm_mode_personal(self, resolver_with_workspace, test_dm_message):
        """Un DM non mappé → mode PERSONAL."""
        ctx = await resolver_with_workspace.resolve(test_dm_message)
        assert ctx.mode == ContextMode.PERSONAL

    async def test_resolve_unknown_conversation_chatbot(self, resolver_with_workspace):
        """Une conversation inconnue → mode CHATBOT."""
        msg = IncomingMessage(
            user_id="@user:test.local",
            conversation_id="!unknown:test.local",
            body="Bonjour",
            conversation_type=ConversationType.PUBLIC,
        )
        ctx = await resolver_with_workspace.resolve(msg)
        assert ctx.mode == ContextMode.CHATBOT

    async def test_resolve_private_unknown_chatbot(self, resolver_with_workspace):
        """Une conversation privée non mappée → mode CHATBOT."""
        msg = IncomingMessage(
            user_id="@user:test.local",
            conversation_id="!private_unknown:test.local",
            body="Test",
            conversation_type=ConversationType.PRIVATE,
        )
        ctx = await resolver_with_workspace.resolve(msg)
        assert ctx.mode == ContextMode.CHATBOT

    async def test_resolve_system_prompt_set(self, resolver_with_workspace, test_message):
        """Le system_prompt est défini dans le contexte résolu."""
        ctx = await resolver_with_workspace.resolve(test_message)
        assert ctx.system_prompt != ""

    async def test_resolve_user_profile(self, resolver_with_workspace, test_message):
        """Le profil utilisateur est extrait."""
        ctx = await resolver_with_workspace.resolve(test_message)
        assert ctx.user_display_name == "Jean Dupont"


class TestRefreshCache:
    """Tests du cache des workspaces."""

    async def test_refresh_populates_workspaces(self, mock_storage_with_workspace):
        """refresh_cache charge les workspaces depuis le storage."""
        mock_storage_with_workspace.metadata["/espace-test/"] = StorageFile(
            path="/espace-test/", name="espace-test", is_directory=True,
        )
        resolver = ContextResolver(mock_storage_with_workspace, cache_ttl=0)
        await resolver.refresh_cache()
        assert len(resolver.workspaces) >= 1

    async def test_cache_conversation_mapping(self, mock_storage_with_workspace):
        """Après refresh, le mapping conversation→workspace est en cache."""
        mock_storage_with_workspace.metadata["/espace-test/"] = StorageFile(
            path="/espace-test/", name="espace-test", is_directory=True,
        )
        resolver = ContextResolver(mock_storage_with_workspace, cache_ttl=0)
        await resolver.refresh_cache()
        assert "!test_room:test.local" in resolver._conversation_mapping

    async def test_empty_storage(self, mock_storage):
        """Storage vide → pas de workspaces."""
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        await resolver.refresh_cache()
        assert resolver.workspaces == []


class TestDMPersonalMode:
    """Tests du mode PERSONAL via DM (workspace personnel)."""

    async def test_dm_creates_personal_workspace(self, mock_storage):
        """Un DM crée un workspace personnel avec workspace_id=personal-{slug}."""
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        msg = IncomingMessage(
            user_id="@alice:tchap.fr",
            conversation_id="!dm_alice:tchap.fr",
            body="Bonjour",
            conversation_type=ConversationType.DM,
        )
        ctx = await resolver.resolve(msg)
        assert ctx.mode == ContextMode.PERSONAL
        assert ctx.workspace is not None
        assert ctx.workspace.workspace_id.startswith("personal-")

    async def test_dm_workspace_bound_to_user(self, mock_storage):
        """Le workspace personnel créé appartient à l'utilisateur (user_ids inclut user_id)."""
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        msg = IncomingMessage(
            user_id="@bob:tchap.fr",
            conversation_id="!dm_bob:tchap.fr",
            body="Bonjour",
            conversation_type=ConversationType.DM,
        )
        ctx = await resolver.resolve(msg)
        assert ctx.workspace is not None
        assert "@bob:tchap.fr" in ctx.workspace.user_ids

    async def test_dm_second_resolve_reuses_workspace(self, mock_storage):
        """Deux résolutions DM pour le même user → même workspace_id."""
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        msg = IncomingMessage(
            user_id="@carol:tchap.fr",
            conversation_id="!dm_carol:tchap.fr",
            body="Première question",
            conversation_type=ConversationType.DM,
        )
        ctx1 = await resolver.resolve(msg)
        msg2 = IncomingMessage(
            user_id="@carol:tchap.fr",
            conversation_id="!dm_carol:tchap.fr",
            body="Deuxième question",
            conversation_type=ConversationType.DM,
        )
        ctx2 = await resolver.resolve(msg2)
        assert ctx1.workspace.workspace_id == ctx2.workspace.workspace_id


class TestDefaultWorkspace:
    """Tests du workspace par défaut (mode CHATBOT avec default_workspace_id)."""

    async def test_chatbot_uses_default_workspace(self, mock_storage_with_workspace, test_workspace):
        """En mode CHATBOT, le resolver utilise le workspace par défaut si configuré."""
        resolver = ContextResolver(
            mock_storage_with_workspace,
            cache_ttl=0,
            default_workspace_id=test_workspace.workspace_id,
        )
        await resolver.refresh_cache()

        msg = IncomingMessage(
            user_id="@stranger:test.local",
            conversation_id="!unknown_public:test.local",
            body="Bonjour",
            conversation_type=ConversationType.PUBLIC,
        )
        ctx = await resolver.resolve(msg)
        assert ctx.mode == ContextMode.CHATBOT
        assert ctx.workspace is not None
        assert ctx.workspace.workspace_id == test_workspace.workspace_id

    async def test_chatbot_no_default_workspace(self, mock_storage):
        """En mode CHATBOT sans default_workspace_id → workspace vide (pas d'erreur)."""
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        msg = IncomingMessage(
            user_id="@stranger:test.local",
            conversation_id="!unknown:test.local",
            body="Bonjour",
            conversation_type=ConversationType.PUBLIC,
        )
        ctx = await resolver.resolve(msg)
        assert ctx.mode == ContextMode.CHATBOT
        # Workspace par défaut synthétique créé (pas None)
        assert ctx.workspace is not None

    async def test_chatbot_default_workspace_not_found(self, mock_storage):
        """default_workspace_id configuré mais introuvable → workspace synthétique."""
        resolver = ContextResolver(
            mock_storage,
            cache_ttl=0,
            default_workspace_id="workspace-inexistant",
        )
        msg = IncomingMessage(
            user_id="@stranger:test.local",
            conversation_id="!unknown:test.local",
            body="Bonjour",
            conversation_type=ConversationType.PUBLIC,
        )
        ctx = await resolver.resolve(msg)
        assert ctx.mode == ContextMode.CHATBOT
        assert ctx.workspace is not None
