"""
Colaig — Point d'entrée

Orchestre le démarrage : charge la config, initialise tous les composants
via factory pattern (storage + messaging), lance les services en parallèle.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from pathlib import Path

from colaig.config import load_config

# Context & Generator
from colaig.context.resolver import ContextResolver
from colaig.exceptions import StorageAuthError, StorageError
from colaig.messaging.handlers import MessageHandler
from colaig.models import ColaigConfig
from colaig.rag.behavior_indexer import BehaviorIndexer

# RAG pipeline
from colaig.rag.chunker import Chunker
from colaig.rag.embeddings import EmbeddingService
from colaig.rag.faiss_store import FaissStore
from colaig.rag.generator import Generator
from colaig.rag.indexer import Indexer
from colaig.rag.retriever import Retriever
from colaig.rag.skill_indexer import SkillIndexer
from colaig.utils.logging import setup_logging

# Web
from colaig.web.routes import create_app

logger = logging.getLogger(__name__)

# Suivi d'usage LLM par tenant — process-global (zéro-DB, reconstructible au restart).
from colaig import paths
from colaig.metrics import UsageTracker

_USAGE_TRACKER = UsageTracker()


# =============================================================================
# Factory pattern — instanciation des backends selon la config
# =============================================================================


def _embedding_namespace(api_key: str, model: str) -> str:
    """Namespace opaque pour le cache d'embeddings.

    Incorporé dans chaque clé de cache pour garantir qu'un vecteur dérivé
    des données d'un client ne peut jamais servir une requête d'un autre client,
    même si le texte source est identique (isolation by design).

    Hash SHA-256 tronqué à 16 chars — stable, sans stocker la clé en clair.
    """
    raw = f"{api_key}\x00{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def create_storage(config: ColaigConfig):
    """Crée le backend de stockage selon STORAGE_BACKEND.

    Returns:
        Instance implémentant StorageProtocol.
    """
    backend = config.storage_backend.lower()

    if backend == "webdav":
        from colaig.integrations.storage.webdav import WebDAVStorage
        return WebDAVStorage(
            base_url=config.webdav_url,
            username=config.webdav_username,
            password=config.webdav_password,
        )
    elif backend == "local":
        from colaig.integrations.storage.local import LocalStorage
        path = config.local_storage_path or f"{config.data_dir}/storage"
        return LocalStorage(base_path=path)
    elif backend == "bigfolder":
        from colaig.integrations.storage.bigfolder import BigfolderStorage
        return BigfolderStorage(
            api_url=config.bigfolder_api_url,
            service_token=config.bigfolder_service_token,
            bigfolder_root=config.bigfolder_root,
        )
    elif backend == "s3":
        from colaig.integrations.storage.s3 import S3Storage
        return S3Storage(
            bucket_name=config.s3_bucket_name,
            access_key=config.s3_access_key,
            secret_key=config.s3_secret_key,
            endpoint_url=config.s3_endpoint_url or None,
            prefix=config.s3_prefix,
            region=config.s3_region,
            session_token=config.s3_session_token or None,
        )
    elif backend == "msgraph":
        from colaig.integrations.storage.msgraph import MicrosoftGraphStorage
        return MicrosoftGraphStorage(
            tenant_id=config.msgraph_tenant_id,
            client_id=config.msgraph_client_id,
            client_secret=config.msgraph_client_secret,
            drive_user_id=config.msgraph_drive_user_id,
            drive_id=config.msgraph_drive_id,
            root_path=config.msgraph_root_path,
        )
    elif backend == "box":
        import json as _json

        from colaig.integrations.storage.box import BoxStorage

        client_id = config.box_client_id
        client_secret = config.box_client_secret
        enterprise_id = config.box_enterprise_id
        public_key_id = config.box_public_key_id
        private_key = config.box_private_key
        passphrase = config.box_passphrase
        webhook_primary_key = config.box_webhook_primary_key
        webhook_secondary_key = config.box_webhook_secondary_key

        # Charger le fichier JSON Box si fourni (prioritaire sur les vars individuelles)
        if config.box_config_file:
            with open(config.box_config_file, encoding="utf-8") as f:
                box_cfg = _json.load(f)
            app = box_cfg.get("boxAppSettings", {})
            auth = app.get("appAuth", {})
            client_id = client_id or app.get("clientID", "")
            client_secret = client_secret or app.get("clientSecret", "")
            enterprise_id = enterprise_id or box_cfg.get("enterpriseID", "")
            public_key_id = public_key_id or auth.get("publicKeyID", "")
            private_key = private_key or auth.get("privateKey", "")
            passphrase = passphrase or auth.get("passphrase", "")
            # Clés webhook présentes dans le JSON config Box
            webhooks = box_cfg.get("webhooks", {})
            webhook_primary_key = webhook_primary_key or webhooks.get("primaryKey", "")
            webhook_secondary_key = webhook_secondary_key or webhooks.get("secondaryKey", "")

        return BoxStorage(
            client_id=client_id,
            client_secret=client_secret,
            enterprise_id=enterprise_id,
            public_key_id=public_key_id,
            private_key=private_key,
            passphrase=passphrase,
            root_folder_id=config.box_root_folder_id,
            user_id=config.box_user_id,
            webhook_primary_key=webhook_primary_key,
            webhook_secondary_key=webhook_secondary_key,
        )
    elif backend == "gdrive":
        from colaig.integrations.storage.gdrive import GoogleDriveStorage
        # Gérer le cas où gdrive_service_account_json est un chemin de fichier
        sa_json = config.gdrive_service_account_json
        if sa_json and not sa_json.strip().startswith("{"):
            with open(sa_json, encoding="utf-8") as f:
                sa_json = f.read()
        return GoogleDriveStorage(
            service_account_json=sa_json,
            root_folder_id=config.gdrive_root_folder_id,
            impersonate_user=config.gdrive_impersonate_user,
        )
    else:
        raise ValueError(f"STORAGE_BACKEND inconnu: {backend}")


def create_messaging(config: ColaigConfig):
    """Crée le canal de messagerie selon MESSAGING_BACKEND.

    Returns:
        Instance implémentant MessagingProtocol.
    """
    backend = config.messaging_backend.lower()

    if backend == "matrix":
        from colaig.messaging.matrix import MatrixMessaging
        return MatrixMessaging(
            homeserver=config.matrix_homeserver,
            username=config.matrix_username,
            password=config.matrix_password,
        )
    elif backend == "telegram":
        from colaig.integrations.messaging.telegram import TelegramMessaging
        return TelegramMessaging(
            bot_token=config.telegram_bot_token,
            webhook_url=config.telegram_webhook_url,
        )
    elif backend == "none":
        # Mode MCP-only / dev — pas de canal de messagerie
        from colaig.messaging.noop import NoopMessaging
        return NoopMessaging()
    elif backend == "webchat":
        from colaig.messaging.webchat import WebChatMessaging
        return WebChatMessaging()
    elif backend == "slack":
        raise NotImplementedError("SlackMessaging pas encore implémenté")
    else:
        raise ValueError(f"MESSAGING_BACKEND inconnu: {backend}")


def create_llm_client(config: ColaigConfig, client_id: str = ""):
    """Crée le client LLM selon LLM_BACKEND.

    LLM_BACKEND=albert (défaut) → AlbertClient — backend souverain FR
    LLM_BACKEND=openai          → OpenAIClient — OpenAI, Mistral, Groq, Together AI...
    LLM_BACKEND=azure           → AzureClient  — Azure OpenAI Service
    LLM_BACKEND=ollama          → OllamaClient — Ollama local autohébergé

    Returns:
        Instance implémentant LLMClientProtocol.
    """
    backend = (config.llm_backend or "albert").lower()

    # Colaig ne restreint aucun endpoint LLM par défaut (décision D9) : la
    # souveraineté est portée par `platform_policy.allowed_llm_endpoints`, décidée par
    # l'exploitant. Le contrepoids de ce choix est la visibilité — l'endpoint
    # réellement joint est tracé au démarrage, sans quoi personne ne sait où partent
    # les conversations.
    _endpoint = {
        "albert": config.albert_api_url,
        "openai": config.llm_api_url or "https://api.openai.com",
        "azure": f"https://{config.llm_azure_resource}.openai.azure.com",
        "ollama": config.llm_api_url or "http://localhost:11434",
    }.get(backend, "?")
    logger.info("LLM : backend=%s endpoint=%s", backend, _endpoint)

    if backend == "albert":
        from colaig.integrations.albert import AlbertClient
        return AlbertClient(config, ocr_page_delay=config.ocr_page_delay_s,
                            usage_tracker=_USAGE_TRACKER, client_id=client_id)

    elif backend == "openai":
        from colaig.integrations.llm.openai_client import OpenAIClient
        return OpenAIClient(
            api_key=config.llm_api_key or config.albert_api_key,
            base_url=config.llm_api_url or "https://api.openai.com",
            model_chat=config.llm_model_chat or "gpt-4o",
            model_embed=config.llm_model_embed or "text-embedding-3-small",
            backend_name="OpenAI",
        )

    elif backend == "azure":
        from colaig.integrations.llm.azure_client import AzureClient
        return AzureClient(
            api_key=config.llm_api_key or config.albert_api_key,
            resource_name=config.llm_azure_resource,
            deployment_chat=config.llm_azure_deployment_chat,
            deployment_embed=config.llm_azure_deployment_embed,
            api_version=config.llm_azure_api_version,
        )

    elif backend == "ollama":
        from colaig.integrations.llm.ollama_client import OllamaClient
        return OllamaClient(
            base_url=config.llm_api_url or "http://localhost:11434",
            model_chat=config.llm_model_chat or "llama3.2",
            model_embed=config.llm_model_embed or "nomic-embed-text",
        )

    else:
        raise ValueError(f"LLM_BACKEND inconnu: {backend} (albert|openai|azure|ollama)")


def create_storage_for_client(cc):
    """Crée le backend de stockage depuis un ClientConfig.

    Args:
        cc: ClientConfig instance.

    Returns:
        Instance implémentant StorageProtocol.
    """
    backend = cc.storage_backend.lower()

    if backend == "webdav":
        from colaig.integrations.storage.webdav import WebDAVStorage
        return WebDAVStorage(
            base_url=cc.storage_webdav_url,
            username=cc.storage_webdav_username,
            password=cc.storage_webdav_password,
        )
    elif backend == "local":
        from colaig.integrations.storage.local import LocalStorage
        return LocalStorage(base_path=cc.storage_local_path)
    elif backend == "bigfolder":
        from colaig.integrations.storage.bigfolder import BigfolderStorage
        return BigfolderStorage(
            api_url=cc.storage_bigfolder_url,
            service_token=cc.storage_bigfolder_service_token,
            bigfolder_root=cc.storage_bigfolder_root,
        )
    elif backend == "s3":
        from colaig.integrations.storage.s3 import S3Storage
        return S3Storage(
            bucket_name=cc.storage_s3_bucket,
            access_key=cc.storage_s3_access_key,
            secret_key=cc.storage_s3_secret_key,
            endpoint_url=cc.storage_s3_endpoint or None,
            prefix=cc.storage_s3_prefix,
            region=cc.storage_s3_region,
            session_token=cc.storage_s3_session_token or None,
        )
    elif backend == "msgraph":
        from colaig.integrations.storage.msgraph import MicrosoftGraphStorage
        return MicrosoftGraphStorage(
            tenant_id=cc.storage_msgraph_tenant_id,
            client_id=cc.storage_msgraph_client_id,
            client_secret=cc.storage_msgraph_client_secret,
            drive_user_id=cc.storage_msgraph_drive_user_id,
            drive_id=cc.storage_msgraph_drive_id,
            root_path=cc.storage_msgraph_root_path,
        )
    elif backend == "box":
        import json as _json

        from colaig.integrations.storage.box import BoxStorage
        cfg_file = cc.storage_box_config_file
        client_id = client_secret = enterprise_id = public_key_id = private_key = passphrase = ""
        if cfg_file:
            if not Path(cfg_file).is_file():
                from colaig.exceptions import ConfigError
                raise ConfigError(
                    f"Client '{cc.client_id}': fichier box_config_file introuvable: {cfg_file} "
                    f"(STORAGE_BACKEND=box). Verifiez le chemin dans config/clients.yml."
                )
            with open(cfg_file, encoding="utf-8") as f:
                box_cfg = _json.load(f)
            app = box_cfg.get("boxAppSettings", {})
            auth = app.get("appAuth", {})
            client_id = app.get("clientID", "")
            client_secret = app.get("clientSecret", "")
            enterprise_id = box_cfg.get("enterpriseID", "")
            public_key_id = auth.get("publicKeyID", "")
            private_key = auth.get("privateKey", "")
            passphrase = auth.get("passphrase", "")
        return BoxStorage(
            client_id=client_id,
            client_secret=client_secret,
            enterprise_id=enterprise_id,
            public_key_id=public_key_id,
            private_key=private_key,
            passphrase=passphrase,
            root_folder_id=cc.storage_box_root_folder_id,
            user_id=cc.storage_box_user_id,
        )
    elif backend == "gdrive":
        from colaig.integrations.storage.gdrive import GoogleDriveStorage
        # Gérer le cas où service_account_json est un chemin de fichier
        sa_json = cc.storage_gdrive_service_account_json
        if sa_json and not sa_json.strip().startswith("{"):
            with open(sa_json, encoding="utf-8") as f:
                sa_json = f.read()
        return GoogleDriveStorage(
            service_account_json=sa_json,
            root_folder_id=cc.storage_gdrive_root_folder_id,
            impersonate_user=cc.storage_gdrive_impersonate_user,
        )
    else:
        raise ValueError(f"storage_backend inconnu pour client {cc.client_id}: {backend}")


def create_messaging_for_client(cc):
    """Crée le canal de messagerie depuis un ClientConfig.

    Args:
        cc: ClientConfig instance.

    Returns:
        Instance implémentant MessagingProtocol.
    """
    backend = cc.messaging_backend.lower()

    if backend == "matrix":
        from colaig.messaging.matrix import MatrixMessaging
        return MatrixMessaging(
            homeserver=cc.messaging_matrix_homeserver,
            username=cc.messaging_matrix_username,
            password=cc.messaging_matrix_password,
        )
    elif backend == "telegram":
        from colaig.integrations.messaging.telegram import TelegramMessaging
        return TelegramMessaging(
            bot_token=cc.messaging_telegram_token,
            webhook_url=cc.messaging_telegram_webhook,
        )
    elif backend == "webchat":
        from colaig.messaging.webchat import WebChatMessaging
        return WebChatMessaging()
    else:
        raise ValueError(f"messaging_backend inconnu pour client {cc.client_id}: {backend}")


def create_llm_for_client(cc, default_config: ColaigConfig):
    """Crée le client LLM depuis un ClientConfig, avec fallback sur ColaigConfig.

    Si la section 'llm' du ClientConfig est vide, hérite du client LLM
    global (instancié depuis ColaigConfig par create_llm_client).

    Args:
        cc: ClientConfig instance.
        default_config: ColaigConfig globale (fallback).

    Returns:
        Instance implémentant LLMClientProtocol.
    """
    if not cc.llm_backend:
        return create_llm_client(default_config, client_id=cc.client_id)

    backend = cc.llm_backend.lower()

    if backend == "albert":
        # Créer un ColaigConfig minimal avec les overrides du client
        override = ColaigConfig(
            albert_api_url=cc.llm_api_url or default_config.albert_api_url,
            albert_api_key=cc.llm_api_key or default_config.albert_api_key,
            albert_model_chat=cc.llm_model_chat or default_config.albert_model_chat,
            albert_model_embed=cc.llm_model_embed or default_config.albert_model_embed,
            albert_model_rerank=default_config.albert_model_rerank,
            albert_model_ocr=default_config.albert_model_ocr,
            albert_model_audio=default_config.albert_model_audio,
        )
        from colaig.integrations.albert import AlbertClient
        return AlbertClient(override, usage_tracker=_USAGE_TRACKER, client_id=cc.client_id)

    elif backend == "openai":
        from colaig.integrations.llm.openai_client import OpenAIClient
        return OpenAIClient(
            api_key=cc.llm_api_key,
            base_url=cc.llm_api_url or "https://api.openai.com",
            model_chat=cc.llm_model_chat or "gpt-4o",
            model_embed=cc.llm_model_embed or "text-embedding-3-small",
            backend_name=f"OpenAI[{cc.client_id}]",
        )

    elif backend == "azure":
        from colaig.integrations.llm.azure_client import AzureClient
        return AzureClient(
            api_key=cc.llm_api_key,
            resource_name=cc.llm_azure_resource,
            deployment_chat=cc.llm_azure_deployment_chat,
            deployment_embed=cc.llm_azure_deployment_embed,
            api_version=cc.llm_azure_api_version,
        )

    elif backend == "ollama":
        from colaig.integrations.llm.ollama_client import OllamaClient
        return OllamaClient(
            base_url=cc.llm_api_url or "http://localhost:11434",
            model_chat=cc.llm_model_chat or "llama3.2",
            model_embed=cc.llm_model_embed or "nomic-embed-text",
        )

    else:
        raise ValueError(f"llm_backend inconnu pour client {cc.client_id}: {backend}")


async def run_client_stack(cc, config: ColaigConfig, shutdown_event: asyncio.Event, messaging_instance=None) -> None:
    """Démarre la stack complète (storage + messaging + pipeline) pour un client.

    Utilisé en mode multi-client (clients.yml). Ne démarre pas le serveur web
    ni le serveur MCP (ceux-ci sont des services d'instance, lancés une seule fois).

    Args:
        cc: ClientConfig — credentials storage + messaging + LLM override.
        config: ColaigConfig globale — paramètres RAG, agents, etc.
        shutdown_event: Signal d'arrêt partagé entre tous les clients.
        messaging_instance: Instance pré-créée (optionnel — partagée avec create_app pour webchat).
    """
    log = logging.getLogger(f"{__name__}[{cc.client_id}]")
    log.info("démarrage stack client (storage=%s, messaging=%s)", cc.storage_backend, cc.messaging_backend)

    storage = create_storage_for_client(cc)
    albert = create_llm_for_client(cc, config)
    messaging = messaging_instance if messaging_instance is not None else create_messaging_for_client(cc)

    chunker = Chunker(chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
    _embed_ns = _embedding_namespace(cc.llm_api_key or config.albert_api_key, cc.llm_model_embed or config.albert_model_embed)
    embedding_service = EmbeddingService(albert, dimension=1024, cache_namespace=_embed_ns, local_fallback=config.local_embeddings)
    faiss_store = FaissStore(dimension=1024)

    retriever = Retriever(
        embedding_service=embedding_service,
        store=faiss_store,
        albert_client=albert,
        hyde_enabled=config.hyde_enabled,
        hyde_query_weight=config.hyde_query_weight,
        rrf_k_constant=config.rrf_k_constant,
    )

    # Contextualiser chunks (opt-in)
    contextualizer = None
    if config.contextual_chunking_enabled:
        from colaig.rag.contextualizer import ChunkContextualizer
        ctx_model = config.contextual_model or config.albert_model_light
        contextualizer = ChunkContextualizer(albert, model=ctx_model)

    workspace_stores: dict = {}
    workspace_indexers: dict = {}
    workspace_bm25_stores: dict | None = {} if config.hybrid_search_enabled else None

    from colaig.context.user_memory import UserMemory
    from colaig.rag.index_registry import FaissIndexRegistry
    index_registry = FaissIndexRegistry()
    user_memory = UserMemory(
        storage=storage,
        embeddings=embedding_service,
        registry=index_registry,
        albert_client=albert,
        light_model=getattr(config, "albert_model_light", None),
        dimension=1024,
    )

    from colaig.rag.federation_service import FederationService
    from colaig.rag.workspace_directory import WorkspaceDirectory
    federation_service = FederationService(storage)
    workspace_directory = WorkspaceDirectory(storage, embedding_service, index_registry)

    resolver = ContextResolver(storage=storage, cache_ttl=config.workspace_cache_ttl_seconds, default_workspace_id=config.default_workspace_id)
    generator = Generator(albert=albert, model=config.albert_model_chat)

    # Agents (partagent les mêmes paramètres que l'instance globale)
    analyser = None
    orchestrator = None
    synthesiser = None
    conversation_memory = None
    tool_registry = None
    trame_manager = None
    pre_exec_builder = None

    if config.agents_enabled:
        from colaig.agents import Analyser, Orchestrator, Synthesiser, build_tool_registry
        from colaig.agents.trame_manager import TrameManager
        from colaig.context.conversation_memory import ConversationMemory

        conversation_memory = ConversationMemory(
            storage=storage,
            embedding_service=embedding_service,
            max_stored=config.conversation_memory_max_stored,
            max_retrieved=config.conversation_memory_max_retrieved,
        )
        synthesiser = Synthesiser(
            albert=albert,
            storage=storage,
            model=config.albert_model_medium,
            temperature=config.synthesiser_temperature,
        )
        tool_registry = build_tool_registry(
            retriever, storage, albert,
            synthesiser=synthesiser if config.agents_phase6_enabled else None,
        )
        analyser = Analyser(
            albert=albert,
            storage=storage,
            temperature=config.analyser_temperature,
            use_tool_calling=config.analyser_use_tool_calling,
            tool_registry=tool_registry,
            model=config.albert_model_chat,
        )
        orchestrator = Orchestrator(
            storage=storage,
            retriever=retriever,
            albert=albert,
            tool_registry=tool_registry,
            max_iterations=config.orchestrator_max_iterations,
            temperature=config.orchestrator_temperature,
            workspace_stores=workspace_stores,
            model=config.albert_model_chat,
            workspace_resolver=resolver,
            bm25_stores=workspace_bm25_stores,
            index_registry=index_registry,
            workspace_directory=workspace_directory,
            admin_user_ids=config.admin_user_ids,
        )
        trame_manager = TrameManager(storage=storage)

    from colaig.messaging.handlers import MessageHandler
    handler = MessageHandler(
        messaging=messaging,
        resolver=resolver,
        retriever=retriever,
        generator=generator,
        storage=storage,
        analyser=analyser,
        orchestrator=orchestrator,
        synthesiser=synthesiser,
        conversation_memory=conversation_memory,
        tool_registry=tool_registry,
        workspace_stores=workspace_stores,
        workspace_bm25_stores=workspace_bm25_stores,
        albert_client=albert,
        trame_manager=trame_manager,
        user_memory=user_memory,
        pre_exec_builder=pre_exec_builder,
    )
    messaging.on_message(handler.handle_message)

    initial_done = asyncio.Event()
    asyncio.create_task(initial_indexation(
        storage, chunker, embedding_service, resolver,
        workspace_indexers, workspace_stores,
        albert_client=albert,
        workspace_bm25_stores=workspace_bm25_stores,
        contextualizer=contextualizer,
        done_event=initial_done,
        workspace_directory=workspace_directory,
        federation_service=federation_service,
        config=config,
    ))

    log.info("connexion messagerie...")
    await messaging.connect()

    tasks = [
        messaging.run(),
        run_indexation_loop(
            storage, chunker, embedding_service, resolver,
            workspace_indexers, workspace_stores,
            config.index_refresh_interval_seconds,
            shutdown_event,
            albert_client=albert,
            workspace_bm25_stores=workspace_bm25_stores,
            contextualizer=contextualizer,
            initial_done=initial_done,
            messaging=messaging,
        ),
    ]
    if config.auto_discover_enabled:
        tasks.append(run_workspace_discovery_loop(
            storage, resolver,
            config.auto_discover_interval,
            shutdown_event,
        ))
    from colaig.context.user_memory import run_user_memory_consolidation_loop
    tasks.append(run_user_memory_consolidation_loop(user_memory, shutdown_event=shutdown_event))

    if config.tasks_enabled and config.agents_enabled:
        from colaig.agents.task_scheduler import run_task_scheduler_loop
        tasks.append(run_task_scheduler_loop(
            storage=storage,
            resolver=resolver,
            analyser=analyser if config.agents_enabled else None,
            orchestrator=orchestrator if config.agents_enabled else None,
            synthesiser=synthesiser if config.agents_enabled else None,
            retriever=retriever,
            generator=generator,
            messaging=messaging,
            conversation_memory=conversation_memory,
            workspace_stores=workspace_stores,
            bm25_stores=workspace_bm25_stores,
            workspace_directory=workspace_directory,
            shutdown_event=shutdown_event,
            poll_interval=config.task_scheduler_interval,
            max_concurrent=config.task_max_concurrent,
            session_timeout=config.task_session_timeout,
            max_error_count=config.task_max_error_count,
        ))

    await asyncio.gather(*tasks)


# =============================================================================
# Tâches de fond
# =============================================================================

async def run_indexation_loop(
    storage,
    chunker: Chunker,
    embedding_service: EmbeddingService,
    resolver: ContextResolver,
    workspace_indexers: dict,
    workspace_stores: dict,
    interval: int,
    shutdown_event: asyncio.Event,
    albert_client=None,
    workspace_bm25_stores: dict | None = None,
    contextualizer=None,
    initial_done: asyncio.Event | None = None,
    messaging=None,
) -> None:
    """Tâche périodique de ré-indexation des workspaces.

    Chaque workspace dispose de son propre Indexer (FaissStore + etags isolés).
    Garantit que les documents d'un workspace ne contaminent pas les autres.
    """
    # Attendre la fin de initial_indexation avant de démarrer le premier cycle.
    # Évite la double indexation OCR concurrente si initial_indexation est encore en cours.
    if initial_done is not None:
        await initial_done.wait()
    else:
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        except TimeoutError:
            pass

    while not shutdown_event.is_set():
        try:
            for ws in resolver.workspaces:
                if not ws.rag_enabled or not ws.storage_path:
                    continue
                # Récupérer ou créer l'Indexer dédié à ce workspace
                ws_indexer = workspace_indexers.get(ws.workspace_id)
                if ws_indexer is None:
                    from colaig.rag.bm25_store import BM25Store
                    ws_store = FaissStore(dimension=1024)
                    ws_bm25 = BM25Store() if workspace_bm25_stores is not None else None
                    # Charger le vocabulaire métier depuis identity.yaml (graceful)
                    _ws_vocabulary: list[str] = []
                    if contextualizer is not None:
                        try:
                            from colaig.agents.profile_service import ProfileService
                            _ws_profile = await ProfileService(storage).load_profile(ws.storage_path)
                            if _ws_profile and _ws_profile.vocabulary.terms:
                                _ws_vocabulary = _ws_profile.vocabulary.terms
                        except Exception:
                            pass

                    ws_indexer = Indexer(
                        storage=storage,
                        chunker=chunker,
                        embeddings=embedding_service,
                        store=ws_store,
                        albert_client=albert_client,
                        bm25_store=ws_bm25,
                        contextualizer=contextualizer,
                        workspace_name=ws.name,
                        workspace_description=ws.description,
                        workspace_system_prompt=ws.system_prompt,
                        workspace_vocabulary=_ws_vocabulary or None,
                    )
                    # Charger l'index persisté si disponible.
                    # Si l'index n'existe pas encore en storage, initial_indexation est
                    # probablement en cours pour ce workspace → skip ce cycle pour éviter
                    # une double indexation OCR concurrente.
                    loaded = await ws_indexer.load_from_storage(ws.index_path)
                    if not loaded:
                        logger.debug("run_indexation_loop: index %s absent du storage, skip (initial_indexation en cours ?)", ws.workspace_id)
                        continue
                    workspace_stores[ws.workspace_id] = ws_store
                    if workspace_bm25_stores is not None and ws_bm25 is not None:
                        workspace_bm25_stores[ws.workspace_id] = ws_bm25
                    workspace_indexers[ws.workspace_id] = ws_indexer

                updated = await ws_indexer.check_updates(ws.storage_path)
                if updated > 0:
                    await ws_indexer.save_to_storage(ws.index_path)
                    # Notifications proactives (Mode A/B) — opt-in par workspace
                    if messaging and ws.proactive_notifications and updated.changed_paths:
                        try:
                            from colaig.rag.notifier import format_notification
                            ws_store = workspace_stores.get(ws.workspace_id)
                            msg = format_notification(
                                workspace_name=ws.name,
                                update=updated,
                                store=ws_store,
                                language=ws.language,
                            )
                            if msg:
                                channels = ws.notification_channels or ws.conversations
                                for conv_id in channels:
                                    await messaging.send(conv_id, msg)
                                logger.info(
                                    "notification proactive envoyée (workspace=%s, docs=%d, channels=%d)",
                                    ws.workspace_id, len(updated.changed_paths), len(channels),
                                )
                        except Exception:
                            logger.warning("erreur notification proactive workspace=%s", ws.workspace_id, exc_info=True)

                # Indexation behaviors + skills (indexes légers, reconstruits à chaque cycle)
                try:
                    await BehaviorIndexer(storage, embedding_service).index_workspace(ws.storage_path)
                    await SkillIndexer(storage, embedding_service).index_workspace(ws.storage_path)
                except Exception:
                    logger.debug("behavior/skill indexation ignorée pour %s", ws.workspace_id)
        except Exception:
            logger.exception("erreur lors de la ré-indexation périodique")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_document_index_loop(
    document_index,
    resolver: ContextResolver,
    interval: int,
    max_pending: int,
    shutdown_event: asyncio.Event,
) -> None:
    """Tâche périodique de scan + analyse DocumentIndex."""
    while not shutdown_event.is_set():
        try:
            for ws in resolver.workspaces:
                if not ws.rag_enabled or not ws.storage_path:
                    continue
                new_count = await document_index.scan_workspace(ws.storage_path)
                analyzed = await document_index.analyze_pending(
                    ws.storage_path, max_docs=max_pending
                )
                if new_count > 0 or analyzed > 0:
                    await document_index.save(ws.storage_path)
        except Exception:
            logger.exception("erreur document_index_loop")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_workspace_discovery_loop(
    storage,
    resolver: ContextResolver,
    interval: int,
    shutdown_event: asyncio.Event,
) -> None:
    """Découverte et scaffold automatique des nouveaux workspaces dans le storage.

    Scanne les dossiers à la racine du storage à chaque cycle :
    - Dossier avec .colaig/config.yaml non encore connu → load + register_workspace
    - Dossier sans .colaig/ → scaffold automatique (create_workspace) + register_workspace
    - Dossier avec .colaig-ignore → ignoré (opt-out explicite)
    - Dossier caché (commence par .) → ignoré
    - StorageError lors du scaffold → ignoré définitivement (droits insuffisants)

    Après register_workspace(), run_indexation_loop prend le relais au cycle suivant.
    Générique : ne dépend que de StorageProtocol (list_files, exists, mkdir, upload).
    """
    from colaig.context.workspace import create_workspace, load_workspace
    from colaig.exceptions import WorkspaceConfigError as _WorkspaceConfigError

    _perm_skip: set[str] = set()  # chemins avec erreur permanente de droits

    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
    except TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            root_entries = await storage.list_files("/", recursive=False)
            known_paths = {ws.storage_path.rstrip("/") for ws in resolver.workspaces}

            for entry in root_entries:
                if not entry.is_directory:
                    continue
                if entry.name.startswith("."):
                    continue

                entry_path = entry.path.rstrip("/")

                # Ignorer la racine elle-même (path vide ou "/")
                if not entry_path or entry_path == "/":
                    continue

                if entry_path in _perm_skip:
                    continue

                # Opt-out explicite par dossier
                if await storage.exists(paths.ignore_file(entry_path)):
                    continue

                config_path = paths.config_file(entry_path)

                try:
                    if await storage.exists(config_path):
                        # Config déjà présente — enregistrer si pas encore connu du resolver
                        if entry_path not in known_paths:
                            ws = await load_workspace(storage, entry.path)
                            await resolver.register_workspace(ws)
                            logger.info(
                                "workspace découvert (config existante): %s → %s",
                                entry_path, ws.workspace_id,
                            )
                    else:
                        # Nouveau dossier sans scaffold — créer .colaig/ + config.yaml
                        if entry_path not in known_paths:
                            name = entry.name.replace("-", " ").replace("_", " ").title()
                            ws = await create_workspace(
                                storage=storage,
                                storage_path=entry.path,
                                name=name,
                            )
                            await resolver.register_workspace(ws)
                            logger.info(
                                "workspace auto-scaffoldé: %s → %s",
                                entry_path, ws.workspace_id,
                            )
                except (StorageError, _WorkspaceConfigError) as exc:
                    # Distinguer le manque de DROITS du reste, parce que l'un se corrige
                    # et l'autre se diagnostique.
                    #
                    # Le cas courant : le collègue a partagé son dossier avec Colaig en
                    # LECTURE SEULE. Colaig ne peut alors pas créer `.colaig/` — donc ni
                    # index, ni historique, ni mémoire — et abandonne le dossier
                    # définitivement. Le comportement est le bon ; le message ne l'était
                    # pas : « WorkspaceConfigError: … WebDAV 403 » ne dit ni la cause ni
                    # le geste, et l'exploitant reste devant un espace qui n'apparaît
                    # jamais sans savoir pourquoi.
                    #
                    # `create_workspace` EMBALLE l'erreur de droits dans un
                    # `WorkspaceConfigError` — la distinction ne survit que par le
                    # chaînage `from e`, d'où la lecture de `__cause__`.
                    if isinstance(exc, StorageAuthError) or isinstance(
                        exc.__cause__, StorageAuthError
                    ):
                        logger.warning(
                            "espace ignoré définitivement : %s — Colaig n'a que la "
                            "LECTURE sur ce dossier et ne peut pas y créer son dossier "
                            "d'instance %s. Accordez-lui l'ÉCRITURE sur ce partage, ou "
                            "déposez un fichier %s pour assumer l'exclusion. "
                            "Détail : %s",
                            entry_path, paths.colaig_dir(entry_path),
                            paths.ignore_file(entry_path), exc,
                        )
                    else:
                        logger.warning(
                            "scaffold ignoré définitivement pour %s (%s: %s)",
                            entry_path, type(exc).__name__, exc,
                        )
                    _perm_skip.add(entry_path)
                except Exception:
                    logger.warning(
                        "erreur discovery pour %s (sera retenté au prochain cycle)",
                        entry_path, exc_info=True,
                    )

        except Exception:
            logger.exception("erreur workspace discovery loop")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_web_server(app, port: int) -> None:
    """Lance le serveur web FastAPI avec uvicorn."""
    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    await server.serve()


async def initial_indexation(
    storage,
    chunker: Chunker,
    embedding_service: EmbeddingService,
    resolver: ContextResolver,
    workspace_indexers: dict,
    workspace_stores: dict,
    albert_client=None,
    workspace_bm25_stores: dict | None = None,
    contextualizer=None,
    done_event: asyncio.Event | None = None,
    workspace_directory=None,
    federation_service=None,
    config: ColaigConfig | None = None,
) -> None:
    """Indexation initiale de tous les workspaces au démarrage.

    Crée un Indexer isolé par workspace (FaissStore + BM25Store + etags dédiés).
    Peuple workspace_stores et workspace_bm25_stores pour les requêtes RAG.
    """
    await resolver.refresh_cache()

    for ws in resolver.workspaces:
        if not ws.rag_enabled or not ws.storage_path:
            continue
        try:
            from colaig.rag.bm25_store import BM25Store
            ws_store = FaissStore(dimension=1024)
            ws_bm25 = BM25Store() if workspace_bm25_stores is not None else None

            # Charger le vocabulaire métier depuis identity.yaml (graceful)
            _ws_vocabulary_init: list[str] = []
            if contextualizer is not None:
                try:
                    from colaig.agents.profile_service import ProfileService
                    _ws_profile_init = await ProfileService(storage).load_profile(ws.storage_path)
                    if _ws_profile_init and _ws_profile_init.vocabulary.terms:
                        _ws_vocabulary_init = _ws_profile_init.vocabulary.terms
                except Exception:
                    pass

            ws_indexer = Indexer(
                storage=storage,
                chunker=chunker,
                embeddings=embedding_service,
                store=ws_store,
                albert_client=albert_client,
                bm25_store=ws_bm25,
                contextualizer=contextualizer,
                workspace_name=ws.name,
                workspace_description=ws.description,
                workspace_system_prompt=ws.system_prompt,
                workspace_vocabulary=_ws_vocabulary_init or None,
            )
            loaded = await ws_indexer.load_from_storage(ws.index_path)
            if not loaded:
                count = await ws_indexer.index_workspace(ws.storage_path)
                if count > 0:
                    await ws_indexer.save_to_storage(ws.index_path)
            else:
                updated = await ws_indexer.check_updates(ws.storage_path)
                if updated > 0:
                    await ws_indexer.save_to_storage(ws.index_path)
            # Enregistrer dans les caches partagés
            workspace_stores[ws.workspace_id] = ws_store
            workspace_indexers[ws.workspace_id] = ws_indexer
            if workspace_bm25_stores is not None and ws_bm25 is not None:
                workspace_bm25_stores[ws.workspace_id] = ws_bm25

            # Auto-spécialisation opt-in : dérive persona/vocabulaire du corpus.
            # dry-run par défaut (knowledge.json seul) ; apply écrit la config.
            if config is not None and config.auto_specialize_enabled and albert_client is not None and ws_store.count > 0:
                try:
                    from colaig.rag.specializer import WorkspaceSpecializer
                    spec = WorkspaceSpecializer(
                        storage, albert_client,
                        model=config.contextual_model or config.albert_model_light,
                    )
                    res = await spec.derive(
                        ws.storage_path, ws_store,
                        dry_run=not config.auto_specialize_apply,
                    )
                    logger.info("auto-spécialisation %s: %s", ws.workspace_id,
                                res.get("updated_fields") or "dry-run")
                except Exception:
                    logger.warning("auto-spécialisation échouée: %s", ws.workspace_id, exc_info=True)

            # Indexation behaviors + skills (index légers, reconstruits si fichiers présents)
            try:
                await BehaviorIndexer(storage, embedding_service).index_workspace(ws.storage_path)
                await SkillIndexer(storage, embedding_service).index_workspace(ws.storage_path)
            except Exception:
                logger.debug("behavior/skill indexation ignorée pour %s", ws.workspace_id)
        except Exception:
            logger.exception("erreur indexation workspace %s", ws.workspace_id)

    # Répertoire vectoriel des workspaces — chargé ou construit après que tous les
    # workspaces sont connus. Permet le routage sémantique cross-workspace (find_workspace).
    if workspace_directory is not None:
        try:
            await workspace_directory.load_or_build(
                resolver.workspaces,
                federation_service=federation_service,
            )
        except Exception:
            logger.exception("initial_indexation: erreur workspace_directory — ignorée")

    if done_event is not None:
        done_event.set()
        logger.info("initial_indexation terminée — run_indexation_loop peut démarrer")


# =============================================================================
# Point d'entrée
# =============================================================================

async def main() -> None:
    """Point d'entrée principal de Colaig.

    Mode single-client (défaut) : env vars → un storage, un messaging, une stack.
    Mode multi-client : config/clients.yml → N stacks concurrentes, un web server partagé.
    """

    # 1. Charger la configuration
    config = load_config()
    setup_logging(config.log_level)

    # Quotas journaliers par tenant (0 = illimité) — enforcement dans les clients LLM.
    _USAGE_TRACKER.set_limits(config.daily_request_limit, config.daily_token_limit)

    # Vérifier si le mode multi-client est activé (clients.yml présent et non vide)
    from colaig.client_registry import ClientRegistry
    _clients_yml = Path(__file__).parent.parent / "config" / "clients.yml"
    client_registry = ClientRegistry.from_yaml_or_empty(_clients_yml)

    if client_registry:
        # ── Mode multi-client ────────────────────────────────────────────────
        logger.info("démarrage Colaig multi-client (%d client(s))", len(client_registry))

        # Valider chaque client contre la PlatformPolicy
        from colaig.config import load_platform_policy, validate_client_config_against_policy
        _policy = load_platform_policy(_clients_yml)
        for _cc in client_registry:
            validate_client_config_against_policy(_cc.client_id, _cc, _policy, config)

        shutdown_event = asyncio.Event()

        def _signal_handler():
            logger.info("signal d'arrêt reçu")
            shutdown_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        # Pré-créer les instances messaging — nécessaire pour partager WebChatMessaging
        # entre create_app (routes /chat) et run_client_stack (handler messages).
        _messaging_instances = {cc.client_id: create_messaging_for_client(cc) for cc in client_registry}

        # Le premier client webchat fournit la WebChatMessaging pour les routes /chat
        from colaig.messaging.webchat import WebChatMessaging as _WCM
        _webchat_messaging = next(
            (m for m in _messaging_instances.values() if isinstance(m, _WCM)),
            None,
        )

        # Lancer toutes les stacks client en parallèle (réutilisent les instances pré-créées)
        client_tasks = [
            asyncio.create_task(run_client_stack(cc, config, shutdown_event, _messaging_instances[cc.client_id]))
            for cc in client_registry
        ]

        # Le serveur web admin reste unique (utilise la config globale)
        app = create_app(
            resolver=None, indexer=None, config=config,
            mcp_server=None, storage=None,
            handler=None, workspace_indexers={},
            clients_yml_path=_clients_yml,
            messaging=_webchat_messaging,
            usage_tracker=_USAGE_TRACKER,
        )
        web_task = asyncio.create_task(run_web_server(app, config.web_port))

        await asyncio.gather(web_task, *client_tasks)
        return

    # ── Mode single-client (rétrocompat — env vars) ──────────────────────────
    # Validation de config : message clair plutôt qu'un traceback tardif.
    from colaig.config import validate_config
    from colaig.exceptions import ConfigError
    try:
        validate_config(config)
    except ConfigError as e:
        logger.error("%s", e)
        raise SystemExit(1) from None

    logger.info("démarrage Colaig (storage=%s, messaging=%s)",
                config.storage_backend, config.messaging_backend)

    # 2. Initialiser les backends via factory pattern
    storage = create_storage(config)

    albert = create_llm_client(config)

    messaging = create_messaging(config)

    # 3. Initialiser le pipeline RAG
    chunker = Chunker(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )

    _embed_ns = _embedding_namespace(config.albert_api_key, config.albert_model_embed)
    embedding_service = EmbeddingService(albert, dimension=1024, cache_namespace=_embed_ns, local_fallback=config.local_embeddings)

    # Store partagé — utilisé uniquement par le web admin / MCP pour reindex manuel
    faiss_store = FaissStore(dimension=1024)

    retriever = Retriever(
        embedding_service=embedding_service,
        store=faiss_store,
        albert_client=albert,
        hyde_enabled=config.hyde_enabled,
        hyde_query_weight=config.hyde_query_weight,
        rrf_k_constant=config.rrf_k_constant,
    )

    # Contextualiser (Contextual Retrieval) — génère des préfixes LLM par chunk à l'indexation
    contextualizer = None
    if config.contextual_chunking_enabled:
        from colaig.rag.contextualizer import ChunkContextualizer
        ctx_model = config.contextual_model or config.albert_model_light
        contextualizer = ChunkContextualizer(albert, model=ctx_model)
        logger.info("contextualisation LLM des chunks activée (modèle=%s)", ctx_model)

    # Indexer partagé — conservé pour compatibilité web admin + MCP (reindex manuel)
    indexer = Indexer(
        storage=storage,
        chunker=chunker,
        embeddings=embedding_service,
        store=faiss_store,
        albert_client=albert,
    )

    # Isolation par workspace : chaque workspace a son propre Indexer + FaissStore + BM25Store
    # Peuplés par initial_indexation, mis à jour par run_indexation_loop
    workspace_stores: dict = {}       # workspace_id → FaissStore
    workspace_indexers: dict = {}     # workspace_id → Indexer
    workspace_bm25_stores: dict | None = {} if config.hybrid_search_enabled else None
    if config.hybrid_search_enabled:
        logger.info("recherche hybride activée (BM25 + RRF k=%d)", config.rrf_k_constant)

    # Registry FAISS partagé — UserMemory + accès concurrent par index
    from colaig.context.user_memory import UserMemory
    from colaig.rag.index_registry import FaissIndexRegistry
    index_registry = FaissIndexRegistry()
    user_memory = UserMemory(
        storage=storage,
        embeddings=embedding_service,
        registry=index_registry,
        albert_client=albert,
        light_model=getattr(config, "albert_model_light", None),
        dimension=1024,
    )

    from colaig.rag.federation_service import FederationService
    from colaig.rag.workspace_directory import WorkspaceDirectory
    federation_service = FederationService(storage)
    workspace_directory = WorkspaceDirectory(storage, embedding_service, index_registry)

    # 4. Initialiser le contexte et le générateur
    resolver = ContextResolver(
        storage=storage,
        cache_ttl=config.workspace_cache_ttl_seconds,
        default_workspace_id=config.default_workspace_id,
    )

    generator = Generator(
        albert=albert,
        model=config.albert_model_chat,
    )

    # 4b. Initialiser le DocumentIndex (si activé — indépendant des agents)
    document_index = None
    if config.document_index_enabled:
        from colaig.rag.document_index import DocumentIndex
        document_index = DocumentIndex(
            storage=storage,
            embedding_service=embedding_service,
            albert=albert,
            config=config,
            index_cache_ttl=config.document_index_cache_ttl,
        )
        logger.info("service DocumentIndex activé (scan interval=%ds)", config.document_index_refresh_interval)

    # 4c. Initialiser les agents Phase 2/5 (si activé)
    analyser = None
    orchestrator = None
    synthesiser = None
    conversation_memory = None
    tool_registry = None

    if config.agents_enabled:
        from colaig.agents import Analyser, Orchestrator, Synthesiser, build_tool_registry
        from colaig.context.conversation_memory import ConversationMemory

        # Mémoire conversationnelle sémantique (Phase 5)
        conversation_memory = ConversationMemory(
            storage=storage,
            embedding_service=embedding_service,
            max_stored=config.conversation_memory_max_stored,
            max_retrieved=config.conversation_memory_max_retrieved,
        )

        # Synthétiseur construit en premier (requis pour assess_completion)
        synthesiser = Synthesiser(
            albert=albert,
            storage=storage,
            model=config.albert_model_medium,    # Phase 6 : modèle medium pour le synthétiseur
            temperature=config.synthesiser_temperature,
        )

        # Registre d'outils built-in — inclut assess_completion si Phase 6 activée
        tool_registry = build_tool_registry(
            retriever, storage, albert,
            document_index=document_index,
            synthesiser=synthesiser if config.agents_phase6_enabled else None,
        )

        analyser = Analyser(
            albert=albert,
            storage=storage,
            temperature=config.analyser_temperature,
            use_tool_calling=config.analyser_use_tool_calling,
            tool_registry=tool_registry,
            model=config.albert_model_chat,
        )
        orchestrator = Orchestrator(
            storage=storage,
            retriever=retriever,
            albert=albert,
            tool_registry=tool_registry,
            max_iterations=config.orchestrator_max_iterations,
            temperature=config.orchestrator_temperature,
            workspace_stores=workspace_stores,
            model=config.albert_model_chat,      # Phase 6 : modèle chat (gpt-oss-120b)
            workspace_resolver=resolver,
            bm25_stores=workspace_bm25_stores,
            index_registry=index_registry,
            workspace_directory=workspace_directory,
            admin_user_ids=config.admin_user_ids,
        )

        if config.agents_phase6_enabled:
            logger.info("pipeline Phase 6 activé (SearchDirectives + Trame Vivante + assess_completion)")
        else:
            logger.info("pipeline Phase 2/5 activé (agents agentiques + mémoire sémantique)")

    # ProfileService + PreExecutionBuilder (Phase 6 uniquement)
    pre_exec_builder = None
    if config.agents_enabled and config.agents_phase6_enabled:
        from colaig.agents.pre_execution import PreExecutionBuilder
        from colaig.agents.profile_service import ProfileService

        profile_service = ProfileService(storage=storage, embeddings=embedding_service, index_registry=index_registry)
        pre_exec_builder = PreExecutionBuilder(
            storage=storage,
            embeddings=embedding_service,
            profile_service=profile_service,
            all_tools=tool_registry.list_definitions() if tool_registry else [],
            retriever=retriever,
            conversation_memory=conversation_memory,
            index_registry=index_registry,
            user_memory=user_memory,
        )
        logger.info("PreExecutionBuilder activé (Phase 6)")

    # TrameManager — trame vivante par conversation (Phase 6)
    from colaig.agents.trame_manager import TrameManager
    trame_manager = TrameManager(storage=storage)

    handler = MessageHandler(
        messaging=messaging,
        resolver=resolver,
        retriever=retriever,
        generator=generator,
        storage=storage,
        analyser=analyser,
        orchestrator=orchestrator,
        synthesiser=synthesiser,
        conversation_memory=conversation_memory,
        tool_registry=tool_registry,
        workspace_stores=workspace_stores,
        workspace_bm25_stores=workspace_bm25_stores,
        albert_client=albert,
        trame_manager=trame_manager,
        pre_exec_builder=pre_exec_builder,
        user_memory=user_memory,
    )

    # 5. Enregistrer le handler de messages
    messaging.on_message(handler.handle_message)

    # 5b. Initialiser le serveur MCP (si activé)
    mcp_server = None
    token_manager = None
    if config.mcp_enabled:
        from colaig.auth import TokenManager
        from colaig.mcp import ColaigMCPServer
        from colaig.platform.config_store import ConfigStore
        _runtime_yml = Path(__file__).resolve().parent.parent / "config" / "runtime.yml"
        _config_store = ConfigStore(_runtime_yml)
        token_manager = TokenManager(storage=storage, base_url=config.base_url)
        mcp_server = ColaigMCPServer(
            resolver=resolver,
            retriever=retriever,
            indexer=indexer,
            storage=storage,
            config=config,
            document_index=document_index,
            generator=generator,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
            workspace_stores=workspace_stores,
            config_store=_config_store,
            conversation_memory=conversation_memory,
            user_memory=user_memory,
            token_manager=token_manager,
        )
        logger.info(
            "serveur MCP activé (auth=%s)",
            "on" if config.mcp_auth_enabled else "off",
        )

    # 6. Créer l'application web
    app = create_app(
        resolver=resolver, indexer=indexer, config=config,
        mcp_server=mcp_server, storage=storage,
        handler=handler,
        workspace_indexers=workspace_indexers,
        messaging=messaging,
        llm_client=albert,
        usage_tracker=_USAGE_TRACKER,
    )

    # 6b. Middleware d'auth MCP (après create_app pour wrapper le mount /mcp)
    if config.mcp_enabled and token_manager is not None:
        from colaig.auth import MCPTokenMiddleware

        # Mode OIDC enterprise : instancier OIDCValidator si configuré
        oidc_validator = None
        if config.mcp_auth_mode == "oidc":
            if not config.oidc_issuer or not config.oidc_audience or not config.oidc_jwks_uri:
                raise ValueError(
                    "COLAIG_MCP_AUTH_MODE=oidc requiert COLAIG_OIDC_ISSUER, "
                    "COLAIG_OIDC_AUDIENCE et COLAIG_OIDC_JWKS_URI"
                )
            from colaig.auth import OIDCValidator
            oidc_validator = OIDCValidator(
                issuer=config.oidc_issuer,
                audience=config.oidc_audience,
                jwks_uri=config.oidc_jwks_uri,
            )
            logger.info(
                "MCP auth mode=oidc issuer=%s audience=%s",
                config.oidc_issuer,
                config.oidc_audience,
            )

        app.add_middleware(
            MCPTokenMiddleware,
            token_manager=token_manager,
            mcp_path="/mcp",
            auth_required=config.mcp_auth_enabled,
            oidc_validator=oidc_validator,
        )

    # 7. Démarrage
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("signal d'arrêt reçu")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows ne supporte pas add_signal_handler
            pass

    # Indexation initiale en tâche de fond — ne bloque pas le démarrage des services.
    # Les workspaces déjà indexés (index.faiss présent) sont disponibles immédiatement.
    # Les nouveaux documents (OCR long) s'indexent en arrière-plan pendant que le bot répond.
    logger.info("indexation initiale (arrière-plan)...")
    initial_done = asyncio.Event()
    asyncio.create_task(initial_indexation(
        storage, chunker, embedding_service, resolver,
        workspace_indexers, workspace_stores,
        albert_client=albert,
        workspace_bm25_stores=workspace_bm25_stores,
        contextualizer=contextualizer,
        done_event=initial_done,
        workspace_directory=workspace_directory,
        federation_service=federation_service,
        config=config,
    ))

    logger.info("connexion messagerie...")
    await messaging.connect()

    logger.info("lancement des services (messaging + web + indexation)")
    tasks = [
        messaging.run(),
        run_web_server(app, config.web_port),
        run_indexation_loop(
            storage, chunker, embedding_service, resolver,
            workspace_indexers, workspace_stores,
            config.index_refresh_interval_seconds,
            shutdown_event,
            albert_client=albert,
            workspace_bm25_stores=workspace_bm25_stores,
            contextualizer=contextualizer,
            initial_done=initial_done,
            messaging=messaging,
        ),
    ]
    if document_index is not None:
        tasks.append(run_document_index_loop(
            document_index, resolver,
            config.document_index_refresh_interval,
            config.document_index_max_pending,
            shutdown_event,
        ))
    if config.auto_discover_enabled:
        logger.info("auto-discovery workspaces activé (interval=%ds)", config.auto_discover_interval)
        tasks.append(run_workspace_discovery_loop(
            storage, resolver,
            config.auto_discover_interval,
            shutdown_event,
        ))
    if user_memory is not None:
        from colaig.context.user_memory import run_user_memory_consolidation_loop
        tasks.append(run_user_memory_consolidation_loop(user_memory, shutdown_event=shutdown_event))
    if config.tasks_enabled and config.agents_enabled:
        logger.info(
            "planificateur de tâches autonomes activé (poll=%ds, max_concurrent=%d)",
            config.task_scheduler_interval,
            config.task_max_concurrent,
        )
        from colaig.agents.task_scheduler import run_task_scheduler_loop
        tasks.append(run_task_scheduler_loop(
            storage=storage,
            resolver=resolver,
            analyser=analyser if config.agents_enabled else None,
            orchestrator=orchestrator if config.agents_enabled else None,
            synthesiser=synthesiser if config.agents_enabled else None,
            retriever=retriever,
            generator=generator,
            messaging=messaging,
            conversation_memory=conversation_memory,
            workspace_stores=workspace_stores,
            bm25_stores=workspace_bm25_stores,
            workspace_directory=workspace_directory,
            shutdown_event=shutdown_event,
            poll_interval=config.task_scheduler_interval,
            max_concurrent=config.task_max_concurrent,
            session_timeout=config.task_session_timeout,
            max_error_count=config.task_max_error_count,
        ))
    await asyncio.gather(*tasks)


def cli_entry() -> None:
    """Point d'entrée CLI : python -m colaig.main"""
    asyncio.run(main())


if __name__ == "__main__":
    cli_entry()
