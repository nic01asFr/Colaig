"""
Colaig — Platform Provisioner

Gestion programmatique des instances client :
  - Ajout / mise à jour d'un client dans clients.yml
  - Génération du package self-hosted (docker-compose + .env) sous forme de ZIP

Zero database — tout passe par fichiers YAML, cohérent avec le reste de Colaig.

Usage typique :
    provisioner = ClientProvisioner("config/clients.yml")
    provisioner.upsert(client_id="org-rh", storage={...}, messaging={...})
    zip_bytes = provisioner.build_selfhosted_package(client_id="org-rh", base_url="https://colaig.org.fr")
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# ── Mapping config → variables d'environnement (.env) ─────────────────────────

_STORAGE_TO_ENV: dict[str, str] = {
    "backend":             "STORAGE_BACKEND",
    "url":                 "WEBDAV_URL",
    "username":            "WEBDAV_USERNAME",
    "password":            "WEBDAV_PASSWORD",
    "path":                "LOCAL_STORAGE_PATH",
    "endpoint_url":        "S3_ENDPOINT_URL",
    "access_key":          "S3_ACCESS_KEY",
    "secret_key":          "S3_SECRET_KEY",
    "bucket":              "S3_BUCKET_NAME",
    "prefix":              "S3_PREFIX",
    "region":              "S3_REGION",
    "tenant_id":           "MSGRAPH_TENANT_ID",
    "client_id":           "MSGRAPH_CLIENT_ID",
    "client_secret":       "MSGRAPH_CLIENT_SECRET",
    "drive_user_id":       "MSGRAPH_DRIVE_USER_ID",
    "drive_id":            "MSGRAPH_DRIVE_ID",
    "root_path":           "MSGRAPH_ROOT_PATH",
    "bigfolder_api_url":   "BIGFOLDER_API_URL",
    "bigfolder_service_token": "BIGFOLDER_SERVICE_TOKEN",
    "bigfolder_root":      "BIGFOLDER_ROOT",
    "service_account_json": "GDRIVE_SERVICE_ACCOUNT_JSON",
    "root_folder_id":      "GDRIVE_ROOT_FOLDER_ID",
    "impersonate_user":    "GDRIVE_IMPERSONATE_USER",
}

_MESSAGING_TO_ENV: dict[str, str] = {
    "backend":     "MESSAGING_BACKEND",
    "homeserver":  "MATRIX_HOMESERVER",
    "username":    "MATRIX_USERNAME",
    "password":    "MATRIX_PASSWORD",
    "bot_token":   "TELEGRAM_BOT_TOKEN",
    "webhook_url": "TELEGRAM_WEBHOOK_URL",
}

_LLM_TO_ENV: dict[str, str] = {
    "backend":                "LLM_BACKEND",
    "api_url":                "LLM_API_URL",
    "api_key":                "LLM_API_KEY",
    "model_chat":             "LLM_MODEL_CHAT",
    "model_embed":            "LLM_MODEL_EMBED",
    "azure_resource":         "LLM_AZURE_RESOURCE",
    "azure_deployment_chat":  "LLM_AZURE_DEPLOYMENT",
    "azure_deployment_embed": "LLM_AZURE_EMBED_DEPLOYMENT",
    "azure_api_version":      "LLM_AZURE_API_VERSION",
}

_MCP_AUTH_TO_ENV: dict[str, str] = {
    "mode":         "COLAIG_MCP_AUTH_MODE",
    "oidc_issuer":  "COLAIG_OIDC_ISSUER",
    "oidc_audience": "COLAIG_OIDC_AUDIENCE",
    "oidc_jwks_uri": "COLAIG_OIDC_JWKS_URI",
}


# ── Template docker-compose.yml ────────────────────────────────────────────────

_DOCKER_COMPOSE_TEMPLATE = """\
version: '3.8'

services:
  colaig:
    image: colaig/colaig:latest
    container_name: colaig-{client_id}
    ports:
      - "${{COLAIG_WEB_PORT:-8000}}:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - colaig_home:/root/.colaig
      - ./secrets:/app/secrets:ro
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  colaig_home:

# Colaig est STATELESS — tout l'état est chez le provider de stockage configuré.
# Aucun service externe (postgres, redis, qdrant) requis.
"""


class ClientProvisioner:
    """Gère les entrées client dans clients.yml et génère les packages self-hosted.

    Args:
        clients_yml_path: Chemin vers clients.yml (crée le fichier si absent).
    """

    def __init__(self, clients_yml_path: str | Path) -> None:
        self._path = Path(clients_yml_path)

    # ── Lecture ───────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """Charge clients.yml. Retourne {"clients": []} si absent ou invalide."""
        if not self._path.is_file():
            return {"clients": []}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return {"clients": []}
            if "clients" not in data:
                data["clients"] = []
            return data
        except Exception as e:
            logger.warning("ClientProvisioner: lecture de %s impossible: %s", self._path, e)
            return {"clients": []}

    def get_client(self, client_id: str) -> Optional[dict]:
        """Retourne la config d'un client par ID, ou None si absent."""
        data = self.load()
        for client in data.get("clients", []):
            if client.get("id") == client_id:
                return client
        return None

    def list_clients(self) -> list[str]:
        """Retourne la liste des IDs client déclarés."""
        data = self.load()
        return [c.get("id", "") for c in data.get("clients", []) if c.get("id")]

    # ── Écriture ──────────────────────────────────────────────────────────────

    def upsert(
        self,
        client_id: str,
        storage: dict,
        messaging: dict,
        llm: Optional[dict] = None,
        mcp_auth: Optional[dict] = None,
    ) -> str:
        """Crée ou met à jour un client dans clients.yml.

        Args:
            client_id: Identifiant unique du client.
            storage: Config storage (backend + credentials).
            messaging: Config messaging (backend + credentials).
            llm: Config LLM optionnelle (hérite de global si None).
            mcp_auth: Config auth MCP optionnelle (mode token/oidc + params OIDC).

        Returns:
            "created" ou "updated".
        """
        data = self.load()
        clients: list = data.get("clients", [])

        # Construire l'entrée client
        entry: dict = {"id": client_id, "storage": storage, "messaging": messaging}
        if llm:
            entry["llm"] = llm
        if mcp_auth:
            entry["mcp_auth"] = mcp_auth

        # Upsert
        action = "created"
        for i, c in enumerate(clients):
            if c.get("id") == client_id:
                clients[i] = entry
                action = "updated"
                break
        else:
            clients.append(entry)

        data["clients"] = clients
        self._save(data)
        logger.info("ClientProvisioner: client '%s' %s dans %s", client_id, action, self._path)
        return action

    def delete(self, client_id: str) -> bool:
        """Supprime un client de clients.yml. Retourne True si trouvé et supprimé."""
        data = self.load()
        clients = data.get("clients", [])
        before = len(clients)
        data["clients"] = [c for c in clients if c.get("id") != client_id]
        if len(data["clients"]) == before:
            return False
        self._save(data)
        logger.info("ClientProvisioner: client '%s' supprimé de %s", client_id, self._path)
        return True

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

    # ── Package self-hosted ────────────────────────────────────────────────────

    def build_selfhosted_package(
        self,
        client_id: str,
        base_url: str = "",
        albert_api_url: str = "https://albert-api.etalab.gouv.fr",
        albert_api_key: str = "",
        albert_model_chat: str = "openai/gpt-oss-120b",
        albert_model_embed: str = "BAAI/bge-m3",
    ) -> bytes:
        """Génère un package self-hosted ZIP (docker-compose.yml + .env).

        Args:
            client_id: ID du client dont on génère le package.
            base_url: URL publique de l'instance Colaig (pour les configs MCP).
            albert_api_url: URL de l'API Albert (LLM).
            albert_api_key: Clé API Albert.
            albert_model_chat: Modèle chat Albert.
            albert_model_embed: Modèle embeddings Albert.

        Returns:
            Contenu ZIP en bytes, prêt à être téléchargé.

        Raises:
            KeyError: Si le client_id n'existe pas dans clients.yml.
        """
        client = self.get_client(client_id)
        if client is None:
            raise KeyError(f"Client '{client_id}' introuvable dans {self._path}")

        env_lines = _build_env_file(
            client=client,
            base_url=base_url,
            albert_api_url=albert_api_url,
            albert_api_key=albert_api_key,
            albert_model_chat=albert_model_chat,
            albert_model_embed=albert_model_embed,
        )
        compose_content = _DOCKER_COMPOSE_TEMPLATE.format(client_id=client_id)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("docker-compose.yml", compose_content)
            zf.writestr(".env", env_lines)
        buf.seek(0)
        return buf.read()


# ── Helpers internes ───────────────────────────────────────────────────────────

def _build_env_file(
    client: dict,
    base_url: str,
    albert_api_url: str,
    albert_api_key: str,
    albert_model_chat: str,
    albert_model_embed: str,
) -> str:
    """Génère le contenu du fichier .env à partir de la config client."""
    lines: list[str] = [
        "# Colaig — Configuration self-hosted",
        "# Généré automatiquement — adapter selon vos besoins",
        "",
        "# === Application ===",
        f"COLAIG_BASE_URL={base_url}",
        "COLAIG_WEB_PORT=8000",
        "COLAIG_LOG_LEVEL=INFO",
        "COLAIG_DATA_DIR=/app/data",
        "COLAIG_MCP_ENABLED=true",
        "COLAIG_AGENTS_ENABLED=true",
        "",
    ]

    # Storage
    storage = client.get("storage", {})
    lines.append("# === Storage ===")
    for key, env_var in _STORAGE_TO_ENV.items():
        val = storage.get(key, "")
        if val:
            lines.append(f"{env_var}={val}")
    lines.append("")

    # Messaging
    messaging = client.get("messaging", {})
    lines.append("# === Messaging ===")
    for key, env_var in _MESSAGING_TO_ENV.items():
        val = messaging.get(key, "")
        if val:
            lines.append(f"{env_var}={val}")
    lines.append("")

    # LLM (client override ou Albert global)
    llm = client.get("llm")
    if llm:
        lines.append("# === LLM (override client) ===")
        for key, env_var in _LLM_TO_ENV.items():
            val = llm.get(key, "")
            if val:
                lines.append(f"{env_var}={val}")
    else:
        lines.append("# === LLM (Albert — souverain) ===")
        lines.append(f"ALBERT_API_URL={albert_api_url}")
        lines.append(f"ALBERT_API_KEY={albert_api_key}")
        lines.append(f"ALBERT_MODEL_CHAT={albert_model_chat}")
        lines.append(f"ALBERT_MODEL_EMBED={albert_model_embed}")
    lines.append("")

    # MCP auth
    mcp_auth = client.get("mcp_auth")
    if mcp_auth:
        lines.append("# === Auth MCP ===")
        for key, env_var in _MCP_AUTH_TO_ENV.items():
            val = mcp_auth.get(key, "")
            if val:
                lines.append(f"{env_var}={val}")
        lines.append("")

    return "\n".join(lines)
