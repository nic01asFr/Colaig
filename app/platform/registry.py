# SPDX-License-Identifier: MIT
"""
Platform Registry - SQLite-backed storage for admins and bot instances.

Uses aiosqlite for async access. Stores only metadata and credentials,
never document content (documents stay on WebDAV).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import AdminRole, BotInstanceConfig, InstanceStatus, PlatformAdmin

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_admins (
    email TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'admin',
    display_name TEXT DEFAULT '',
    organization TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_login TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bot_instances (
    instance_id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    matrix_homeserver TEXT DEFAULT '',
    matrix_username TEXT DEFAULT '',
    matrix_password TEXT DEFAULT '',
    webdav_url TEXT DEFAULT '',
    webdav_username TEXT DEFAULT '',
    webdav_password TEXT DEFAULT '',
    webdav_root_path TEXT DEFAULT '/documents',
    albert_api_url TEXT DEFAULT '',
    albert_api_token TEXT DEFAULT '',
    albert_model TEXT DEFAULT 'meta-llama/Llama-3.1-8B-Instruct',
    albert_model_embedding TEXT DEFAULT 'BAAI/bge-m3',
    admin_email TEXT NOT NULL,
    allowed_domains TEXT DEFAULT '["*"]',
    groups_used TEXT DEFAULT 'document,web',
    browser_extraction_enabled INTEGER DEFAULT 0,
    e2e_keys_data TEXT DEFAULT '',
    e2e_keys_passphrase TEXT DEFAULT '',
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    last_started_at TEXT,
    error_message TEXT,
    FOREIGN KEY (admin_email) REFERENCES platform_admins(email)
);

CREATE TABLE IF NOT EXISTS platform_sessions (
    session_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    FOREIGN KEY (email) REFERENCES platform_admins(email)
);
"""


class PlatformRegistry:
    """Async SQLite registry for the Colaig platform."""

    def __init__(self, db_path: str = "/app/data/platform.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        """Open DB connection and ensure schema exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Migrations: add new columns to existing databases
        for col, definition in [
            ("e2e_keys_data", "TEXT DEFAULT ''"),
            ("e2e_keys_passphrase", "TEXT DEFAULT ''"),
        ]:
            try:
                await self._db.execute(
                    f"ALTER TABLE bot_instances ADD COLUMN {col} {definition}"
                )
                await self._db.commit()
                logger.info(f"Migration: added column {col} to bot_instances")
            except Exception:
                pass  # Column already exists
        logger.info(f"Platform registry opened: {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Platform registry closed")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Registry not opened. Call await registry.open() first.")
        return self._db

    # === Admin CRUD ===

    async def add_admin(self, admin: PlatformAdmin) -> None:
        data = admin.to_dict()
        await self.db.execute(
            """INSERT OR REPLACE INTO platform_admins
               (email, role, display_name, organization, created_at, last_login, is_active)
               VALUES (:email, :role, :display_name, :organization, :created_at, :last_login, :is_active)""",
            data,
        )
        await self.db.commit()

    async def get_admin(self, email: str) -> Optional[PlatformAdmin]:
        async with self.db.execute(
            "SELECT * FROM platform_admins WHERE email = ?", (email,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return PlatformAdmin.from_dict(dict(row))
        return None

    async def list_admins(self, role: Optional[str] = None) -> list[PlatformAdmin]:
        if role:
            query = "SELECT * FROM platform_admins WHERE role = ? AND is_active = 1"
            params = (role,)
        else:
            query = "SELECT * FROM platform_admins WHERE is_active = 1"
            params = ()
        async with self.db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [PlatformAdmin.from_dict(dict(r)) for r in rows]

    async def update_admin_login(self, email: str, timestamp: str) -> None:
        await self.db.execute(
            "UPDATE platform_admins SET last_login = ? WHERE email = ?",
            (timestamp, email),
        )
        await self.db.commit()

    async def deactivate_admin(self, email: str) -> None:
        await self.db.execute(
            "UPDATE platform_admins SET is_active = 0 WHERE email = ?", (email,)
        )
        await self.db.commit()

    # === Bot Instance CRUD ===

    async def add_instance(self, instance: BotInstanceConfig) -> None:
        data = instance.to_dict()
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        await self.db.execute(
            f"INSERT OR REPLACE INTO bot_instances ({columns}) VALUES ({placeholders})",
            data,
        )
        await self.db.commit()

    async def get_instance(self, instance_id: str) -> Optional[BotInstanceConfig]:
        async with self.db.execute(
            "SELECT * FROM bot_instances WHERE instance_id = ?", (instance_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return BotInstanceConfig.from_dict(dict(row))
        return None

    async def list_instances(
        self, admin_email: Optional[str] = None
    ) -> list[BotInstanceConfig]:
        if admin_email:
            query = "SELECT * FROM bot_instances WHERE admin_email = ?"
            params = (admin_email,)
        else:
            query = "SELECT * FROM bot_instances"
            params = ()
        async with self.db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [BotInstanceConfig.from_dict(dict(r)) for r in rows]

    async def update_instance_status(
        self,
        instance_id: str,
        status: str,
        error_message: Optional[str] = None,
        last_started_at: Optional[str] = None,
    ) -> None:
        fields = ["status = ?"]
        params: list = [status]
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        if last_started_at is not None:
            fields.append("last_started_at = ?")
            params.append(last_started_at)
        params.append(instance_id)
        await self.db.execute(
            f"UPDATE bot_instances SET {', '.join(fields)} WHERE instance_id = ?",
            params,
        )
        await self.db.commit()

    async def delete_instance(self, instance_id: str) -> None:
        await self.db.execute(
            "DELETE FROM bot_instances WHERE instance_id = ?", (instance_id,)
        )
        await self.db.commit()

    # === Session storage (for web UI) ===

    async def save_session(
        self, session_id: str, email: str, created_at: str, expires_at: str, data: dict
    ) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO platform_sessions
               (session_id, email, created_at, expires_at, data)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, email, created_at, expires_at, json.dumps(data)),
        )
        await self.db.commit()

    async def get_session(self, session_id: str) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM platform_sessions WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                data = dict(row)
                data["data"] = json.loads(data.get("data", "{}"))
                return data
        return None

    async def delete_session(self, session_id: str) -> None:
        await self.db.execute(
            "DELETE FROM platform_sessions WHERE session_id = ?", (session_id,)
        )
        await self.db.commit()

    async def cleanup_expired_sessions(self, now_iso: str) -> int:
        """Delete expired sessions. Returns count of deleted rows."""
        async with self.db.execute(
            "DELETE FROM platform_sessions WHERE expires_at < ?", (now_iso,)
        ) as cursor:
            count = cursor.rowcount
        await self.db.commit()
        return count or 0
