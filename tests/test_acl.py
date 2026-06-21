"""
Tests — WorkspaceACL.can_access / filter_accessible / assert_can_access.

Verrouille la cohérence des droits d'ACCÈS workspace (lecture/interrogation),
distincte des droits d'administration (couverts par test_admin_tools.py).
"""

import pytest

from colaig.exceptions import WorkspaceAccessDenied
from colaig.models import WorkspaceConfig
from colaig.security.acl import WorkspaceACL

ALICE = "@alice:tchap.fr"
BOB = "@bob:tchap.fr"


def _ws(workspace_id="rh", user_ids=None, public=False):
    return WorkspaceConfig(
        workspace_id=workspace_id, name=workspace_id, storage_path=f"/{workspace_id}/",
        user_ids=user_ids or [], public=public,
    )


class TestCanAccess:

    def test_auth_disabled_allows_all(self):
        # Backward compat : sans auth, tout le monde accède
        assert WorkspaceACL.can_access(_ws(user_ids=[BOB]), ALICE, auth_enabled=False) is True

    def test_member_allowed(self):
        assert WorkspaceACL.can_access(_ws(user_ids=[ALICE]), ALICE, auth_enabled=True) is True

    def test_non_member_denied(self):
        assert WorkspaceACL.can_access(_ws(user_ids=[BOB]), ALICE, auth_enabled=True) is False

    def test_public_allows_any_user(self):
        assert WorkspaceACL.can_access(_ws(public=True), ALICE, auth_enabled=True) is True

    def test_public_allows_anonymous(self):
        assert WorkspaceACL.can_access(_ws(public=True), "", auth_enabled=True) is True

    def test_anonymous_private_denied(self):
        assert WorkspaceACL.can_access(_ws(user_ids=[ALICE]), "", auth_enabled=True) is False

    def test_empty_user_ids_private_denied(self):
        assert WorkspaceACL.can_access(_ws(user_ids=[]), ALICE, auth_enabled=True) is False


class TestFilterAccessible:

    def test_filters_to_accessible_only(self):
        workspaces = [
            _ws("rh", user_ids=[ALICE]),
            _ws("urba", user_ids=[BOB]),
            _ws("public", public=True),
        ]
        visible = WorkspaceACL.filter_accessible(workspaces, ALICE, auth_enabled=True)
        ids = {w.workspace_id for w in visible}
        assert ids == {"rh", "public"}

    def test_auth_disabled_returns_all(self):
        workspaces = [_ws("rh", user_ids=[BOB]), _ws("urba", user_ids=[BOB])]
        visible = WorkspaceACL.filter_accessible(workspaces, ALICE, auth_enabled=False)
        assert len(visible) == 2


class TestAssertCanAccess:

    def test_allowed_does_not_raise(self):
        WorkspaceACL.assert_can_access(_ws(user_ids=[ALICE]), ALICE, auth_enabled=True)

    def test_denied_raises(self):
        with pytest.raises(WorkspaceAccessDenied):
            WorkspaceACL.assert_can_access(_ws(user_ids=[BOB]), ALICE, auth_enabled=True)
