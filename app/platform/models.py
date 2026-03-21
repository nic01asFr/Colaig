# SPDX-License-Identifier: MIT
"""
Data models for the Colaig platform.

BotInstanceConfig holds all configuration needed to run a single bot instance.
PlatformAdmin represents an administrator who can manage bot instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


class InstanceStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    STARTING = "starting"


class AdminRole(str, Enum):
    OPERATOR = "operator"  # DINUM-level, can create admins
    ADMIN = "admin"  # Ministry-level, can manage their own instances


@dataclass
class BotInstanceConfig:
    """Configuration complète pour une instance de bot Colaig."""

    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Identity
    name: str = ""
    description: str = ""

    # Matrix / Tchap
    matrix_homeserver: str = ""
    matrix_username: str = ""
    matrix_password: str = ""

    # WebDAV
    webdav_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""
    webdav_root_path: str = "/documents"

    # Albert API (can inherit from platform defaults)
    albert_api_url: str = ""
    albert_api_token: str = ""
    albert_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    albert_model_embedding: str = "BAAI/bge-m3"

    # Ownership
    admin_email: str = ""
    allowed_domains: list[str] = field(default_factory=lambda: ["*"])

    # Feature flags
    groups_used: str = "document,web"
    browser_extraction_enabled: bool = False

    # E2E key import (Megolm session export from Tchap/Element)
    # e2e_keys_data: base64-encoded content of the key export file
    # e2e_keys_passphrase: passphrase used when exporting the keys
    e2e_keys_data: str = ""
    e2e_keys_passphrase: str = ""

    # Status
    status: str = InstanceStatus.STOPPED
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_started_at: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dict for DB storage."""
        import json

        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "description": self.description,
            "matrix_homeserver": self.matrix_homeserver,
            "matrix_username": self.matrix_username,
            "matrix_password": self.matrix_password,
            "webdav_url": self.webdav_url,
            "webdav_username": self.webdav_username,
            "webdav_password": self.webdav_password,
            "webdav_root_path": self.webdav_root_path,
            "albert_api_url": self.albert_api_url,
            "albert_api_token": self.albert_api_token,
            "albert_model": self.albert_model,
            "albert_model_embedding": self.albert_model_embedding,
            "admin_email": self.admin_email,
            "allowed_domains": json.dumps(self.allowed_domains),
            "groups_used": self.groups_used,
            "browser_extraction_enabled": self.browser_extraction_enabled,
            "e2e_keys_data": self.e2e_keys_data,
            "e2e_keys_passphrase": self.e2e_keys_passphrase,
            "status": self.status,
            "created_at": self.created_at,
            "last_started_at": self.last_started_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BotInstanceConfig:
        """Deserialize from dict."""
        import json

        domains = data.get("allowed_domains", '["*"]')
        if isinstance(domains, str):
            try:
                domains = json.loads(domains)
            except (json.JSONDecodeError, TypeError):
                domains = ["*"]

        return cls(
            instance_id=data["instance_id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            matrix_homeserver=data.get("matrix_homeserver", ""),
            matrix_username=data.get("matrix_username", ""),
            matrix_password=data.get("matrix_password", ""),
            webdav_url=data.get("webdav_url", ""),
            webdav_username=data.get("webdav_username", ""),
            webdav_password=data.get("webdav_password", ""),
            webdav_root_path=data.get("webdav_root_path", "/documents"),
            albert_api_url=data.get("albert_api_url", ""),
            albert_api_token=data.get("albert_api_token", ""),
            albert_model=data.get("albert_model", "meta-llama/Llama-3.1-8B-Instruct"),
            albert_model_embedding=data.get("albert_model_embedding", "BAAI/bge-m3"),
            admin_email=data.get("admin_email", ""),
            allowed_domains=domains,
            groups_used=data.get("groups_used", "document,web"),
            browser_extraction_enabled=bool(data.get("browser_extraction_enabled", False)),
            e2e_keys_data=data.get("e2e_keys_data", ""),
            e2e_keys_passphrase=data.get("e2e_keys_passphrase", ""),
            status=data.get("status", InstanceStatus.STOPPED),
            created_at=data.get("created_at", ""),
            last_started_at=data.get("last_started_at"),
            error_message=data.get("error_message"),
        )


@dataclass
class PlatformAdmin:
    """Administrateur de la plateforme Colaig."""

    email: str
    role: str = AdminRole.ADMIN
    display_name: str = ""
    organization: str = ""  # SIRET or org name from ProConnect
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_login: Optional[str] = None
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "role": self.role,
            "display_name": self.display_name,
            "organization": self.organization,
            "created_at": self.created_at,
            "last_login": self.last_login,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlatformAdmin:
        return cls(
            email=data["email"],
            role=data.get("role", AdminRole.ADMIN),
            display_name=data.get("display_name", ""),
            organization=data.get("organization", ""),
            created_at=data.get("created_at", ""),
            last_login=data.get("last_login"),
            is_active=bool(data.get("is_active", True)),
        )
