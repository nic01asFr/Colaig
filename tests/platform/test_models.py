# SPDX-License-Identifier: MIT
"""Tests for platform.models."""

import json
import pytest

from app.platform.models import (
    AdminRole,
    BotInstanceConfig,
    InstanceStatus,
    PlatformAdmin,
)


class TestBotInstanceConfig:
    def test_defaults(self):
        cfg = BotInstanceConfig()
        assert cfg.instance_id  # auto-generated
        assert len(cfg.instance_id) == 12
        assert cfg.status == InstanceStatus.STOPPED
        assert cfg.allowed_domains == ["*"]
        assert cfg.groups_used == "document,web"

    def test_to_dict_from_dict_roundtrip(self):
        cfg = BotInstanceConfig(
            name="Bot MTE",
            matrix_homeserver="https://matrix.tchap.gouv.fr",
            matrix_username="bot@mte.gouv.fr",
            matrix_password="secret",
            admin_email="admin@mte.gouv.fr",
            allowed_domains=["mte.gouv.fr", "ecologie.gouv.fr"],
        )
        data = cfg.to_dict()

        # allowed_domains should be JSON-serialized
        assert isinstance(data["allowed_domains"], str)
        assert json.loads(data["allowed_domains"]) == ["mte.gouv.fr", "ecologie.gouv.fr"]

        restored = BotInstanceConfig.from_dict(data)
        assert restored.instance_id == cfg.instance_id
        assert restored.name == "Bot MTE"
        assert restored.matrix_password == "secret"
        assert restored.allowed_domains == ["mte.gouv.fr", "ecologie.gouv.fr"]

    def test_from_dict_handles_string_domains(self):
        data = {
            "instance_id": "abc123",
            "allowed_domains": '["gouv.fr"]',
            "admin_email": "x@y.fr",
        }
        cfg = BotInstanceConfig.from_dict(data)
        assert cfg.allowed_domains == ["gouv.fr"]

    def test_from_dict_handles_invalid_json_domains(self):
        data = {
            "instance_id": "abc123",
            "allowed_domains": "not-json",
            "admin_email": "x@y.fr",
        }
        cfg = BotInstanceConfig.from_dict(data)
        assert cfg.allowed_domains == ["*"]


class TestPlatformAdmin:
    def test_defaults(self):
        admin = PlatformAdmin(email="admin@gouv.fr")
        assert admin.role == AdminRole.ADMIN
        assert admin.is_active is True
        assert admin.created_at  # auto-generated

    def test_to_dict_from_dict_roundtrip(self):
        admin = PlatformAdmin(
            email="op@dinum.gouv.fr",
            role=AdminRole.OPERATOR,
            display_name="Opérateur DINUM",
            organization="DINUM",
        )
        data = admin.to_dict()
        restored = PlatformAdmin.from_dict(data)
        assert restored.email == "op@dinum.gouv.fr"
        assert restored.role == AdminRole.OPERATOR
        assert restored.display_name == "Opérateur DINUM"
