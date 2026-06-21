"""
Colaig — Routes web admin

Dashboard FastAPI + HTMX minimaliste.
Phase 1 : santé, workspaces, ré-indexation, health check.
Phase 2+ : CRUD workspaces (création, liaison conversations, mise à jour config).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ── Helpers auth session admin ─────────────────────────────────────────────────

def _admin_key() -> str:
    """Retourne COLAIG_PLATFORM_API_KEY, ou '' si absent (mode dev)."""
    return os.environ.get("COLAIG_PLATFORM_API_KEY", "")


def _is_authenticated(request: Request) -> bool:
    """Vérifie si la session courante est authentifiée en tant qu'admin."""
    key = _admin_key()
    if not key:
        return True  # Pas de clé configurée → accès libre (self-hosted dev)
    return request.session.get("admin") == "1"


def _require_admin(request: Request) -> None:
    """Lève une redirection vers /login si non authentifié."""
    if not _is_authenticated(request):
        raise _LoginRedirect(_safe_next(request.url.path))


class _LoginRedirect(Exception):
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


def _safe_next(value: str) -> str:
    """Normalise un paramètre 'next' en chemin relatif sûr (anti open-redirect)."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


# ── Modèles de requête (Pydantic) ─────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    storage_path: str
    name: str
    workspace_id: str = ""
    description: str = ""
    conversations: list[str] = []
    system_prompt: str = ""
    tone: str = "professional"
    language: str = "fr"
    rag_enabled: bool = True


class LinkConversationRequest(BaseModel):
    conversation_id: str


class UnlinkConversationRequest(BaseModel):
    conversation_id: str


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tone: str | None = None
    language: str | None = None
    rag_enabled: bool | None = None
    similarity_threshold: float | None = None
    max_results: int | None = None


class AskRequest(BaseModel):
    message: str
    conversation_id: str = "api-test"
    user_id: str = "api-user"


class ProvisionStorageConfig(BaseModel):
    backend: str
    # WebDAV / Nextcloud
    url: str = ""
    username: str = ""
    password: str = ""
    # Local
    path: str = ""
    # S3 / MinIO
    endpoint_url: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    prefix: str = ""
    region: str = ""
    # MS Graph / OneDrive
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    drive_user_id: str = ""
    drive_id: str = ""
    root_path: str = ""
    # Bigfolder
    bigfolder_api_url: str = ""
    bigfolder_service_token: str = ""
    bigfolder_root: str = ""
    # Google Drive
    service_account_json: str = ""
    root_folder_id: str = ""
    impersonate_user: str = ""


class ProvisionMessagingConfig(BaseModel):
    backend: str
    homeserver: str = ""
    username: str = ""
    password: str = ""
    bot_token: str = ""
    webhook_url: str = ""


class ProvisionLLMConfig(BaseModel):
    backend: str
    api_url: str = ""
    api_key: str = ""
    model_chat: str = ""
    model_embed: str = ""
    azure_resource: str = ""
    azure_deployment_chat: str = ""
    azure_deployment_embed: str = ""
    azure_api_version: str = ""


class ProvisionMCPAuthConfig(BaseModel):
    mode: str = "token"           # "token" | "oidc"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_uri: str = ""


class ProvisionRequest(BaseModel):
    client_id: str
    storage: ProvisionStorageConfig
    messaging: ProvisionMessagingConfig
    llm: ProvisionLLMConfig | None = None
    mcp_auth: ProvisionMCPAuthConfig | None = None
    test_backends: bool = True


# Heure de démarrage pour l'uptime
_START_TIME = time.monotonic()
_START_DATETIME = datetime.utcnow().isoformat()


def create_app(
    resolver=None,          # ContextResolverProtocol
    indexer=None,           # Indexer (workspace par défaut)
    config=None,            # ColaigConfig
    mcp_server=None,        # ColaigMCPServer (Phase 2, optionnel)
    storage=None,           # StorageProtocol (pour lifecycle workspace)
    handler=None,           # MessageHandler (optionnel — active POST /ask)
    workspace_indexers=None,  # dict[workspace_id, Indexer] — indexers par workspace
    discovery_fn=None,        # async callable() — déclenche une re-découverte de workspaces
    clients_yml_path=None,    # str|Path — chemin vers clients.yml (active POST /api/platform/provision)
    messaging=None,           # MessagingProtocol (si WebChatMessaging → monte /chat/*)
    llm_client=None,          # AlbertClientProtocol — readiness probe LLM (/ready)
    usage_tracker=None,       # UsageTracker — métriques tokens/requêtes par tenant
) -> FastAPI:
    """Crée l'application FastAPI admin.

    Args:
        resolver: Résolveur de contexte (pour lister les workspaces).
        indexer: Indexer (pour forcer la ré-indexation).
        config: Configuration globale.
        mcp_server: Serveur MCP (monté sur /mcp si fourni).

    Returns:
        Application FastAPI configurée.
    """
    # Lifespan : démarre le session manager FastMCP si MCP activé.
    # FastAPI ne propage pas automatiquement les lifespans des sub-apps montées
    # → on gère explicitement le démarrage du task group anyio de FastMCP.
    @asynccontextmanager
    async def _lifespan(app):
        if mcp_server is not None:
            async with mcp_server.mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title="Colaig Admin", version="0.1.0", lifespan=_lifespan)

    # Session middleware (cookie signé) — clé = PLATFORM_API_KEY ou fallback dev
    _session_secret = _admin_key() or "colaig-dev-secret-change-in-production"
    app.add_middleware(SessionMiddleware, secret_key=_session_secret, session_cookie="colaig_session", https_only=False)

    # Request-ID / W3C Trace Context — corrélation des logs de bout en bout.
    # Ajouté après SessionMiddleware → outermost → s'applique à toute la requête.
    import uuid as _uuid

    import structlog
    from starlette.middleware.base import BaseHTTPMiddleware

    class _RequestIDMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            traceparent = request.headers.get("traceparent", "")
            rid = request.headers.get("x-request-id", "")
            if not rid and traceparent:
                parts = traceparent.split("-")
                rid = parts[1] if len(parts) >= 2 else ""
            rid = rid or _uuid.uuid4().hex
            structlog.contextvars.bind_contextvars(request_id=rid)
            try:
                response = await call_next(request)
            finally:
                structlog.contextvars.unbind_contextvars("request_id")
            response.headers["x-request-id"] = rid
            if traceparent:
                response.headers["traceparent"] = traceparent
            return response

    app.add_middleware(_RequestIDMiddleware)

    # Monter le serveur MCP si disponible
    if mcp_server is not None:
        mcp_app = mcp_server.http_app(path="/mcp")
        app.mount("/mcp", mcp_app)

    # ── Auth admin ────────────────────────────────────────────────────────────

    @app.exception_handler(_LoginRedirect)
    async def _login_redirect_handler(request: Request, exc: _LoginRedirect):
        return RedirectResponse(url=f"/login?next={quote(_safe_next(exc.next_url), safe='')}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        next = _safe_next(next)
        key = _admin_key()
        if not key:
            return RedirectResponse(url=next, status_code=303)
        return _templates.TemplateResponse(request, "login.html", {
            "error": None, "next": next,
            "logged_in": False, "show_platform": False, "show_chat": False,
        })

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, password: str = Form(""), next: str = Form("/")):
        next = _safe_next(next)
        key = _admin_key()
        if not key or password == key:
            request.session["admin"] = "1"
            return RedirectResponse(url=next, status_code=303)
        return _templates.TemplateResponse(request, "login.html", {
            "error": "Clé incorrecte.", "next": next,
            "logged_in": False, "show_platform": False, "show_chat": False,
        })

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/health", response_class=JSONResponse)
    async def health():
        """Health check JSON."""
        uptime = int(time.monotonic() - _START_TIME)
        return {
            "status": "ok",
            "started_at": _START_DATETIME,
            "uptime_seconds": uptime,
        }

    @app.get("/live", response_class=JSONResponse)
    async def live():
        """Liveness probe — le process tourne et répond."""
        return {"status": "alive", "uptime_seconds": int(time.monotonic() - _START_TIME)}

    @app.get("/ready", response_class=JSONResponse)
    async def ready():
        """Readiness probe — teste les dépendances (storage, LLM).

        Retourne 503 si une dépendance critique est indisponible (utile pour
        les probes Kubernetes/Onyxia : pas de trafic tant que pas prêt).
        """
        checks: dict = {}
        ok = True

        if storage is not None:
            try:
                await storage.exists("/")
                checks["storage"] = "ok"
            except Exception as e:  # noqa: BLE001
                checks["storage"] = f"error: {e}"
                ok = False

        if llm_client is not None:
            try:
                ping = getattr(llm_client, "ping", None)
                checks["llm"] = "ok" if (ping and await ping()) else "unavailable"
                ok = ok and checks["llm"] == "ok"
            except Exception as e:  # noqa: BLE001
                checks["llm"] = f"error: {e}"
                ok = False

        body = {
            "status": "ready" if ok else "not_ready",
            "checks": checks,
            "uptime_seconds": int(time.monotonic() - _START_TIME),
        }
        return JSONResponse(status_code=200 if ok else 503, content=body)

    @app.get("/metrics", response_class=JSONResponse)
    async def metrics():
        """Métriques JSON (workspaces, uptime, usage LLM par tenant)."""
        workspace_count = len(resolver.workspaces) if resolver else 0
        body = {
            "workspaces": workspace_count,
            "uptime_seconds": int(time.monotonic() - _START_TIME),
        }
        if usage_tracker is not None:
            body["llm_usage"] = usage_tracker.snapshot()
        return body

    @app.get("/metrics/prometheus", response_class=Response)
    async def metrics_prometheus():
        """Métriques au format texte Prometheus (usage LLM par tenant)."""
        text = usage_tracker.prometheus_text() if usage_tracker is not None else ""
        return Response(content=text, media_type="text/plain; charset=utf-8")

    _has_platform = clients_yml_path is not None
    _has_webchat = False  # mis à jour plus bas si WebChatMessaging

    def _tpl_ctx(request: Request, active: str, **kw) -> dict:
        """Contexte de base pour tous les templates Jinja2."""
        from colaig.messaging.webchat import WebChatMessaging
        return {
            "active": active,
            "logged_in": _is_authenticated(request),
            "show_platform": _has_platform and _is_authenticated(request),
            "show_chat": isinstance(messaging, WebChatMessaging),
            **kw,
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Dashboard admin — protégé si COLAIG_PLATFORM_API_KEY défini."""
        _require_admin(request)
        workspaces = resolver.workspaces if resolver else []
        uptime_sec = int(time.monotonic() - _START_TIME)
        ctx = _tpl_ctx(request, "dashboard",
                       workspaces=workspaces,
                       uptime_min=uptime_sec // 60,
                       storage_backend=config.storage_backend if config else "—",
                       messaging_backend=config.messaging_backend if config else "—",
                       mcp_enabled=config.mcp_enabled if config else False,
                       agents_enabled=config.agents_enabled if config else False,
                       started_at=_START_DATETIME)
        return _templates.TemplateResponse(request, "dashboard.html", ctx)

    @app.get("/workspaces", response_class=JSONResponse)
    async def list_workspaces():
        """Liste des workspaces en JSON."""
        workspaces = resolver.workspaces if resolver else []
        return [
            {
                "workspace_id": ws.workspace_id,
                "name": ws.name,
                "storage_path": ws.storage_path,
                "rag_enabled": ws.rag_enabled,
                "conversations": len(ws.conversations),
            }
            for ws in workspaces
        ]

    @app.get("/workspaces/{workspace_id}", response_class=JSONResponse)
    async def get_workspace(workspace_id: str):
        """Détail d'un workspace."""
        workspaces = resolver.workspaces if resolver else []
        for ws in workspaces:
            if ws.workspace_id == workspace_id:
                return {
                    "workspace_id": ws.workspace_id,
                    "name": ws.name,
                    "description": ws.description,
                    "storage_path": ws.storage_path,
                    "conversations": ws.conversations,
                    "rag_enabled": ws.rag_enabled,
                    "similarity_threshold": ws.similarity_threshold,
                    "max_results": ws.max_results,
                    "tone": ws.tone,
                    "language": ws.language,
                }
        return JSONResponse(status_code=404, content={"error": "workspace introuvable"})

    @app.post("/workspaces/{workspace_id}/reindex", response_class=JSONResponse)
    async def reindex_workspace(workspace_id: str, force: bool = False):
        """Force la ré-indexation d'un workspace.

        Args:
            force: Si True, réinitialise l'index avant de ré-indexer (full rebuild).
                   Si False (défaut), ré-indexe uniquement les documents modifiés.
        """
        ws_indexers = workspace_indexers or {}
        ws_indexer = ws_indexers.get(workspace_id) or indexer

        if not ws_indexer:
            return JSONResponse(status_code=503, content={"error": "indexer non disponible"})

        workspaces = resolver.workspaces if resolver else []
        for ws in workspaces:
            if ws.workspace_id == workspace_id:
                if force:
                    ws_indexer.reset()
                count = await ws_indexer.index_workspace(ws.storage_path)
                await ws_indexer.save_to_storage(ws.index_path)
                return {
                    "workspace_id": workspace_id,
                    "documents_indexed": count,
                    "force": force,
                }

        return JSONResponse(status_code=404, content={"error": "workspace introuvable"})

    @app.get("/workspaces/{workspace_id}/index-status", response_class=JSONResponse)
    async def index_status(workspace_id: str):
        """Retourne l'état courant de l'index d'un workspace."""
        ws_indexers = workspace_indexers or {}
        ws_indexer = ws_indexers.get(workspace_id) or (
            indexer if indexer else None
        )
        if ws_indexer is None:
            return JSONResponse(status_code=404, content={"error": "indexer non disponible"})
        return {"workspace_id": workspace_id, **ws_indexer.get_status()}

    # ── CRUD workspaces ───────────────────────────────────────────────────────

    @app.post("/workspaces", response_class=JSONResponse, status_code=201)
    async def create_workspace_endpoint(body: CreateWorkspaceRequest):
        """Crée un workspace avec scaffold .colaig/ complet.

        Idempotent : si le workspace existe déjà (config.yaml présent),
        retourne la configuration existante avec status 200.
        """
        if not storage:
            raise HTTPException(status_code=503, detail="storage non disponible")

        from colaig.context.workspace import create_workspace
        from colaig.exceptions import WorkspaceConfigError

        try:
            ws = await create_workspace(
                storage=storage,
                storage_path=body.storage_path,
                name=body.name,
                workspace_id=body.workspace_id or None,
                description=body.description,
                conversations=body.conversations,
                system_prompt=body.system_prompt,
                tone=body.tone,
                language=body.language,
                rag_enabled=body.rag_enabled,
            )
            if resolver:
                await resolver.register_workspace(ws)
            return {
                "workspace_id": ws.workspace_id,
                "name": ws.name,
                "storage_path": ws.storage_path,
                "conversations": ws.conversations,
                "rag_enabled": ws.rag_enabled,
                "status": "created",
            }
        except WorkspaceConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/workspaces/{workspace_id}/conversations", response_class=JSONResponse)
    async def link_conversation(workspace_id: str, body: LinkConversationRequest):
        """Lie un salon/conversation à un workspace existant."""
        if not storage:
            raise HTTPException(status_code=503, detail="storage non disponible")

        workspaces = resolver.workspaces if resolver else []
        ws = next((w for w in workspaces if w.workspace_id == workspace_id), None)
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace introuvable")

        from colaig.context.workspace import add_conversation_to_workspace
        from colaig.exceptions import WorkspaceConfigError

        try:
            updated_ws = await add_conversation_to_workspace(
                storage, ws.storage_path, body.conversation_id
            )
            if resolver:
                await resolver.register_workspace(updated_ws)
            return {
                "workspace_id": updated_ws.workspace_id,
                "conversation_id": body.conversation_id,
                "conversations": updated_ws.conversations,
                "status": "linked",
            }
        except WorkspaceConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/workspaces/{workspace_id}/conversations/{conversation_id}",
                response_class=JSONResponse)
    async def unlink_conversation(workspace_id: str, conversation_id: str):
        """Délie un salon/conversation d'un workspace."""
        if not storage:
            raise HTTPException(status_code=503, detail="storage non disponible")

        workspaces = resolver.workspaces if resolver else []
        ws = next((w for w in workspaces if w.workspace_id == workspace_id), None)
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace introuvable")

        from colaig.context.workspace import remove_conversation_from_workspace

        try:
            updated_ws = await remove_conversation_from_workspace(
                storage, ws.storage_path, conversation_id
            )
            if resolver:
                await resolver.register_workspace(updated_ws)
                resolver.invalidate_conversation(conversation_id)
            return {
                "workspace_id": updated_ws.workspace_id,
                "conversation_id": conversation_id,
                "conversations": updated_ws.conversations,
                "status": "unlinked",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/workspaces/{workspace_id}", response_class=JSONResponse)
    async def update_workspace(workspace_id: str, body: UpdateWorkspaceRequest):
        """Met à jour la configuration d'un workspace existant."""
        if not storage:
            raise HTTPException(status_code=503, detail="storage non disponible")

        workspaces = resolver.workspaces if resolver else []
        ws = next((w for w in workspaces if w.workspace_id == workspace_id), None)
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace introuvable")

        from colaig.context.workspace import update_workspace_config
        from colaig.exceptions import WorkspaceConfigError

        # Ne passer que les champs explicitement fournis (non None)
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            return JSONResponse(status_code=200, content={"warning": "aucun champ à mettre à jour"})

        try:
            updated_ws = await update_workspace_config(storage, ws.storage_path, **updates)
            if resolver:
                await resolver.register_workspace(updated_ws)
            return {
                "workspace_id": updated_ws.workspace_id,
                "updated_fields": list(updates.keys()),
                "status": "updated",
            }
        except WorkspaceConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ── Pipeline ask (handler requis) ────────────────────────────────────────

    # ── Webhook storage (push events depuis le provider de stockage) ─────────

    @app.post("/webhooks/storage", response_class=JSONResponse)
    async def storage_webhook(request: Request):
        """Webhook générique pour les événements de stockage.

        Reçoit les événements push du provider de stockage (ex: Box).
        Valide la signature HMAC, normalise l'événement, puis déclenche
        la re-découverte (collaboration_added) ou la ré-indexation
        (file_created / file_modified / file_deleted).

        Retourne toujours 200 sauf si la signature est invalide (401),
        pour éviter les retries inutiles du provider.
        """
        if storage is None or not hasattr(storage, "handle_webhook_event"):
            return {"status": "ignored", "reason": "no webhook handler"}

        from colaig.exceptions import StorageAuthError

        payload = await request.body()
        headers = dict(request.headers)

        try:
            event = await storage.handle_webhook_event(payload, headers)
        except StorageAuthError as exc:
            logger.warning("webhook storage: signature invalide — %s", exc)
            return JSONResponse(status_code=401, content={"error": "signature invalide"})
        except Exception as exc:
            logger.error("webhook storage: erreur inattendue — %s", exc)
            return {"status": "error"}

        if event is None:
            return {"status": "ignored"}

        logger.info(
            "webhook storage: %s — dossier=%s fichier=%s",
            event.event_type, event.folder_path, event.file_path,
        )

        if event.event_type == "collaboration_added":
            if discovery_fn is not None:
                asyncio.create_task(discovery_fn())
                return {"status": "ok", "action": "discovery_triggered", "folder": event.folder_path}
            return {"status": "ok", "action": "discovery_pending_next_poll", "folder": event.folder_path}

        if event.event_type in ("file_created", "file_modified", "file_deleted"):
            ws_indexers = workspace_indexers or {}
            workspaces = resolver.workspaces if resolver else []

            # Trouver le workspace dont le storage_path préfixe l'événement
            matched_ws = None
            event_path = (event.file_path or event.folder_path).lstrip("/")
            for ws in workspaces:
                ws_prefix = ws.storage_path.strip("/")
                if event_path.startswith(ws_prefix):
                    matched_ws = ws
                    break

            if matched_ws:
                ws_indexer = ws_indexers.get(matched_ws.workspace_id) or indexer
                if ws_indexer:
                    asyncio.create_task(ws_indexer.index_workspace(matched_ws.storage_path))
                    return {
                        "status": "ok",
                        "action": "reindex_triggered",
                        "workspace": matched_ws.workspace_id,
                    }

            return {"status": "ok", "action": "no_workspace_matched"}

        return {"status": "ok"}

    # ── Platform provision (multi-client) ─────────────────────────────────────
    # Actif uniquement si clients_yml_path est fourni.
    # Auth JSON : header "Authorization: Bearer <COLAIG_PLATFORM_API_KEY>" (env var).
    # Auth HTML : session cookie (même clé).

    if clients_yml_path is not None:
        import colaig.mcp.server as _mcp_server_mod
        from colaig.platform.provisioner import ClientProvisioner

        _provisioner = ClientProvisioner(clients_yml_path)
        _platform_api_key = os.environ.get("COLAIG_PLATFORM_API_KEY", "")

        # ── UI plateforme ─────────────────────────────────────────────────────

        @app.get("/platform", response_class=HTMLResponse)
        async def platform_page(request: Request):
            """Page d'administration de la plateforme (liste clients + provision)."""
            _require_admin(request)
            data = _provisioner.load()
            clients_raw = data.get("clients", [])
            ctx = _tpl_ctx(request, "platform",
                           clients=clients_raw,
                           api_key=_platform_api_key,
                           restart_notice=False)
            return _templates.TemplateResponse(request, "platform.html", ctx)

        # Fragments HTMX — champs dynamiques selon le backend choisi

        @app.get("/platform/fields/storage", response_class=HTMLResponse)
        async def platform_fields_storage(request: Request, storage_backend: str = "local"):
            return _templates.TemplateResponse(request, "fields_storage.html", {
                "backend": storage_backend,
            })

        @app.get("/platform/fields/messaging", response_class=HTMLResponse)
        async def platform_fields_messaging(request: Request, messaging_backend: str = "webchat"):
            return _templates.TemplateResponse(request, "fields_messaging.html", {
                "backend": messaging_backend,
            })

        def _check_platform_auth(request: Request) -> None:
            """Vérifie le Bearer token de la plateforme (HTTP 401/403 si invalide)."""
            if not _platform_api_key:
                return  # Pas de clé configurée → accès ouvert (self-hosted dev)
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Authorization header requis")
            token = auth.removeprefix("Bearer ").strip()
            if token != _platform_api_key:
                raise HTTPException(status_code=403, detail="Token plateforme invalide")

        @app.post("/api/platform/provision", response_class=JSONResponse)
        async def provision_client(body: ProvisionRequest, request: Request):
            """Crée ou met à jour un client Colaig dans clients.yml.

            Requiert le header Authorization: Bearer <COLAIG_PLATFORM_API_KEY>.

            Si test_backends=true (défaut), teste storage et messaging avant de
            persister la configuration. Retourne une erreur si un test échoue.
            """
            _check_platform_auth(request)

            import time as _time
            _ms = lambda: int((_time.monotonic()) * 1000)  # noqa: E731

            storage_dict = {k: v for k, v in body.storage.model_dump().items() if v}
            messaging_dict = {k: v for k, v in body.messaging.model_dump().items() if v}
            llm_dict = {k: v for k, v in body.llm.model_dump().items() if v} if body.llm else None
            mcp_auth_dict = {k: v for k, v in body.mcp_auth.model_dump().items() if v} if body.mcp_auth else None

            if body.test_backends:
                t0 = _time.monotonic()
                ok_s, err_s, lat_s = await _mcp_server_mod._test_storage(body.storage.backend, storage_dict, lambda: int((_time.monotonic() - t0) * 1000))
                if not ok_s:
                    return JSONResponse(
                        status_code=422,
                        content={"status": "error", "step": "storage", "error": err_s, "latency_ms": lat_s},
                    )
                t1 = _time.monotonic()
                ok_m, err_m, lat_m = await _mcp_server_mod._test_messaging(body.messaging.backend, messaging_dict, lambda: int((_time.monotonic() - t1) * 1000))
                if not ok_m:
                    return JSONResponse(
                        status_code=422,
                        content={"status": "error", "step": "messaging", "error": err_m, "latency_ms": lat_m},
                    )

            action = _provisioner.upsert(
                client_id=body.client_id,
                storage=storage_dict,
                messaging=messaging_dict,
                llm=llm_dict,
                mcp_auth=mcp_auth_dict,
            )
            return {
                "status": "ok",
                "client_id": body.client_id,
                "action": action,
                "config_path": str(_provisioner._path),
                "restart_required": True,
                "note": "Redémarrage de Colaig requis pour activer le nouveau client.",
            }

        @app.delete("/api/platform/provision/{client_id}", response_class=JSONResponse)
        async def delete_client(client_id: str, request: Request):
            """Supprime un client de clients.yml."""
            _check_platform_auth(request)
            deleted = _provisioner.delete(client_id)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"Client '{client_id}' introuvable")
            return {"status": "ok", "client_id": client_id, "action": "deleted", "restart_required": True}

        @app.get("/api/platform/provision", response_class=JSONResponse)
        async def list_clients(request: Request):
            """Liste les IDs client déclarés dans clients.yml."""
            _check_platform_auth(request)
            return {"clients": _provisioner.list_clients()}

        @app.get("/api/platform/provision/{client_id}/package")
        async def download_selfhosted_package(client_id: str, request: Request):
            """Génère et retourne le package self-hosted (docker-compose.yml + .env) en ZIP.

            Requiert le header Authorization: Bearer <COLAIG_PLATFORM_API_KEY>.
            Les credentials LLM (api_key) sont à compléter manuellement dans le .env.
            """
            _check_platform_auth(request)
            try:
                base_url = (config.base_url if config else "") or os.environ.get("COLAIG_BASE_URL", "")
                zip_bytes = _provisioner.build_selfhosted_package(
                    client_id=client_id,
                    base_url=base_url,
                    albert_api_url=os.environ.get("ALBERT_API_URL", "https://albert-api.etalab.gouv.fr"),
                    albert_api_key=os.environ.get("ALBERT_API_KEY", ""),
                    albert_model_chat=os.environ.get("ALBERT_MODEL_CHAT", "openai/gpt-oss-120b"),
                    albert_model_embed=os.environ.get("ALBERT_MODEL_EMBED", "BAAI/bge-m3"),
                )
            except KeyError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="colaig-{client_id}.zip"'},
            )

    if handler is not None:
        @app.post("/ask", response_class=JSONResponse)
        async def ask_endpoint(body: AskRequest):
            """Envoie un message dans le pipeline IA et retourne la réponse.

            Endpoint de test/intégration : contourne le canal de messagerie
            et appelle directement le pipeline (Phase 1 ou Phase 2 selon la config).
            Le conversation_id doit être lié à un workspace pour activer le RAG.
            """
            response_text = await handler.process(
                body=body.message,
                conversation_id=body.conversation_id,
                user_id=body.user_id,
            )
            return {
                "response": response_text,
                "conversation_id": body.conversation_id,
                "pipeline": "phase2" if handler.is_phase2 else "phase1",
            }

    # ── WebChat (si MESSAGING_BACKEND=webchat) ────────────────────────────────
    from colaig.messaging.webchat import WebChatMessaging
    if isinstance(messaging, WebChatMessaging):
        _wc = messaging

        @app.get("/chat/{conv_id}", response_class=HTMLResponse)
        async def webchat_page(conv_id: str):
            """Widget WebChat — interface navigateur pour une conversation."""
            return HTMLResponse(content=_webchat_html(conv_id))

        @app.get("/chat", response_class=HTMLResponse)
        async def webchat_new():
            """Nouvelle conversation WebChat — génère un conv_id aléatoire."""
            import uuid as _uuid
            return HTMLResponse(
                content="",
                status_code=302,
                headers={"location": f"/chat/{_uuid.uuid4().hex}"},
            )

        @app.websocket("/chat/ws/{conv_id}")
        async def webchat_ws(ws: WebSocket, conv_id: str, user_id: str = ""):
            """WebSocket bidirectionnel pour une conversation WebChat."""
            uid = user_id or f"anon-{conv_id[:8]}"
            await _wc.handle_websocket(ws, conv_id=conv_id, user_id=uid)

        logger.info("WebChat actif — /chat, /chat/{conv_id}, ws:/chat/ws/{conv_id}")

    return app


_WEBCHAT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" href="data:,">
    <title>Colaig</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; height: 100vh; display: flex; flex-direction: column; background: #f7f7f8; color: #1a1a1a; }
        header { display: flex; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem; background: #fff; border-bottom: 1px solid #e6e6e6; }
        header .logo { width: 32px; height: 32px; border-radius: 8px; background: #1a73e8; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; }
        header .title { font-weight: 600; }
        header .subtitle { font-size: 0.78rem; color: #888; }
        header .dot { margin-left: auto; width: 9px; height: 9px; border-radius: 50%; background: #ccc; }
        header .dot.on { background: #1a9d4b; }
        #messages { flex: 1; overflow-y: auto; padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem; max-width: 860px; width: 100%; margin: 0 auto; }
        .row { display: flex; flex-direction: column; max-width: 80%; }
        .row.user { align-self: flex-end; align-items: flex-end; }
        .row.assistant, .row.status { align-self: flex-start; align-items: flex-start; }
        .label { font-size: 0.72rem; color: #999; margin: 0 0.3rem 0.15rem; }
        .msg { padding: 0.6rem 0.9rem; border-radius: 12px; line-height: 1.5; word-break: break-word; }
        .msg.user { background: #1a73e8; color: #fff; border-bottom-right-radius: 4px; white-space: pre-wrap; }
        .msg.assistant { background: #fff; border: 1px solid #e6e6e6; border-bottom-left-radius: 4px; }
        .msg.status { background: transparent; color: #888; font-style: italic; font-size: 0.85rem; padding: 0.2rem 0.3rem; }
        .msg p { margin: 0 0 0.5rem; } .msg p:last-child { margin-bottom: 0; }
        .msg ul { margin: 0.3rem 0 0.3rem 1.2rem; } .msg li { margin: 0.1rem 0; }
        .msg code { background: rgba(0,0,0,0.06); padding: 0.05rem 0.3rem; border-radius: 4px; font-size: 0.9em; }
        .msg pre { background: #f0f0f2; padding: 0.6rem; border-radius: 8px; overflow-x: auto; margin: 0.4rem 0; }
        .msg pre code { background: none; padding: 0; }
        .msg a { color: #1a73e8; }
        #thinking { align-self: flex-start; color: #888; font-style: italic; font-size: 0.85rem; padding: 0.3rem 1rem; display: none; }
        #form { display: flex; gap: 0.5rem; padding: 0.75rem; border-top: 1px solid #e6e6e6; background: #fff; max-width: 860px; width: 100%; margin: 0 auto; }
        #input { flex: 1; padding: 0.6rem 0.9rem; border: 1px solid #ccc; border-radius: 8px; font-size: 1rem; resize: none; font-family: inherit; }
        #send { padding: 0.6rem 1.2rem; background: #1a73e8; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 1rem; }
        #send:disabled { background: #ccc; cursor: default; }
        #status { text-align: center; font-size: 0.75rem; color: #aaa; padding: 0.25rem; }
    </style>
</head>
<body>
    <header>
        <div class="logo">C</div>
        <div>
            <div class="title">Colaig</div>
            <div class="subtitle">Assistant documentaire souverain</div>
        </div>
        <div class="dot" id="dot"></div>
    </header>
    <div id="messages"></div>
    <div id="thinking">Colaig reflechit...</div>
    <div id="status">Connexion...</div>
    <form id="form">
        <textarea id="input" rows="1" placeholder="Votre message..." disabled></textarea>
        <button id="send" type="submit" disabled>Envoyer</button>
    </form>
    <script>
    const convId = __CONV_ID__;
    const userId = sessionStorage.getItem("colaig_uid") || (() => {
        const uid = "u-" + Math.random().toString(36).slice(2, 10);
        sessionStorage.setItem("colaig_uid", uid);
        return uid;
    })();

    const msgs = document.getElementById("messages");
    const input = document.getElementById("input");
    const send = document.getElementById("send");
    const status = document.getElementById("status");
    const thinking = document.getElementById("thinking");
    const dot = document.getElementById("dot");

    function escapeHtml(s) {
        return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }
    function renderMarkdown(src) {
        let t = escapeHtml(src);
        t = t.replace(/```([\s\S]*?)```/g, (m, c) => "<pre><code>" + c.replace(/^\n/, "") + "</code></pre>");
        t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
        t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
        t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        const lines = t.split(/\n/);
        let html = "", inList = false, para = [];
        const flush = () => { if (para.length) { html += "<p>" + para.join("<br>") + "</p>"; para = []; } };
        for (const line of lines) {
            const li = line.match(/^\s*[-*]\s+(.*)$/);
            if (li) { flush(); if (!inList) { html += "<ul>"; inList = true; } html += "<li>" + li[1] + "</li>"; }
            else if (line.trim() === "") { flush(); if (inList) { html += "</ul>"; inList = false; } }
            else { if (inList) { html += "</ul>"; inList = false; } para.push(line); }
        }
        flush(); if (inList) html += "</ul>";
        return html;
    }

    function addMsg(role, text) {
        const row = document.createElement("div");
        row.className = "row " + role;
        if (role === "assistant" || role === "user") {
            const lbl = document.createElement("div");
            lbl.className = "label";
            lbl.textContent = role === "user" ? "Vous" : "Colaig";
            row.appendChild(lbl);
        }
        const d = document.createElement("div");
        d.className = "msg " + role;
        if (role === "assistant") d.innerHTML = renderMarkdown(text);
        else d.textContent = text;
        row.appendChild(d);
        msgs.appendChild(row);
        msgs.scrollTop = msgs.scrollHeight;
        return d;
    }

    addMsg("assistant", "Bonjour, je suis **Colaig**, votre assistant documentaire. Posez-moi une question sur vos documents, ou tapez `colaig creer <nom>` pour creer un espace de travail.");

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + location.host + "/chat/ws/" + convId + "?user_id=" + userId);

    ws.onopen = () => { status.textContent = ""; dot.classList.add("on"); input.disabled = false; send.disabled = false; input.focus(); };
    ws.onclose = () => { status.textContent = "Deconnecte - rechargez la page."; dot.classList.remove("on"); input.disabled = true; send.disabled = true; };
    ws.onerror = () => { status.textContent = "Erreur de connexion."; };

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "x-mode") return;
        if (msg.type === "chat_thinking") { thinking.style.display = "block"; msgs.scrollTop = msgs.scrollHeight; return; }
        if (msg.type === "chat_thinking_done" || msg.type === "chat_message") { thinking.style.display = "none"; }
        if (msg.type === "chat_message") { addMsg(msg.is_status ? "status" : "assistant", msg.text); }
    };

    document.getElementById("form").onsubmit = (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if (!text || ws.readyState !== WebSocket.OPEN) return;
        addMsg("user", text);
        ws.send(JSON.stringify({ body: text, user_id: userId }));
        input.value = "";
        input.style.height = "auto";
    };

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); document.getElementById("form").onsubmit(e); }
    });
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });
    </script>
</body>
</html>"""


def _webchat_html(conv_id: str) -> str:
    """Widget HTML pour une conversation WebChat (rendu Markdown, accueil)."""
    return _WEBCHAT_TEMPLATE.replace("__CONV_ID__", json.dumps(conv_id))
