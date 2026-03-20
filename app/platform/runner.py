# SPDX-License-Identifier: MIT
"""
Multi-instance bot runner with subprocess isolation.

Each bot instance runs in its own Python subprocess, ensuring complete
isolation of global state (singletons, registries, caches). The runner
manages the lifecycle of these subprocesses:

    start_instance  ->  spawns subprocess  ->  reads READY/RUNNING from stdout
    stop_instance   ->  sends SIGTERM       ->  waits for process exit
    shutdown_all    ->  stops all instances

Communication protocol (stdout lines from worker):
    READY    - Config loaded, about to start bot
    RUNNING  - Bot connected and sync_forever started
    ERROR X  - Fatal error with message X

Logs from each worker go to stderr and are captured by the runner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from .models import BotInstanceConfig, InstanceStatus
from .registry import PlatformRegistry

logger = logging.getLogger(__name__)

# Grace period before SIGKILL after SIGTERM
_STOP_TIMEOUT = 15


class _InstanceProcess:
    """Tracks a running subprocess for one bot instance."""

    __slots__ = ("instance_id", "name", "process", "monitor_task", "log_task")

    def __init__(
        self,
        instance_id: str,
        name: str,
        process: asyncio.subprocess.Process,
        monitor_task: asyncio.Task,
        log_task: asyncio.Task,
    ):
        self.instance_id = instance_id
        self.name = name
        self.process = process
        self.monitor_task = monitor_task
        self.log_task = log_task

    @property
    def is_alive(self) -> bool:
        return self.process.returncode is None


class BotRunner:
    """
    Manages multiple bot instances as isolated subprocesses.

    Each instance gets its own Python process via `python -m app.platform.worker`,
    ensuring no shared global state (command_registry, context managers, etc.).
    """

    def __init__(self, registry: PlatformRegistry):
        self.registry = registry
        self._instances: dict[str, _InstanceProcess] = {}

    @property
    def running_instances(self) -> dict[str, _InstanceProcess]:
        """Return currently running instance processes."""
        return {k: v for k, v in self._instances.items() if v.is_alive}

    async def start_instance(self, instance: BotInstanceConfig) -> None:
        """
        Start a bot instance in a subprocess.

        Validates required fields, spawns the worker process, passes the
        config as JSON on stdin, then monitors stdout for status updates.
        """
        iid = instance.instance_id

        # Check not already running
        if iid in self._instances and self._instances[iid].is_alive:
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

        # Serialize config to JSON
        config_json = json.dumps(instance.to_dict(), ensure_ascii=False)

        # Spawn subprocess
        python = sys.executable
        process = await asyncio.create_subprocess_exec(
            python, "-m", "app.platform.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Inherit parent env so PYTHONPATH etc. are available
            env={**os.environ, "COLAIG_INSTANCE_ID": iid},
        )

        # Send config on stdin and close it
        process.stdin.write(config_json.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

        # Start monitoring tasks
        monitor_task = asyncio.create_task(
            self._monitor_stdout(iid, process),
            name=f"monitor-{iid}",
        )
        log_task = asyncio.create_task(
            self._stream_stderr(iid, instance.name, process),
            name=f"logs-{iid}",
        )

        self._instances[iid] = _InstanceProcess(
            instance_id=iid,
            name=instance.name,
            process=process,
            monitor_task=monitor_task,
            log_task=log_task,
        )

        logger.info(f"Instance {iid} ({instance.name}) subprocess started (pid={process.pid})")

    async def stop_instance(self, instance_id: str) -> None:
        """
        Gracefully stop a running bot instance.

        Sends SIGTERM and waits up to _STOP_TIMEOUT seconds before SIGKILL.
        """
        inst = self._instances.get(instance_id)
        if not inst:
            raise RuntimeError(f"Instance {instance_id} is not managed")

        if not inst.is_alive:
            raise RuntimeError(f"Instance {instance_id} is not running")

        pid = inst.process.pid
        logger.info(f"Stopping instance {instance_id} (pid={pid})")

        # Send SIGTERM
        try:
            inst.process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass  # Already dead

        # Wait for graceful shutdown
        try:
            await asyncio.wait_for(inst.process.wait(), timeout=_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"Instance {instance_id} did not stop gracefully, killing")
            try:
                inst.process.kill()
            except ProcessLookupError:
                pass
            await inst.process.wait()

        # Cancel monitoring tasks
        inst.monitor_task.cancel()
        inst.log_task.cancel()

        await self.registry.update_instance_status(instance_id, InstanceStatus.STOPPED)
        logger.info(f"Instance {instance_id} stopped (exit={inst.process.returncode})")

    async def shutdown_all(self) -> None:
        """Stop all running instances."""
        instance_ids = list(self.running_instances.keys())
        if not instance_ids:
            return

        logger.info(f"Shutting down {len(instance_ids)} instances")

        # Send SIGTERM to all
        for iid in instance_ids:
            inst = self._instances[iid]
            if inst.is_alive:
                try:
                    inst.process.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass

        # Wait for all with timeout
        tasks = []
        for iid in instance_ids:
            inst = self._instances[iid]
            tasks.append(inst.process.wait())

        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            # Kill remaining
            for iid in instance_ids:
                inst = self._instances[iid]
                if inst.is_alive:
                    try:
                        inst.process.kill()
                    except ProcessLookupError:
                        pass

        # Update status and cleanup
        for iid in instance_ids:
            inst = self._instances[iid]
            inst.monitor_task.cancel()
            inst.log_task.cancel()
            await self.registry.update_instance_status(iid, InstanceStatus.STOPPED)

        logger.info("All instances shut down")

    async def _monitor_stdout(
        self, instance_id: str, process: asyncio.subprocess.Process
    ) -> None:
        """
        Read status lines from the worker's stdout.

        Expected lines: READY, RUNNING, ERROR <message>
        """
        try:
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break  # EOF, process exited

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if line == "READY":
                    logger.info(f"Instance {instance_id}: worker ready")

                elif line == "RUNNING":
                    await self.registry.update_instance_status(
                        instance_id, InstanceStatus.RUNNING
                    )
                    logger.info(f"Instance {instance_id}: now RUNNING")

                elif line.startswith("ERROR "):
                    error_msg = line[6:]
                    await self.registry.update_instance_status(
                        instance_id, InstanceStatus.ERROR, error_message=error_msg
                    )
                    logger.error(f"Instance {instance_id}: {error_msg}")

                else:
                    logger.debug(f"Instance {instance_id} stdout: {line}")

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Monitor error for {instance_id}: {e}")

        # Process exited - check return code
        await process.wait()
        rc = process.returncode

        if rc is not None and rc != 0:
            current = await self.registry.get_instance(instance_id)
            if current and current.status != InstanceStatus.STOPPED:
                await self.registry.update_instance_status(
                    instance_id,
                    InstanceStatus.ERROR,
                    error_message=f"Process exited with code {rc}",
                )
                logger.error(f"Instance {instance_id}: exited with code {rc}")
        elif rc == 0:
            current = await self.registry.get_instance(instance_id)
            if current and current.status == InstanceStatus.RUNNING:
                await self.registry.update_instance_status(
                    instance_id, InstanceStatus.STOPPED
                )
                logger.info(f"Instance {instance_id}: exited normally")

    async def _stream_stderr(
        self, instance_id: str, name: str, process: asyncio.subprocess.Process
    ) -> None:
        """Stream worker stderr to platform logger with instance prefix."""
        prefix = f"[{name or instance_id}]"
        try:
            while True:
                line_bytes = await process.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.info(f"{prefix} {line}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Stderr reader error for {instance_id}: {e}")

    def get_instance_pid(self, instance_id: str) -> Optional[int]:
        """Get the PID of a running instance, or None."""
        inst = self._instances.get(instance_id)
        if inst and inst.is_alive:
            return inst.process.pid
        return None
