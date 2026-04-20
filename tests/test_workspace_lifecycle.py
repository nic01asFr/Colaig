"""
Tests pour le cycle de vie des workspaces Colaig.

Couvre :
- create_workspace() : scaffold .colaig/ + config.yaml
- add_conversation_to_workspace() : liaison salon
- remove_conversation_from_workspace() : déliaison
- update_workspace_config() : modification champs
- ContextResolver.register_workspace() : cache immédiat
- ContextResolver.invalidate_conversation() : invalidation cache
- Onboarding handlers : commandes "colaig créer" / "colaig lier"
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from colaig.context.workspace import (
    _slugify,
    _workspace_to_dict,
    add_conversation_to_workspace,
    create_workspace,
    remove_conversation_from_workspace,
    update_workspace_config,
)
from colaig.context.resolver import ContextResolver
from colaig.exceptions import WorkspaceConfigError
from colaig.models import ContextMode, ConversationType, IncomingMessage, WorkspaceConfig
from colaig.messaging.handlers import MessageHandler


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    """Storage mock complet — persiste un dict in-memory."""
    files: dict[str, bytes] = {}

    storage = MagicMock()

    async def upload(path, content):
        files[path] = content

    async def download(path):
        if path not in files:
            from colaig.exceptions import StorageFileNotFoundError
            raise StorageFileNotFoundError(f"not found: {path}")
        return files[path]

    async def exists(path):
        return path in files

    async def mkdir(path):
        pass  # No-op comme Bigfolder

    async def list_files(path, recursive=False):
        return []

    async def delete(path):
        files.pop(path, None)

    storage.upload = AsyncMock(side_effect=upload)
    storage.download = AsyncMock(side_effect=download)
    storage.exists = AsyncMock(side_effect=exists)
    storage.mkdir = AsyncMock(side_effect=mkdir)
    storage.list_files = AsyncMock(side_effect=list_files)
    storage.delete = AsyncMock(side_effect=delete)
    storage._files = files  # accès direct pour assertions
    return storage


@pytest.fixture
def resolver(mock_storage):
    return ContextResolver(storage=mock_storage, cache_ttl=60)


# =============================================================================
# TestSlugify
# =============================================================================


class TestSlugify:

    def test_simple(self):
        assert _slugify("Espace RH") == "espace-rh"

    def test_accents(self):
        assert _slugify("Gestion générale") == "gestion-generale"

    def test_special_chars(self):
        # _slugify collapse les tirets multiples → pas de tirets consécutifs
        result = _slugify("Équipe & Projets !")
        assert all(c.isalnum() or c in "-_" for c in result)
        assert "--" not in result  # tirets multiples collapsés

    def test_empty_fallback(self):
        assert _slugify("") == "workspace"

    def test_already_slug(self):
        assert _slugify("mon-workspace") == "mon-workspace"


# =============================================================================
# TestCreateWorkspace
# =============================================================================


class TestCreateWorkspace:

    async def test_creates_config_yaml(self, mock_storage):
        ws = await create_workspace(mock_storage, "/equipe-rh/", "Équipe RH")
        config_path = "/equipe-rh/.colaig/config.yaml"
        assert await mock_storage.exists(config_path)

    async def test_config_yaml_content(self, mock_storage):
        ws = await create_workspace(
            mock_storage, "/equipe-rh/", "Équipe RH",
            description="Service des ressources humaines",
            tone="formal",
            language="fr",
        )
        config_path = "/equipe-rh/.colaig/config.yaml"
        raw = await mock_storage.download(config_path)
        data = yaml.safe_load(raw.decode())
        assert data["name"] == "Équipe RH"
        assert data["description"] == "Service des ressources humaines"
        assert data["tone"] == "formal"
        assert data["language"] == "fr"
        assert data["rag_enabled"] is True

    async def test_workspace_id_auto_from_name(self, mock_storage):
        ws = await create_workspace(mock_storage, "/equipe-rh/", "Équipe RH")
        assert ws.workspace_id == "equipe-rh"

    async def test_workspace_id_explicit(self, mock_storage):
        ws = await create_workspace(
            mock_storage, "/equipe-rh/", "Équipe RH", workspace_id="rh-2026"
        )
        assert ws.workspace_id == "rh-2026"

    async def test_conversations_saved(self, mock_storage):
        ws = await create_workspace(
            mock_storage, "/equipe-rh/", "Équipe RH",
            conversations=["!abc:tchap.gouv.fr", "!xyz:tchap.gouv.fr"],
        )
        assert "!abc:tchap.gouv.fr" in ws.conversations
        assert "!xyz:tchap.gouv.fr" in ws.conversations

    async def test_scaffold_directories_created(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        mkdir_calls = [str(c.args[0]) for c in mock_storage.mkdir.call_args_list]
        assert any(".colaig/" in p for p in mkdir_calls)
        assert any("indexes/" in p for p in mkdir_calls)
        assert any("conversations/" in p for p in mkdir_calls)

    async def test_idempotent_returns_existing(self, mock_storage):
        ws1 = await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws2 = await create_workspace(mock_storage, "/equipe-rh/", "Nouveau nom")
        # Doit retourner la config existante, pas écraser
        assert ws2.name == ws1.name

    async def test_returns_workspace_config(self, mock_storage):
        ws = await create_workspace(mock_storage, "/equipe-rh/", "Équipe RH")
        assert isinstance(ws, WorkspaceConfig)
        assert ws.storage_path == "/equipe-rh/"

    async def test_index_path_set(self, mock_storage):
        ws = await create_workspace(mock_storage, "/equipe-rh/", "RH")
        assert ws.index_path == "/equipe-rh/.colaig/indexes/"

    async def test_storage_error_raises_workspace_config_error(self, mock_storage):
        mock_storage.exists = AsyncMock(return_value=False)
        mock_storage.mkdir = AsyncMock(side_effect=Exception("storage unreachable"))
        with pytest.raises(WorkspaceConfigError):
            await create_workspace(mock_storage, "/equipe-rh/", "RH")


# =============================================================================
# TestAddConversation
# =============================================================================


class TestAddConversation:

    async def test_adds_conversation(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws = await add_conversation_to_workspace(
            mock_storage, "/equipe-rh/", "!new-room:tchap.gouv.fr"
        )
        assert "!new-room:tchap.gouv.fr" in ws.conversations

    async def test_persists_to_config_yaml(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        await add_conversation_to_workspace(
            mock_storage, "/equipe-rh/", "!room:tchap.gouv.fr"
        )
        raw = await mock_storage.download("/equipe-rh/.colaig/config.yaml")
        data = yaml.safe_load(raw.decode())
        assert "!room:tchap.gouv.fr" in data["conversations"]

    async def test_idempotent(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH",
                               conversations=["!room:tchap.gouv.fr"])
        ws = await add_conversation_to_workspace(
            mock_storage, "/equipe-rh/", "!room:tchap.gouv.fr"
        )
        assert ws.conversations.count("!room:tchap.gouv.fr") == 1

    async def test_multiple_conversations(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        await add_conversation_to_workspace(mock_storage, "/equipe-rh/", "!r1:srv")
        ws = await add_conversation_to_workspace(mock_storage, "/equipe-rh/", "!r2:srv")
        assert "!r1:srv" in ws.conversations
        assert "!r2:srv" in ws.conversations

    async def test_raises_if_workspace_missing(self, mock_storage):
        with pytest.raises(WorkspaceConfigError):
            await add_conversation_to_workspace(
                mock_storage, "/nonexistent/", "!room:srv"
            )


# =============================================================================
# TestRemoveConversation
# =============================================================================


class TestRemoveConversation:

    async def test_removes_conversation(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH",
                               conversations=["!r1:srv", "!r2:srv"])
        ws = await remove_conversation_from_workspace(mock_storage, "/equipe-rh/", "!r1:srv")
        assert "!r1:srv" not in ws.conversations
        assert "!r2:srv" in ws.conversations

    async def test_persists_removal(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH",
                               conversations=["!r1:srv"])
        await remove_conversation_from_workspace(mock_storage, "/equipe-rh/", "!r1:srv")
        raw = await mock_storage.download("/equipe-rh/.colaig/config.yaml")
        data = yaml.safe_load(raw.decode())
        assert "!r1:srv" not in data["conversations"]

    async def test_noop_if_absent(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH",
                               conversations=["!r1:srv"])
        ws = await remove_conversation_from_workspace(
            mock_storage, "/equipe-rh/", "!nonexistent:srv"
        )
        assert ws.conversations == ["!r1:srv"]


# =============================================================================
# TestUpdateWorkspaceConfig
# =============================================================================


class TestUpdateWorkspaceConfig:

    async def test_updates_name(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws = await update_workspace_config(mock_storage, "/equipe-rh/", name="Direction RH")
        assert ws.name == "Direction RH"

    async def test_updates_system_prompt(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws = await update_workspace_config(
            mock_storage, "/equipe-rh/",
            system_prompt="Tu es l'assistant RH de la DINUM."
        )
        assert ws.system_prompt == "Tu es l'assistant RH de la DINUM."

    async def test_updates_multiple_fields(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws = await update_workspace_config(
            mock_storage, "/equipe-rh/",
            tone="formal", language="en", max_results=10,
        )
        assert ws.tone == "formal"
        assert ws.language == "en"
        assert ws.max_results == 10

    async def test_persists_to_yaml(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        await update_workspace_config(mock_storage, "/equipe-rh/", name="Nouveau nom")
        raw = await mock_storage.download("/equipe-rh/.colaig/config.yaml")
        data = yaml.safe_load(raw.decode())
        assert data["name"] == "Nouveau nom"

    async def test_unknown_field_ignored(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        # Ne doit pas lever d'exception
        ws = await update_workspace_config(
            mock_storage, "/equipe-rh/", champ_inconnu="valeur"
        )
        assert isinstance(ws, WorkspaceConfig)

    async def test_raises_if_workspace_missing(self, mock_storage):
        with pytest.raises(WorkspaceConfigError):
            await update_workspace_config(mock_storage, "/nonexistent/", name="X")

    async def test_rag_enabled_false(self, mock_storage):
        await create_workspace(mock_storage, "/equipe-rh/", "RH")
        ws = await update_workspace_config(mock_storage, "/equipe-rh/", rag_enabled=False)
        assert ws.rag_enabled is False


# =============================================================================
# TestResolverRegisterWorkspace
# =============================================================================


class TestResolverRegisterWorkspace:

    async def test_register_adds_to_cache(self, resolver):
        ws = WorkspaceConfig(
            workspace_id="new-ws", name="New", storage_path="/new/",
            conversations=["!room:srv"],
        )
        await resolver.register_workspace(ws)
        assert any(w.workspace_id == "new-ws" for w in resolver.workspaces)

    async def test_register_updates_conversation_mapping(self, resolver):
        ws = WorkspaceConfig(
            workspace_id="new-ws", name="New", storage_path="/new/",
            conversations=["!room:srv"],
        )
        await resolver.register_workspace(ws)
        assert resolver._conversation_mapping.get("!room:srv") is ws

    async def test_register_replaces_existing(self, resolver):
        ws1 = WorkspaceConfig(
            workspace_id="ws", name="V1", storage_path="/ws/",
            conversations=["!r1:srv"],
        )
        ws2 = WorkspaceConfig(
            workspace_id="ws", name="V2", storage_path="/ws/",
            conversations=["!r1:srv", "!r2:srv"],
        )
        await resolver.register_workspace(ws1)
        await resolver.register_workspace(ws2)
        cached = next(w for w in resolver.workspaces if w.workspace_id == "ws")
        assert cached.name == "V2"
        assert len([w for w in resolver.workspaces if w.workspace_id == "ws"]) == 1

    def test_invalidate_conversation_removes_mapping(self, resolver):
        ws = WorkspaceConfig(
            workspace_id="ws", name="WS", storage_path="/ws/",
            conversations=["!r1:srv"],
        )
        resolver._conversation_mapping["!r1:srv"] = ws
        resolver.invalidate_conversation("!r1:srv")
        assert "!r1:srv" not in resolver._conversation_mapping

    def test_invalidate_nonexistent_is_noop(self, resolver):
        # Ne doit pas lever d'exception
        resolver.invalidate_conversation("!nonexistent:srv")


# =============================================================================
# TestOnboardingHandlers
# =============================================================================


def make_message(body: str, conversation_id: str = "!unknown:srv",
                 is_reply: bool = False) -> IncomingMessage:
    return IncomingMessage(
        user_id="@user:tchap.gouv.fr",
        conversation_id=conversation_id,
        body=body,
        conversation_type=ConversationType.PUBLIC,
        is_reply=is_reply,
    )


def make_handler(storage, resolver):
    """Construit un MessageHandler minimal pour tester l'onboarding."""
    messaging = MagicMock()
    messaging.send = AsyncMock()
    messaging.send_typing = AsyncMock()

    retriever = MagicMock()
    generator = MagicMock()

    return MessageHandler(
        messaging=messaging,
        resolver=resolver,
        retriever=retriever,
        generator=generator,
        storage=storage,
    ), messaging


class TestOnboarding:

    async def test_unknown_room_gets_onboarding_message(self, mock_storage, resolver):
        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("Bonjour !")
        await handler.handle_message(msg)
        messaging.send.assert_called_once()
        call_text = messaging.send.call_args[0][1]
        assert "colaig_create_workspace" in call_text or "colaig créer" in call_text.lower() or ".colaig" in call_text

    async def test_reply_in_unknown_room_not_onboarding(self, mock_storage, resolver):
        """Un message is_reply dans un salon inconnu ne déclenche pas l'onboarding."""
        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("Merci", is_reply=True)

        # Le generator doit lever pour qu'on ne plante pas
        handler._generator.generate = AsyncMock(side_effect=Exception("no generator"))
        # On vérifie juste qu'on ne passe pas par l'onboarding
        try:
            await handler.handle_message(msg)
        except Exception:
            pass
        # send_typing doit avoir été appelé (pipeline tenté)
        assert messaging.send_typing.called

    async def test_create_command_creates_workspace(self, mock_storage, resolver):
        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("colaig créer Équipe Support")
        await handler.handle_message(msg)

        # Le workspace doit avoir été créé sur le storage
        config_path = "/equipe-support/.colaig/config.yaml"
        assert await mock_storage.exists(config_path)

        # Confirmation envoyée
        messaging.send.assert_called()
        call_text = messaging.send.call_args[0][1]
        assert "✅" in call_text

    async def test_create_command_links_conversation(self, mock_storage, resolver):
        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("colaig créer Mon Espace", conversation_id="!newroom:srv")
        await handler.handle_message(msg)

        # Le salon doit être dans le resolver
        mapped_ws = resolver._conversation_mapping.get("!newroom:srv")
        assert mapped_ws is not None
        assert "!newroom:srv" in mapped_ws.conversations

    async def test_link_command_existing_workspace(self, mock_storage, resolver):
        # Créer un workspace d'abord
        ws = await create_workspace(mock_storage, "/juridique/", "Juridique",
                                    workspace_id="juridique")
        await resolver.register_workspace(ws)

        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("colaig lier juridique", conversation_id="!salon-jur:srv")
        await handler.handle_message(msg)

        # Le salon doit être lié
        raw = await mock_storage.download("/juridique/.colaig/config.yaml")
        data = yaml.safe_load(raw.decode())
        assert "!salon-jur:srv" in data["conversations"]
        assert "✅" in messaging.send.call_args[0][1]

    async def test_link_command_unknown_workspace_sends_error(self, mock_storage, resolver):
        handler, messaging = make_handler(mock_storage, resolver)
        msg = make_message("colaig lier inexistant")
        await handler.handle_message(msg)
        call_text = messaging.send.call_args[0][1]
        assert "❌" in call_text

    async def test_link_command_no_id_lists_workspaces(self, mock_storage, resolver):
        ws = await create_workspace(mock_storage, "/rh/", "RH", workspace_id="rh")
        await resolver.register_workspace(ws)

        handler, messaging = make_handler(mock_storage, resolver)
        # Important : utiliser un salon différent pour qu'il soit en mode CHATBOT
        msg = make_message("colaig lier", conversation_id="!autre-salon:srv")
        await handler.handle_message(msg)
        call_text = messaging.send.call_args[0][1]
        # Le message doit mentionner le workspace ou l'usage
        assert "rh" in call_text or "workspace_id" in call_text or "Usage" in call_text
