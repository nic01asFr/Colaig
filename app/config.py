# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._version import __version__

COMMAND_PREFIX = "!"
APP_VERSION = __version__


def _parse_list(v: Any) -> list[str]:
    """Parse une liste depuis une string JSON ou CSV, ou retourne la liste telle quelle."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in v.split(",") if item.strip()]
    return ["*"]


class Config(BaseSettings):
    """
    Configuration unifiée de l'application Colaig.

    Fusionne l'ancienne Config (Pydantic) et l'ancienne EnvConfig (dataclass).
    Toutes les valeurs sont chargées automatiquement depuis les variables d'environnement
    grâce à pydantic-settings (plus besoin de os.getenv() explicites).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    # === General ===
    systemd_logging: bool = Field(True, description="Enable/disable systemd journal logging")
    loglevel: str = Field("INFO", description="Log level")
    console_logging: bool = Field(True, description="Enable console logging")
    log_directory: str = Field("", description="Log directory path")

    # === Matrix / Tchap ===
    matrix_home_server: str = Field(
        "https://matrix.agent.dev-durable.tchap.gouv.fr",
        description="Tchap home server URL",
    )
    matrix_bot_username: str = Field("colaig.assistant", description="Matrix bot username")
    matrix_bot_password: str = Field("", description="Matrix bot password")
    matrix_login_type: str = Field("password", description="Matrix login type")
    matrix_access_token: str = Field("", description="Matrix access token (if pre-authenticated)")
    matrix_device_id: str = Field("", description="Matrix device ID")
    matrix_room_id: str = Field("", description="Matrix room ID")
    matrix_bot_display_name: str = Field(
        "Albert - Assistant Tchap", description="Bot display name in Tchap"
    )
    matrix_sync_timeout_s: int = Field(30, description="Matrix sync timeout in seconds")
    errors_room_id: Optional[str] = Field(None, description="Room ID for error notifications")
    help_room_id: str = Field("", description="Room ID for help messages")
    session_path: str = Field("data/session/session.txt", description="Session file path")

    # === User access ===
    user_allowed_domains: list[str] = Field(
        ["*"],
        description="Allowed email domains (JSON list or comma-separated)",
    )

    @field_validator("user_allowed_domains", mode="before")
    @classmethod
    def parse_allowed_domains(cls, v: Any) -> list[str]:
        return _parse_list(v)

    # === Command groups ===
    groups_used: str = Field("document,web", description="Command groups (comma-separated)")
    commands_exclusions: list[str] = Field(default_factory=list, description="Excluded commands")
    modules_exclusions: list[str] = Field(default_factory=list, description="Excluded modules")
    strict_inclusion_mode: bool = Field(True, description="Strict inclusion mode for commands")
    use_threading: bool = Field(False, description="Enable threading mode")

    @field_validator("commands_exclusions", "modules_exclusions", mode="before")
    @classmethod
    def parse_exclusion_lists(cls, v: Any) -> list[str]:
        return _parse_list(v) if v else []

    # === Albert API ===
    albert_api_url: str = Field("https://albert.api.etalab.gouv.fr/v1", description="Albert API base URL")
    albert_api_token: str = Field("", description="Albert API token")
    albert_api_key: str = Field("", description="Albert API key (alias for token)")
    albert_model: str = Field(
        "openai/gpt-oss-120b", description="Albert LLM model"
    )
    albert_model_embedding: str = Field("BAAI/bge-m3", description="Embedding model")
    albert_mode: str = Field("rag", description="Albert mode (rag/norag)")
    albert_with_history: bool = Field(True, description="Conversational mode")
    albert_history_lookup: int = Field(0, description="History lookup depth")
    albert_max_rewind: int = Field(20, description="Max history rewind")
    albert_my_private_collection_name: str = Field(
        "ma_collection_privée", description="Private collection name"
    )
    albert_all_public_command: str = Field(
        "<all_public>", description="Command for all public collections"
    )
    albert_collections_by_id: dict[str, dict] = Field(
        default_factory=dict, description="Collections for RAG"
    )

    # === WebDAV ===
    webdav_url: str = Field("", description="WebDAV server URL")
    webdav_username: str = Field("", description="WebDAV username")
    webdav_password: str = Field("", description="WebDAV password")
    webdav_root_path: str = Field("/documents", description="WebDAV root path")
    webdav_root_dir: str = Field(".albert", description="WebDAV root directory")
    webdav_index_name: str = Field("index.json", description="WebDAV index filename")

    # === Embeddings & Index ===
    embedding_dimension: int = Field(1024, description="Embedding dimension (1024 for BAAI/bge-m3)")
    embedding_cache_duration: int = Field(24, description="Embedding cache TTL in hours")
    embedding_cache_size: int = Field(10000, description="Max cached embeddings")
    embedding_batch_size: int = Field(20, description="Embedding batch size")
    index_dir: str = Field(".index", description="Index directory")
    chunk_size: int = Field(1000, description="Document chunk size in characters")

    # === Behavior & Paths ===
    behavior_path: str = Field(".albert/behavior", description="Behavior definitions path")
    behavior_dir: str = Field("./behaviors", description="Behavior directory")
    indexes_path: str = Field(".albert/indexes", description="Indexes storage path")

    # === Conversation ===
    conversation_obsolescence: int = Field(
        15 * 60, description="Conversation obsolescence in seconds"
    )
    last_activity: int = Field(default_factory=lambda: int(time.time()), description="Last activity")
    last_rag_chunks: Optional[list[dict]] = Field(None, description="Last RAG chunks")

    # === Colaig UI ===
    colaig_response_format: str = Field("concise", description="Response format")
    colaig_show_sources: bool = Field(False, description="Show sources in response")
    colaig_config_command: str = Field("!config", description="Config command")
    colaig_config_timeout: int = Field(3600, description="Config mode timeout in seconds")

    # === Attachments ===
    last_classification_result: Optional[Any] = None
    last_classified_file: Optional[Any] = None
    waiting_for_custom_path: bool = False

    # === Context Management ===
    context_cache_size: int = Field(1000, description="Context cache max size")
    context_cache_ttl: int = Field(3600, description="Context cache TTL in seconds")
    context_save_interval: int = Field(300, description="Context save interval in seconds")
    context_cleanup_interval: int = Field(3600, description="Context cleanup interval in seconds")
    context_max_age_days: int = Field(30, description="Max context age in days")
    context_auto_cleanup: bool = Field(True, description="Auto-cleanup old contexts")

    # === Web extraction ===
    allow_fallback_extraction: bool = Field(
        True, description="Allow fallback extraction methods"
    )
    browser_extraction_enabled: bool = Field(
        False, description="Enable browser-based extraction (Playwright)"
    )

    # === Webhooks ===
    webhook_base_url: Optional[str] = Field(None, description="Webhook base URL")
    webhook_enabled: bool = Field(True, description="Enable webhooks")
    webhook_secret: Optional[str] = Field(None, description="Webhook signing secret")
    webhook_auth_required: bool = Field(True, description="Require webhook auth")
    webhook_timeout: int = Field(30, description="Webhook timeout in seconds")

    # === Grist (legacy, optional) ===
    grist_api_server: str = Field("", description="Grist API server (legacy)")
    grist_api_key: str = Field("", description="Grist API key (legacy)")
    grist_users_table_id: str = Field("", description="Grist users doc ID (legacy)")
    grist_users_table_name: str = Field("", description="Grist users table name (legacy)")

    # === Platform (new) ===
    platform_session_secret: str = Field("", description="Platform session signing secret")
    platform_operator_emails: list[str] = Field(
        default_factory=list, description="Platform operator emails"
    )
    platform_db_path: str = Field("/app/data/platform.db", description="Platform SQLite DB path")

    # === MCP ===
    mcp_default_servers: list[dict] = Field(
        default=[
            {
                "name": "datagouv",
                "url": "https://mcp.data.gouv.fr/mcp",
                "description": "Données ouvertes françaises — data.gouv.fr",
            },
        ],
        description=(
            "Pool de serveurs MCP par défaut pour cette instance. "
            "JSON list. Chaque entrée : {name, url, token?, description?, timeout?}. "
            "Les workspaces peuvent override ou désactiver un serveur par nom."
        ),
    )

    @field_validator("mcp_default_servers", mode="before")
    @classmethod
    def parse_mcp_default_servers(cls, v: Any) -> list[dict]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            return []
        return []

    # === ProConnect OIDC (new) ===
    proconnect_client_id: str = Field("", description="ProConnect OIDC client ID")
    proconnect_client_secret: str = Field("", description="ProConnect OIDC client secret")
    proconnect_redirect_uri: str = Field(
        "", description="ProConnect OIDC redirect URI"
    )
    proconnect_issuer: str = Field(
        "https://auth.proconnect.gouv.fr/api/v2",
        description="ProConnect OIDC issuer URL",
    )
    proconnect_enabled: bool = Field(
        False, description="Enable ProConnect auth (False = password fallback)"
    )
    platform_admin_password: str = Field(
        "", description="Fallback admin password for dev/test"
    )

    @field_validator("platform_operator_emails", mode="before")
    @classmethod
    def parse_operator_emails(cls, v: Any) -> list[str]:
        return _parse_list(v) if v else []

    # === Computed properties ===
    @property
    def is_conversation_obsolete(self) -> bool:
        return int(time.time()) - self.last_activity > self.conversation_obsolescence

    def update_last_activity(self) -> None:
        self.last_activity = int(time.time())


# Singleton global - chargé automatiquement depuis les env vars par pydantic-settings
env_config = Config()


def get_config() -> Config:
    """Retourne la configuration globale."""
    return env_config


def use_systemd_config():
    if not env_config.systemd_logging:
        return

    from systemd import journal

    existing_handlers = logging.getLogger().handlers
    for handler in existing_handlers:
        logging.getLogger().removeHandler(handler)
    logging_handler = (
        journal.JournalHandler() if env_config.systemd_logging else logging.StreamHandler()
    )
    logging.getLogger().addHandler(logging_handler)


# === Backward compatibility aliases ===
# EnvConfig n'existe plus, mais ces attributs sont maintenant dans Config.
# Les imports existants de `from app.config import env_config` continuent de fonctionner.
EnvConfig = Config  # alias pour les imports legacy
