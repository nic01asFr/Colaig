"""Tests — tasks/executor.py (Phase 6)"""

import asyncio
import pytest

from colaig.tasks.executor import TaskExecutor
from colaig.models import TaskHandle


class TestTaskExecutor:
    @pytest.mark.asyncio
    async def test_submit_returns_handle_immediately(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)

        async def work():
            await asyncio.sleep(0.01)
            return "done"

        handle = await executor.submit(work(), "task-1", "conv-1")
        assert isinstance(handle, TaskHandle)
        assert handle.task_id == "task-1"
        assert handle.conversation_id == "conv-1"
        # Statut initial : pending ou running (non bloquant)
        assert handle.status in ("pending", "running")

    @pytest.mark.asyncio
    async def test_on_complete_called_with_result(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)
        results = []

        async def work():
            return "résultat"

        async def on_complete(result):
            results.append(result)

        handle = await executor.submit(work(), "task-1", "conv-1", on_complete=on_complete)
        # Attendre que la tâche se termine
        await asyncio.sleep(0.05)
        assert results == ["résultat"]
        assert handle.status == "done"

    @pytest.mark.asyncio
    async def test_on_error_called_on_exception(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)
        errors = []

        async def work():
            raise ValueError("erreur test")

        async def on_error(exc):
            errors.append(exc)

        handle = await executor.submit(work(), "task-1", "conv-1", on_error=on_error)
        await asyncio.sleep(0.05)
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert handle.status == "error"

    @pytest.mark.asyncio
    async def test_sequential_within_conversation(self):
        executor = TaskExecutor(max_concurrent=10, queue_ttl=60)
        order = []

        async def make_work(value: int, delay: float):
            async def work():
                await asyncio.sleep(delay)
                order.append(value)
            return work()

        handle1 = await executor.submit(await make_work(1, 0.02), "t1", "conv-A")
        handle2 = await executor.submit(await make_work(2, 0.01), "t2", "conv-A")
        handle3 = await executor.submit(await make_work(3, 0.0), "t3", "conv-A")

        await asyncio.sleep(0.15)
        # Mêmes conv → séquentiels
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_parallel_across_conversations(self):
        executor = TaskExecutor(max_concurrent=10, queue_ttl=60)
        started = []

        async def make_work(conv: str, delay: float):
            async def work():
                started.append(conv)
                await asyncio.sleep(delay)
            return work()

        await executor.submit(await make_work("conv-A", 0.05), "t1", "conv-A")
        await executor.submit(await make_work("conv-B", 0.05), "t2", "conv-B")

        await asyncio.sleep(0.02)
        # Les deux doivent avoir démarré (parallèles)
        assert len(started) == 2

    @pytest.mark.asyncio
    async def test_multiple_tasks_same_conv(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)
        done = []

        async def make_work(i: int):
            async def work():
                done.append(i)
                return i
            return work()

        handles = []
        for i in range(5):
            h = await executor.submit(await make_work(i), f"t{i}", "conv-1")
            handles.append(h)

        await asyncio.sleep(0.1)
        assert len(done) == 5
        assert all(h.status == "done" for h in handles)

    @pytest.mark.asyncio
    async def test_no_on_complete_no_crash(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)

        async def work():
            return 42

        handle = await executor.submit(work(), "task-1", "conv-1")
        await asyncio.sleep(0.05)
        assert handle.status == "done"

    @pytest.mark.asyncio
    async def test_start_stop(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)
        await executor.start()
        assert executor._cleanup_task is not None
        assert not executor._cleanup_task.done()
        await executor.stop()
        assert executor._cleanup_task.cancelled() or executor._cleanup_task.done()
