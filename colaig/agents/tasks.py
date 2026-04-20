"""
Colaig — Mode C : Tâches autonomes planifiées

TaskDefinition représente une tâche planifiée créée par un utilisateur
depuis son espace DM (ou via un tool MCP authentifié). La tâche est
persistée dans le workspace personnel du user :

    /{safe_slug}/.colaig/tasks/{task_id}.json

Chaque exécution génère :
    - Une session d'orchestration (conversation_id = task_{task_id}_{ts})
      stockée dans {workspace_path}/.colaig/conversations/
    - Un répertoire current/ pendant l'exécution (heartbeat + plan live)
    - Un répertoire runs/{ts}/ après archivage (audit immuable)

Cycle de vie par schedule_type :
    once     : pending → running → [archive] → done → archived
    cron     : pending → running → [archive] → pending (next_run_at recalculé)
    interval : idem cron

Principe Zero-DB : tout passe par StorageProtocol — aucune base de données.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from colaig.exceptions import StorageFileNotFoundError

logger = logging.getLogger(__name__)


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class TaskDefinition:
    """Tâche autonome planifiée créée par un utilisateur.

    Persistée dans {workspace_path}/.colaig/tasks/{task_id}.json.
    """
    task_id: str
    user_id: str                        # Résolu depuis token au moment de la création
    source_conversation_id: str         # DM/session depuis lequel la tâche a été créée
    workspace_path: str                 # Workspace personnel du user (/{safe_slug}/)
    name: str                           # Nom lisible de la tâche
    query: str                          # Instruction à exécuter

    # Planification
    schedule_type: str                  # "once" | "cron" | "interval"
    schedule_value: str                 # "0 8 * * 1" | "7d" | "24h" | "30m"

    # Livraison
    delivery_type: str                  # "messaging" | "document"
    delivery_target: str                # conversation_id (messaging) | path (document)

    # Cycle de vie
    enabled: bool = True
    status: str = "pending"             # "pending" | "running" | "waiting_for_user" | "done" | "failed" | "archived"
    next_run_at: Optional[str] = None   # ISO8601 UTC — prochaine exécution planifiée
    last_run_at: Optional[str] = None   # ISO8601 UTC — dernière exécution
    last_run_status: Optional[str] = None  # "done" | "failed"
    error_count: int = 0                # Erreurs consécutives (reset sur succès)

    # Contraintes d'exécution
    max_steps: int = 10                 # Max itérations de la boucle orchestrateur
    max_subtasks: int = 5               # Max run_subtask calls par session

    # Métadonnées
    created_at: str = field(default_factory=lambda: _now_iso())
    workspace_ids_allowed: list[str] = field(default_factory=list)
    # Vide = tous les workspaces accessibles à user_id


@dataclass
class TaskSessionState:
    """État d'une session d'orchestration de tâche.

    Fichier : {workspace_path}/.colaig/tasks/{task_id}/current/session.json
    Écrit au démarrage, mis à jour (heartbeat) à chaque étape.
    Supprimé lors de l'archivage.
    """
    task_id: str
    conversation_id: str                # task_{task_id}_{ts_safe}
    status: str = "running"             # "running" | "completing" | "failed" | "waiting_for_user"
    started_at: str = field(default_factory=lambda: _now_iso())
    last_heartbeat: str = field(default_factory=lambda: _now_iso())
    current_step: int = 0
    current_step_description: str = ""
    subtasks_done: int = 0
    pending_user_reply: str = ""        # Réponse DM reçue pendant waiting_for_user (injectée par handlers.py)


@dataclass
class TaskRunSummary:
    """Résumé immuable d'une exécution archivée.

    Fichier : {workspace_path}/.colaig/tasks/{task_id}/runs/{ts}/summary.json
    """
    task_id: str
    task_name: str
    run_id: str                         # Timestamp ISO8601 safe (tirets)
    user_id: str
    started_at: str
    completed_at: str
    status: str                         # "done" | "failed"
    steps_executed: int = 0
    subtasks_executed: int = 0
    answer_preview: str = ""            # Premiers 200 chars de la réponse finale
    sources: list[str] = field(default_factory=list)
    error_message: str = ""


# =============================================================================
# Helpers temporels
# =============================================================================


def _now_iso() -> str:
    """ISO8601 UTC du moment présent."""
    return datetime.now(timezone.utc).isoformat()


def _safe_ts(iso: str) -> str:
    """Rend un ISO8601 safe comme nom de répertoire (remplace ':' et '+')."""
    return iso.replace(":", "-").replace("+", "").split(".")[0]


def compute_next_run(
    schedule_type: str,
    schedule_value: str,
    from_dt: Optional[datetime] = None,
) -> Optional[str]:
    """Calcule la date/heure ISO8601 UTC de la prochaine exécution.

    Args:
        schedule_type : "once" | "cron" | "interval"
        schedule_value: Expression cron ("0 8 * * 1") ou durée ("7d", "24h", "30m")
        from_dt       : Point de départ (défaut : maintenant UTC)

    Returns:
        ISO8601 UTC string, ou None pour "once" (exécution unique, pas de récurrence).
    """
    if from_dt is None:
        from_dt = datetime.now(timezone.utc)

    if schedule_type == "once":
        return None  # Exécution unique — pas de prochaine date

    if schedule_type == "interval":
        delta = _parse_interval(schedule_value)
        if delta:
            return (from_dt + delta).isoformat()
        logger.warning("compute_next_run: durée invalide %r", schedule_value)
        return None

    if schedule_type == "cron":
        try:
            from croniter import croniter  # type: ignore[import]
            cron = croniter(schedule_value, from_dt)
            next_dt = cron.get_next(datetime)
            # croniter peut retourner un naive datetime — forcer UTC
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            return next_dt.isoformat()
        except ImportError:
            logger.warning("compute_next_run: croniter non installé — impossible de planifier cron")
            return None
        except Exception as exc:
            logger.warning("compute_next_run: expression cron invalide %r: %s", schedule_value, exc)
            return None

    logger.warning("compute_next_run: schedule_type inconnu %r", schedule_type)
    return None


def _parse_interval(value: str) -> Optional[timedelta]:
    """Parse une durée courte : '7d', '24h', '30m', '3600s'.

    Returns:
        timedelta correspondant, ou None si format invalide.
    """
    match = re.fullmatch(r"(\d+)(d|h|m|s)", value.strip().lower())
    if not match:
        return None
    n, unit = int(match.group(1)), match.group(2)
    return {"d": timedelta(days=n), "h": timedelta(hours=n),
            "m": timedelta(minutes=n), "s": timedelta(seconds=n)}[unit]


def generate_task_id() -> str:
    """Génère un task_id court unique."""
    return "task-" + secrets.token_hex(4)


def is_due(task: TaskDefinition) -> bool:
    """Retourne True si la tâche doit être exécutée maintenant.

    Conditions : enabled=True, status=pending, next_run_at <= now (ou None).
    Les statuts "running" et "waiting_for_user" sont gérés séparément par le scheduler.
    """
    if not task.enabled or task.status != "pending":
        return False
    if task.next_run_at is None:
        return True  # Pas encore planifiée → exécuter immédiatement
    try:
        next_run = datetime.fromisoformat(task.next_run_at)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= next_run
    except ValueError:
        return False


# =============================================================================
# Chemins dans le storage
# =============================================================================


def task_file_path(workspace_path: str, task_id: str) -> str:
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}.json"


def session_file_path(workspace_path: str, task_id: str) -> str:
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}/current/session.json"


def plan_file_path(workspace_path: str, task_id: str) -> str:
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}/current/plan.json"


def subtask_file_path(workspace_path: str, task_id: str, subtask_id: str) -> str:
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}/current/subtasks/{subtask_id}.json"


def run_summary_path(workspace_path: str, task_id: str, run_ts: str) -> str:
    safe = _safe_ts(run_ts)
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}/runs/{safe}/summary.json"


def run_plan_path(workspace_path: str, task_id: str, run_ts: str) -> str:
    safe = _safe_ts(run_ts)
    return f"{workspace_path.rstrip('/')}/.colaig/tasks/{task_id}/runs/{safe}/plan.json"


# =============================================================================
# File I/O via StorageProtocol
# =============================================================================


async def load_task(storage, workspace_path: str, task_id: str) -> Optional[TaskDefinition]:
    """Charge un TaskDefinition depuis le storage.

    Returns:
        TaskDefinition ou None si introuvable/corrompu.
    """
    path = task_file_path(workspace_path, task_id)
    try:
        raw = await storage.download(path)
        data = json.loads(raw.decode("utf-8"))
        return _dict_to_task(data)
    except (FileNotFoundError, StorageFileNotFoundError):
        return None
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("load_task: fichier corrompu %s: %s", path, exc)
        return None


async def save_task(storage, task: TaskDefinition) -> None:
    """Persiste un TaskDefinition dans le storage."""
    path = task_file_path(task.workspace_path, task.task_id)
    content = json.dumps(asdict(task), ensure_ascii=False, indent=2).encode("utf-8")
    await storage.upload(path, content)


async def list_tasks(storage, workspace_path: str) -> list[TaskDefinition]:
    """Liste toutes les tâches persistées dans le workspace personnel d'un user.

    Returns:
        Liste de TaskDefinition triée par created_at (plus récent en premier).
    """
    tasks_dir = f"{workspace_path.rstrip('/')}/.colaig/tasks/"
    tasks = []
    try:
        files = await storage.list_files(tasks_dir, recursive=False)
        for f in files:
            if f.is_directory or not f.name.endswith(".json"):
                continue
            task_id = f.name.removesuffix(".json")
            task = await load_task(storage, workspace_path, task_id)
            if task:
                tasks.append(task)
    except Exception:
        pass  # Pas encore de dossier tasks = aucune tâche, c'est normal
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks


async def save_session_state(storage, task: TaskDefinition, state: TaskSessionState) -> None:
    """Écrit/met à jour current/session.json (heartbeat).

    Non-bloquant sur erreur — le scheduler continue même si le heartbeat échoue.
    """
    state.last_heartbeat = _now_iso()
    path = session_file_path(task.workspace_path, task.task_id)
    content = json.dumps(asdict(state), ensure_ascii=False, indent=2).encode("utf-8")
    try:
        await storage.upload(path, content)
    except Exception as exc:
        logger.warning("save_session_state: erreur heartbeat %s: %s", path, exc)


async def save_plan(storage, task: TaskDefinition, plan_data: dict) -> None:
    """Écrit/met à jour current/plan.json (plan dynamique de l'orchestrateur)."""
    path = plan_file_path(task.workspace_path, task.task_id)
    content = json.dumps(plan_data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        await storage.upload(path, content)
    except Exception as exc:
        logger.warning("save_plan: erreur écriture plan %s: %s", path, exc)


async def save_subtask_result(
    storage,
    task: TaskDefinition,
    subtask_id: str,
    result_data: dict,
) -> None:
    """Écrit le résultat d'un sous-agent dans current/subtasks/{id}.json."""
    path = subtask_file_path(task.workspace_path, task.task_id, subtask_id)
    content = json.dumps(result_data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        await storage.upload(path, content)
    except Exception as exc:
        logger.warning("save_subtask_result: erreur %s: %s", path, exc)


async def load_session_state(storage, task: TaskDefinition) -> Optional[TaskSessionState]:
    """Charge current/session.json si présent.

    Returns:
        TaskSessionState ou None si absent/corrompu.
    """
    path = session_file_path(task.workspace_path, task.task_id)
    try:
        raw = await storage.download(path)
        data = json.loads(raw.decode("utf-8"))
        known = set(TaskSessionState.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return TaskSessionState(**filtered)
    except (FileNotFoundError, StorageFileNotFoundError):
        return None
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("load_session_state: fichier corrompu %s: %s", path, exc)
        return None


async def load_plan(storage, task: TaskDefinition) -> Optional[dict]:
    """Charge current/plan.json si présent."""
    path = plan_file_path(task.workspace_path, task.task_id)
    try:
        raw = await storage.download(path)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


async def archive_run(
    storage,
    task: TaskDefinition,
    run_ts: str,
    summary: TaskRunSummary,
    plan_data: Optional[dict] = None,
) -> None:
    """Archive current/ → runs/{ts}/.

    Écrit summary.json et plan.json (si fourni) dans le répertoire d'archive,
    puis supprime les fichiers current/ (session.json, plan.json).
    Idempotent — les erreurs de suppression sont ignorées.
    """
    # Écrire summary.json
    try:
        path = run_summary_path(task.workspace_path, task.task_id, run_ts)
        content = json.dumps(asdict(summary), ensure_ascii=False, indent=2).encode("utf-8")
        await storage.upload(path, content)
    except Exception as exc:
        logger.warning("archive_run: erreur écriture summary: %s", exc)

    # Archiver plan.json si fourni
    if plan_data:
        try:
            path = run_plan_path(task.workspace_path, task.task_id, run_ts)
            content = json.dumps(plan_data, ensure_ascii=False, indent=2).encode("utf-8")
            await storage.upload(path, content)
        except Exception as exc:
            logger.warning("archive_run: erreur archivage plan: %s", exc)

    # Nettoyer current/
    for cleanup_fn in (session_file_path, plan_file_path):
        path = cleanup_fn(task.workspace_path, task.task_id)
        try:
            await storage.delete(path)
        except Exception:
            pass  # Déjà absent ou non supporté = ok


# =============================================================================
# Helpers internes
# =============================================================================


def _dict_to_task(data: dict) -> TaskDefinition:
    """Reconstruit un TaskDefinition depuis un dict JSON (forward-compatible)."""
    known = set(TaskDefinition.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in known}
    return TaskDefinition(**filtered)
