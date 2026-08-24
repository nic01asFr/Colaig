"""
Tests — colaig/agents/tasks.py

Couvre :
    - TaskDefinition dataclass (création, sérialisation)
    - compute_next_run() pour "once", "interval", "cron"
    - _parse_interval() pour les durées
    - is_due() selon status et next_run_at
    - generate_task_id()
    - Chemins de fichiers storage (task_file_path, etc.)
    - File I/O : load_task, save_task, list_tasks
    - save_session_state, save_plan, save_subtask_result, archive_run
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from colaig.agents.tasks import (
    TaskDefinition,
    TaskRunSummary,
    TaskSessionState,
    _parse_interval,
    archive_run,
    compute_next_run,
    generate_task_id,
    is_due,
    list_tasks,
    load_task,
    plan_file_path,
    save_plan,
    save_session_state,
    save_subtask_result,
    save_task,
    session_file_path,
    subtask_file_path,
    task_file_path,
)
from tests.conftest import MockStorage

# =============================================================================
# Fixtures
# =============================================================================


def _make_task(**kwargs) -> TaskDefinition:
    defaults = dict(
        task_id="task-test01",
        user_id="@alice:tchap.fr",
        source_conversation_id="!dm_alice:tchap.fr",
        workspace_path="/alice_tchap_fr/",
        name="Veille hebdomadaire",
        query="Résume les derniers documents RH",
        schedule_type="interval",
        schedule_value="7d",
        delivery_type="messaging",
        delivery_target="!dm_alice:tchap.fr",
    )
    defaults.update(kwargs)
    return TaskDefinition(**defaults)


# =============================================================================
# TaskDefinition — création et sérialisation
# =============================================================================


class TestTaskDefinition:
    def test_default_values(self):
        task = _make_task()
        assert task.status == "pending"
        assert task.enabled is True
        assert task.error_count == 0
        assert task.max_steps == 10
        assert task.next_run_at is None

    def test_asdict_round_trip(self):
        task = _make_task()
        d = asdict(task)
        assert d["task_id"] == "task-test01"
        assert d["user_id"] == "@alice:tchap.fr"
        assert d["schedule_type"] == "interval"

    def test_created_at_is_set(self):
        task = _make_task()
        assert task.created_at is not None
        # Doit être parseable
        dt = datetime.fromisoformat(task.created_at)
        assert dt.year >= 2025


# =============================================================================
# generate_task_id
# =============================================================================


class TestGenerateTaskId:
    def test_format(self):
        tid = generate_task_id()
        assert tid.startswith("task-")
        assert len(tid) == 13  # "task-" + 8 hex chars

    def test_unique(self):
        ids = {generate_task_id() for _ in range(100)}
        assert len(ids) == 100


# =============================================================================
# _parse_interval
# =============================================================================


class TestParseInterval:
    def test_days(self):
        assert _parse_interval("7d") == timedelta(days=7)

    def test_hours(self):
        assert _parse_interval("24h") == timedelta(hours=24)

    def test_minutes(self):
        assert _parse_interval("30m") == timedelta(minutes=30)

    def test_seconds(self):
        assert _parse_interval("3600s") == timedelta(seconds=3600)

    def test_invalid(self):
        assert _parse_interval("invalid") is None
        assert _parse_interval("") is None
        assert _parse_interval("7") is None

    def test_case_insensitive(self):
        assert _parse_interval("7D") == timedelta(days=7)


# =============================================================================
# compute_next_run
# =============================================================================


class TestComputeNextRun:
    def test_once_returns_none(self):
        result = compute_next_run("once", "")
        assert result is None

    def test_interval_7d(self):
        now = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        result = compute_next_run("interval", "7d", from_dt=now)
        expected = datetime(2026, 3, 8, 10, 0, 0, tzinfo=UTC)
        assert result is not None
        parsed = datetime.fromisoformat(result)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        assert parsed == expected

    def test_interval_24h(self):
        now = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)
        result = compute_next_run("interval", "24h", from_dt=now)
        assert result is not None
        parsed = datetime.fromisoformat(result)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        assert parsed == now + timedelta(hours=24)

    def test_interval_invalid(self):
        result = compute_next_run("interval", "invalid", from_dt=datetime.now(UTC))
        assert result is None

    def test_cron_invalid(self):
        # croniter peut ne pas être installé en test — on s'attend à None ou à une valeur
        result = compute_next_run("cron", "not-a-cron", from_dt=datetime.now(UTC))
        assert result is None

    def test_unknown_type(self):
        result = compute_next_run("daily", "something")
        assert result is None


# =============================================================================
# is_due
# =============================================================================


class TestIsDue:
    def test_pending_no_next_run(self):
        task = _make_task(status="pending", next_run_at=None)
        assert is_due(task) is True

    def test_pending_past_next_run(self):
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        task = _make_task(status="pending", next_run_at=past)
        assert is_due(task) is True

    def test_pending_future_next_run(self):
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        task = _make_task(status="pending", next_run_at=future)
        assert is_due(task) is False

    def test_running_not_due(self):
        task = _make_task(status="running")
        assert is_due(task) is False

    def test_disabled_not_due(self):
        task = _make_task(enabled=False, status="pending")
        assert is_due(task) is False

    def test_archived_not_due(self):
        task = _make_task(status="archived")
        assert is_due(task) is False


# =============================================================================
# Chemins de fichiers
# =============================================================================


class TestFilePaths:
    def test_task_file_path(self):
        path = task_file_path("/alice/", "task-abc123")
        assert path == "/alice/.colaig/tasks/task-abc123.json"

    def test_session_file_path(self):
        path = session_file_path("/alice/", "task-abc123")
        assert path == "/alice/.colaig/tasks/task-abc123/current/session.json"

    def test_plan_file_path(self):
        path = plan_file_path("/alice/", "task-abc123")
        assert path == "/alice/.colaig/tasks/task-abc123/current/plan.json"

    def test_subtask_file_path(self):
        path = subtask_file_path("/alice/", "task-abc123", "sub-001")
        assert path == "/alice/.colaig/tasks/task-abc123/current/subtasks/sub-001.json"

    def test_trailing_slash_normalized(self):
        # Sans slash final dans workspace_path
        path = task_file_path("/alice", "task-abc123")
        assert path == "/alice/.colaig/tasks/task-abc123.json"


# =============================================================================
# File I/O
# =============================================================================


class TestFileIO:
    @pytest.mark.asyncio
    async def test_save_and_load_task(self):
        storage = MockStorage()
        task = _make_task()
        await save_task(storage, task)

        loaded = await load_task(storage, task.workspace_path, task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id
        assert loaded.name == task.name
        assert loaded.user_id == task.user_id

    @pytest.mark.asyncio
    async def test_load_task_not_found(self):
        storage = MockStorage()
        result = await load_task(storage, "/nope/", "task-unknown")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_task_corrupted(self):
        storage = MockStorage()
        path = task_file_path("/alice/", "task-bad")
        storage.add_file(path, b"not json at all", "application/json")
        result = await load_task(storage, "/alice/", "task-bad")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self):
        storage = MockStorage()
        tasks = await list_tasks(storage, "/alice/")
        assert tasks == []

    @pytest.mark.asyncio
    async def test_list_tasks_multiple(self):
        storage = MockStorage()
        for i in range(3):
            task = _make_task(task_id=f"task-{i:04d}", name=f"Tâche {i}")
            await save_task(storage, task)

        tasks = await list_tasks(storage, "/alice_tchap_fr/")
        assert len(tasks) == 3
        names = {t.name for t in tasks}
        assert names == {"Tâche 0", "Tâche 1", "Tâche 2"}

    @pytest.mark.asyncio
    async def test_save_updates_task(self):
        storage = MockStorage()
        task = _make_task()
        await save_task(storage, task)

        task.status = "running"
        task.error_count = 1
        await save_task(storage, task)

        loaded = await load_task(storage, task.workspace_path, task.task_id)
        assert loaded.status == "running"
        assert loaded.error_count == 1


# =============================================================================
# Session state + Plan + Subtask
# =============================================================================


class TestSessionAndPlan:
    @pytest.mark.asyncio
    async def test_save_session_state(self):
        storage = MockStorage()
        task = _make_task()
        state = TaskSessionState(
            task_id=task.task_id,
            conversation_id="task_test01_2026",
            status="running",
            started_at="2026-03-12T08:00:00+00:00",
        )
        await save_session_state(storage, task, state)

        path = session_file_path(task.workspace_path, task.task_id)
        assert path in storage.files
        data = json.loads(storage.files[path].decode("utf-8"))
        assert data["task_id"] == task.task_id
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_save_plan(self):
        storage = MockStorage()
        task = _make_task()
        plan = {"status": "in_progress", "steps_done": "Recherche terminée"}
        await save_plan(storage, task, plan)

        path = plan_file_path(task.workspace_path, task.task_id)
        assert path in storage.files
        data = json.loads(storage.files[path].decode("utf-8"))
        assert data["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_save_subtask_result(self):
        storage = MockStorage()
        task = _make_task()
        result = {"success": True, "answer": "42", "sources": ["doc.pdf"]}
        await save_subtask_result(storage, task, "sub-001", result)

        path = subtask_file_path(task.workspace_path, task.task_id, "sub-001")
        assert path in storage.files
        data = json.loads(storage.files[path].decode("utf-8"))
        assert data["success"] is True


# =============================================================================
# archive_run
# =============================================================================


class TestArchiveRun:
    @pytest.mark.asyncio
    async def test_archive_writes_summary(self):
        storage = MockStorage()
        task = _make_task()
        run_ts = "2026-03-12T08:00:00+00:00"

        summary = TaskRunSummary(
            task_id=task.task_id,
            task_name=task.name,
            run_id="2026-03-12T08-00-00",
            user_id=task.user_id,
            started_at=run_ts,
            completed_at="2026-03-12T08:05:00+00:00",
            status="done",
            steps_executed=3,
            subtasks_executed=1,
            answer_preview="Voici le résumé...",
        )

        await archive_run(storage, task, run_ts, summary)

        # summary.json doit exister dans runs/
        found = any("summary.json" in k for k in storage.files)
        assert found

    @pytest.mark.asyncio
    async def test_archive_writes_plan_if_provided(self):
        storage = MockStorage()
        task = _make_task()
        run_ts = "2026-03-12T09:00:00+00:00"
        plan_data = {"status": "done", "steps_done": "Tout fait"}

        summary = TaskRunSummary(
            task_id=task.task_id,
            task_name=task.name,
            run_id="x",
            user_id=task.user_id,
            started_at=run_ts,
            completed_at=run_ts,
            status="done",
        )
        await archive_run(storage, task, run_ts, summary, plan_data=plan_data)

        found_plan = any("plan.json" in k for k in storage.files)
        assert found_plan

    @pytest.mark.asyncio
    async def test_archive_cleans_current(self):
        storage = MockStorage()
        task = _make_task()
        run_ts = "2026-03-12T10:00:00+00:00"

        # Pré-peupler current/
        session_path = session_file_path(task.workspace_path, task.task_id)
        plan_path_val = plan_file_path(task.workspace_path, task.task_id)
        storage.add_file(session_path, b"{}", "application/json")
        storage.add_file(plan_path_val, b"{}", "application/json")

        summary = TaskRunSummary(
            task_id=task.task_id,
            task_name=task.name,
            run_id="x",
            user_id=task.user_id,
            started_at=run_ts,
            completed_at=run_ts,
            status="done",
        )
        await archive_run(storage, task, run_ts, summary)

        # current/session.json et current/plan.json doivent être supprimés
        assert session_path not in storage.files
        assert plan_path_val not in storage.files
