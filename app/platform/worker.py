# SPDX-License-Identifier: MIT
"""
Worker subprocess for a single Colaig bot instance.

This script is invoked by BotRunner as a subprocess. It reads a JSON config
from stdin, builds an app.Config, creates a TchapBot, and runs it.

Communication protocol:
    - Parent sends JSON config on stdin, then closes stdin.
    - Worker writes status lines to stdout: READY, RUNNING, ERROR <msg>
    - Worker logs to stderr.
    - Parent sends SIGTERM for graceful shutdown.

Usage (internal, called by BotRunner):
    echo '{"matrix_homeserver": "...", ...}' | python -m app.platform.worker
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import traceback

logger = logging.getLogger("colaig.worker")


def _build_config(instance_data: dict):
    """Build an app.Config from instance JSON data."""
    from app.config import Config

    kwargs = {}

    field_map = {
        "matrix_homeserver": "matrix_home_server",
        "matrix_username": "matrix_bot_username",
        "matrix_password": "matrix_bot_password",
        "webdav_url": "webdav_url",
        "webdav_username": "webdav_username",
        "webdav_password": "webdav_password",
        "webdav_root_path": "webdav_root_path",
        "albert_api_url": "albert_api_url",
        "albert_api_token": "albert_api_token",
        "albert_model": "albert_model",
        "albert_model_embedding": "albert_model_embedding",
        "groups_used": "groups_used",
        "browser_extraction_enabled": "browser_extraction_enabled",
    }

    # Champs qui peuvent légitimement être vides (override les défauts de Config)
    allow_empty = {"webdav_root_path"}

    for src, dst in field_map.items():
        val = instance_data.get(src)
        if val is not None and (val != "" or src in allow_empty):
            kwargs[dst] = val

    # allowed_domains needs special handling
    domains = instance_data.get("allowed_domains")
    if domains:
        if isinstance(domains, str):
            try:
                domains = json.loads(domains)
            except (json.JSONDecodeError, TypeError):
                domains = ["*"]
        kwargs["user_allowed_domains"] = domains

    return Config(**kwargs)


async def _run_bot(instance_data: dict):
    """Run the bot until SIGTERM or error."""
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    instance_id = instance_data.get("instance_id", "unknown")
    logger.info(f"Worker starting for instance {instance_id}")

    # Build config
    config = _build_config(instance_data)

    # Import and create bot
    from app.bot import TchapBot

    bot = TchapBot(
        homeserver=config.matrix_home_server,
        username=config.matrix_bot_username,
        password=config.matrix_bot_password,
        config=config,
    )

    # Signal RUNNING to parent
    sys.stdout.write("RUNNING\n")
    sys.stdout.flush()

    # Run bot in a task
    bot_task = asyncio.create_task(bot.main(), name=f"bot-main-{instance_id}")

    # Wait for shutdown signal or bot exit
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown-wait")

    done, pending = await asyncio.wait(
        [bot_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel remaining tasks
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    # Check for bot errors
    if bot_task in done and not bot_task.cancelled():
        exc = bot_task.exception()
        if exc:
            raise exc

    logger.info(f"Worker for instance {instance_id} shutting down")


def main():
    """Entry point: read config from stdin, run bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Read JSON config from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stdout.write("ERROR stdin empty\n")
            sys.stdout.flush()
            sys.exit(1)

        instance_data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stdout.write(f"ERROR invalid JSON: {e}\n")
        sys.stdout.flush()
        sys.exit(1)

    # Isoler les chemins de session par instance AVANT tout import de l'app
    # (bot_lib_config est un singleton instancié à l'import de app.matrix_bot.config)
    instance_id = instance_data.get("instance_id", "unknown")
    data_dir = f"/code/data/instances/{instance_id}"
    os.makedirs(f"{data_dir}/store", exist_ok=True)
    os.environ["STORE_PATH"] = f"{data_dir}/store"
    os.environ["SESSION_PATH"] = f"{data_dir}/session.txt"

    # E2E keys: decode base64 content and write to a temp file in the instance dir
    e2e_keys_data = instance_data.get("e2e_keys_data", "")
    e2e_keys_passphrase = instance_data.get("e2e_keys_passphrase", "")
    if e2e_keys_data and e2e_keys_passphrase:
        import base64
        try:
            keys_bytes = base64.b64decode(e2e_keys_data)
            keys_path = f"{data_dir}/e2e_keys.txt"
            with open(keys_path, "wb") as f:
                f.write(keys_bytes)
            os.environ["E2E_KEYS_PATH"] = keys_path
            os.environ["E2E_KEYS_PASSPHRASE"] = e2e_keys_passphrase
            logger.info(f"E2E keys written to {keys_path} for instance {instance_id}")
        except Exception as e:
            logger.error(f"Failed to decode E2E keys for instance {instance_id}: {e}")

    # Signal ready
    sys.stdout.write("READY\n")
    sys.stdout.flush()

    try:
        asyncio.run(_run_bot(instance_data))
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Worker fatal error: {error_msg}")
        logger.error(traceback.format_exc())
        sys.stdout.write(f"ERROR {error_msg}\n")
        sys.stdout.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
