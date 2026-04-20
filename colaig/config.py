"""
Colaig — Chargement de configuration

Priorité de chargement :
1. Variables d'environnement (via .env / python-dotenv)
2. Fichier YAML config/default.yml
3. Valeurs par défaut dans ColaigConfig

Les variables d'env écrasent le YAML, qui écrase les defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from colaig.models import ColaigConfig, PlatformPolicy


# Chemin racine du projet (parent du package colaig/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Charge le fichier .env s'il existe à la racine du projet."""
    env_path = _PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _load_yaml(path: Path | None = None) -> dict:
    """Charge le fichier YAML de configuration par défaut.

    Args:
        path: Chemin vers le fichier YAML. Si None, utilise config/default.yml.

    Returns:
        Dictionnaire de configuration (vide si le fichier n'existe pas).
    """
    if path is None:
        path = _PROJECT_ROOT / "config" / "default.yml"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _env(name: str, default: str = "") -> str:
    """Lit une variable d'environnement (chaîne vide si absente)."""
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    """Lit une variable d'environnement comme entier."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Lit une variable d'environnement comme flottant."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_platform_policy(clients_yaml_path: Path | None = None) -> PlatformPolicy:
    """Charge la politique plateforme depuis clients.yml (section platform_policy).

    Retourne une PlatformPolicy vide (aucune contrainte) si le fichier est absent
    ou si la section platform_policy est omise — rétrocompatible single-client.

    Args:
        clients_yaml_path: Chemin vers clients.yml. Par défaut config/clients.yml.

    Returns:
        PlatformPolicy chargée (ou vide si absente).
    """
    if clients_yaml_path is None:
        clients_yaml_path = _PROJECT_ROOT / "config" / "clients.yml"
    data = _load_yaml(clients_yaml_path)
    policy_data = data.get("platform_policy", {})
    if not policy_data:
        return PlatformPolicy()
    return PlatformPolicy(
        allowed_storage_backends=policy_data.get("allowed_storage_backends", []),
        allowed_llm_endpoints=policy_data.get("allowed_llm_endpoints", []),
        allowed_mcp_auth_modes=policy_data.get("allowed_mcp_auth_modes", []),
        enforce_mcp_auth=bool(policy_data.get("enforce_mcp_auth", False)),
    )


def validate_client_against_policy(client_id: str, client_config: ColaigConfig, policy: PlatformPolicy) -> None:
    """Valide la config d'un client contre la politique plateforme.

    Appelée au démarrage pour chaque client en mode multi-client.
    Lève ValueError si la config viole la policy — erreur fatale.

    Args:
        client_id    : Identifiant du client (pour le message d'erreur).
        client_config: Config résolue du client.
        policy       : PlatformPolicy de l'opérateur.
    """
    if policy.allowed_storage_backends and client_config.storage_backend not in policy.allowed_storage_backends:
        raise ValueError(
            f"Client '{client_id}': backend storage '{client_config.storage_backend}' "
            f"non autorisé par la policy plateforme "
            f"(autorisés : {policy.allowed_storage_backends})"
        )

    if policy.allowed_mcp_auth_modes and client_config.mcp_auth_mode not in policy.allowed_mcp_auth_modes:
        raise ValueError(
            f"Client '{client_id}': mode auth MCP '{client_config.mcp_auth_mode}' "
            f"non autorisé par la policy plateforme "
            f"(autorisés : {policy.allowed_mcp_auth_modes})"
        )

    if policy.allowed_llm_endpoints and client_config.albert_api_url:
        if not any(client_config.albert_api_url.startswith(ep) for ep in policy.allowed_llm_endpoints):
            raise ValueError(
                f"Client '{client_id}': endpoint LLM '{client_config.albert_api_url}' "
                f"non autorisé par la policy plateforme "
                f"(autorisés : {policy.allowed_llm_endpoints})"
            )


def validate_client_config_against_policy(
    client_id: str,
    cc: "ClientConfig",
    policy: PlatformPolicy,
    global_config: "ColaigConfig",
) -> None:
    """Valide un ClientConfig contre la PlatformPolicy.

    Version de validate_client_against_policy opérant directement sur ClientConfig.
    Appelée au démarrage multi-client avant de lancer chaque stack.

    Args:
        client_id    : Identifiant du client.
        cc           : ClientConfig chargé depuis clients.yml.
        policy       : PlatformPolicy opérateur.
        global_config: ColaigConfig globale (pour résoudre les valeurs héritées).
    """
    # Storage backend
    effective_storage = cc.storage_backend or global_config.storage_backend
    if policy.allowed_storage_backends and effective_storage not in policy.allowed_storage_backends:
        raise ValueError(
            f"Client '{client_id}': backend storage '{effective_storage}' "
            f"non autorisé par la policy plateforme "
            f"(autorisés : {policy.allowed_storage_backends})"
        )

    # MCP auth mode (hérite "token" si absent)
    effective_auth_mode = cc.mcp_auth_mode or global_config.mcp_auth_mode or "token"
    if policy.allowed_mcp_auth_modes and effective_auth_mode not in policy.allowed_mcp_auth_modes:
        raise ValueError(
            f"Client '{client_id}': mode auth MCP '{effective_auth_mode}' "
            f"non autorisé par la policy plateforme "
            f"(autorisés : {policy.allowed_mcp_auth_modes})"
        )

    # LLM endpoint (hérite de la config globale si pas d'override)
    effective_llm_url = cc.llm_api_url or global_config.albert_api_url
    if policy.allowed_llm_endpoints and effective_llm_url:
        if not any(effective_llm_url.startswith(ep) for ep in policy.allowed_llm_endpoints):
            raise ValueError(
                f"Client '{client_id}': endpoint LLM '{effective_llm_url}' "
                f"non autorisé par la policy plateforme "
                f"(autorisés : {policy.allowed_llm_endpoints})"
            )


def load_config(yaml_path: Path | None = None) -> ColaigConfig:
    """Charge la configuration complète de Colaig.

    Ordre de priorité (le plus prioritaire en premier) :
    1. config/runtime.yml (écrit par les outils MCP colaig_set_backend)
    2. Variables d'environnement / .env
    3. Fichier YAML (config/default.yml)
    4. Valeurs par défaut de ColaigConfig

    Args:
        yaml_path: Chemin optionnel vers un fichier YAML alternatif.

    Returns:
        Instance ColaigConfig prête à l'emploi.
    """
    # Étape 1 : charger .env dans os.environ
    _load_env()

    # Étape 2 : appliquer config/runtime.yml (priorité sur .env)
    # runtime.yml est écrit par colaig_set_backend via MCP — il reflète
    # la config active choisie par l'admin, indépendamment des env vars.
    _runtime_path = _PROJECT_ROOT / "config" / "runtime.yml"
    if _runtime_path.is_file():
        try:
            from colaig.platform.config_store import ConfigStore
            ConfigStore(_runtime_path).apply_to_env()
        except Exception:
            pass  # pas critique — .env reste valide

    # Étape 3 : charger le YAML de defaults
    yml = _load_yaml(yaml_path)
    rag = yml.get("rag", {})
    indexing = yml.get("indexing", {})
    context = yml.get("context", {})

    # Étape 3 : construire la config — env > yaml > defaults
    return ColaigConfig(
        # Backends (choix du provider)
        storage_backend=_env("STORAGE_BACKEND", "webdav"),
        messaging_backend=_env("MESSAGING_BACKEND", "matrix"),
        # Matrix / Tchap
        matrix_homeserver=_env("MATRIX_HOMESERVER"),
        matrix_username=_env("MATRIX_USERNAME"),
        matrix_password=_env("MATRIX_PASSWORD"),
        # Albert API
        albert_api_url=_env("ALBERT_API_URL", "https://albert-api.etalab.gouv.fr"),
        albert_api_key=_env("ALBERT_API_KEY"),
        albert_model_chat=_env("ALBERT_MODEL_CHAT", "openai/gpt-oss-120b"),
        albert_model_embed=_env("ALBERT_MODEL_EMBED", "BAAI/bge-m3"),
        albert_model_rerank=_env("ALBERT_MODEL_RERANK", "BAAI/bge-reranker-v2-m3"),
        albert_model_ocr=_env("ALBERT_MODEL_OCR", "mistralai/Mistral-Small-3.2-24B-Instruct-2506"),
        albert_model_audio=_env("ALBERT_MODEL_AUDIO", "openai/whisper-large-v3"),
        ocr_page_delay_s=float(_env("COLAIG_OCR_PAGE_DELAY_S", "3.0")),
        # Capability chains
        cap_chat=_env("COLAIG_CAP_CHAT"),
        cap_embed=_env("COLAIG_CAP_EMBED"),
        cap_ocr=_env("COLAIG_CAP_OCR"),
        cap_rerank=_env("COLAIG_CAP_RERANK"),
        cap_audio=_env("COLAIG_CAP_AUDIO"),
        # LLM Backend générique (openai / azure / ollama — vide = albert par défaut)
        llm_backend=_env("LLM_BACKEND", "albert"),
        llm_api_url=_env("LLM_API_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model_chat=_env("LLM_MODEL_CHAT"),
        llm_model_embed=_env("LLM_MODEL_EMBED"),
        llm_azure_resource=_env("LLM_AZURE_RESOURCE"),
        llm_azure_deployment_chat=_env("LLM_AZURE_DEPLOYMENT", "gpt-4o"),
        llm_azure_deployment_embed=_env("LLM_AZURE_EMBED_DEPLOYMENT", "text-embedding-3-small"),
        llm_azure_api_version=_env("LLM_AZURE_API_VERSION", "2024-02-01"),
        # Storage : WebDAV
        webdav_url=_env("WEBDAV_URL"),
        webdav_username=_env("WEBDAV_USERNAME"),
        webdav_password=_env("WEBDAV_PASSWORD"),
        # Storage : Bigfolder
        bigfolder_api_url=_env("BIGFOLDER_API_URL"),
        bigfolder_service_token=_env("BIGFOLDER_SERVICE_TOKEN"),
        bigfolder_root=_env("BIGFOLDER_ROOT", "/"),
        # Storage : Local
        local_storage_path=_env("LOCAL_STORAGE_PATH"),
        # Storage : S3 / MinIO
        s3_endpoint_url=_env("S3_ENDPOINT_URL"),
        s3_access_key=_env("S3_ACCESS_KEY"),
        s3_secret_key=_env("S3_SECRET_KEY"),
        s3_bucket_name=_env("S3_BUCKET_NAME"),
        s3_prefix=_env("S3_PREFIX"),
        s3_region=_env("S3_REGION", "us-east-1"),
        # Storage : Microsoft Graph (OneDrive / SharePoint)
        msgraph_tenant_id=_env("MSGRAPH_TENANT_ID"),
        msgraph_client_id=_env("MSGRAPH_CLIENT_ID"),
        msgraph_client_secret=_env("MSGRAPH_CLIENT_SECRET"),
        msgraph_drive_user_id=_env("MSGRAPH_DRIVE_USER_ID"),
        msgraph_drive_id=_env("MSGRAPH_DRIVE_ID"),
        msgraph_root_path=_env("MSGRAPH_ROOT_PATH"),
        # Storage : Box (JWT Server Authentication)
        box_config_file=_env("BOX_CONFIG_FILE"),       # JSON config Box (prioritaire)
        box_client_id=_env("BOX_CLIENT_ID"),
        box_client_secret=_env("BOX_CLIENT_SECRET"),
        box_enterprise_id=_env("BOX_ENTERPRISE_ID"),   # Enterprise ID Box
        box_public_key_id=_env("BOX_PUBLIC_KEY_ID"),   # ID clé publique JWT
        box_private_key=_env("BOX_PRIVATE_KEY"),       # PEM (avec \n)
        box_passphrase=_env("BOX_PASSPHRASE"),         # Passphrase clé privée
        box_root_folder_id=_env("BOX_ROOT_FOLDER_ID", "0"),
        box_user_id=_env("BOX_USER_ID"),               # Vide = service account
        box_webhook_primary_key=_env("BOX_WEBHOOK_PRIMARY_KEY"),     # Clé HMAC principale
        box_webhook_secondary_key=_env("BOX_WEBHOOK_SECONDARY_KEY"), # Clé secondaire (rotation)
        # Storage : Google Drive (Service Account JWT)
        gdrive_service_account_json=_env("GDRIVE_SERVICE_ACCOUNT_JSON"),  # JSON ou chemin de fichier
        gdrive_root_folder_id=_env("GDRIVE_ROOT_FOLDER_ID", "root"),
        gdrive_impersonate_user=_env("GDRIVE_IMPERSONATE_USER"),
        # Messaging : Telegram
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_webhook_url=_env("TELEGRAM_WEBHOOK_URL"),
        # Application
        log_level=_env("COLAIG_LOG_LEVEL", "INFO"),
        data_dir=_env("COLAIG_DATA_DIR", "/app/data"),
        web_port=_env_int("COLAIG_WEB_PORT", 8000),
        # RAG — env > yaml > dataclass defaults
        default_similarity_threshold=_env_float(
            "COLAIG_SIMILARITY_THRESHOLD",
            rag.get("similarity_threshold", 0.3),
        ),
        default_max_results=_env_int(
            "COLAIG_MAX_RESULTS",
            rag.get("max_results", 5),
        ),
        chunk_size=_env_int(
            "COLAIG_CHUNK_SIZE",
            rag.get("chunk_size", 800),
        ),
        chunk_overlap=_env_int(
            "COLAIG_CHUNK_OVERLAP",
            rag.get("chunk_overlap", 100),
        ),
        # Polling
        index_refresh_interval_seconds=_env_int(
            "COLAIG_INDEX_REFRESH",
            indexing.get("refresh_interval_seconds", 300),
        ),
        workspace_cache_ttl_seconds=_env_int(
            "COLAIG_WORKSPACE_CACHE_TTL",
            context.get("workspace_cache_ttl_seconds", 60),
        ),
        # Phase 2 : Pipeline agents + MCP
        agents_enabled=_env("COLAIG_AGENTS_ENABLED", "").lower() in ("1", "true", "yes"),
        mcp_enabled=_env("COLAIG_MCP_ENABLED", "").lower() in ("1", "true", "yes"),
        analyser_temperature=_env_float("COLAIG_ANALYSER_TEMPERATURE", 0.1),
        synthesiser_temperature=_env_float("COLAIG_SYNTHESISER_TEMPERATURE", 0.3),
        # Phase 5 : Orchestrateur agentique + Mémoire conversationnelle
        orchestrator_max_iterations=_env_int("COLAIG_ORCHESTRATOR_MAX_ITERATIONS", 5),
        orchestrator_temperature=_env_float("COLAIG_ORCHESTRATOR_TEMPERATURE", 0.1),
        conversation_memory_max_stored=_env_int("COLAIG_CONVERSATION_MEMORY_MAX_STORED", 100),
        conversation_memory_max_retrieved=_env_int("COLAIG_CONVERSATION_MEMORY_MAX_RETRIEVED", 10),
        analyser_use_tool_calling=_env("COLAIG_ANALYSER_USE_TOOL_CALLING", "").lower() in ("1", "true", "yes"),
        # DocumentIndex : métadonnées de fichiers décentralisées
        document_index_enabled=_env("COLAIG_DOCUMENT_INDEX_ENABLED", "").lower() in ("1", "true", "yes"),
        document_index_max_pending=_env_int("COLAIG_DOCUMENT_INDEX_MAX_PENDING", 10),
        document_index_refresh_interval=_env_int("COLAIG_DOCUMENT_INDEX_REFRESH", 600),
        document_index_cache_ttl=_env_int("COLAIG_DOCUMENT_INDEX_CACHE_TTL", 300),
        document_index_ai_analysis=_env("COLAIG_DOCUMENT_INDEX_AI_ANALYSIS", "true").lower() not in ("0", "false", "no"),
        # Phase 6 : modèles multi-niveaux + TaskExecutor
        albert_model_light=_env("ALBERT_MODEL_LIGHT", "mistralai/Ministral-3-8B-Instruct-2512"),
        albert_model_medium=_env("ALBERT_MODEL_MEDIUM", "mistralai/Mistral-Small-3.2-24B-Instruct-2506"),
        task_executor_max_concurrent=_env_int("TASK_EXECUTOR_MAX_CONCURRENT", 20),
        task_executor_queue_ttl=_env_int("TASK_EXECUTOR_QUEUE_TTL", 1800),
        agents_phase6_enabled=_env("COLAIG_AGENTS_PHASE6_ENABLED", "").lower() in ("1", "true", "yes"),
        # Auto-discovery de workspaces
        auto_discover_enabled=_env("COLAIG_AUTO_DISCOVER_ENABLED", "").lower() in ("1", "true", "yes"),
        auto_discover_interval=_env_int("COLAIG_AUTO_DISCOVER_INTERVAL", 600),
        # Mode C : Tâches autonomes planifiées
        tasks_enabled=_env("COLAIG_TASKS_ENABLED", "").lower() in ("1", "true", "yes"),
        task_max_concurrent=_env_int("COLAIG_TASK_MAX_CONCURRENT", 3),
        task_scheduler_interval=_env_int("COLAIG_TASK_SCHEDULER_INTERVAL", 60),
        task_max_runs_kept=_env_int("COLAIG_TASK_MAX_RUNS_KEPT", 10),
        task_session_timeout=_env_int("COLAIG_TASK_SESSION_TIMEOUT", 1800),
        task_max_error_count=_env_int("COLAIG_TASK_MAX_ERROR_COUNT", 3),
        # Authentification MCP par token
        base_url=_env("COLAIG_BASE_URL", ""),
        mcp_auth_enabled=_env("COLAIG_MCP_AUTH_ENABLED", "").lower() in ("1", "true", "yes"),
        admin_user_ids=[u.strip() for u in _env("COLAIG_ADMIN_USER_IDS", "").split(",") if u.strip()],
        default_workspace_id=_env("COLAIG_DEFAULT_WORKSPACE_ID", ""),
        # OIDC SSO (auth MCP enterprise)
        mcp_auth_mode=_env("COLAIG_MCP_AUTH_MODE", "token"),
        oidc_issuer=_env("COLAIG_OIDC_ISSUER"),
        oidc_audience=_env("COLAIG_OIDC_AUDIENCE"),
        oidc_jwks_uri=_env("COLAIG_OIDC_JWKS_URI"),
        # RAG hybride : BM25 + RRF + Contextual Chunking + HyDE
        hybrid_search_enabled=_env("COLAIG_HYBRID_SEARCH_ENABLED", "").lower() in ("1", "true", "yes"),
        contextual_chunking_enabled=_env("COLAIG_CONTEXTUAL_CHUNKING_ENABLED", "").lower() in ("1", "true", "yes"),
        contextual_model=_env("COLAIG_CONTEXTUAL_MODEL", ""),
        hyde_enabled=_env("COLAIG_HYDE_ENABLED", "").lower() in ("1", "true", "yes"),
        hyde_query_weight=_env_float("COLAIG_HYDE_QUERY_WEIGHT", 0.5),
        rrf_k_constant=_env_int("COLAIG_RRF_K_CONSTANT", 60),
    )
