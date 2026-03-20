# SPDX-License-Identifier: MIT
"""
Entry point for the Colaig platform server.

Usage:
    python -m platform                     # Start platform server on :8080
    python -m platform --port 9000         # Custom port
    python -m platform --host 0.0.0.0      # Bind to all interfaces

The platform server provides:
- Web API for managing bot instances
- ProConnect OIDC authentication (or password fallback)
- Multi-instance bot runner
"""

import argparse
import asyncio
import logging
import secrets
import sys

import uvicorn

from .proconnect import create_auth_client
from .registry import PlatformRegistry
from .runner import BotRunner
from .session import SessionManager
from .web import create_app


def parse_args():
    parser = argparse.ArgumentParser(description="Colaig Platform Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument("--log-level", default="info", help="Log level (default: info)")
    return parser.parse_args()


async def setup_and_run(host: str, port: int, log_level: str):
    """Initialize components and start the server."""
    # Load config
    from app.config import get_config

    config = get_config()

    # Ensure session secret exists
    session_secret = config.platform_session_secret
    if not session_secret:
        session_secret = secrets.token_urlsafe(48)
        logging.warning(
            "PLATFORM_SESSION_SECRET not set, using random value. "
            "Sessions will not survive restarts."
        )

    # Open registry
    registry = PlatformRegistry(db_path=config.platform_db_path)
    await registry.open()

    # Seed operator admins from config
    from .models import AdminRole, PlatformAdmin
    from datetime import datetime, timezone

    for email in config.platform_operator_emails:
        existing = await registry.get_admin(email)
        if not existing:
            admin = PlatformAdmin(
                email=email,
                role=AdminRole.OPERATOR,
                display_name=email.split("@")[0],
            )
            await registry.add_admin(admin)
            logging.info(f"Seeded operator: {email}")

    # Create session manager
    session_manager = SessionManager(secret=session_secret, registry=registry)

    # Create auth client
    auth_client = create_auth_client(
        proconnect_enabled=config.proconnect_enabled,
        client_id=config.proconnect_client_id,
        client_secret=config.proconnect_client_secret,
        redirect_uri=config.proconnect_redirect_uri,
        issuer=config.proconnect_issuer,
        admin_password=config.platform_admin_password,
        operator_emails=config.platform_operator_emails,
    )

    # Create runner
    runner = BotRunner(registry=registry)

    # Create web app
    app = create_app(
        registry=registry,
        session_manager=session_manager,
        auth_client=auth_client,
        runner=runner,
    )

    # Run with uvicorn
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=True,
    )
    server = uvicorn.Server(uvicorn_config)

    logging.info(f"Colaig Platform starting on {host}:{port}")
    logging.info(
        f"Auth mode: {'ProConnect OIDC' if auth_client.is_enabled else 'Password fallback (dev/test)'}"
    )

    try:
        await server.serve()
    finally:
        await runner.shutdown_all()
        await auth_client.close()
        await registry.close()
        logging.info("Colaig Platform shut down")


def main():
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    asyncio.run(setup_and_run(args.host, args.port, args.log_level))


if __name__ == "__main__":
    main()
