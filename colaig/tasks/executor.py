"""
Colaig — TaskExecutor (Phase 6)

Exécuteur de tâches async non-bloquant avec queue par conversation.

Propriétés :
- Messages d'une même conversation → séquentiels (cohérence trame)
- Messages de conversations différentes → parallèles (max_concurrent global)
- TTL sur les queues inactives (évite les fuites mémoire)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine

from colaig.models import TaskHandle

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Exécuteur de tâches async non-bloquant avec queue par conversation.

    Args:
        max_concurrent: Nombre maximum de tâches actives simultanément (toutes convs).
        queue_ttl: Durée en secondes avant suppression d'une queue inactive.
    """

    def __init__(self, max_concurrent: int = 20, queue_ttl: int = 1800) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue_ttl = queue_ttl
        self._conv_queues: dict[str, asyncio.Queue] = {}
        self._last_activity: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Démarre la tâche de nettoyage périodique des queues inactives."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Arrête proprement le TaskExecutor."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def submit(
        self,
        coro: Coroutine,
        task_id: str,
        conv_id: str,
        on_complete: Callable | None = None,
        on_error: Callable | None = None,
    ) -> TaskHandle:
        """Soumet une tâche pour exécution. Retourne TaskHandle immédiatement (non-bloquant).

        La coroutine est placée dans la queue de la conversation et exécutée
        dès que les tâches précédentes de cette conversation sont terminées.

        Args:
            coro: Coroutine à exécuter.
            task_id: Identifiant unique de la tâche.
            conv_id: Identifiant de la conversation (séquentialise les tâches de la même conv).
            on_complete: Callback async appelé avec le résultat si succès.
            on_error: Callback async appelé avec l'exception si erreur.

        Returns:
            TaskHandle avec status="pending" (mise à jour au fil de l'exécution).
        """
        handle = TaskHandle(task_id=task_id, conversation_id=conv_id, status="pending")

        if conv_id not in self._conv_queues:
            self._conv_queues[conv_id] = asyncio.Queue()
            asyncio.create_task(self._process_queue(conv_id))

        self._last_activity[conv_id] = time.monotonic()
        await self._conv_queues[conv_id].put((coro, handle, on_complete, on_error))

        logger.debug("task %s soumise (conv %s)", task_id, conv_id)
        return handle

    async def _process_queue(self, conv_id: str) -> None:
        """Traite les tâches d'une conversation de manière séquentielle."""
        while True:
            queue = self._conv_queues.get(conv_id)
            if queue is None:
                return

            try:
                # Attente avec timeout = TTL (permet la suppression des queues vides)
                item = await asyncio.wait_for(queue.get(), timeout=self._queue_ttl)
            except TimeoutError:
                self._conv_queues.pop(conv_id, None)
                self._last_activity.pop(conv_id, None)
                logger.debug("queue conv %s supprimée (inactive)", conv_id)
                return

            coro, handle, on_complete, on_error = item
            handle.status = "running"
            self._last_activity[conv_id] = time.monotonic()

            try:
                async with self._semaphore:
                    try:
                        result = await coro
                        handle.status = "done"
                        if on_complete:
                            await on_complete(result)
                    except asyncio.CancelledError:
                        handle.status = "cancelled"
                        raise
                    except Exception as exc:
                        handle.status = "error"
                        logger.error(
                            "erreur tâche %s: %s", handle.task_id, exc, exc_info=True
                        )
                        if on_error:
                            try:
                                await on_error(exc)
                            except Exception:
                                pass
            finally:
                queue.task_done()

    async def _cleanup_loop(self) -> None:
        """Nettoie périodiquement les queues inactives vides."""
        while True:
            await asyncio.sleep(self._queue_ttl // 2)
            now = time.monotonic()
            inactive = [
                conv_id
                for conv_id, last in list(self._last_activity.items())
                if now - last > self._queue_ttl
            ]
            for conv_id in inactive:
                queue = self._conv_queues.get(conv_id)
                if queue is not None and queue.empty():
                    self._conv_queues.pop(conv_id, None)
                    self._last_activity.pop(conv_id, None)
                    logger.debug("queue conv %s nettoyée (TTL)", conv_id)
