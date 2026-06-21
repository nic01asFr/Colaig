# SPDX-License-Identifier: MIT
"""
Colaig Platform Web API - Starlette application.

Provides:
- /login          POST  Password login (mock/dev)
- /auth/login     GET   ProConnect OIDC redirect
- /auth/callback  GET   ProConnect OIDC callback
- /auth/logout    GET   Logout (destroy session)
- /api/me         GET   Current user info
- /api/instances  GET   List bot instances
- /api/instances  POST  Create bot instance
- /api/instances/{id}       GET    Get instance details
- /api/instances/{id}       PUT    Update instance
- /api/instances/{id}       DELETE Delete instance
- /api/instances/{id}/start POST   Start instance
- /api/instances/{id}/stop  POST   Stop instance
- /health         GET   Health check
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .models import AdminRole, BotInstanceConfig, InstanceStatus, PlatformAdmin
from .proconnect import MockProConnectClient, ProConnectClient, create_auth_client
from .registry import PlatformRegistry
from .session import SESSION_COOKIE_NAME, SessionManager

# Jinja2 templates
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)

logger = logging.getLogger(__name__)


# === Helpers ===


async def _get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate the current user from session cookie."""
    session_mgr: SessionManager = request.app.state.session_manager
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return await session_mgr.validate_session(cookie)


def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _require_auth(handler):
    """Decorator: require authenticated session."""
    async def wrapper(request: Request) -> JSONResponse:
        session = await _get_current_user(request)
        if not session:
            return _json_error("Authentication required", 401)
        request.state.session = session
        request.state.email = session["email"]
        return await handler(request)
    return wrapper


def _require_operator(handler):
    """Decorator: require operator role."""
    async def wrapper(request: Request) -> JSONResponse:
        session = await _get_current_user(request)
        if not session:
            return _json_error("Authentication required", 401)
        request.state.session = session
        request.state.email = session["email"]

        registry: PlatformRegistry = request.app.state.registry
        admin = await registry.get_admin(session["email"])
        if not admin or admin.role != AdminRole.OPERATOR:
            return _json_error("Operator role required", 403)
        return await handler(request)
    return wrapper


# === Auth endpoints ===


async def login_password(request: Request) -> JSONResponse:
    """Password login (mock/dev mode only)."""
    auth_client = request.app.state.auth_client
    if isinstance(auth_client, ProConnectClient) and auth_client.is_enabled:
        return _json_error("Password login disabled when ProConnect is active", 400)

    try:
        body = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return _json_error("Email and password required")

    user_info = auth_client.authenticate(email, password)
    if not user_info:
        return _json_error("Invalid credentials", 401)

    # Ensure admin exists in registry
    registry: PlatformRegistry = request.app.state.registry
    admin = await registry.get_admin(email)
    if not admin:
        # Auto-create admin for operator emails
        operator_emails = getattr(auth_client, "operator_emails", [])
        role = AdminRole.OPERATOR if email in operator_emails else AdminRole.ADMIN
        admin = PlatformAdmin(
            email=email,
            role=role,
            display_name=user_info.display_name,
        )
        await registry.add_admin(admin)

    # Update last login
    now = datetime.now(timezone.utc).isoformat()
    await registry.update_admin_login(email, now)

    # Create session
    session_mgr: SessionManager = request.app.state.session_manager
    cookie_value = await session_mgr.create_session(
        email=email,
        extra_data={"display_name": user_info.display_name, "role": admin.role},
    )

    response = JSONResponse({
        "ok": True,
        "email": email,
        "display_name": user_info.display_name,
        "role": admin.role,
    })
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return response


async def auth_proconnect_login(request: Request) -> RedirectResponse:
    """Redirect to ProConnect authorization endpoint."""
    auth_client = request.app.state.auth_client
    if not isinstance(auth_client, ProConnectClient) or not auth_client.is_enabled:
        return _json_error("ProConnect not enabled", 400)

    url, state, nonce = auth_client.get_authorization_url()

    # Store state + nonce in a temporary session for CSRF verification
    session_mgr: SessionManager = request.app.state.session_manager
    cookie_value = await session_mgr.create_session(
        email="__pending_oidc__",
        extra_data={"state": state, "nonce": nonce},
    )

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie("__oidc_pending", cookie_value, httponly=True, max_age=300)
    return response


async def auth_proconnect_callback(request: Request) -> JSONResponse:
    """Handle ProConnect OIDC callback."""
    auth_client = request.app.state.auth_client
    if not isinstance(auth_client, ProConnectClient):
        return _json_error("ProConnect not enabled", 400)

    code = request.query_params.get("code")
    received_state = request.query_params.get("state")
    if not code or not received_state:
        return _json_error("Missing code or state parameter", 400)

    # Retrieve stored state from pending session
    session_mgr: SessionManager = request.app.state.session_manager
    pending_cookie = request.cookies.get("__oidc_pending")
    if not pending_cookie:
        return _json_error("No pending OIDC session", 400)

    pending_session = await session_mgr.validate_session(pending_cookie)
    if not pending_session:
        return _json_error("Expired OIDC session", 400)

    stored_state = pending_session.get("data", {}).get("state", "")
    stored_nonce = pending_session.get("data", {}).get("nonce", "")

    try:
        user_info = await auth_client.handle_callback(
            code=code,
            expected_state=stored_state,
            received_state=received_state,
            stored_nonce=stored_nonce,
        )
    except ValueError as e:
        return _json_error(str(e), 401)

    # Clean up pending session
    await session_mgr.destroy_session(pending_cookie)

    email = user_info.email.lower()

    # Ensure admin exists
    registry: PlatformRegistry = request.app.state.registry
    admin = await registry.get_admin(email)
    if not admin:
        admin = PlatformAdmin(
            email=email,
            role=AdminRole.ADMIN,
            display_name=user_info.display_name,
            organization=user_info.siret,
        )
        await registry.add_admin(admin)

    now = datetime.now(timezone.utc).isoformat()
    await registry.update_admin_login(email, now)

    # Create real session
    cookie_value = await session_mgr.create_session(
        email=email,
        extra_data={
            "display_name": user_info.display_name,
            "role": admin.role,
            "organization": user_info.siret,
        },
    )

    response = JSONResponse({
        "ok": True,
        "email": email,
        "display_name": user_info.display_name,
        "role": admin.role,
    })
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    response.delete_cookie("__oidc_pending")
    return response


async def auth_logout(request: Request) -> JSONResponse:
    """Destroy session and logout."""
    session_mgr: SessionManager = request.app.state.session_manager
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        await session_mgr.destroy_session(cookie)

    response = JSONResponse({"ok": True, "message": "Logged out"})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# === User endpoint ===


@_require_auth
async def api_me(request: Request) -> JSONResponse:
    """Return current user info."""
    registry: PlatformRegistry = request.app.state.registry
    admin = await registry.get_admin(request.state.email)
    if not admin:
        return _json_error("User not found", 404)
    return JSONResponse({
        "email": admin.email,
        "role": admin.role,
        "display_name": admin.display_name,
        "organization": admin.organization,
        "last_login": admin.last_login,
    })


# === Instance CRUD ===


@_require_auth
async def api_list_instances(request: Request) -> JSONResponse:
    """List bot instances for the current user (operators see all)."""
    registry: PlatformRegistry = request.app.state.registry
    admin = await registry.get_admin(request.state.email)

    if admin and admin.role == AdminRole.OPERATOR:
        instances = await registry.list_instances()
    else:
        instances = await registry.list_instances(admin_email=request.state.email)

    return JSONResponse({
        "instances": [
            {
                "instance_id": inst.instance_id,
                "name": inst.name,
                "description": inst.description,
                "status": inst.status,
                "admin_email": inst.admin_email,
                "matrix_homeserver": inst.matrix_homeserver,
                "matrix_username": inst.matrix_username,
                "created_at": inst.created_at,
                "last_started_at": inst.last_started_at,
                "error_message": inst.error_message,
            }
            for inst in instances
        ]
    })


@_require_auth
async def api_create_instance(request: Request) -> JSONResponse:
    """Create a new bot instance."""
    try:
        body = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    registry: PlatformRegistry = request.app.state.registry

    instance = BotInstanceConfig(
        name=body.get("name", ""),
        description=body.get("description", ""),
        matrix_homeserver=body.get("matrix_homeserver", ""),
        matrix_username=body.get("matrix_username", ""),
        matrix_password=body.get("matrix_password", ""),
        webdav_url=body.get("webdav_url", ""),
        webdav_username=body.get("webdav_username", ""),
        webdav_password=body.get("webdav_password", ""),
        webdav_root_path=body.get("webdav_root_path", "/documents"),
        albert_api_url=body.get("albert_api_url", ""),
        albert_api_token=body.get("albert_api_token", ""),
        albert_model=body.get("albert_model", "meta-llama/Llama-3.1-8B-Instruct"),
        albert_model_embedding=body.get("albert_model_embedding", "BAAI/bge-m3"),
        admin_email=request.state.email,
        allowed_domains=body.get("allowed_domains", ["*"]),
        groups_used=body.get("groups_used", "document,web"),
        e2e_keys_data=body.get("e2e_keys_data", ""),
        e2e_keys_passphrase=body.get("e2e_keys_passphrase", ""),
    )

    await registry.add_instance(instance)
    logger.info(f"Instance {instance.instance_id} created by {request.state.email}")

    return JSONResponse({
        "ok": True,
        "instance_id": instance.instance_id,
        "name": instance.name,
    }, status_code=201)


@_require_auth
async def api_get_instance(request: Request) -> JSONResponse:
    """Get instance details."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry
    instance = await registry.get_instance(instance_id)

    if not instance:
        return _json_error("Instance not found", 404)

    # Check ownership (operators can see all)
    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    data = instance.to_dict()
    # Never expose passwords/secrets in API responses
    data.pop("matrix_password", None)
    data.pop("webdav_password", None)
    data.pop("albert_api_token", None)
    data.pop("e2e_keys_data", None)
    data.pop("e2e_keys_passphrase", None)

    return JSONResponse(data)


@_require_auth
async def api_update_instance(request: Request) -> JSONResponse:
    """Update an existing instance."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry
    instance = await registry.get_instance(instance_id)

    if not instance:
        return _json_error("Instance not found", 404)

    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    if instance.status == InstanceStatus.RUNNING:
        return _json_error("Cannot update a running instance. Stop it first.", 409)

    try:
        body = await request.json()
    except Exception:
        return _json_error("Invalid JSON body")

    # Update allowed fields
    updatable = [
        "name", "description", "matrix_homeserver", "matrix_username",
        "matrix_password", "webdav_url", "webdav_username", "webdav_password",
        "webdav_root_path", "albert_api_url", "albert_api_token",
        "albert_model", "albert_model_embedding", "allowed_domains",
        "groups_used", "e2e_keys_data", "e2e_keys_passphrase",
    ]
    for field in updatable:
        if field in body:
            setattr(instance, field, body[field])

    await registry.add_instance(instance)  # INSERT OR REPLACE
    return JSONResponse({"ok": True, "instance_id": instance_id})


@_require_auth
async def api_delete_instance(request: Request) -> JSONResponse:
    """Delete an instance."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry
    instance = await registry.get_instance(instance_id)

    if not instance:
        return _json_error("Instance not found", 404)

    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    if instance.status == InstanceStatus.RUNNING:
        return _json_error("Cannot delete a running instance. Stop it first.", 409)

    await registry.delete_instance(instance_id)
    logger.info(f"Instance {instance_id} deleted by {request.state.email}")
    return JSONResponse({"ok": True})


@_require_auth
async def api_start_instance(request: Request) -> JSONResponse:
    """Start a bot instance."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry
    runner = request.app.state.runner

    instance = await registry.get_instance(instance_id)
    if not instance:
        return _json_error("Instance not found", 404)

    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    if instance.status == InstanceStatus.RUNNING:
        return _json_error("Instance already running", 409)

    try:
        await runner.start_instance(instance)
        return JSONResponse({"ok": True, "status": InstanceStatus.STARTING})
    except Exception as e:
        logger.error(f"Failed to start instance {instance_id}: {e}")
        return _json_error(f"Failed to start: {e}", 500)


@_require_auth
async def api_stop_instance(request: Request) -> JSONResponse:
    """Stop a bot instance."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry
    runner = request.app.state.runner

    instance = await registry.get_instance(instance_id)
    if not instance:
        return _json_error("Instance not found", 404)

    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    try:
        await runner.stop_instance(instance_id)
        return JSONResponse({"ok": True, "status": InstanceStatus.STOPPED})
    except Exception as e:
        logger.error(f"Failed to stop instance {instance_id}: {e}")
        return _json_error(f"Failed to stop: {e}", 500)


@_require_auth
async def api_force_stop_instance(request: Request) -> JSONResponse:
    """Force-stop an orphaned instance by resetting its status in DB."""
    instance_id = request.path_params["instance_id"]
    registry: PlatformRegistry = request.app.state.registry

    instance = await registry.get_instance(instance_id)
    if not instance:
        return _json_error("Instance not found", 404)

    admin = await registry.get_admin(request.state.email)
    if admin.role != AdminRole.OPERATOR and instance.admin_email != request.state.email:
        return _json_error("Access denied", 403)

    await registry.update_instance_status(instance_id, InstanceStatus.STOPPED)
    logger.info(f"Force-stopped instance {instance_id} (status reset in DB)")
    return JSONResponse({"ok": True, "status": InstanceStatus.STOPPED})


# === Health ===


async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    runner = getattr(request.app.state, "runner", None)
    running_count = len(runner.running_instances) if runner else 0
    return JSONResponse({
        "status": "ok",
        "running_instances": running_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# === HTML pages ===


async def ui_login(request: Request) -> HTMLResponse:
    """Render the login page."""
    auth_client = request.app.state.auth_client
    proconnect_enabled = isinstance(auth_client, ProConnectClient) and auth_client.is_enabled
    template = _jinja_env.get_template("login.html")
    return HTMLResponse(template.render(proconnect_enabled=proconnect_enabled))


async def ui_dashboard(request: Request) -> HTMLResponse:
    """Render the dashboard. Auth check is done client-side via /api/me."""
    session = await _get_current_user(request)
    if not session:
        return RedirectResponse(url="/ui/login", status_code=302)
    template = _jinja_env.get_template("dashboard.html")
    return HTMLResponse(template.render())


async def ui_root(request: Request):
    """Page d'accueil publique (onboarding) ; redirige vers le dashboard si connecté."""
    session = await _get_current_user(request)
    if session:
        return RedirectResponse(url="/dashboard", status_code=302)
    template = _jinja_env.get_template("welcome.html")
    return HTMLResponse(template.render())


# === App factory ===


def create_app(
    registry: PlatformRegistry,
    session_manager: SessionManager,
    auth_client,
    runner=None,
) -> Starlette:
    """
    Create the Starlette application for the Colaig platform.

    Args:
        registry: PlatformRegistry instance (must be opened)
        session_manager: SessionManager instance
        auth_client: ProConnectClient or MockProConnectClient
        runner: BotRunner instance (optional, for start/stop)
    """
    routes = [
        # HTML pages
        Route("/", ui_root, methods=["GET"]),
        Route("/ui/login", ui_login, methods=["GET"]),
        Route("/dashboard", ui_dashboard, methods=["GET"]),
        # Auth API
        Route("/login", login_password, methods=["POST"]),
        Route("/auth/login", auth_proconnect_login, methods=["GET"]),
        Route("/auth/callback", auth_proconnect_callback, methods=["GET"]),
        Route("/auth/logout", auth_logout, methods=["GET", "POST"]),
        # User API
        Route("/api/me", api_me, methods=["GET"]),
        # Instances API
        Route("/api/instances", api_list_instances, methods=["GET"]),
        Route("/api/instances", api_create_instance, methods=["POST"]),
        Route("/api/instances/{instance_id}", api_get_instance, methods=["GET"]),
        Route("/api/instances/{instance_id}", api_update_instance, methods=["PUT"]),
        Route("/api/instances/{instance_id}", api_delete_instance, methods=["DELETE"]),
        Route("/api/instances/{instance_id}/start", api_start_instance, methods=["POST"]),
        Route("/api/instances/{instance_id}/stop", api_stop_instance, methods=["POST"]),
        Route("/api/instances/{instance_id}/force-stop", api_force_stop_instance, methods=["POST"]),
        # Health
        Route("/health", health, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    app.state.registry = registry
    app.state.session_manager = session_manager
    app.state.auth_client = auth_client
    app.state.runner = runner

    return app
