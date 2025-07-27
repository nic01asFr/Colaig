# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import logging
import time
import os
from pathlib import Path
from typing import Optional, Any, List
from dataclasses import dataclass, field

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._version import __version__

COMMAND_PREFIX = "!"

APP_VERSION = __version__


class BaseConfig(BaseSettings):
    # allows us to clean up the imports into multiple parts
    # https://stackoverflow.com/questions/77328900/nested-settings-with-pydantic-settings
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env", 
        extra="ignore"
    )  # allows nested configs


class Config(BaseConfig):
    # General
    systemd_logging: bool = Field(
        True, description="Enable / disable logging with systemd.journal.JournalHandler"
    )
    matrix_home_server: str = Field("", description="Tchap home server URL")
    matrix_bot_username: str = Field("", description="Username of our matrix bot")
    matrix_bot_password: str = Field("", description="Password of our matrix bot")
    errors_room_id: Optional[str] = Field(None, description="Room ID for error and notification messages")
    user_allowed_domains: list[str] = Field(
        ["*"],
        description="List of allowed Tchap users email domains allowed to use Albert Tchap",
    )
    groups_used: str = Field("document,web", description="List of commands groups to use (comma-separated)")
    last_activity: int = Field(int(time.time()), description="Last activity timestamp")

    # Grist Api Key
    grist_api_server: str = Field("", description="Grist Api Server")
    grist_api_key: str = Field("", description="Grist API Key")
    grist_users_table_id: str = Field("", description="Grist Users doc ID")
    grist_users_table_name: str = Field("", description="Grist Users table name/ID")

    # Albert API settings
    albert_api_url: str = Field("https://api.albert.ai", description="Albert API base URL")
    albert_api_token: str = Field(default="", description="Albert API token")

    # WebDAV Configuration
    webdav_url: str = Field("", description="URL du serveur WebDAV")
    webdav_username: str = Field("", description="Nom d'utilisateur WebDAV")
    webdav_password: str = Field("", description="Mot de passe WebDAV")
    webdav_root_path: str = Field("/documents", description="Chemin racine WebDAV")
    webdav_index_name: str = Field("index.json", description="Nom du fichier d'index WebDAV")

    # Extraction web avec browser-use
    allow_fallback_extraction: bool = Field(True, description="Autoriser les méthodes alternatives d'extraction quand browser-use n'est pas disponible")

    # Albert Conversation settings
    albert_collections_by_id: dict[str, dict] = Field({}, description="Collections to use for Albert API chat completion with RAG")
    albert_model: str = Field(
        "meta-llama/Llama-3.1-8B-Instruct",
        description="Albert model name to use (see Albert models hub on HuggingFace)",
    )
    albert_model_embedding: str = Field("BAAI/bge-m3", description="Embedding model (Rag, COT, etc)")
    albert_mode: str = Field("rag", description="Albert API mode")
    albert_with_history: bool = Field(True, description="Conversational mode")
    albert_history_lookup: int = Field(0, description="How far we lookup in the history")
    albert_max_rewind: int = Field(20, description="Max history rewind for stability purposes")
    albert_my_private_collection_name: str = Field("ma_collection_privée", description="Name of the private collection for the user")
    albert_all_public_command: str = Field("<all_public>", description="Command to use to get all public collections")
    conversation_obsolescence: int = Field(
        15 * 60, description="time after which a conversation is considered obsolete, in seconds"
    )
    last_rag_chunks: list[dict] | None = Field(None, description="Last chunks used for the RAG.")

    # Configuration des embeddings
    embedding_dimension: int = Field(1024, description="Dimension des embeddings (1024 pour BAAI/bge-m3)")
    embedding_cache_duration: int = Field(24, description="Durée de rétention du cache des embeddings en heures")
    embedding_cache_size: int = Field(10000, description="Nombre maximum d'embeddings en cache")
    embedding_batch_size: int = Field(20, description="Taille des lots pour les requêtes d'embedding")
    
    # Configuration de l'index
    index_dir: str = Field(".index", description="Répertoire de l'index")
    chunk_size: int = Field(1000, description="Taille des chunks en caractères")

    # Configuration de Albert
    behavior_path: str = Field(
        ".albert/behavior",
        description="Chemin vers le dossier des comportements"
    )
    indexes_path: str = Field(
        ".albert/indexes",
        description="Chemin vers le dossier des index"
    )
    colaig_response_format: str = Field(
        "concise",
        description="Format de réponse (concise, detailed)"
    )
    colaig_show_sources: bool = Field(
        False,
        description="Afficher les sources dans la réponse"
    )
    colaig_config_command: str = Field(
        "!config",
        description="Commande pour activer le mode paramétrage"
    )
    colaig_config_timeout: int = Field(
        3600,
        description="Délai d'expiration du mode paramétrage en secondes"
    )

    # Champs pour la gestion des pièces jointes
    last_classification_result: Optional[Any] = None
    last_classified_file: Optional[Any] = None
    waiting_for_custom_path: bool = False

    # Context Management
    context_cache_size: int = Field(1000, description="Taille maximale du cache de contexte")
    context_cache_ttl: int = Field(3600, description="Durée de vie du cache en secondes")
    context_save_interval: int = Field(300, description="Intervalle de sauvegarde des contextes en secondes")
    context_cleanup_interval: int = Field(3600, description="Intervalle de nettoyage des contextes en secondes")
    context_max_age_days: int = Field(30, description="Âge maximum des contextes en jours")
    context_auto_cleanup: bool = Field(True, description="Nettoyage automatique des vieux contextes")

    # Configuration pour la conversion des noms de variables d'environnement
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_nested_delimiter="__"
    )

    # Configuration pour les webhooks
    webhook_base_url: Optional[str] = Field(
        None,
        description="URL de base pour les webhooks entrants (ex: https://example.com)"
    )
    webhook_enabled: bool = Field(
        True,
        description="Activer/désactiver les webhooks"
    )
    webhook_secret: Optional[str] = Field(
        None,
        description="Secret partagé pour signer les webhooks"
    )
    webhook_auth_required: bool = Field(
        True,
        description="Nécessite une authentification pour les webhooks entrants"
    )
    webhook_timeout: int = Field(
        30,
        description="Timeout des requêtes webhook en secondes"
    )

    @property
    def is_conversation_obsolete(self) -> bool:
        return int(time.time()) - self.last_activity > self.conversation_obsolescence

    def update_last_activity(self) -> None:
        self.last_activity = int(time.time())


# Default config
env_config = Config(
    # Charger les valeurs depuis les variables d'environnement
    matrix_home_server=os.getenv("MATRIX_HOME_SERVER", "https://matrix.agent.dev-durable.tchap.gouv.fr"),
    matrix_bot_username=os.getenv("MATRIX_BOT_USERNAME", "colaig.assistant"),
    matrix_bot_password=os.getenv("MATRIX_BOT_PASSWORD", ""),
    errors_room_id=os.getenv("ERRORS_ROOM_ID", None),
    user_allowed_domains=eval(os.getenv("USER_ALLOWED_DOMAINS", "['*']")),
    groups_used=os.getenv("GROUPS_USED", "document,web"),
    
    # Albert API
    albert_api_url=os.getenv("ALBERT_API_URL", "https://api.albert.ai"),
    albert_api_token=os.getenv("ALBERT_API_TOKEN", ""),
    albert_model=os.getenv("ALBERT_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
    albert_model_embedding=os.getenv("ALBERT_MODEL_EMBEDDING", "BAAI/bge-m3"),
    albert_mode=os.getenv("ALBERT_MODE", "rag"),
    albert_with_history=os.getenv("ALBERT_WITH_HISTORY", "True").lower() in ("true", "1", "yes"),
    albert_history_lookup=int(os.getenv("ALBERT_HISTORY_LOOKUP", "0")),
    albert_max_rewind=int(os.getenv("ALBERT_MAX_REWIND", "20")),
    
    # WebDAV
    webdav_url=os.getenv("WEBDAV_URL", ""),
    webdav_username=os.getenv("WEBDAV_USERNAME", ""),
    webdav_password=os.getenv("WEBDAV_PASSWORD", ""),
    webdav_root_path=os.getenv("WEBDAV_ROOT_PATH", "/documents"),
    webdav_index_name=os.getenv("WEBDAV_INDEX_NAME", "index.json"),
    
    # Autres configurations
    behavior_path=os.getenv("BEHAVIOR_PATH", ".albert/behavior"),
    indexes_path=os.getenv("INDEXES_PATH", ".albert/indexes"),
    colaig_response_format=os.getenv("COLAIG_RESPONSE_FORMAT", "concise"),
    colaig_show_sources=os.getenv("COLAIG_SHOW_SOURCES", "False").lower() in ("true", "1", "yes"),
    allow_fallback_extraction=os.getenv("ALLOW_FALLBACK_EXTRACTION", "True").lower() in ("true", "1", "yes"),
    
    # Embedding
    embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
    embedding_cache_duration=int(os.getenv("EMBEDDING_CACHE_DURATION", "24")),
    embedding_cache_size=int(os.getenv("EMBEDDING_CACHE_SIZE", "10000")),
    embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "20")),
    
    # Index
    index_dir=os.getenv("INDEX_DIR", ".index"),
    chunk_size=int(os.getenv("CHUNK_SIZE", "1000")),
    
    # Webhooks
    webhook_base_url=os.getenv("WEBHOOK_BASE_URL", None),
    webhook_enabled=os.getenv("WEBHOOK_ENABLED", "True").lower() in ("true", "1", "yes"),
    webhook_secret=os.getenv("WEBHOOK_SECRET", None),
    webhook_auth_required=os.getenv("WEBHOOK_AUTH_REQUIRED", "True").lower() in ("true", "1", "yes"),
    webhook_timeout=int(os.getenv("WEBHOOK_TIMEOUT", "30")),
)


def get_config() -> Config:
    """
    Retourne la configuration globale du système.
    
    Cette fonction est utilisée pour accéder à la configuration depuis n'importe quel module.
    
    Returns:
        Config: L'instance de configuration globale.
    """
    return env_config


def use_systemd_config():
    if not env_config.systemd_logging:
        return

    from systemd import journal

    # remove the default handler, if already initialized
    existing_handlers = logging.getLogger().handlers
    for handlers in existing_handlers:
        logging.getLogger().removeHandler(handlers)
    # Sending logs to systemd-journal if run via systemd, printing out on console otherwise.
    logging_handler = (
        journal.JournalHandler() if env_config.systemd_logging else logging.StreamHandler()
    )
    logging.getLogger().addHandler(logging_handler)


@dataclass
class EnvConfig:
    """
    Class to hold environment configuration
    """

    matrix_home_server: str = os.getenv("MATRIX_HOME_SERVER", "https://matrix.agent.dev-durable.tchap.gouv.fr")
    matrix_bot_username: str = os.getenv("MATRIX_BOT_USERNAME", "colaig.assistant")
    matrix_bot_password: str = os.getenv("MATRIX_BOT_PASSWORD", "")
    matrix_login_type: str = os.getenv("MATRIX_LOGIN_TYPE", "password")
    matrix_access_token: str = os.getenv("MATRIX_ACCESS_TOKEN", "")
    matrix_device_id: str = os.getenv("MATRIX_DEVICE_ID", "")
    matrix_room_id: str = os.getenv("MATRIX_ROOM_ID", "")
    matrix_bot_display_name: str = os.getenv(
        "MATRIX_BOT_DISPLAY_NAME", "Albert - Assistant Tchap"
    )
    help_room_id: str = os.getenv("HELP_ROOM_ID", "")
    session_path: str = os.getenv("SESSION_PATH", "data/session/session.txt")
    loglevel: str = os.getenv("LOGLEVEL", "INFO")
    matrix_sync_timeout_s: int = int(os.getenv("MATRIX_SYNC_TIMEOUT_S", "30"))

    # Configuration API Albert
    albert_api_url: str = os.getenv("ALBERT_API_URL", "")
    albert_api_key: str = os.getenv("ALBERT_API_KEY", "")

    # Configuration du mode de logging
    console_logging: bool = os.getenv("CONSOLE_LOGGING", "true").lower() == "true"
    log_directory: str = os.getenv("LOG_DIRECTORY", "")

    # Configuration WebDAV
    webdav_url: str = os.getenv("WEBDAV_URL", "")
    webdav_username: str = os.getenv("WEBDAV_USERNAME", "")
    webdav_password: str = os.getenv("WEBDAV_PASSWORD", "")
    webdav_root_dir: str = os.getenv("WEBDAV_ROOT_DIR", ".albert")
    
    # Configuration des comportements
    BEHAVIOR_DIR: str = os.getenv("BEHAVIOR_DIR", "./behaviors")

    # Constantes pour les commandes et les modules actifs
    commands_exclusions: List[str] = field(default_factory=lambda: os.getenv("COMMANDS_EXCLUSIONS", "").split(",") if os.getenv("COMMANDS_EXCLUSIONS", "") else [])
    modules_exclusions: List[str] = field(default_factory=lambda: os.getenv("MODULES_EXCLUSIONS", "").split(",") if os.getenv("MODULES_EXCLUSIONS", "") else [])
    strict_inclusion_mode: bool = os.getenv("STRICT_INCLUSION_MODE", "true").lower() == "true"
    
    use_threading: bool = os.getenv("USE_THREADING", "false").lower() == "true"
