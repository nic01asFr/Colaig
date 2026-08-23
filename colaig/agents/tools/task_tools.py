"""
agents/tools/task_tools.py — Outils background pour l'orchestrateur Mode C.

Ces outils sont injectés dans le ToolRegistry lorsque l'orchestrateur tourne
en session de tâche autonome (Mode C). Ils permettent à l'orchestrateur de :

    create_background_task — Créer une tâche planifiée (injecté en mode PERSONAL)
    run_subtask            — Déléguer à un sous-agent pipeline complet (run_workspace_task)
    update_plan            — Mettre à jour le plan dynamique (current/plan.json)
    report_to_user         — Notifier l'utilisateur via messaging (delivery_type=messaging)
    create_document        — Livrer un document dans un workspace (delivery_type=document)

Pattern : même architecture que delegate_tools.py (ask_workspace).
Chaque handler est une closure sur les dépendances nécessaires.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)


# =============================================================================
# Définitions des outils
# =============================================================================

CREATE_BACKGROUND_TASK_DEFINITION = ToolDefinition(
    name="create_background_task",
    description=(
        "Crée une tâche autonome planifiée qui s'exécutera en arrière-plan. "
        "La tâche sera pilotée par Colaig selon la planification définie et le "
        "résultat livré selon delivery_type. "
        "Disponible uniquement en mode DM (workspace personnel)."
    ),
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description="Nom lisible de la tâche (ex: 'Veille hebdomadaire RH').",
            required=True,
        ),
        ToolParameter(
            name="query",
            type="string",
            description="Instruction complète décrivant ce que doit faire la tâche.",
            required=True,
        ),
        ToolParameter(
            name="schedule_type",
            type="string",
            description="Type de planification : 'once' (unique), 'interval' (récurrent), 'cron' (expression cron).",
            required=True,
        ),
        ToolParameter(
            name="schedule_value",
            type="string",
            description=(
                "Valeur de planification selon schedule_type. "
                "'once' → vide ou 'now'. "
                "'interval' → durée : '7d', '24h', '30m', '3600s'. "
                "'cron' → expression cron POSIX : '0 8 * * 1' (lundi 8h)."
            ),
            required=True,
        ),
        ToolParameter(
            name="delivery_type",
            type="string",
            description=(
                "'messaging' → résultat envoyé dans la conversation DM. "
                "'document' → résultat sauvegardé comme fichier dans un workspace."
            ),
            required=True,
        ),
        ToolParameter(
            name="delivery_target",
            type="string",
            description=(
                "Pour 'messaging' : conversation_id du salon destinataire (vide = conversation actuelle). "
                "Pour 'document' : chemin du fichier à créer (ex: /espace-rh/rapports/veille.md)."
            ),
            required=False,
        ),
    ],
    category="task",
)

RUN_SUBTASK_DEFINITION = ToolDefinition(
    name="run_subtask",
    description=(
        "Lance un sous-agent Colaig dans un workspace cible pour exécuter une sous-tâche. "
        "Le pipeline complet est exécuté (Analyser → Orchestrateur → Synthétiseur). "
        "Retourne le résultat texte + sources. "
        "L'utilisateur doit avoir accès au workspace cible (user_id dans workspace.user_ids). "
        "Utiliser pour des tâches complexes nécessitant RAG + raisonnement dans un espace cible."
    ),
    parameters=[
        ToolParameter(
            name="workspace_id",
            type="string",
            description="Identifiant du workspace cible (ex: 'espace-rh', 'conception-routiere').",
            required=True,
        ),
        ToolParameter(
            name="subtask_query",
            type="string",
            description="Question ou instruction détaillée pour le sous-agent dans le workspace cible.",
            required=True,
        ),
        ToolParameter(
            name="subtask_id",
            type="string",
            description=(
                "Identifiant unique de cette sous-tâche pour le suivi dans le plan "
                "(ex: 'recherche-rh-1', 'analyse-budget-2'). Utilisé pour les fichiers de suivi."
            ),
            required=False,
        ),
    ],
    category="task",
)

UPDATE_PLAN_DEFINITION = ToolDefinition(
    name="update_plan",
    description=(
        "Met à jour le plan dynamique de la session en cours (current/plan.json). "
        "Permet à l'orchestrateur de documenter sa progression, les étapes réalisées "
        "et celles restantes. Appeler après chaque sous-tâche significative."
    ),
    parameters=[
        ToolParameter(
            name="status",
            type="string",
            description="État global de la tâche : 'in_progress' | 'completing' | 'done' | 'blocked'.",
            required=True,
        ),
        ToolParameter(
            name="steps_done",
            type="string",
            description="Description des étapes accomplies (format libre, ex: liste markdown).",
            required=False,
        ),
        ToolParameter(
            name="steps_remaining",
            type="string",
            description="Description des étapes restantes (format libre).",
            required=False,
        ),
        ToolParameter(
            name="notes",
            type="string",
            description="Notes libres sur l'état courant (observations, problèmes rencontrés).",
            required=False,
        ),
    ],
    category="task",
)

REPORT_TO_USER_DEFINITION = ToolDefinition(
    name="report_to_user",
    description=(
        "Envoie le résultat final de la tâche à l'utilisateur via messagerie. "
        "À appeler en fin de session pour livrer le résultat dans la conversation source. "
        "N'utiliser qu'une seule fois par session (livraison finale)."
    ),
    parameters=[
        ToolParameter(
            name="message",
            type="string",
            description="Message complet à envoyer à l'utilisateur (markdown supporté).",
            required=True,
        ),
    ],
    category="task",
)

PAUSE_AND_ASK_USER_DEFINITION = ToolDefinition(
    name="pause_and_ask_user",
    description=(
        "Suspend la session en cours et envoie une question à l'utilisateur via messagerie. "
        "La session reprendra automatiquement lorsque l'utilisateur répondra dans son DM. "
        "À utiliser uniquement si une information essentielle manque et ne peut pas être déduite. "
        "Ne pas abuser — chaque pause interrompt le flux d'exécution."
    ),
    parameters=[
        ToolParameter(
            name="question",
            type="string",
            description="Question posée à l'utilisateur (markdown supporté). Soyez précis et concis.",
            required=True,
        ),
    ],
    category="task",
)

CREATE_DOCUMENT_DEFINITION = ToolDefinition(
    name="create_document",
    description=(
        "Sauvegarde le résultat de la tâche comme fichier dans un workspace. "
        "À utiliser pour delivery_type='document'. "
        "Le fichier est créé ou écrasé au chemin spécifié."
    ),
    parameters=[
        ToolParameter(
            name="content",
            type="string",
            description="Contenu textuel du document à créer (markdown recommandé).",
            required=True,
        ),
        ToolParameter(
            name="path",
            type="string",
            description=(
                "Chemin complet du fichier dans le storage "
                "(ex: /espace-rh/rapports/veille-2026-03.md)."
            ),
            required=True,
        ),
    ],
    category="task",
)


# =============================================================================
# Handlers
# =============================================================================


def create_task_handler(
    storage,
    user_id: str,
    workspace_path: str,
    source_conversation_id: str,
) -> Callable:
    """Handler pour create_background_task.

    Crée un TaskDefinition et le persiste dans {workspace_path}/.colaig/tasks/.

    Args:
        storage              : StorageProtocol.
        user_id              : Identifiant de l'utilisateur (résolu depuis token).
        workspace_path       : Workspace personnel du user.
        source_conversation_id: Conversation DM depuis laquelle la tâche est créée.
    """
    _storage = storage
    _user_id = user_id
    _workspace_path = workspace_path
    _source_conversation_id = source_conversation_id

    async def _handler(
        name: str,
        query: str,
        schedule_type: str,
        schedule_value: str,
        delivery_type: str,
        delivery_target: str = "",
        **kwargs,
    ) -> str:
        from colaig.agents.tasks import (
            TaskDefinition,
            compute_next_run,
            generate_task_id,
            save_task,
        )

        task_id = generate_task_id()
        next_run = compute_next_run(schedule_type, schedule_value)

        # Pour "once" sans next_run : exécution immédiate (next_run_at=None → is_due=True)
        delivery_target_resolved = delivery_target or _source_conversation_id

        # Une livraison « document » fait écrire Colaig avec SES identifiants, à un
        # chemin que le demandeur désigne. Sans contrôle, `.colaig/prompts/…` ferait de
        # la réponse du modèle le prompt système de l'agent — une escalade qui contourne
        # le partage de stockage, puisque l'écrivain n'est pas l'utilisateur.
        #
        # Refus à la création, pour que l'erreur soit lisible au moment où elle se
        # commet. La barrière qui protège vraiment est à la livraison : une tâche
        # enregistrée peut être éditée après coup.
        if delivery_type == "document":
            from colaig.security.path_validator import validate_storage_path
            try:
                delivery_target_resolved = validate_storage_path(
                    delivery_target_resolved, allow_dotcolaig=False,
                    context="create_task",
                )
            except Exception as exc:
                return json.dumps({
                    "success": False,
                    "error": (
                        f"destination refusée : {exc}. Une tâche livre un document dans "
                        "l'espace documentaire, jamais dans le dossier d'instance "
                        ".colaig/ — y écrire reviendrait à reconfigurer l'agent."
                    ),
                }, ensure_ascii=False)

        task = TaskDefinition(
            task_id=task_id,
            user_id=_user_id,
            source_conversation_id=_source_conversation_id,
            workspace_path=_workspace_path,
            name=name,
            query=query,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            delivery_type=delivery_type,
            delivery_target=delivery_target_resolved,
            next_run_at=next_run,
            status="pending",
            enabled=True,
        )

        try:
            await save_task(_storage, task)
        except Exception as exc:
            logger.error("create_task_handler: erreur sauvegarde tâche %s: %s", task_id, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "task_id": task_id,
            "name": name,
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "next_run_at": next_run,
            "delivery_type": delivery_type,
            "delivery_target": delivery_target_resolved,
            "message": (
                f"Tâche '{name}' créée (id={task_id}). "
                + (f"Prochaine exécution : {next_run}." if next_run else "Exécution immédiate planifiée.")
            ),
        }, ensure_ascii=False)

    return _handler


def create_run_subtask_handler(
    user_id: str,
    all_workspaces: list,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    conversation_memory=None,
    storage=None,
    task: TaskDefinition | None = None,
    session_state: TaskSessionState | None = None,
) -> Callable:
    """Handler pour run_subtask.

    Appelle run_workspace_task() — pipeline complet dans le workspace cible.
    Écrit le résultat dans current/subtasks/{subtask_id}.json si task fourni.

    Args:
        user_id          : Identifiant de l'utilisateur (ACL).
        all_workspaces   : Liste complète des workspaces disponibles.
        analyser/orchestrator/synthesiser : Agents Phase 2.
        retriever/generator : Fallback Phase 1.
        conversation_memory : ConversationMemory partagée.
        storage          : StorageProtocol (pour écriture fichier subtask).
        task             : TaskDefinition courante (pour heartbeat + fichiers suivi).
        session_state    : TaskSessionState courante (pour mise à jour compteur).
    """
    _user_id = user_id
    _all_workspaces = all_workspaces
    _analyser = analyser
    _orchestrator = orchestrator
    _synthesiser = synthesiser
    _retriever = retriever
    _generator = generator
    _conv_memory = conversation_memory
    _storage = storage
    _task = task
    _state = session_state

    async def _handler(
        workspace_id: str,
        subtask_query: str,
        subtask_id: str = "",
        **kwargs,
    ) -> str:
        from colaig.agents.tasks import save_subtask_result
        from colaig.agents.workspace_delegate import (
            WorkspaceAccessDenied,
            WorkspaceNotFound,
            run_workspace_task,
        )

        sid = subtask_id or f"sub-{len(_all_workspaces)}"

        # ── Guard max_subtasks ──────────────────────────────────────────────
        if _state and _task and _state.subtasks_done >= _task.max_subtasks:
            logger.warning(
                "run_subtask_handler: max_subtasks=%d atteint pour tâche %s",
                _task.max_subtasks, _task.task_id,
            )
            return json.dumps({
                "success": False,
                "error": f"Limite de sous-tâches atteinte ({_task.max_subtasks}). "
                         "Synthétisez les résultats existants.",
            }, ensure_ascii=False)

        # ── Guard workspace_ids_allowed ─────────────────────────────────────
        if _task:
            try:
                from colaig.security.acl import WorkspaceACL
                WorkspaceACL.validate_task_workspace(_task, workspace_id, _all_workspaces)
            except Exception as exc:
                return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

        try:
            result = await run_workspace_task(
                workspace_id=workspace_id,
                query=subtask_query,
                user_id=_user_id,
                all_workspaces=_all_workspaces,
                analyser=_analyser,
                orchestrator=_orchestrator,
                synthesiser=_synthesiser,
                retriever=_retriever,
                generator=_generator,
                conversation_memory=_conv_memory,
            )
        except WorkspaceNotFound as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        except WorkspaceAccessDenied as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            logger.error("run_subtask_handler: erreur workspace=%s: %s", workspace_id, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

        output = {
            "success": result.success,
            "workspace_id": workspace_id,
            "subtask_id": sid,
            "answer": result.response_text,
            "sources": result.sources,
            "confidence": result.confidence,
        }
        if result.error:
            output["error"] = result.error

        # Persister le résultat dans current/subtasks/{sid}.json
        if _storage and _task:
            await save_subtask_result(_storage, _task, sid, output)

        # Mettre à jour le compteur de sous-tâches dans la session
        if _state:
            _state.subtasks_done += 1

        return json.dumps(output, ensure_ascii=False)

    return _handler


def create_update_plan_handler(
    storage,
    task: TaskDefinition,
    session_state: TaskSessionState,
) -> Callable:
    """Handler pour update_plan.

    Écrit current/plan.json et met à jour le step dans session.json.
    """
    _storage = storage
    _task = task
    _state = session_state

    async def _handler(
        status: str = "in_progress",
        steps_done: str = "",
        steps_remaining: str = "",
        notes: str = "",
        **kwargs,
    ) -> str:
        from colaig.agents.tasks import _now_iso, save_plan, save_session_state

        plan_data = {
            "task_id": _task.task_id,
            "updated_at": _now_iso(),
            "status": status,
            "steps_done": steps_done,
            "steps_remaining": steps_remaining,
            "notes": notes,
        }
        await save_plan(_storage, _task, plan_data)

        # Mettre à jour le step dans la session
        _state.current_step += 1
        _state.current_step_description = steps_done[:120] if steps_done else ""
        await save_session_state(_storage, _task, _state)

        return json.dumps({
            "success": True,
            "step": _state.current_step,
            "status": status,
        }, ensure_ascii=False)

    return _handler


def create_report_to_user_handler(
    messaging,
    delivery_target: str,
) -> Callable:
    """Handler pour report_to_user.

    Envoie le résultat final dans la conversation source via MessagingProtocol.

    Args:
        messaging       : MessagingProtocol.
        delivery_target : conversation_id de destination.
    """
    _messaging = messaging
    _target = delivery_target

    async def _handler(message: str, **kwargs) -> str:
        if not _messaging or not _target:
            return json.dumps({
                "success": False,
                "error": "messaging ou delivery_target non configuré",
            }, ensure_ascii=False)
        try:
            await _messaging.send(_target, message)
            return json.dumps({"success": True, "delivered_to": _target}, ensure_ascii=False)
        except Exception as exc:
            logger.error("report_to_user_handler: erreur envoi → %s: %s", _target, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    return _handler


def create_pause_handler(
    storage,
    task: TaskDefinition,
    session_state: TaskSessionState,
    messaging,
) -> Callable:
    """Handler pour pause_and_ask_user.

    Envoie la question à l'utilisateur, passe task.status = "waiting_for_user"
    et session.status = "waiting_for_user".

    Le scheduler ignorera cette tâche (status != "pending") jusqu'à ce que
    handlers.py injecte la réponse dans session.pending_user_reply et repasse
    task.status = "pending".

    Args:
        storage       : StorageProtocol.
        task          : TaskDefinition courante.
        session_state : TaskSessionState courante.
        messaging     : MessagingProtocol.
    """
    _storage = storage
    _task = task
    _state = session_state
    _messaging = messaging

    async def _handler(question: str, **kwargs) -> str:
        from colaig.agents.tasks import save_session_state, save_task

        if not _messaging or not _task.delivery_target:
            return json.dumps({
                "success": False,
                "error": "messaging ou delivery_target non configuré",
            }, ensure_ascii=False)

        try:
            await _messaging.send(
                _task.delivery_target,
                f"**[Tâche : {_task.name}]** — Question en attente de votre réponse :\n\n{question}",
            )
        except Exception as exc:
            logger.error("pause_handler: erreur envoi question → %s: %s", _task.delivery_target, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

        # Passer les statuts waiting_for_user
        _task.status = "waiting_for_user"
        await save_task(_storage, _task)

        _state.status = "waiting_for_user"
        await save_session_state(_storage, _task, _state)

        return json.dumps({
            "success": True,
            "status": "waiting_for_user",
            "message": (
                "Question envoyée. La session est suspendue en attente de la réponse de l'utilisateur. "
                "Ne pas appeler d'autres tools — la session reprendra automatiquement."
            ),
        }, ensure_ascii=False)

    return _handler


def create_document_handler(storage) -> Callable:
    """Handler pour create_document.

    Sauvegarde le contenu textuel dans le storage au chemin spécifié.

    Args:
        storage: StorageProtocol.
    """
    _storage = storage

    async def _handler(content: str, path: str, **kwargs) -> str:
        if not path:
            return json.dumps({"success": False, "error": "path manquant"}, ensure_ascii=False)

        # Ici, c'est LE MODÈLE qui choisit la cible — et ses entrées comprennent les
        # documents de l'espace, qui sont du contenu non fiable par construction. Sans
        # ce contrôle, une consigne déposée dans un document pouvait faire écrire l'agent
        # dans son propre `.colaig/prompts/` : la chaîne complète, de l'injection à la
        # persistance, sans qu'aucun utilisateur n'ait rien demandé.
        #
        # Le refus est ANNONCÉ au modèle, pas silencieux : un échec muet le fait
        # réessayer, et une boucle agentique a plusieurs tours pour insister.
        from colaig.security.path_validator import validate_storage_path
        try:
            path = validate_storage_path(path, allow_dotcolaig=False,
                                         context="create_document")
        except Exception as exc:
            return json.dumps({
                "success": False,
                "error": (
                    f"destination refusée : {exc}. Le dossier d'instance .colaig/ n'est "
                    "pas un emplacement de document — y écrire reconfigurerait l'agent."
                ),
            }, ensure_ascii=False)

        try:
            await _storage.upload(path, content.encode("utf-8"))
            return json.dumps({
                "success": True,
                "path": path,
                "size": len(content),
            }, ensure_ascii=False)
        except Exception as exc:
            logger.error("create_document_handler: erreur écriture %s: %s", path, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    return _handler
