"""Tests — tasks/executor.py (Phase 6)"""

import asyncio
import pytest

from colaig.tasks.executor import TaskExecutor
from colaig.models import TaskHandle


async def attendre(condition, delai_max: float = 5.0) -> None:
    """Attend qu'une condition soit vraie, plutôt qu'une durée d'horloge.

    POURQUOI CE HELPER EXISTE
    --------------------------
    Ces tests dormaient un temps fixe — `await asyncio.sleep(0.15)` — puis vérifiaient
    que le travail était fait.

    Ce qui a été OBSERVÉ, le 24/08/2026 : `test_sequential_within_conversation` a échoué
    une fois en suite complète, sur une exécution à 93 s au lieu de 30, concomitante
    d'une mesure LLM en tâche de fond. Il a repassé isolément immédiatement après.

    Ce qui n'a PAS été établi : la cause. Quatre tentatives de reproduction — le test
    seul sous charge processeur, la suite complète deux fois à vitesse normale, la suite
    complète sous forte charge à 79 s — sont toutes vertes. L'explication « la boucle
    d'événements n'a pas rendu la main assez vite » est **plausible et non démontrée**,
    et elle est écrite ici comme telle.

    Pourquoi corriger quand même. Un test qui échoue une fois sur vingt sans qu'on sache
    pourquoi est le pire cas : on finit par accuser la CI plutôt que le code, et le jour
    où un vrai défaut s'y ajoute, personne ne le voit. C'est mot pour mot ce que
    `tests/CLAUDE.md` interdit.

    Attendre une **condition** plutôt qu'une durée retire la question : le test converge,
    ou il échoue au bout d'un délai franchement dimensionné — et cet échec-là voudra dire
    quelque chose.
    """
    echeance = asyncio.get_running_loop().time() + delai_max
    while not condition():
        if asyncio.get_running_loop().time() > echeance:
            raise AssertionError(
                f"condition non atteinte en {delai_max} s — ce n'est pas de la lenteur, "
                "c'est un défaut"
            )
        await asyncio.sleep(0.005)


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
        await attendre(lambda: handle.status == "done")
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
        await attendre(lambda: handle.status == "error")
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

        await executor.submit(await make_work(1, 0.02), "t1", "conv-A")
        await executor.submit(await make_work(2, 0.01), "t2", "conv-A")
        await executor.submit(await make_work(3, 0.0), "t3", "conv-A")

        # Les temporisations sont décroissantes A DESSEIN : sans séquencement, la
        # troisième tâche finirait la première et l'ordre serait [3, 2, 1].
        await attendre(lambda: len(order) == 3)
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

        # Les deux démarrent AVANT que l'une des deux ne se termine : c'est ce qui
        # distingue le parallélisme d'un simple enchaînement rapide. Chaque tâche dort
        # 0,05 s après avoir signalé son démarrage — si le séquencement s'appliquait
        # entre conversations, `started` n'en contiendrait qu'une pendant ce temps.
        await attendre(lambda: len(started) == 2)
        assert set(started) == {"conv-A", "conv-B"}

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

        await attendre(lambda: all(h.status == "done" for h in handles))
        assert len(done) == 5
        assert all(h.status == "done" for h in handles)

    @pytest.mark.asyncio
    async def test_no_on_complete_no_crash(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)

        async def work():
            return 42

        handle = await executor.submit(work(), "task-1", "conv-1")
        await attendre(lambda: handle.status == "done")
        assert handle.status == "done"

    @pytest.mark.asyncio
    async def test_start_stop(self):
        executor = TaskExecutor(max_concurrent=5, queue_ttl=60)
        await executor.start()
        assert executor._cleanup_task is not None
        assert not executor._cleanup_task.done()
        await executor.stop()
        assert executor._cleanup_task.cancelled() or executor._cleanup_task.done()
