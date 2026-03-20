# SPDX-License-Identifier: MIT
"""Tests for platform.runner - subprocess-based bot runner."""

import asyncio
import json
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.platform.models import AdminRole, BotInstanceConfig, InstanceStatus, PlatformAdmin
from app.platform.registry import PlatformRegistry
from app.platform.runner import BotRunner


@pytest_asyncio.fixture
async def registry(tmp_path):
    db_path = str(tmp_path / "test_runner.db")
    reg = PlatformRegistry(db_path=db_path)
    await reg.open()
    await reg.add_admin(PlatformAdmin(email="test@gouv.fr"))
    yield reg
    await reg.close()


def _make_instance(**kwargs) -> BotInstanceConfig:
    defaults = {
        "name": "Test Bot",
        "matrix_homeserver": "https://matrix.tchap.gouv.fr",
        "matrix_username": "bot_user",
        "matrix_password": "bot_pass",
        "admin_email": "test@gouv.fr",
    }
    defaults.update(kwargs)
    return BotInstanceConfig(**defaults)


class _FakeProcess:
    """Simulates an asyncio subprocess for testing."""

    def __init__(self, stdout_lines=None, exit_code=0):
        self._stdout_lines = stdout_lines or ["READY\n", "RUNNING\n"]
        self._stderr_lines = ["2026-01-01 INFO Worker started\n"]
        self._stdout_idx = 0
        self._stderr_idx = 0
        self._exit_code = exit_code
        self.returncode = None
        self.pid = 12345
        self._wait_event = asyncio.Event()

        # stdin mock
        self.stdin = MagicMock()
        self.stdin.write = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdin.close = MagicMock()
        self.stdin.wait_closed = AsyncMock()

        # stdout/stderr as async readline
        self.stdout = MagicMock()
        self.stdout.readline = AsyncMock(side_effect=self._read_stdout)

        self.stderr = MagicMock()
        self.stderr.readline = AsyncMock(side_effect=self._read_stderr)

    async def _read_stdout(self):
        if self._stdout_idx < len(self._stdout_lines):
            line = self._stdout_lines[self._stdout_idx]
            self._stdout_idx += 1
            return line.encode("utf-8")
        # After all lines, wait for process to "exit"
        await self._wait_event.wait()
        return b""

    async def _read_stderr(self):
        if self._stderr_idx < len(self._stderr_lines):
            line = self._stderr_lines[self._stderr_idx]
            self._stderr_idx += 1
            return line.encode("utf-8")
        await self._wait_event.wait()
        return b""

    async def wait(self):
        await self._wait_event.wait()
        return self.returncode

    def send_signal(self, sig):
        # Simulate signal: mark process as done
        self.returncode = 0
        self._wait_event.set()

    def kill(self):
        self.returncode = -9
        self._wait_event.set()

    def simulate_exit(self, code=0):
        """Simulate the process exiting."""
        self.returncode = code
        self._wait_event.set()


@pytest.mark.asyncio
class TestBotRunner:
    async def test_start_instance_spawns_process(self, registry):
        runner = BotRunner(registry)
        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)

        assert instance.instance_id in runner._instances
        assert runner._instances[instance.instance_id].is_alive

        # Config should have been sent to stdin
        fake_proc.stdin.write.assert_called_once()
        written_data = fake_proc.stdin.write.call_args[0][0]
        config_sent = json.loads(written_data.decode("utf-8"))
        assert config_sent["matrix_homeserver"] == "https://matrix.tchap.gouv.fr"
        assert config_sent["matrix_username"] == "bot_user"

        # Wait a bit for monitor to process READY and RUNNING
        await asyncio.sleep(0.1)

        # Check status was updated to RUNNING
        fetched = await registry.get_instance(instance.instance_id)
        assert fetched.status == InstanceStatus.RUNNING

        # Cleanup
        fake_proc.simulate_exit(0)
        await asyncio.sleep(0.1)

    async def test_start_validates_required_fields(self, registry):
        runner = BotRunner(registry)

        with pytest.raises(ValueError, match="matrix_homeserver"):
            await runner.start_instance(BotInstanceConfig(
                matrix_username="u", matrix_password="p", admin_email="t@g.fr"
            ))

        with pytest.raises(ValueError, match="matrix_username"):
            await runner.start_instance(BotInstanceConfig(
                matrix_homeserver="https://h", matrix_password="p", admin_email="t@g.fr"
            ))

        with pytest.raises(ValueError, match="matrix_password"):
            await runner.start_instance(BotInstanceConfig(
                matrix_homeserver="https://h", matrix_username="u", admin_email="t@g.fr"
            ))

    async def test_start_rejects_duplicate(self, registry):
        runner = BotRunner(registry)
        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)

            with pytest.raises(RuntimeError, match="already running"):
                await runner.start_instance(instance)

        fake_proc.simulate_exit(0)
        await asyncio.sleep(0.1)

    async def test_stop_instance_sends_sigterm(self, registry):
        runner = BotRunner(registry)
        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)
            await asyncio.sleep(0.1)

            await runner.stop_instance(instance.instance_id)

        # Process should have received SIGTERM (simulated by send_signal)
        assert fake_proc.returncode == 0

        # Status should be STOPPED
        fetched = await registry.get_instance(instance.instance_id)
        assert fetched.status == InstanceStatus.STOPPED

    async def test_stop_nonexistent_raises(self, registry):
        runner = BotRunner(registry)

        with pytest.raises(RuntimeError, match="not managed"):
            await runner.stop_instance("nonexistent")

    async def test_error_status_on_worker_error(self, registry):
        runner = BotRunner(registry)
        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess(
            stdout_lines=["READY\n", "ERROR ConnectionRefused: matrix server down\n"]
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)
            await asyncio.sleep(0.1)

        # Status should be ERROR with message
        fetched = await registry.get_instance(instance.instance_id)
        assert fetched.status == InstanceStatus.ERROR
        assert "ConnectionRefused" in fetched.error_message

        fake_proc.simulate_exit(1)
        await asyncio.sleep(0.1)

    async def test_running_instances_property(self, registry):
        runner = BotRunner(registry)
        assert len(runner.running_instances) == 0

        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)

        assert len(runner.running_instances) == 1

        fake_proc.simulate_exit(0)
        await asyncio.sleep(0.1)
        assert len(runner.running_instances) == 0

    async def test_get_instance_pid(self, registry):
        runner = BotRunner(registry)
        instance = _make_instance()
        await registry.add_instance(instance)

        fake_proc = _FakeProcess()
        fake_proc.pid = 42

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await runner.start_instance(instance)

        assert runner.get_instance_pid(instance.instance_id) == 42
        assert runner.get_instance_pid("unknown") is None

        fake_proc.simulate_exit(0)
        await asyncio.sleep(0.1)

    async def test_shutdown_all(self, registry):
        runner = BotRunner(registry)
        procs = []

        for i in range(3):
            instance = _make_instance(name=f"Bot {i}")
            await registry.add_instance(instance)
            fake_proc = _FakeProcess()
            procs.append((instance, fake_proc))

        with patch("asyncio.create_subprocess_exec", side_effect=[p for _, p in procs]):
            for inst, _ in procs:
                await runner.start_instance(inst)

        await asyncio.sleep(0.1)
        assert len(runner.running_instances) == 3

        await runner.shutdown_all()

        # All should be stopped
        for inst, _ in procs:
            fetched = await registry.get_instance(inst.instance_id)
            assert fetched.status == InstanceStatus.STOPPED


@pytest.mark.asyncio
class TestWorkerScript:
    """Test the worker module can at least parse config."""

    async def test_build_config_from_instance_data(self):
        """Verify _build_config correctly maps fields."""
        # Import worker directly
        from app.platform.worker import _build_config

        data = {
            "matrix_homeserver": "https://matrix.tchap.gouv.fr",
            "matrix_username": "bot",
            "matrix_password": "pass",
            "albert_api_url": "https://albert.api.gouv.fr",
            "albert_model": "meta-llama/Llama-3.1-8B-Instruct",
            "allowed_domains": ["gouv.fr", "ecologie.gouv.fr"],
            "groups_used": "document,web",
        }

        config = _build_config(data)

        assert config.matrix_home_server == "https://matrix.tchap.gouv.fr"
        assert config.matrix_bot_username == "bot"
        assert config.matrix_bot_password == "pass"
        assert config.albert_api_url == "https://albert.api.gouv.fr"
        assert config.user_allowed_domains == ["gouv.fr", "ecologie.gouv.fr"]
        assert config.groups_used == "document,web"

    async def test_build_config_string_domains(self):
        from app.platform.worker import _build_config

        data = {
            "matrix_homeserver": "https://h",
            "matrix_username": "u",
            "matrix_password": "p",
            "allowed_domains": '["gouv.fr"]',
        }
        config = _build_config(data)
        assert config.user_allowed_domains == ["gouv.fr"]

    async def test_build_config_empty_fields_skipped(self):
        from app.platform.worker import _build_config

        data = {
            "matrix_homeserver": "https://h",
            "matrix_username": "u",
            "matrix_password": "p",
            "webdav_url": "",  # empty, should not override default
        }
        config = _build_config(data)
        assert config.matrix_home_server == "https://h"
