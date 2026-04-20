"""
Colaig — Mode C : Planificateur de tâches autonomes

run_task_scheduler_loop() : boucle asyncio lancée dans main.py, similaire
à run_indexation_loop() et run_user_memory_consolidation_loop().

    - Scan tous les workspaces personnels connus du resolver
    - Identifie les tâches dues (enabled=True, status=pending, next_run_at <= now)
    - Lance run_background_session() pour chaque tâche due
    - Concurrence limitée par un asyncio.Semaphore (COLAIG_TASK_MAX_CONCURRENT)

run_background_session() : exécute une session complète pour une tâche :
    1. Marque la tâche RUNNING + écrit current/session.json
    2. Construit la ToolRegistry background (run_subtask, update_plan, etc.)
    3. Lance le pipeline agents (Analyser → Orchestrateur avec tools background)
    4. Gère la livraison (messaging ou document)
    5. Archive current/ → runs/{ts}/ + met à jour task.json (lifecycle)

Séparation des responsabilités :
    - task_scheduler.py  → cycle de vie, scheduling, archivage
    - task_tools.py      → handlers des tools injectés dans l'orchestrateur
    - tasks.py           → dataclasses + file I/O
    - workspace_delegate.py → run_workspace_task() (réutilisé par run_subtask)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Concurrence maximale par défaut — surchargeable via ColaigConfig.task_max_concurrent
_DEFAULT_MAX_CONCURRENT = 3
# Délai de polling par défaut — surchargeable via ColaigConfig.task_scheduler_interval
_DEFAULT_POLL_INTERVAL = 60
# Timeout session par défaut — surchargeable via ColaigConfig.task_session_timeout
_DEFAULT_SESSION_TIMEOUT = 1800
# Nombre max d'échecs consécutifs avant désactivation auto — via ColaigConfig.task_max_error_count
_DEFAULT_MAX_ERROR_COUNT = 3


# =============================================================================
# Boucle de planification
# =============================================================================


async def run_task_scheduler_loop(
    storage,
    resolver,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    messaging=None,
    conversation_memory=None,
    workspace_stores: Optional[dict] = None,
    bm25_stores: Optional[dict] = None,
    workspace_directory=None,
    shutdown_event: Optional[asyncio.Event] = None,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    session_timeout: int = _DEFAULT_SESSION_TIMEOUT,
    max_error_count: int = _DEFAULT_MAX_ERROR_COUNT,
) -> None:
    """Boucle asyncio du planificateur de tâches autonomes (Mode C).

    À lancer via asyncio.gather() dans main.py au même titre que
    run_indexation_loop() et run_user_memory_consolidation_loop().

    Args:
        storage             : StorageProtocol.
        resolver            : ContextResolver — liste les workspaces connus.
        analyser/orchestrator/synthesiser : Pipeline agents Phase 2.
        retriever/generator : Fallback Phase 1.
        messaging           : MessagingProtocol (pour report_to_user).
        conversation_memory : ConversationMemory (persistance session).
        workspace_stores    : dict workspace_id → FaissStore.
        bm25_stores         : dict workspace_id → BM25Store.
        shutdown_event      : asyncio.Event — arrêt propre.
        poll_interval       : Secondes entre deux scans (défaut 60).
        max_concurrent      : Max sessions simultanées (défaut 3).
        session_timeout     : Secondes avant qu'une session running soit considérée
                              bloquée (défaut 1800 = 30 min).
        max_error_count     : Nombre max d'échecs consécutifs avant désactivation
                              automatique de la tâche (défaut 3).
    """
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    semaphore = asyncio.Semaphore(max_concurrent)
    logger.info(
        "task_scheduler: démarrage (poll=%ds, max_concurrent=%d, timeout=%ds, max_errors=%d)",
        poll_interval, max_concurrent, session_timeout, max_error_count,
    )

    # Attente initiale — laisser l'indexation démarrer
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            await _dispatch_due_tasks(
                storage=storage,
                resolver=resolver,
                analyser=analyser,
                orchestrator=orchestrator,
                synthesiser=synthesiser,
                retriever=retriever,
                generator=generator,
                messaging=messaging,
                conversation_memory=conversation_memory,
                workspace_stores=workspace_stores,
                bm25_stores=bm25_stores,
                workspace_directory=workspace_directory,
                semaphore=semaphore,
                session_timeout=session_timeout,
                max_error_count=max_error_count,
            )
        except Exception:
            logger.exception("task_scheduler: erreur pendant le dispatch")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass

    logger.info("task_scheduler: arrêt propre")


async def _dispatch_due_tasks(
    storage,
    resolver,
    semaphore: asyncio.Semaphore,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    messaging=None,
    conversation_memory=None,
    workspace_stores: Optional[dict] = None,
    bm25_stores: Optional[dict] = None,
    workspace_directory=None,
    session_timeout: int = _DEFAULT_SESSION_TIMEOUT,
    max_error_count: int = _DEFAULT_MAX_ERROR_COUNT,
) -> None:
    """Scanne les workspaces personnels, détecte les sessions bloquées et lance les tâches dues."""
    from colaig.agents.tasks import is_due, list_tasks

    # Collect les chemins de workspaces personnels (workspace_id commence par "personal-")
    personal_paths = [
        ws.storage_path
        for ws in resolver.workspaces
        if ws.workspace_id.startswith("personal-") and ws.storage_path
    ]

    for workspace_path in personal_paths:
        tasks = await list_tasks(storage, workspace_path)
        for task in tasks:
            # ── Détection sessions bloquées (status=running + heartbeat expiré) ──
            if task.status == "running":
                await _check_session_timeout(
                    storage=storage,
                    task=task,
                    messaging=messaging,
                    conversation_memory=conversation_memory,
                    session_timeout=session_timeout,
                )
                continue  # Ne pas dispatcher en parallèle

            if not is_due(task):
                continue

            # ── Guard error_count : trop d'échecs consécutifs → désactiver ──
            if task.error_count >= max_error_count:
                logger.warning(
                    "task_scheduler: tâche %s désactivée après %d échecs consécutifs",
                    task.task_id, task.error_count,
                )
                task.enabled = False
                from colaig.agents.tasks import save_task
                await save_task(storage, task)
                await _deliver_result(
                    task=task,
                    response_text=(
                        f"⚠️ Tâche **{task.name}** désactivée automatiquement après "
                        f"{task.error_count} échecs consécutifs. "
                        "Vérifiez la configuration ou relancez via `colaig_run_task_now`."
                    ),
                    messaging=messaging,
                    storage=storage,
                    conversation_memory=conversation_memory,
                )
                continue

            logger.info(
                "task_scheduler: dispatch tâche %s (%s) pour user=%s",
                task.task_id, task.name, task.user_id,
            )
            asyncio.create_task(
                _run_with_semaphore(
                    semaphore=semaphore,
                    storage=storage,
                    task=task,
                    resolver=resolver,
                    analyser=analyser,
                    orchestrator=orchestrator,
                    synthesiser=synthesiser,
                    retriever=retriever,
                    generator=generator,
                    messaging=messaging,
                    conversation_memory=conversation_memory,
                    workspace_stores=workspace_stores,
                    bm25_stores=bm25_stores,
                    workspace_directory=workspace_directory,
                )
            )


async def _check_session_timeout(
    storage,
    task,
    messaging=None,
    conversation_memory=None,
    session_timeout: int = _DEFAULT_SESSION_TIMEOUT,
) -> None:
    """Vérifie si une session running a dépassé son timeout.

    Si le last_heartbeat de current/session.json est plus ancien que session_timeout
    secondes, la session est considérée bloquée : la tâche est marquée FAILED,
    un résumé archivé et l'utilisateur notifié.
    """
    from colaig.agents.tasks import (
        TaskRunSummary,
        archive_run,
        compute_next_run,
        load_session_state,
        save_task,
        load_plan,
        _now_iso,
    )
    from datetime import timezone

    session = await load_session_state(storage, task)
    if session is None:
        # Pas de session.json — session fantôme, forcer le reset
        logger.warning(
            "task_scheduler: tâche %s en status=running sans session.json — reset forcé",
            task.task_id,
        )
    else:
        try:
            last_hb = datetime.fromisoformat(session.last_heartbeat)
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_hb).total_seconds()
            if elapsed < session_timeout:
                return  # Session encore active
            logger.warning(
                "task_scheduler: tâche %s timeout (heartbeat il y a %.0fs > %ds)",
                task.task_id, elapsed, session_timeout,
            )
        except (ValueError, AttributeError):
            logger.warning("task_scheduler: tâche %s heartbeat illisible", task.task_id)

    # ── Session bloquée : archiver et notifier ──────────────────────────────
    run_ts = session.started_at if session else task.last_run_at or _now_iso()
    plan_data = await load_plan(storage, task)
    summary = TaskRunSummary(
        task_id=task.task_id,
        task_name=task.name,
        run_id=run_ts.replace(":", "-").replace("+", "").split(".")[0],
        user_id=task.user_id,
        started_at=run_ts,
        completed_at=_now_iso(),
        status="failed",
        steps_executed=session.current_step if session else 0,
        error_message=f"Timeout : session inactive depuis plus de {session_timeout}s",
    )
    await archive_run(storage, task, run_ts, summary, plan_data)

    task.last_run_status = "failed"
    task.error_count += 1
    if task.schedule_type == "once":
        task.status = "archived"
        task.enabled = False
    else:
        task.next_run_at = compute_next_run(task.schedule_type, task.schedule_value)
        task.status = "pending"
    await save_task(storage, task)

    await _deliver_result(
        task=task,
        response_text=(
            f"⚠️ Tâche **{task.name}** interrompue : "
            f"session inactive depuis plus de {session_timeout // 60} minutes."
        ),
        messaging=messaging,
        storage=storage,
        conversation_memory=conversation_memory,
    )
    logger.info("task_scheduler: tâche %s timeout → %s", task.task_id, task.status)


async def _run_with_semaphore(semaphore: asyncio.Semaphore, **kwargs) -> None:
    """Enveloppe run_background_session dans un semaphore de concurrence."""
    async with semaphore:
        try:
            await run_background_session(**kwargs)
        except Exception:
            logger.exception(
                "task_scheduler: exception non gérée dans la session tâche %s",
                kwargs.get("task", {}).task_id if kwargs.get("task") else "?",
            )


# =============================================================================
# Session d'orchestration de tâche
# =============================================================================


async def run_background_session(
    storage,
    task,
    resolver,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    messaging=None,
    conversation_memory=None,
    workspace_stores: Optional[dict] = None,
    bm25_stores: Optional[dict] = None,
    workspace_directory=None,
) -> None:
    """Exécute une session complète pour une tâche autonome.

    Flux :
        1. Marquer RUNNING + écrire current/session.json
        2. Construire ToolRegistry background
        3. Résoudre contexte PERSONAL du user (workspace personnel)
        4. Analyser l'instruction → Intent
        5. Orchestrer avec tools background → ExecutionPlan
        6. Synthétiser → GeneratedResponse
        7. Livrer (messaging ou document)
        8. Archiver current/ → runs/{ts}/
        9. Mettre à jour task.json (lifecycle + next_run_at)

    Args:
        storage             : StorageProtocol.
        task                : TaskDefinition à exécuter.
        resolver            : ContextResolver.
        analyser/orchestrator/synthesiser : Pipeline agents.
        retriever/generator : Fallback Phase 1.
        messaging           : MessagingProtocol.
        conversation_memory : ConversationMemory.
        workspace_stores    : dict workspace_id → FaissStore.
        bm25_stores         : dict workspace_id → BM25Store.
    """
    from colaig.agents.tasks import (
        TaskSessionState,
        TaskRunSummary,
        archive_run,
        compute_next_run,
        load_session_state,
        save_task,
        save_session_state,
        load_plan,
        _now_iso,
    )

    run_ts = _now_iso()
    safe_ts = run_ts.replace(":", "-").replace("+", "").split(".")[0]

    # ── Détection resume depuis session existante (waiting_for_user) ────────
    existing_session = await load_session_state(storage, task)
    is_resume = (
        existing_session is not None
        and existing_session.pending_user_reply
    )
    if is_resume:
        conversation_id = existing_session.conversation_id
        pending_reply = existing_session.pending_user_reply
        logger.info(
            "run_background_session: reprise session %s (tâche=%s, réponse=%r)",
            conversation_id, task.task_id, pending_reply[:60],
        )
    else:
        conversation_id = f"task_{task.task_id}_{safe_ts}"
        pending_reply = ""

    # ── 1. Marquer RUNNING ──────────────────────────────────────────────────
    task.status = "running"
    task.last_run_at = run_ts
    await save_task(storage, task)

    if is_resume and existing_session is not None:
        session_state = existing_session
        session_state.status = "running"
        session_state.pending_user_reply = ""  # Consommé
        session_state.last_heartbeat = run_ts
    else:
        session_state = TaskSessionState(
            task_id=task.task_id,
            conversation_id=conversation_id,
            status="running",
            started_at=run_ts,
        )
    await save_session_state(storage, task, session_state)

    logger.info("run_background_session: démarrage session %s (tâche=%s)", conversation_id, task.task_id)

    response_text = ""
    sources: list[str] = []
    error_message = ""
    steps_executed = 0
    subtasks_executed = 0
    success = False

    try:
        # ── 2. ToolRegistry background ──────────────────────────────────────
        background_registry = _build_background_registry(
            storage=storage,
            task=task,
            session_state=session_state,
            resolver=resolver,
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
            retriever=retriever,
            generator=generator,
            messaging=messaging,
            conversation_memory=conversation_memory,
            workspace_stores=workspace_stores,
            bm25_stores=bm25_stores,
            workspace_directory=workspace_directory,
        )

        # ── 3. Contexte PERSONAL du user ────────────────────────────────────
        from colaig.models import ConversationType, IncomingMessage
        from colaig.context.workspace import get_or_create_personal_workspace
        from colaig.context.layers import build_context
        from colaig.models import ContextMode

        personal_ws = await get_or_create_personal_workspace(storage, task.user_id)

        from colaig.models import WorkspaceContext
        context = WorkspaceContext(
            workspace=personal_ws,
            mode=ContextMode.PERSONAL,
            system_prompt=personal_ws.system_prompt or "",
            user_id=task.user_id,
            user_display_name=task.user_id,
        )

        # ── 4. Charger historique de la session ─────────────────────────────
        history = []
        if conversation_memory:
            try:
                history = await conversation_memory.load_relevant_history(
                    workspace_path=task.workspace_path,
                    conversation_id=conversation_id,
                    current_query=task.query,
                )
            except Exception as exc:
                logger.warning("run_background_session: impossible de charger historique: %s", exc)

        # ── 5. Message virtuel représentant la tâche ─────────────────────────
        # En cas de resume, la query devient la réponse de l'utilisateur
        query_body = pending_reply if is_resume and pending_reply else task.query
        message = IncomingMessage(
            user_id=task.user_id,
            conversation_id=conversation_id,
            body=query_body,
            conversation_type=ConversationType.DM,
            platform="task_scheduler",
        )

        # ── 6. Pipeline Phase 2 ou Phase 1 ──────────────────────────────────
        if analyser and orchestrator and synthesiser:
            response_text, sources, steps_executed = await _run_agents_pipeline(
                message=message,
                context=context,
                analyser=analyser,
                orchestrator=orchestrator,
                synthesiser=synthesiser,
                background_registry=background_registry,
                conversation_history=history,
                max_steps=task.max_steps if task.max_steps > 0 else None,
            )
            subtasks_executed = session_state.subtasks_done
        elif retriever and generator:
            response_text, sources = await _run_generator_pipeline(
                query=task.query,
                context=context,
                retriever=retriever,
                generator=generator,
                workspace_stores=workspace_stores,
            )
        else:
            raise RuntimeError("Ni pipeline agents ni generator disponible pour la session")

        # ── 7. Sauvegarder l'historique ──────────────────────────────────────
        if conversation_memory:
            try:
                await conversation_memory.save_turn(
                    workspace_path=task.workspace_path,
                    conversation_id=conversation_id,
                    user_message=task.query,
                    assistant_response=response_text,
                    existing_history=history,
                )
            except Exception as exc:
                logger.warning("run_background_session: impossible de sauvegarder historique: %s", exc)

        # ── 8. Livrer le résultat ────────────────────────────────────────────
        await _deliver_result(
            task=task,
            response_text=response_text,
            messaging=messaging,
            storage=storage,
            conversation_memory=conversation_memory,
        )

        success = True
        session_state.status = "completing"
        await save_session_state(storage, task, session_state)

    except Exception as exc:
        error_message = str(exc)
        logger.exception(
            "run_background_session: erreur session %s (tâche=%s): %s",
            conversation_id, task.task_id, exc,
        )
        task.error_count += 1

    # ── 9. Archivage current/ → runs/{ts}/ ──────────────────────────────────
    plan_data = await load_plan(storage, task)
    summary = TaskRunSummary(
        task_id=task.task_id,
        task_name=task.name,
        run_id=safe_ts,
        user_id=task.user_id,
        started_at=run_ts,
        completed_at=_now_iso(),
        status="done" if success else "failed",
        steps_executed=steps_executed,
        subtasks_executed=subtasks_executed,
        answer_preview=response_text[:200] if response_text else "",
        sources=sources[:10],
        error_message=error_message,
    )
    await archive_run(storage, task, run_ts, summary, plan_data)

    # ── 10. Mise à jour cycle de vie task.json ───────────────────────────────
    task.last_run_status = "done" if success else "failed"
    if success:
        task.error_count = 0

    if task.schedule_type == "once":
        # Exécution unique — archiver la tâche
        task.status = "archived"
        task.enabled = False
    else:
        # Récurrente — reprogrammer
        task.next_run_at = compute_next_run(task.schedule_type, task.schedule_value)
        task.status = "pending"

    await save_task(storage, task)
    logger.info(
        "run_background_session: session %s terminée (status=%s, next=%s)",
        conversation_id,
        task.status,
        task.next_run_at,
    )

    # ── 11. Notification FAILED ──────────────────────────────────────────────
    if not success:
        await _deliver_result(
            task=task,
            response_text=(
                f"⚠️ Tâche **{task.name}** — échec lors de l'exécution.\n\n"
                f"Erreur : {error_message[:300] if error_message else 'inconnue'}"
            ),
            messaging=messaging,
            storage=storage,
            conversation_memory=conversation_memory,
        )


# =============================================================================
# Pipeline helpers
# =============================================================================


async def _run_agents_pipeline(
    message,
    context,
    analyser,
    orchestrator,
    synthesiser,
    background_registry,
    conversation_history: list,
    max_steps: Optional[int] = None,
) -> tuple[str, list[str], int]:
    """Exécute Analyser → Orchestrateur (avec background_registry) → Synthétiseur.

    Args:
        max_steps : Override de max_iterations pour l'orchestrateur (TaskDefinition.max_steps).

    Returns:
        (response_text, sources, steps_executed)
    """
    # Analyser
    intent = await analyser.analyse(message, context)

    if intent.is_direct:
        return intent.direct_response or "", [], 0

    # Orchestrateur avec ToolRegistry background injecté
    # Substituer temporairement le tool_registry et max_iterations
    original_registry = getattr(orchestrator, "_tool_registry", None)
    original_max_iter = getattr(orchestrator, "_max_iterations", None)
    try:
        orchestrator._tool_registry = background_registry
        if max_steps is not None and max_steps > 0:
            orchestrator._max_iterations = max_steps
        plan = await orchestrator.execute(intent, context)
    finally:
        orchestrator._tool_registry = original_registry
        if original_max_iter is not None:
            orchestrator._max_iterations = original_max_iter

    steps = len(plan.steps) if hasattr(plan, "steps") and plan.steps else 0

    # Synthétiseur
    response = await synthesiser.synthesise(
        plan=plan,
        context=context,
        conversation_history=conversation_history,
    )

    sources = list(response.sources) if response.sources else []
    return response.text or "", sources, steps


async def _run_generator_pipeline(
    query: str,
    context,
    retriever,
    generator,
    workspace_stores: Optional[dict] = None,
) -> tuple[str, list[str]]:
    """Pipeline Phase 1 : RAG + Generator.

    Returns:
        (response_text, sources)
    """
    ws = context.workspace
    ws_id = ws.workspace_id if ws else None
    store = (workspace_stores or {}).get(ws_id) if ws_id else None

    results = await retriever.retrieve(
        query=query,
        k=5,
        score_threshold=0.3,
        store=store,
    )
    response = await generator.generate(
        query=query,
        context=context,
        search_results=results,
    )
    sources = list(response.sources) if response.sources else []
    return response.text or "", sources


# =============================================================================
# Livraison du résultat
# =============================================================================


async def _deliver_result(
    task, response_text: str, messaging=None, storage=None, conversation_memory=None
) -> None:
    """Livre le résultat selon delivery_type de la tâche.

    - "messaging"     → messaging.send(delivery_target, response_text)
    - "document"      → storage.upload(delivery_target, content)
    - "conversation"  → conversation_memory.save_turn(delivery_target, ...)
                        (pull-based : récupérable via colaig_ask)

    Non-bloquant sur erreur — logge seulement.
    """
    if not response_text:
        logger.warning("_deliver_result: réponse vide pour tâche %s, livraison ignorée", task.task_id)
        return

    if task.delivery_type == "messaging":
        if not messaging:
            logger.warning("_deliver_result: messaging non disponible pour tâche %s", task.task_id)
            return
        try:
            # En-tête pour identifier la tâche dans le salon
            header = f"**[Tâche : {task.name}]**\n\n"
            await messaging.send(task.delivery_target, header + response_text)
            logger.info(
                "_deliver_result: résultat tâche %s envoyé → %s",
                task.task_id, task.delivery_target,
            )
        except Exception as exc:
            logger.error(
                "_deliver_result: erreur envoi messaging tâche %s → %s: %s",
                task.task_id, task.delivery_target, exc,
            )

    elif task.delivery_type == "document":
        if not storage:
            logger.warning("_deliver_result: storage non disponible pour tâche %s", task.task_id)
            return
        try:
            await storage.upload(task.delivery_target, response_text.encode("utf-8"))
            logger.info(
                "_deliver_result: document tâche %s créé → %s",
                task.task_id, task.delivery_target,
            )
        except Exception as exc:
            logger.error(
                "_deliver_result: erreur écriture document tâche %s → %s: %s",
                task.task_id, task.delivery_target, exc,
            )
    elif task.delivery_type == "conversation":
        if not conversation_memory:
            logger.warning("_deliver_result: conversation_memory non disponible pour tâche %s", task.task_id)
            return
        try:
            await conversation_memory.save_turn(
                workspace_path=task.workspace_path,
                conversation_id=task.delivery_target,
                user_message=task.query,
                assistant_response=f"**[Tâche : {task.name}]**\n\n{response_text}",
                existing_history=[],
            )
            logger.info(
                "_deliver_result: résultat tâche %s écrit → conversation %s",
                task.task_id, task.delivery_target,
            )
        except Exception as exc:
            logger.error(
                "_deliver_result: erreur écriture conversation tâche %s → %s: %s",
                task.task_id, task.delivery_target, exc,
            )
    else:
        logger.warning(
            "_deliver_result: delivery_type inconnu %r pour tâche %s",
            task.delivery_type, task.task_id,
        )


# =============================================================================
# Construction du ToolRegistry background
# =============================================================================


def _build_background_registry(
    storage,
    task,
    session_state,
    resolver,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    messaging=None,
    conversation_memory=None,
    workspace_stores=None,
    bm25_stores=None,
    workspace_directory=None,
):
    """Construit le ToolRegistry pour une session de tâche autonome.

    Tools injectés :
        - Tous les tools standard (search_documents, fetch_document, etc.)
        - run_subtask         → run_workspace_task() cross-workspace
        - update_plan         → écrit current/plan.json
        - report_to_user      → messaging.send() (delivery_type=messaging)
        - create_document     → storage.upload() (delivery_type=document)

    ask_workspace est également injecté si resolver et user_id disponibles.
    """
    from colaig.agents.context_builder import build_background_tool_registry
    return build_background_tool_registry(
        storage=storage,
        task=task,
        session_state=session_state,
        all_workspaces=resolver.workspaces,
        analyser=analyser,
        orchestrator=orchestrator,
        synthesiser=synthesiser,
        retriever=retriever,
        generator=generator,
        messaging=messaging,
        workspace_stores=workspace_stores,
        bm25_stores=bm25_stores,
        conversation_memory=conversation_memory,
        workspace_directory=workspace_directory,
    )
