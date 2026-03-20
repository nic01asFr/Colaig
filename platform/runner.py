# SPDX-License-Identifier: MIT
"""
Multi-instance bot runner for the Colaig platform.

Manages the lifecycle of multiple TchapBot instances, each running
in its own asyncio task. The runner translates BotInstanceConfig
into the app.Config expected by TchapBot.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

from .models import BotInstanceConfig, InstanceStatus
from .registry import PlatformRegistry

logger = logging.getLogger(__name__)


class BotRunner:
    """
    Manages multiple bot instances as asyncio tasks.

    Usage:
        runner = BotRunner(registry)
        await runner.start_instance(instance_config)
        await runner.stop_instance(instance_id)
        await runner.shutdown_all()
    """

    def __init__(self, registry: PlatformRegistry):
        self.registry = registry
        self._tasks: dict[str, asyncio.Task] = {}
        self._shutdown_events: dict[str, asyncio.Event] = {}

    @property
    def running_instances(self) -> dict[str, asyncio.Task]:
        """Return currently running instance tasks."""
        return {k: v for k, v in self._tasks.items() if not v.done()}

    async def start_instance(self, instance: BotInstanceConfig) -> None:
        """
        Start a bot instance as an asyncio task.

        Validates required fields, creates an app.Config from
        the instance config, then runs TchapBot.main() in a task.
        """
        iid = instance.instance_id

        # Check not already running
        if iid in self._tasks and not self._tasks[iid].done():
            raise RuntimeError(f"Instance {iid} is already running")

        # Validate minimum required fields
        if not instance.matrix_homeserver:
            raise ValueError("matrix_homeserver is required")
        if not instance.matrix_username:
            raise ValueError("matrix_username is required")
        if not instance.matrix_password:
            raise ValueError("matrix_password is required")

        # Update status to STARTING
        now = datetime.now(timezone.utc).isoformat()
        await self.registry.update_instance_status(
            iid, InstanceStatus.STARTING, last_started_at=now
        )

        shutdown_event = asyncio.Event()
        self._shutdown_events[iid] = shutdown_event

        task = asyncio.create_task(
            self._run_instance(instance, shutdown_event),
            name=f"bot-{iid}",
        )
        self._tasks[iid] = task

        # Add a done callback for cleanup
        task.add_done_callback(lambda t: asyncio.create_task(self._on_task_done(iid, t)))

        logger.info(f"Instance {iid} ({instance.name}) starting")

    async def stop_instance(self, instance_id: str) -> None:
        """
        Gracefully stop a running bot instance.
        """
        if instance_id not in self._tasks:
            raise RuntimeError(f"Instance {instance_id} is not managed")

        task = self._tasks[instance_id]
        if task.done():
            raise RuntimeError(f"Instance {instance_id} is not running")

        # Signal shutdown
        event = self._shutdown_events.get(instance_id)
        if event:
            event.set()

        # Cancel the task with a grace period
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        await self.registry.update_instance_status(instance_id, InstanceStatus.STOPPED)
        logger.info(f"Instance {instance_id} stopped")

    async def shutdown_all(self) -> None:
        """Stop all running instances."""
        instance_ids = list(self.running_instances.keys())
        for iid in instance_ids:
            try:
                await self.stop_instance(iid)
            except Exception as e:
                logger.error(f"Error stopping instance {iid}: {e}")

    async def _run_instance(
        self, instance: BotInstanceConfig, shutdown_event: asyncio.Event
    ) -> None:
        """
        Run a single bot instance. This is the task body.

        Builds an app.Config from the BotInstanceConfig, creates
        a TchapBot, and runs it until shutdown is signaled.
        """
        iid = instance.instance_id
        try:
            # Build a Config for this instance
            config = self._build_config(instance)

            # Import here to avoid circular imports
            from app.bot import TchapBot

            bot = TchapBot(
                homeserver=config.matrix_home_server,
                username=config.matrix_bot_username,
                password=config.matrix_bot_password,
                config=config,
            )

            # Update status to RUNNING
            await self.registry.update_instance_status(iid, InstanceStatus.RUNNING)
            logger.info(f"Instance {iid} now RUNNING")

            # Run the bot main loop
            # TchapBot.main() is long-running (sync_forever), so we run it
            # as a subtask and also wait for shutdown_event
            bot_task = asyncio.create_task(bot.main(), name=f"bot-main-{iid}")

            # Wait for either bot to finish or shutdown signal
            done, pending = await asyncio.wait(
                [bot_task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel any remaining tasks
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            # Check if bot exited with error
            if bot_task in done and bot_task.exception():
                raise bot_task.exception()

        except asyncio.CancelledError:
            logger.info(f"Instance {iid} cancelled")
            raise
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"Instance {iid} error: {error_msg}")
            logger.error(traceback.format_exc())
            await self.registry.update_instance_status(
                iid, InstanceStatus.ERROR, error_message=error_msg
            )

    async def _on_task_done(self, instance_id: str, task: asyncio.Task) -> None:
        """Callback when a bot task finishes."""
        self._shutdown_events.pop(instance_id, None)

        if task.cancelled():
            logger.info(f"Instance {instance_id} task was cancelled")
        elif task.exception():
            logger.error(f"Instance {instance_id} task failed: {task.exception()}")
        else:
            logger.info(f"Instance {instance_id} task completed normally")
            await self.registry.update_instance_status(instance_id, InstanceStatus.STOPPED)

    @staticmethod
    def _build_config(instance: BotInstanceConfig):
        """
        Build an app.Config from a BotInstanceConfig.

        Creates a fresh Config instance with the instance-specific values,
        inheriting defaults for everything else.
        """
        from app.config import Config

        # Build kwargs for Config constructor
        # Only override fields that are set in the instance
        kwargs = {}

        if instance.matrix_homeserver:
            kwargs["matrix_home_server"] = instance.matrix_homeserver
        if instance.matrix_username:
            kwargs["matrix_bot_username"] = instance.matrix_username
        if instance.matrix_password:
            kwargs["matrix_bot_password"] = instance.matrix_password
        if instance.webdav_url:
            kwargs["webdav_url"] = instance.webdav_url
        if instance.webdav_username:
            kwargs["webdav_username"] = instance.webdav_username
        if instance.webdav_password:
            kwargs["webdav_password"] = instance.webdav_password
        if instance.webdav_root_path:
            kwargs["webdav_root_path"] = instance.webdav_root_path
        if instance.albert_api_url:
            kwargs["albert_api_url"] = instance.albert_api_url
        if instance.albert_api_token:
            kwargs["albert_api_token"] = instance.albert_api_token
        if instance.albert_model:
            kwargs["albert_model"] = instance.albert_model
        if instance.albert_model_embedding:
            kwargs["albert_model_embedding"] = instance.albert_model_embedding
        if instance.allowed_domains:
            kwargs["user_allowed_domains"] = instance.allowed_domains
        if instance.groups_used:
            kwargs["groups_used"] = instance.groups_used

        kwargs["browser_extraction_enabled"] = instance.browser_extraction_enabled

        # Create Config - pydantic-settings will NOT read env vars when
        # values are passed explicitly, so instance-level overrides win.
        return Config(**kwargs)
