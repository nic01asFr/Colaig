"""
Tests — Administration réflexive (méta-tools + gardes can_manage / can_manage_workspace).

Vérifie que l'agent, dans le contexte approprié (DM + admin/owner), peut opérer les
fonctionnalités Colaig (créer/configurer/lier des workspaces) en conversation,
et que la garde fine par workspace (owner) est respectée.
"""

import json

import pytest

from colaig.agents.tool_registry import ToolRegistry
from colaig.agents.tools.admin_tools import (
    create_link_conversation_handler,
    create_list_manageable_workspaces_handler,
    create_manage_workspace_handler,
    create_manage_workspace_owners_handler,
    create_set_workspace_prompt_handler,
    register_admin_tools,
)
from colaig.integrations.storage.local import LocalStorage
from colaig.models import ContextMode, WorkspaceContext
from colaig.security.acl import WorkspaceACL

ALICE = "@alice:tchap.fr"
BOB = "@bob:tchap.fr"


# =============================================================================
# Fixtures
# =============================================================================


class FakeResolver:
    """Resolver minimal : liste vivante + register_workspace async (comme le vrai)."""

    def __init__(self):
        self.workspaces = []

    async def register_workspace(self, ws):
        self.workspaces = [
            w for w in self.workspaces if w.workspace_id != ws.workspace_id
        ] + [ws]


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(base_path=str(tmp_path))


@pytest.fixture
def resolver():
    return FakeResolver()


def _ctx(mode=ContextMode.PERSONAL, user_id=ALICE):
    return WorkspaceContext(
        workspace=None, mode=mode, available_tools=[],
        conversation_history=[], context_anchors=[], user_id=user_id,
    )


async def _create_ws(storage, resolver, user_id, name="RH", path="/rh/"):
    """Helper : crée un workspace dont user_id devient owner."""
    handler = create_manage_workspace_handler(storage, resolver, user_id, [])
    out = json.loads(await handler(action="create", name=name, storage_path=path))
    return out["workspace_id"]


# =============================================================================
# Garde d'injection : can_manage
# =============================================================================


class TestCanManage:

    def test_dm_global_admin_allowed(self):
        assert WorkspaceACL.can_manage(_ctx(), [ALICE]) is True

    def test_assistant_mode_denied(self):
        assert WorkspaceACL.can_manage(_ctx(mode=ContextMode.ASSISTANT), [ALICE]) is False

    def test_non_admin_no_workspace_denied(self):
        assert WorkspaceACL.can_manage(_ctx(), [BOB]) is False

    def test_empty_admin_no_workspace_denied(self):
        assert WorkspaceACL.can_manage(_ctx(), []) is False

    def test_no_user_id_denied(self):
        assert WorkspaceACL.can_manage(_ctx(user_id=""), [ALICE]) is False

    def test_owner_of_a_workspace_allowed(self):
        class WS:
            owners = [ALICE]
        # Pas admin global, mais owner d'un workspace → injection autorisée
        assert WorkspaceACL.can_manage(_ctx(), [], workspaces=[WS()]) is True

    def test_not_owner_denied(self):
        class WS:
            owners = [BOB]
        assert WorkspaceACL.can_manage(_ctx(), [], workspaces=[WS()]) is False


# =============================================================================
# Garde fine : can_manage_workspace
# =============================================================================


class TestCanManageWorkspace:

    def test_global_admin_any_workspace(self):
        class WS:
            owners = []
        assert WorkspaceACL.can_manage_workspace(ALICE, WS(), [ALICE]) is True

    def test_owner_allowed(self):
        class WS:
            owners = [ALICE]
        assert WorkspaceACL.can_manage_workspace(ALICE, WS(), []) is True

    def test_non_owner_non_admin_denied(self):
        class WS:
            owners = [BOB]
        assert WorkspaceACL.can_manage_workspace(ALICE, WS(), []) is False


# =============================================================================
# manage_workspace
# =============================================================================


class TestManageWorkspace:

    async def test_create_sets_creator_as_owner(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert ALICE in ws.owners
        assert await storage.exists(f"{ws.storage_path}.colaig/config.yaml")

    async def test_create_requires_storage_path(self, storage, resolver):
        handler = create_manage_workspace_handler(storage, resolver, ALICE, [])
        out = json.loads(await handler(action="create", name="X"))
        assert out["success"] is False
        assert "storage_path" in out["error"]

    async def test_owner_can_update(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        handler = create_manage_workspace_handler(storage, resolver, ALICE, [])
        out = json.loads(await handler(
            action="update", workspace_id=wid, name="RH v2", tone="formal"))
        assert out["success"] is True
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert ws.name == "RH v2" and ws.tone == "formal"

    async def test_non_owner_cannot_update(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)  # alice owner
        # bob, ni owner ni admin global
        handler = create_manage_workspace_handler(storage, resolver, BOB, [])
        out = json.loads(await handler(action="update", workspace_id=wid, name="hack"))
        assert out["success"] is False
        assert "Droits insuffisants" in out["error"]

    async def test_global_admin_can_update_any(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)  # alice owner
        # bob admin global → autorisé même sans être owner
        handler = create_manage_workspace_handler(storage, resolver, BOB, [BOB])
        out = json.loads(await handler(action="update", workspace_id=wid, name="RH admin"))
        assert out["success"] is True

    async def test_update_unknown_workspace(self, storage, resolver):
        handler = create_manage_workspace_handler(storage, resolver, ALICE, [ALICE])
        out = json.loads(await handler(action="update", workspace_id="inconnu", name="X"))
        assert out["success"] is False
        assert "introuvable" in out["error"]


# =============================================================================
# link_conversation + set_workspace_prompt (garde fine)
# =============================================================================


class TestScopedTools:

    async def test_owner_can_link(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        link = create_link_conversation_handler(storage, resolver, ALICE, [])
        out = json.loads(await link(workspace_id=wid, conversation_id="!s:tchap.fr"))
        assert out["success"] is True
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert "!s:tchap.fr" in ws.conversations

    async def test_non_owner_cannot_link(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        link = create_link_conversation_handler(storage, resolver, BOB, [])
        out = json.loads(await link(workspace_id=wid, conversation_id="!s:tchap.fr"))
        assert out["success"] is False

    async def test_owner_can_set_prompt(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        setp = create_set_workspace_prompt_handler(storage, resolver, ALICE, [])
        out = json.loads(await setp(workspace_id=wid, system_prompt="Expert RH."))
        assert out["success"] is True
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert ws.system_prompt == "Expert RH."

    async def test_non_owner_cannot_set_prompt(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        setp = create_set_workspace_prompt_handler(storage, resolver, BOB, [])
        out = json.loads(await setp(workspace_id=wid, system_prompt="hack"))
        assert out["success"] is False


# =============================================================================
# list_manageable_workspaces (filtré) + register
# =============================================================================


class TestListAndRegister:

    async def test_list_filters_to_manageable(self, storage, resolver):
        await _create_ws(storage, resolver, ALICE, name="RH", path="/rh/")
        await _create_ws(storage, resolver, BOB, name="Urba", path="/urba/")
        # alice ne voit que son espace (owner), pas celui de bob
        lst = create_list_manageable_workspaces_handler(resolver, ALICE, [])
        out = json.loads(await lst())
        assert out["count"] == 1
        assert out["workspaces"][0]["name"] == "RH"

    async def test_global_admin_sees_all(self, storage, resolver):
        await _create_ws(storage, resolver, ALICE, name="RH", path="/rh/")
        await _create_ws(storage, resolver, BOB, name="Urba", path="/urba/")
        lst = create_list_manageable_workspaces_handler(resolver, "@ops:x", ["@ops:x"])
        out = json.loads(await lst())
        assert out["count"] == 2

    def test_register_admin_tools_adds_four(self, storage, resolver):
        registry = ToolRegistry()
        register_admin_tools(registry, storage, resolver, ALICE, [ALICE])
        for name in ("manage_workspace", "link_conversation",
                     "set_workspace_prompt", "list_manageable_workspaces"):
            assert registry.get(name) is not None

    def test_owners_tool_only_for_global_admin(self, storage, resolver):
        reg_admin = ToolRegistry()
        register_admin_tools(reg_admin, storage, resolver, ALICE, [ALICE])
        assert reg_admin.get("manage_workspace_owners") is not None
        # Owner non-admin global → tool owners ABSENT
        reg_owner = ToolRegistry()
        register_admin_tools(reg_owner, storage, resolver, BOB, [])
        assert reg_owner.get("manage_workspace_owners") is None


class TestManageOwners:

    async def test_global_admin_adds_owner(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)  # alice owner
        h = create_manage_workspace_owners_handler(storage, resolver, "@ops:x", ["@ops:x"])
        out = json.loads(await h(action="add", workspace_id=wid, target_user_id=BOB))
        assert out["success"] is True
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert BOB in ws.owners

    async def test_global_admin_removes_owner(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)
        h = create_manage_workspace_owners_handler(storage, resolver, "@ops:x", ["@ops:x"])
        await h(action="add", workspace_id=wid, target_user_id=BOB)
        out = json.loads(await h(action="remove", workspace_id=wid, target_user_id=BOB))
        assert out["success"] is True
        ws = next(w for w in resolver.workspaces if w.workspace_id == wid)
        assert BOB not in ws.owners

    async def test_non_global_admin_denied(self, storage, resolver):
        wid = await _create_ws(storage, resolver, ALICE)  # alice owner, pas admin global
        h = create_manage_workspace_owners_handler(storage, resolver, ALICE, [])
        out = json.loads(await h(action="add", workspace_id=wid, target_user_id="@x:y"))
        assert out["success"] is False
        assert "globale" in out["error"].lower()
