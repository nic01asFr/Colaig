"""Tests pour colaig/context/workspace.py — Gestion des workspaces."""

import pytest

from colaig.context.workspace import (
    create_default_workspace,
    find_workspace_for_conversation,
    list_workspaces,
    load_workspace,
)
from colaig.exceptions import WorkspaceConfigError


class TestLoadWorkspace:
    """Tests du chargement de workspace depuis le storage."""

    async def test_load_workspace_success(self, mock_storage_with_workspace, test_workspace):
        """Charge un workspace valide."""
        ws = await load_workspace(mock_storage_with_workspace, test_workspace.storage_path)
        assert ws.workspace_id == test_workspace.workspace_id
        assert ws.name == test_workspace.name

    async def test_load_workspace_missing_file(self, mock_storage):
        """Lève WorkspaceConfigError si config.yaml absent."""
        with pytest.raises(WorkspaceConfigError):
            await load_workspace(mock_storage, "/inexistant/")

    async def test_load_workspace_invalid_yaml(self, mock_storage):
        """Lève WorkspaceConfigError si YAML invalide."""
        mock_storage.add_file("/ws/.colaig/config.yaml", b"[invalid: yaml: {{")
        with pytest.raises(WorkspaceConfigError):
            await load_workspace(mock_storage, "/ws/")

    async def test_load_workspace_empty_yaml(self, mock_storage):
        """Lève WorkspaceConfigError si YAML vide."""
        mock_storage.add_file("/ws/.colaig/config.yaml", b"")
        with pytest.raises(WorkspaceConfigError):
            await load_workspace(mock_storage, "/ws/")

    async def test_load_workspace_defaults(self, mock_storage):
        """Les champs manquants prennent les valeurs par défaut."""
        mock_storage.add_file("/ws/.colaig/config.yaml", b"workspace_id: test-ws\nname: Test")
        ws = await load_workspace(mock_storage, "/ws/")
        assert ws.workspace_id == "test-ws"
        assert ws.rag_enabled is True  # default
        assert ws.tone == "professional"  # default
        assert ws.language == "fr"  # default

    async def test_load_workspace_index_path_resolved(self, mock_storage):
        """Le index_path est résolu automatiquement."""
        mock_storage.add_file("/ws/.colaig/config.yaml", b"workspace_id: test-ws\nname: Test")
        ws = await load_workspace(mock_storage, "/ws/")
        assert ws.index_path == "/ws/.colaig/indexes/"

    async def test_load_workspace_conversations(self, mock_storage):
        """Les conversations sont chargées depuis le YAML."""
        yaml_content = b"workspace_id: test\nname: Test\nconversations:\n  - '!room1:test'\n  - '!room2:test'"
        mock_storage.add_file("/ws/.colaig/config.yaml", yaml_content)
        ws = await load_workspace(mock_storage, "/ws/")
        assert ws.conversations == ["!room1:test", "!room2:test"]

    async def test_load_workspace_tchap_rooms_compat(self, mock_storage):
        """Les tchap_rooms sont chargées depuis le YAML (rétrocompatibilité)."""
        yaml_content = b"workspace_id: test\nname: Test\ntchap_rooms:\n  - '!room1:test'\n  - '!room2:test'"
        mock_storage.add_file("/ws/.colaig/config.yaml", yaml_content)
        ws = await load_workspace(mock_storage, "/ws/")
        assert ws.conversations == ["!room1:test", "!room2:test"]


class TestListWorkspaces:
    """Tests du scan des workspaces."""

    async def test_list_workspaces_empty(self, mock_storage):
        """Storage vide retourne une liste vide."""
        result = await list_workspaces(mock_storage)
        assert result == []

    async def test_list_workspaces_with_workspace(self, mock_storage):
        """Trouve les workspaces avec .colaig/config.yaml."""
        mock_storage.add_file("/espace-a/.colaig/config.yaml",
                            b"workspace_id: a\nname: Espace A")
        from colaig.models import StorageFile
        mock_storage.metadata["/espace-a/"] = StorageFile(
            path="/espace-a/", name="espace-a", is_directory=True)

        result = await list_workspaces(mock_storage)
        assert len(result) == 1
        assert result[0].workspace_id == "a"

    async def test_list_workspaces_repairs_invalid(self, mock_storage):
        """Répare automatiquement les workspaces avec config invalide."""
        from colaig.models import StorageFile
        mock_storage.metadata["/bad/"] = StorageFile(
            path="/bad/", name="bad", is_directory=True)
        mock_storage.add_file("/bad/.colaig/config.yaml", b"not valid yaml: [[[")

        result = await list_workspaces(mock_storage)
        assert len(result) == 1
        assert result[0].workspace_id == "bad"
        assert result[0].name == "bad"

    async def test_list_workspaces_skips_unrepairable(self, mock_storage):
        """Ignore les workspaces non réparables (scaffold échoue)."""
        from colaig.models import StorageFile
        mock_storage.metadata["/bad/"] = StorageFile(
            path="/bad/", name="bad", is_directory=True)
        mock_storage.add_file("/bad/.colaig/config.yaml", b"not valid yaml: [[[")
        # Forcer l'échec de la sauvegarde réparation
        original_upload = mock_storage.upload
        async def fail_upload(path, data):
            raise OSError("upload interdit")
        mock_storage.upload = fail_upload

        result = await list_workspaces(mock_storage)
        assert len(result) == 0
        mock_storage.upload = original_upload


class TestFindWorkspaceForConversation:
    """Tests de la recherche de workspace par conversation_id."""

    def test_found(self, test_workspace):
        ws = find_workspace_for_conversation([test_workspace], "!test_room:test.local")
        assert ws is not None
        assert ws.workspace_id == test_workspace.workspace_id

    def test_not_found(self, test_workspace):
        ws = find_workspace_for_conversation([test_workspace], "!unknown:test.local")
        assert ws is None

    def test_empty_list(self):
        ws = find_workspace_for_conversation([], "!room:test.local")
        assert ws is None


class TestCreateDefaultWorkspace:
    """Tests du workspace par défaut."""

    def test_default_workspace(self):
        ws = create_default_workspace()
        assert ws.workspace_id == "_default"
        assert ws.rag_enabled is False
        assert ws.storage_path == ""
        assert "Colaig" in ws.system_prompt
