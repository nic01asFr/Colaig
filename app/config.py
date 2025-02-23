# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import logging
import time
from pathlib import Path
from typing import Optional, Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from _version import __version__

COMMAND_PREFIX = "!"

APP_VERSION = __version__


class BaseConfig(BaseSettings):
    # allows us to clean up the imports into multiple parts
    # https://stackoverflow.com/questions/77328900/nested-settings-with-pydantic-settings
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env", extra="ignore"
    )  # allows nested configs


class Config(BaseConfig):
    # General
    systemd_logging: bool = Field(
        True, description="Enable / disable logging with systemd.journal.JournalHandler"
    )
    matrix_home_server: str = Field("", description="Tchap home server URL")
    matrix_bot_username: str = Field("", description="Username of our matrix bot")
    matrix_bot_password: str = Field("", description="Password of our matrix bot")
    errors_room_id: str | None = Field(None, description="Room ID to send errors to")
    user_allowed_domains: list[str] = Field(
        ["*"],
        description="List of allowed Tchap users email domains allowed to use Albert Tchap",
    )
    groups_used: list[str] = Field(["basic"], description="List of commands groups to use")
    last_activity: int = Field(int(time.time()), description="Last activity timestamp")

    # Grist Api Key
    grist_api_server: str = Field("", description="Grist Api Server")
    grist_api_key: str = Field("", description="Grist API Key")
    grist_users_table_id: str = Field("", description="Grist Users doc ID")
    grist_users_table_name: str = Field("", description="Grist Users table name/ID")

    # Albert API settings
    albert_api_url: str = Field("https://api.albert.ai", description="Albert API base URL")
    albert_api_token: str = Field(..., description="Albert API token")

    # WebDAV Configuration
    webdav_url: str = Field("", description="URL du serveur WebDAV")
    webdav_username: str = Field("", description="Nom d'utilisateur WebDAV")
    webdav_password: str = Field("", description="Mot de passe WebDAV")
    webdav_root_path: str = Field("/documents", description="Chemin racine WebDAV")
    webdav_index_name: str = Field("index.json", description="Nom du fichier d'index WebDAV")

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

    # Configuration de Colaig
    colaig_behavior_path: str = Field(
        ".colaig/behavior",
        description="Chemin vers le dossier de configuration comportementale"
    )
    colaig_indexes_path: str = Field(
        ".colaig/indexes",
        description="Chemin vers les index FAISS"
    )
    colaig_response_format: str = Field(
        "concise",
        description="Format de réponse (concise, detailed)"
    )
    colaig_show_sources: bool = Field(
        False,
        description="Afficher les sources dans la réponse"
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

    @property
    def is_conversation_obsolete(self) -> bool:
        return int(time.time()) - self.last_activity > self.conversation_obsolescence

    def update_last_activity(self) -> None:
        self.last_activity = int(time.time())


# Default config
env_config = Config(
    colaig_behavior_path=".colaig/behavior",
    colaig_indexes_path=".colaig/indexes",
    colaig_response_format="concise",
    colaig_show_sources=False
)


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
