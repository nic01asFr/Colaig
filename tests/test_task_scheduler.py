"""
Tests — colaig/agents/task_scheduler.py + colaig/agents/tools/task_tools.py

Couvre :
    - _deliver_result() : messaging et document
    - create_task_handler : création d'une tâche depuis le tool
    - create_update_plan_handler : mise à jour plan.json + heartbeat
    - create_report_to_user_handler : envoi messaging
    - create_document_handler : upload storage
    - run_background_session() : cycle de vie complet avec mocks
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.agents.task_scheduler import _check_session_timeout, _deliver_result
from colaig.agents.tasks import (
    TaskDefinition,
    TaskSessionState,
    load_task,
    plan_file_path,
    save_session_state,
    save_task,
    session_file_path,
    task_file_path,
)
from colaig.agents.tools.task_tools import (
    create_document_handler,
    create_report_to_user_handler,
    create_run_subtask_handler,
    create_task_handler,
    create_update_plan_handler,
)
from tests.conftest import MockStorage

# =============================================================================
# Fixtures
# =============================================================================


def _make_task(**kwargs) -> TaskDefinition:
    defaults = dict(
        task_id="task-sched01",
        user_id="@alice:tchap.fr",
        source_conversation_id="!dm_alice:tchap.fr",
        workspace_path="/alice_tchap_fr/",
        name="Test Scheduler",
        query="Que se passe-t-il dans les RH ?",
        schedule_type="interval",
        schedule_value="7d",
        delivery_type="messaging",
        delivery_target="!dm_alice:tchap.fr",
    )
    defaults.update(kwargs)
    return TaskDefinition(**defaults)


def _make_session(task: TaskDefinition) -> TaskSessionState:
    return TaskSessionState(
        task_id=task.task_id,
        conversation_id=f"task_{task.task_id}_2026",
    )


# =============================================================================
# _deliver_result
# =============================================================================


class TestDeliverResult:
    @pytest.mark.asyncio
    async def test_messaging_delivery(self):
        task = _make_task(delivery_type="messaging", delivery_target="!dm_alice:tchap.fr")
        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _deliver_result(task, "Voici le résultat.", messaging=messaging, storage=None)

        messaging.send.assert_called_once()
        call_args = messaging.send.call_args
        assert call_args[0][0] == "!dm_alice:tchap.fr"
        assert "Voici le résultat." in call_args[0][1]
        assert task.name in call_args[0][1]  # En-tête avec nom de tâche

    @pytest.mark.asyncio
    async def test_document_delivery(self):
        task = _make_task(delivery_type="document", delivery_target="/espace-rh/rapport.md")
        storage = MockStorage()

        await _deliver_result(task, "Contenu du rapport.", messaging=None, storage=storage)

        assert "/espace-rh/rapport.md" in storage.files
        content = storage.files["/espace-rh/rapport.md"].decode("utf-8")
        assert "Contenu du rapport." in content

    @pytest.mark.asyncio
    async def test_empty_response_skipped(self):
        task = _make_task(delivery_type="messaging")
        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _deliver_result(task, "", messaging=messaging, storage=None)

        messaging.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_messaging_error_does_not_raise(self):
        task = _make_task(delivery_type="messaging")
        messaging = MagicMock()
        messaging.send = AsyncMock(side_effect=Exception("Connexion perdue"))

        # Ne doit pas propager l'exception
        await _deliver_result(task, "résultat", messaging=messaging, storage=None)


# =============================================================================
# create_task_handler
# =============================================================================


class TestCreateTaskHandler:
    @pytest.mark.asyncio
    async def test_creates_task_file(self):
        storage = MockStorage()
        handler = create_task_handler(
            storage=storage,
            user_id="@alice:tchap.fr",
            workspace_path="/alice_tchap_fr/",
            source_conversation_id="!dm_alice:tchap.fr",
        )

        result_json = await handler(
            name="Veille RH",
            query="Résume les docs RH",
            schedule_type="interval",
            schedule_value="7d",
            delivery_type="messaging",
            delivery_target="!dm_alice:tchap.fr",
        )
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["schedule_type"] == "interval"
        assert result["task_id"].startswith("task-")

        # La tâche doit être persistée
        task = await load_task(storage, "/alice_tchap_fr/", result["task_id"])
        assert task is not None
        assert task.name == "Veille RH"
        assert task.user_id == "@alice:tchap.fr"

    @pytest.mark.asyncio
    async def test_once_task_no_next_run(self):
        storage = MockStorage()
        handler = create_task_handler(
            storage=storage,
            user_id="@alice:tchap.fr",
            workspace_path="/alice_tchap_fr/",
            source_conversation_id="!dm:tchap.fr",
        )

        result_json = await handler(
            name="Rapport unique",
            query="Génère un rapport",
            schedule_type="once",
            schedule_value="",
            delivery_type="messaging",
        )
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_delivery_target_defaults_to_source(self):
        storage = MockStorage()
        handler = create_task_handler(
            storage=storage,
            user_id="@alice:tchap.fr",
            workspace_path="/alice_tchap_fr/",
            source_conversation_id="!source_dm:tchap.fr",
        )

        result_json = await handler(
            name="Test",
            query="Test",
            schedule_type="once",
            schedule_value="",
            delivery_type="messaging",
            delivery_target="",  # Vide → source_conversation_id
        )
        result = json.loads(result_json)
        assert result["delivery_target"] == "!source_dm:tchap.fr"


# =============================================================================
# create_update_plan_handler
# =============================================================================


class TestUpdatePlanHandler:
    @pytest.mark.asyncio
    async def test_writes_plan_json(self):
        storage = MockStorage()
        task = _make_task()
        state = _make_session(task)
        handler = create_update_plan_handler(storage=storage, task=task, session_state=state)

        result_json = await handler(
            status="in_progress",
            steps_done="Recherche terminée",
            steps_remaining="Synthèse en cours",
        )
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["step"] == 1  # Incrémenté depuis 0

        # plan.json doit exister
        path = plan_file_path(task.workspace_path, task.task_id)
        assert path in storage.files
        plan_data = json.loads(storage.files[path].decode("utf-8"))
        assert plan_data["status"] == "in_progress"
        assert plan_data["steps_done"] == "Recherche terminée"

    @pytest.mark.asyncio
    async def test_increments_step_counter(self):
        storage = MockStorage()
        task = _make_task()
        state = _make_session(task)
        handler = create_update_plan_handler(storage=storage, task=task, session_state=state)

        await handler(status="in_progress", steps_done="Étape 1")
        result_json = await handler(status="in_progress", steps_done="Étape 2")
        result = json.loads(result_json)

        assert result["step"] == 2


# =============================================================================
# create_report_to_user_handler
# =============================================================================


class TestReportToUserHandler:
    @pytest.mark.asyncio
    async def test_sends_message(self):
        messaging = MagicMock()
        messaging.send = AsyncMock()
        handler = create_report_to_user_handler(
            messaging=messaging,
            delivery_target="!dm_alice:tchap.fr",
        )

        result_json = await handler(message="Voici le rapport final.")
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["delivered_to"] == "!dm_alice:tchap.fr"
        messaging.send.assert_called_once_with("!dm_alice:tchap.fr", "Voici le rapport final.")

    @pytest.mark.asyncio
    async def test_error_returns_failure(self):
        messaging = MagicMock()
        messaging.send = AsyncMock(side_effect=Exception("Offline"))
        handler = create_report_to_user_handler(
            messaging=messaging,
            delivery_target="!dm:tchap.fr",
        )

        result_json = await handler(message="Test")
        result = json.loads(result_json)

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_messaging_returns_failure(self):
        handler = create_report_to_user_handler(messaging=None, delivery_target="!dm:tchap.fr")
        result_json = await handler(message="Test")
        result = json.loads(result_json)
        assert result["success"] is False


# =============================================================================
# create_document_handler
# =============================================================================


class TestCreateDocumentHandler:
    @pytest.mark.asyncio
    async def test_uploads_content(self):
        storage = MockStorage()
        handler = create_document_handler(storage=storage)

        result_json = await handler(
            content="# Rapport\n\nContenu du rapport.",
            path="/espace-rh/rapport-2026.md",
        )
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["path"] == "/espace-rh/rapport-2026.md"
        assert "/espace-rh/rapport-2026.md" in storage.files
        content = storage.files["/espace-rh/rapport-2026.md"].decode("utf-8")
        assert "Contenu du rapport." in content

    @pytest.mark.asyncio
    async def test_missing_path_returns_failure(self):
        storage = MockStorage()
        handler = create_document_handler(storage=storage)
        result_json = await handler(content="Contenu", path="")
        result = json.loads(result_json)
        assert result["success"] is False


# =============================================================================
# run_background_session — cycle de vie complet (mocks)
# =============================================================================


class TestRunBackgroundSession:
    @pytest.mark.asyncio
    async def test_session_marks_running_then_done(self):
        """La tâche passe RUNNING → archived après une session once."""
        from unittest.mock import patch

        from colaig.agents.task_scheduler import run_background_session
        from colaig.models import GeneratedResponse

        storage = MockStorage()
        task = _make_task(schedule_type="once", schedule_value="")
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        messaging = MagicMock()
        messaging.send = AsyncMock()

        mock_ws = MagicMock()
        mock_ws.workspace_id = "personal-alice"
        mock_ws.storage_path = task.workspace_path
        mock_ws.system_prompt = ""
        mock_ws.user_ids = [task.user_id]
        mock_ws.rag_enabled = False
        mock_ws.storage_readonly = False
        mock_ws.index_path = f"{task.workspace_path}.colaig/indexes/"

        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[])

        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=GeneratedResponse(
            text="Voici le résumé.", sources=["doc.pdf"], confidence=0.9,
        ))

        mock_resolver = MagicMock()
        mock_resolver.workspaces = [mock_ws]

        patch_target = "colaig.context.workspace.get_or_create_personal_workspace"
        with patch(patch_target, AsyncMock(return_value=mock_ws)):
            await run_background_session(
                storage=storage,
                task=task,
                resolver=mock_resolver,
                retriever=mock_retriever,
                generator=mock_generator,
                messaging=messaging,
            )

        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded is not None
        assert reloaded.status == "archived"
        assert reloaded.enabled is False
        assert reloaded.last_run_status == "done"
        messaging.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_recurring_task_gets_next_run_at(self):
        """Tâche interval : status=pending + next_run_at recalculé."""
        from unittest.mock import patch

        from colaig.agents.task_scheduler import run_background_session
        from colaig.models import GeneratedResponse

        storage = MockStorage()
        task = _make_task(schedule_type="interval", schedule_value="1h")
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        mock_ws = MagicMock()
        mock_ws.workspace_id = "personal-alice"
        mock_ws.storage_path = task.workspace_path
        mock_ws.system_prompt = ""
        mock_ws.user_ids = [task.user_id]
        mock_ws.rag_enabled = False
        mock_ws.storage_readonly = False

        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[])

        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=GeneratedResponse(
            text="Résultat", sources=[], confidence=0.8,
        ))

        messaging = MagicMock()
        messaging.send = AsyncMock()

        mock_resolver = MagicMock()
        mock_resolver.workspaces = [mock_ws]

        patch_target = "colaig.context.workspace.get_or_create_personal_workspace"
        with patch(patch_target, AsyncMock(return_value=mock_ws)):
            await run_background_session(
                storage=storage,
                task=task,
                resolver=mock_resolver,
                retriever=mock_retriever,
                generator=mock_generator,
                messaging=messaging,
            )

        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded is not None
        assert reloaded.status == "pending"
        assert reloaded.next_run_at is not None
        next_dt = datetime.fromisoformat(reloaded.next_run_at)
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=UTC)
        assert next_dt > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_failed_session_notifies_user(self):
        """Un pipeline qui lève une exception notifie le user via messaging."""
        from unittest.mock import patch

        from colaig.agents.task_scheduler import run_background_session

        storage = MockStorage()
        task = _make_task(schedule_type="once", schedule_value="", delivery_type="messaging")
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        messaging = MagicMock()
        messaging.send = AsyncMock()

        mock_ws = MagicMock()
        mock_ws.workspace_id = "personal-alice"
        mock_ws.storage_path = task.workspace_path
        mock_ws.system_prompt = ""
        mock_ws.user_ids = [task.user_id]
        mock_ws.rag_enabled = False
        mock_ws.storage_readonly = False

        mock_retriever = MagicMock()
        # generator lève une exception
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(side_effect=RuntimeError("Albert API down"))

        mock_resolver = MagicMock()
        mock_resolver.workspaces = [mock_ws]

        with patch("colaig.context.workspace.get_or_create_personal_workspace", AsyncMock(return_value=mock_ws)):
            await run_background_session(
                storage=storage,
                task=task,
                resolver=mock_resolver,
                retriever=mock_retriever,
                generator=mock_generator,
                messaging=messaging,
            )

        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.last_run_status == "failed"
        assert reloaded.error_count == 1
        # Notification FAILED envoyée
        messaging.send.assert_called_once()
        call_msg = messaging.send.call_args[0][1]
        assert "échec" in call_msg.lower() or "⚠️" in call_msg


# =============================================================================
# _check_session_timeout
# =============================================================================


class TestCheckSessionTimeout:
    @pytest.mark.asyncio
    async def test_active_session_not_timed_out(self):
        """Session avec heartbeat récent → pas de timeout."""
        storage = MockStorage()
        task = _make_task()
        session = _make_session(task)
        # heartbeat = maintenant
        await save_session_state(storage, task, session)
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _check_session_timeout(
            storage=storage,
            task=task,
            messaging=messaging,
            session_timeout=3600,  # 1h — heartbeat est < 1s
        )

        # Aucune action — tâche non modifiée, pas de message
        messaging.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_session_triggers_timeout(self):
        """Session avec heartbeat expiré → tâche FAILED + notification."""
        from datetime import timedelta
        storage = MockStorage()
        task = _make_task(schedule_type="interval", schedule_value="1h")
        # Sauvegarder la tâche avec status=running
        task.status = "running"
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        # Session avec heartbeat vieux de 2h — écriture directe pour bypass save_session_state()
        # (save_session_state écrase last_heartbeat avec _now_iso())
        import json as _json
        old_hb = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        session_data = {
            "task_id": task.task_id,
            "conversation_id": "task_old_conv",
            "status": "running",
            "started_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
            "last_heartbeat": old_hb,
            "current_step": 0,
            "current_step_description": "",
            "subtasks_done": 0,
        }
        await storage.upload(session_file_path(task.workspace_path, task.task_id), _json.dumps(session_data).encode())

        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _check_session_timeout(
            storage=storage,
            task=task,
            messaging=messaging,
            session_timeout=1800,  # 30 min — session est vieille de 2h
        )

        # Tâche remise pending (interval) + notification envoyée
        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.status == "pending"
        assert reloaded.last_run_status == "failed"
        assert reloaded.error_count == 1
        messaging.send.assert_called_once()
        call_msg = messaging.send.call_args[0][1]
        assert "interrompue" in call_msg or "⚠️" in call_msg

    @pytest.mark.asyncio
    async def test_timeout_once_task_becomes_archived(self):
        """Session once expirée → tâche archivée (pas pending)."""
        from datetime import timedelta
        storage = MockStorage()
        task = _make_task(schedule_type="once", schedule_value="")
        task.status = "running"
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )

        import json as _json
        session_data = {
            "task_id": task.task_id,
            "conversation_id": "task_old_once",
            "status": "running",
            "started_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
            "last_heartbeat": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "current_step": 0,
            "current_step_description": "",
            "subtasks_done": 0,
        }
        await storage.upload(session_file_path(task.workspace_path, task.task_id), _json.dumps(session_data).encode())

        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _check_session_timeout(storage=storage, task=task, messaging=messaging, session_timeout=1800)

        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.status == "archived"
        assert reloaded.enabled is False

    @pytest.mark.asyncio
    async def test_no_session_json_resets_task(self):
        """Tâche running sans session.json → reset forcé."""
        storage = MockStorage()
        task = _make_task(schedule_type="interval", schedule_value="1h")
        task.status = "running"
        await storage.upload(
            task_file_path(task.workspace_path, task.task_id),
            json.dumps(asdict(task)).encode(),
        )
        # Pas de session.json uploadé

        messaging = MagicMock()
        messaging.send = AsyncMock()

        await _check_session_timeout(storage=storage, task=task, messaging=messaging, session_timeout=1800)

        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.status == "pending"


# =============================================================================
# max_subtasks guard
# =============================================================================


class TestMaxSubtasksGuard:
    @pytest.mark.asyncio
    async def test_max_subtasks_blocks_call(self):
        """run_subtask refusé si subtasks_done >= max_subtasks."""
        task = _make_task()
        task.max_subtasks = 2
        session = _make_session(task)
        session.subtasks_done = 2  # Limite atteinte

        handler = create_run_subtask_handler(
            user_id="@alice:tchap.fr",
            all_workspaces=[],
            task=task,
            session_state=session,
        )

        result_json = await handler(workspace_id="espace-rh", subtask_query="Résume")
        result = json.loads(result_json)

        assert result["success"] is False
        assert "Limite" in result["error"] or "max" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_max_subtasks_allows_within_limit(self):
        """run_subtask autorisé si subtasks_done < max_subtasks (workspace introuvable mais guard passé)."""
        task = _make_task()
        task.max_subtasks = 5
        session = _make_session(task)
        session.subtasks_done = 3  # Encore 2 disponibles

        handler = create_run_subtask_handler(
            user_id="@alice:tchap.fr",
            all_workspaces=[],  # Workspace introuvable → WorkspaceNotFound
            task=task,
            session_state=session,
        )

        result_json = await handler(workspace_id="inexistant", subtask_query="Test")
        result = json.loads(result_json)

        # Échec sur WorkspaceNotFound (pas sur le guard)
        assert result["success"] is False
        assert "Limite" not in result.get("error", "")


# =============================================================================
# pause_and_ask_user
# =============================================================================


class TestPauseAndAskUser:
    @pytest.mark.asyncio
    async def test_pause_sends_question_and_sets_waiting(self):
        """pause_and_ask_user envoie la question et passe task + session en waiting_for_user."""
        from colaig.agents.tasks import load_session_state
        from colaig.agents.tools.task_tools import create_pause_handler

        storage = MockStorage()
        task = _make_task(delivery_type="messaging", delivery_target="!dm_alice:tchap.fr")
        await save_task(storage, task)

        session = _make_session(task)
        await save_session_state(storage, task, session)

        messaging = MagicMock()
        messaging.send = AsyncMock()

        handler = create_pause_handler(
            storage=storage,
            task=task,
            session_state=session,
            messaging=messaging,
        )

        result_json = await handler(question="Quelle période souhaitez-vous couvrir ?")
        result = json.loads(result_json)

        assert result["success"] is True
        assert result["status"] == "waiting_for_user"
        messaging.send.assert_called_once()
        call_msg = messaging.send.call_args[0][1]
        assert "Quelle période" in call_msg

        # task.status mis à jour
        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.status == "waiting_for_user"

        # session.status mis à jour
        reloaded_session = await load_session_state(storage, task)
        assert reloaded_session.status == "waiting_for_user"

    @pytest.mark.asyncio
    async def test_pause_no_messaging_returns_failure(self):
        """pause_and_ask_user sans messaging retourne un échec."""
        from colaig.agents.tools.task_tools import create_pause_handler

        storage = MockStorage()
        task = _make_task()
        session = _make_session(task)

        handler = create_pause_handler(
            storage=storage,
            task=task,
            session_state=session,
            messaging=None,
        )

        result_json = await handler(question="Test ?")
        result = json.loads(result_json)
        assert result["success"] is False


# =============================================================================
# _handle_waiting_task_reply (handlers.py)
# =============================================================================


class TestHandleWaitingTaskReply:
    # patch importé localement dans les tests
    @pytest.mark.asyncio
    async def test_dm_reply_injects_into_waiting_task(self):
        """Un DM reçu pendant waiting_for_user est injecté dans session.pending_user_reply."""
        from unittest.mock import patch

        from colaig.agents.tasks import load_session_state, load_task
        from colaig.messaging.handlers import MessageHandler
        from colaig.models import ContextMode, ConversationType, IncomingMessage

        storage = MockStorage()
        task = _make_task(schedule_type="once", schedule_value="")
        task.status = "waiting_for_user"
        task.delivery_target = "!dm_alice:tchap.fr"
        await save_task(storage, task)

        # Session en waiting_for_user
        session = _make_session(task)
        session.status = "waiting_for_user"
        await save_session_state(storage, task, session)

        # Mock du personal workspace resolver
        mock_personal_ws = MagicMock()
        mock_personal_ws.storage_path = task.workspace_path
        mock_personal_ws.workspace_id = "personal-alice"
        mock_personal_ws.user_ids = [task.user_id]
        mock_personal_ws.rag_enabled = False
        mock_personal_ws.storage_readonly = False
        mock_personal_ws.system_prompt = ""

        messaging = MagicMock()
        messaging.send = AsyncMock()

        mock_resolver = MagicMock()
        mock_context = MagicMock()
        mock_context.mode = ContextMode.PERSONAL
        mock_resolver.resolve = AsyncMock(return_value=mock_context)

        mock_retriever = MagicMock()
        mock_generator = MagicMock()

        handler = MessageHandler(
            messaging=messaging,
            resolver=mock_resolver,
            retriever=mock_retriever,
            generator=mock_generator,
            storage=storage,
        )

        dm_message = IncomingMessage(
            user_id=task.user_id,
            conversation_id="!dm_alice:tchap.fr",
            body="La période visée est mars 2026.",
            conversation_type=ConversationType.DM,
        )

        with patch("colaig.context.workspace.get_or_create_personal_workspace",
                   AsyncMock(return_value=mock_personal_ws)):
            handled = await handler._handle_waiting_task_reply(dm_message)

        assert handled is True

        # task remise en pending
        reloaded_task = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded_task.status == "pending"

        # session.pending_user_reply rempli
        reloaded_session = await load_session_state(storage, task)
        assert reloaded_session.pending_user_reply == "La période visée est mars 2026."

        # Accusé de réception envoyé
        messaging.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_waiting_task_returns_false(self):
        """Sans tâche waiting_for_user, la méthode retourne False."""
        from unittest.mock import patch

        from colaig.messaging.handlers import MessageHandler
        from colaig.models import ConversationType, IncomingMessage

        storage = MockStorage()
        mock_personal_ws = MagicMock()
        mock_personal_ws.storage_path = "/alice_tchap_fr/"

        handler = MessageHandler(
            messaging=MagicMock(),
            resolver=MagicMock(),
            retriever=MagicMock(),
            generator=MagicMock(),
            storage=storage,
        )

        dm_message = IncomingMessage(
            user_id="@alice:tchap.fr",
            conversation_id="!dm:tchap.fr",
            body="Bonjour",
            conversation_type=ConversationType.DM,
        )

        with patch("colaig.context.workspace.get_or_create_personal_workspace",
                   AsyncMock(return_value=mock_personal_ws)):
            handled = await handler._handle_waiting_task_reply(dm_message)

        assert handled is False


# =============================================================================
# run_background_session — resume depuis session.json
# =============================================================================


class TestRunBackgroundSessionResume:
    @pytest.mark.asyncio
    async def test_resume_uses_existing_conversation_id(self):
        """Reprise avec pending_user_reply → réutilise conversation_id existant."""
        from unittest.mock import patch

        from colaig.agents.task_scheduler import run_background_session
        from colaig.models import GeneratedResponse

        storage = MockStorage()
        task = _make_task(schedule_type="once", schedule_value="")
        await save_task(storage, task)

        # Session en attente avec réponse utilisateur
        import json as _json
        session_data = {
            "task_id": task.task_id,
            "conversation_id": "task_resume_existing_conv",
            "status": "waiting_for_user",
            "started_at": "2026-01-01T00:00:00+00:00",
            "last_heartbeat": "2026-01-01T00:01:00+00:00",
            "current_step": 2,
            "current_step_description": "Recherche terminée",
            "subtasks_done": 1,
            "pending_user_reply": "La période visée est mars 2026.",
        }
        await storage.upload(
            session_file_path(task.workspace_path, task.task_id),
            _json.dumps(session_data).encode(),
        )

        messaging = MagicMock()
        messaging.send = AsyncMock()

        mock_ws = MagicMock()
        mock_ws.workspace_id = "personal-alice"
        mock_ws.storage_path = task.workspace_path
        mock_ws.system_prompt = ""
        mock_ws.user_ids = [task.user_id]
        mock_ws.rag_enabled = False
        mock_ws.storage_readonly = False

        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[])

        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=GeneratedResponse(
            text="Voici le résumé pour mars 2026.", sources=[], confidence=0.9,
        ))

        mock_resolver = MagicMock()
        mock_resolver.workspaces = [mock_ws]

        with patch("colaig.context.workspace.get_or_create_personal_workspace", AsyncMock(return_value=mock_ws)):
            await run_background_session(
                storage=storage,
                task=task,
                resolver=mock_resolver,
                retriever=mock_retriever,
                generator=mock_generator,
                messaging=messaging,
            )

        # La tâche once est archivée
        reloaded = await load_task(storage, task.workspace_path, task.task_id)
        assert reloaded.status == "archived"
        # Réponse envoyée à l'utilisateur
        messaging.send.assert_called_once()
