"""
Colaig — ConfigStore

Persistance de la configuration runtime (backends, credentials) dans
config/runtime.yml. Priorité maximale au démarrage : écrase les valeurs
de .env pour permettre la configuration via les outils MCP.

Zero database — utilise un fichier YAML, comme tout le reste de Colaig.

Format de config/runtime.yml :

    storage:
      backend: webdav
      url: https://nextcloud.example.fr/remote.php/dav/files/colaig/
      username: colaig
      password: xxx

    messaging:
      backend: matrix
      homeserver: https://matrix.agent.tchap.gouv.fr
      username: "@colaig:agent.tchap.gouv.fr"
      password: xxx

    llm:
      backend: albert
      api_url: https://albert.api.etalab.gouv.fr
      api_key: sk-xxx
      model_chat: openai/gpt-oss-120b
      model_embed: BAAI/bge-m3
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Mapping section runtime.yml → variables d'environnement correspondantes
_STORAGE_ENV_MAP = {
    "backend":     "STORAGE_BACKEND",
    "url":         "WEBDAV_URL",
    "username":    "WEBDAV_USERNAME",
    "password":    "WEBDAV_PASSWORD",
    "path":        "LOCAL_STORAGE_PATH",
    "endpoint_url": "S3_ENDPOINT_URL",
    "access_key":  "S3_ACCESS_KEY",
    "secret_key":  "S3_SECRET_KEY",
    "bucket":      "S3_BUCKET_NAME",
    "prefix":      "S3_PREFIX",
    "region":      "S3_REGION",
    "tenant_id":   "MSGRAPH_TENANT_ID",
    "client_id":   "MSGRAPH_CLIENT_ID",
    "client_secret": "MSGRAPH_CLIENT_SECRET",
    "drive_user_id": "MSGRAPH_DRIVE_USER_ID",
    "drive_id":    "MSGRAPH_DRIVE_ID",
    "root_path":   "MSGRAPH_ROOT_PATH",
    "bigfolder_api_url": "BIGFOLDER_API_URL",
    "bigfolder_service_token": "BIGFOLDER_SERVICE_TOKEN",
    "bigfolder_root": "BIGFOLDER_ROOT",
}

_MESSAGING_ENV_MAP = {
    "backend":      "MESSAGING_BACKEND",
    "homeserver":   "MATRIX_HOMESERVER",
    "username":     "MATRIX_USERNAME",
    "password":     "MATRIX_PASSWORD",
    "bot_token":    "TELEGRAM_BOT_TOKEN",
    "webhook_url":  "TELEGRAM_WEBHOOK_URL",
}

_LLM_ENV_MAP = {
    "backend":       "LLM_BACKEND",
    "api_url":       "LLM_API_URL",
    "api_key":       "LLM_API_KEY",
    "model_chat":    "LLM_MODEL_CHAT",
    "model_embed":   "LLM_MODEL_EMBED",
    "azure_resource": "LLM_AZURE_RESOURCE",
    "azure_deployment_chat":  "LLM_AZURE_DEPLOYMENT",
    "azure_deployment_embed": "LLM_AZURE_EMBED_DEPLOYMENT",
    "azure_api_version":      "LLM_AZURE_API_VERSION",
}

_SECTION_ENV_MAPS = {
    "storage":   _STORAGE_ENV_MAP,
    "messaging": _MESSAGING_ENV_MAP,
    "llm":       _LLM_ENV_MAP,
}


class ConfigStore:
    """Stockage de configuration runtime dans config/runtime.yml.

    Écrit par les outils MCP colaig_set_backend.
    Lu par config.py au démarrage (priorité sur .env).

    Args:
        path: Chemin vers le fichier YAML (défaut : config/runtime.yml).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    # ── Lecture ───────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """Charge le fichier runtime.yml. Retourne {} si absent ou invalide."""
        if not self._path.is_file():
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning("ConfigStore: lecture de %s impossible: %s", self._path, e)
            return {}

    def get(self, section: str) -> dict:
        """Retourne la section (storage / messaging / llm), ou {}."""
        return self.load().get(section, {})

    def get_all(self) -> dict:
        """Retourne la config complète."""
        return self.load()

    # ── Écriture ──────────────────────────────────────────────────────────────

    def set(self, section: str, values: dict) -> None:
        """Écrit une section dans runtime.yml (crée le fichier si nécessaire)."""
        data = self.load()
        data[section] = {k: v for k, v in values.items() if v is not None and v != ""}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info("ConfigStore: section '%s' sauvegardée dans %s", section, self._path)

    # ── Application dans os.environ ───────────────────────────────────────────

    def apply_to_env(self, section: str | None = None) -> None:
        """Applique runtime.yml dans os.environ (priorité sur .env existant).

        Args:
            section: Si fourni, applique uniquement cette section.
                     Si None, applique toutes les sections.
        """
        data = self.load()
        sections = [section] if section else list(_SECTION_ENV_MAPS.keys())

        for sec in sections:
            env_map = _SECTION_ENV_MAPS.get(sec, {})
            sec_data = data.get(sec, {})
            for yaml_key, env_var in env_map.items():
                if yaml_key in sec_data and sec_data[yaml_key]:
                    os.environ[env_var] = str(sec_data[yaml_key])

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return self._path.is_file()
