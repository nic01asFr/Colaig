# SPDX-License-Identifier: MIT
"""Tests for platform.registry - SQLite-backed storage."""

import os
import tempfile

import pytest
import pytest_asyncio

from app.platform.models import AdminRole, BotInstanceConfig, InstanceStatus, PlatformAdmin
from app.platform.registry import PlatformRegistry


@pytest_asyncio.fixture
async def registry(tmp_path):
    """Create a temporary registry for each test."""
    db_path = str(tmp_path / "test_platform.db")
    reg = PlatformRegistry(db_path=db_path)
    await reg.open()
    yield reg
    await reg.close()


@pytest.mark.asyncio
class TestAdminCRUD:
    async def test_add_and_get_admin(self, registry):
        admin = PlatformAdmin(
            email="test@gouv.fr",
            role=AdminRole.OPERATOR,
            display_name="Test User",
        )
        await registry.add_admin(admin)

        fetched = await registry.get_admin("test@gouv.fr")
        assert fetched is not None
        assert fetched.email == "test@gouv.fr"
        assert fetched.role == AdminRole.OPERATOR
        assert fetched.display_name == "Test User"

    async def test_get_nonexistent_admin(self, registry):
        result = await registry.get_admin("nobody@gouv.fr")
        assert result is None

    async def test_list_admins(self, registry):
        await registry.add_admin(PlatformAdmin(email="op1@gouv.fr", role=AdminRole.OPERATOR))
        await registry.add_admin(PlatformAdmin(email="admin1@gouv.fr", role=AdminRole.ADMIN))
        await registry.add_admin(PlatformAdmin(email="op2@gouv.fr", role=AdminRole.OPERATOR))

        all_admins = await registry.list_admins()
        assert len(all_admins) == 3

        operators = await registry.list_admins(role=AdminRole.OPERATOR)
        assert len(operators) == 2
        assert all(a.role == AdminRole.OPERATOR for a in operators)

    async def test_update_admin_login(self, registry):
        await registry.add_admin(PlatformAdmin(email="test@gouv.fr"))
        await registry.update_admin_login("test@gouv.fr", "2026-03-20T12:00:00Z")

        admin = await registry.get_admin("test@gouv.fr")
        assert admin.last_login == "2026-03-20T12:00:00Z"

    async def test_deactivate_admin(self, registry):
        await registry.add_admin(PlatformAdmin(email="test@gouv.fr"))
        await registry.deactivate_admin("test@gouv.fr")

        # Deactivated admin should not appear in list
        admins = await registry.list_admins()
        assert len(admins) == 0

        # But should still be gettable directly
        admin = await registry.get_admin("test@gouv.fr")
        assert admin is not None
        assert admin.is_active is False


@pytest.mark.asyncio
class TestInstanceCRUD:
    async def test_add_and_get_instance(self, registry):
        await registry.add_admin(PlatformAdmin(email="owner@gouv.fr"))

        instance = BotInstanceConfig(
            name="Test Bot",
            matrix_homeserver="https://matrix.tchap.gouv.fr",
            matrix_username="bot",
            matrix_password="pass",
            admin_email="owner@gouv.fr",
        )
        await registry.add_instance(instance)

        fetched = await registry.get_instance(instance.instance_id)
        assert fetched is not None
        assert fetched.name == "Test Bot"
        assert fetched.matrix_password == "pass"
        assert fetched.admin_email == "owner@gouv.fr"

    async def test_list_instances_by_admin(self, registry):
        await registry.add_admin(PlatformAdmin(email="a@gouv.fr"))
        await registry.add_admin(PlatformAdmin(email="b@gouv.fr"))

        await registry.add_instance(BotInstanceConfig(name="Bot A", admin_email="a@gouv.fr"))
        await registry.add_instance(BotInstanceConfig(name="Bot B", admin_email="b@gouv.fr"))
        await registry.add_instance(BotInstanceConfig(name="Bot A2", admin_email="a@gouv.fr"))

        all_instances = await registry.list_instances()
        assert len(all_instances) == 3

        a_instances = await registry.list_instances(admin_email="a@gouv.fr")
        assert len(a_instances) == 2
        assert all(i.admin_email == "a@gouv.fr" for i in a_instances)

    async def test_update_instance_status(self, registry):
        await registry.add_admin(PlatformAdmin(email="x@gouv.fr"))
        instance = BotInstanceConfig(admin_email="x@gouv.fr")
        await registry.add_instance(instance)

        await registry.update_instance_status(
            instance.instance_id,
            InstanceStatus.RUNNING,
            last_started_at="2026-03-20T10:00:00Z",
        )

        fetched = await registry.get_instance(instance.instance_id)
        assert fetched.status == InstanceStatus.RUNNING
        assert fetched.last_started_at == "2026-03-20T10:00:00Z"

    async def test_update_instance_error(self, registry):
        await registry.add_admin(PlatformAdmin(email="x@gouv.fr"))
        instance = BotInstanceConfig(admin_email="x@gouv.fr")
        await registry.add_instance(instance)

        await registry.update_instance_status(
            instance.instance_id,
            InstanceStatus.ERROR,
            error_message="Connection refused",
        )

        fetched = await registry.get_instance(instance.instance_id)
        assert fetched.status == InstanceStatus.ERROR
        assert fetched.error_message == "Connection refused"

    async def test_delete_instance(self, registry):
        await registry.add_admin(PlatformAdmin(email="x@gouv.fr"))
        instance = BotInstanceConfig(admin_email="x@gouv.fr")
        await registry.add_instance(instance)

        await registry.delete_instance(instance.instance_id)

        fetched = await registry.get_instance(instance.instance_id)
        assert fetched is None


@pytest.mark.asyncio
class TestSessionStorage:
    async def test_save_and_get_session(self, registry):
        await registry.save_session(
            session_id="sess123",
            email="user@gouv.fr",
            created_at="2026-03-20T10:00:00Z",
            expires_at="2026-03-20T18:00:00Z",
            data={"role": "admin"},
        )

        session = await registry.get_session("sess123")
        assert session is not None
        assert session["email"] == "user@gouv.fr"
        assert session["data"] == {"role": "admin"}

    async def test_get_nonexistent_session(self, registry):
        result = await registry.get_session("nope")
        assert result is None

    async def test_delete_session(self, registry):
        await registry.save_session("s1", "u@g.fr", "2026-01-01", "2026-12-31", {})
        await registry.delete_session("s1")
        assert await registry.get_session("s1") is None

    async def test_cleanup_expired_sessions(self, registry):
        await registry.save_session("expired", "u@g.fr", "2025-01-01", "2025-06-01", {})
        await registry.save_session("valid", "u@g.fr", "2026-01-01", "2027-01-01", {})

        count = await registry.cleanup_expired_sessions("2026-03-20T00:00:00Z")
        assert count >= 1

        assert await registry.get_session("expired") is None
        assert await registry.get_session("valid") is not None
