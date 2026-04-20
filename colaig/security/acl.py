"""
Colaig — Couche d'autorisation centralisée

Source unique de vérité pour tous les contrôles d'accès workspace.
Remplace les vérifications ad-hoc dispersées dans server.py, workspace_delegate.py.

Usage :
    from colaig.security.acl import WorkspaceACL

    # Vérification simple
    if WorkspaceACL.can_access(workspace, user_id, auth_enabled):
        ...

    # Vérification avec exception
    WorkspaceACL.assert_can_access(workspace, user_id, auth_enabled)

    # Filtrage d'une liste
    visible = WorkspaceACL.filter_accessible(all_workspaces, user_id, auth_enabled)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceACL:
    """Contrôleur d'accès workspace — méthodes statiques pures, sans état."""

    @staticmethod
    def can_access(workspace, user_id: str, auth_enabled: bool) -> bool:
        """Retourne True si user_id peut accéder au workspace.

        Règles (ordre de priorité) :
        1. auth_enabled=False → True (backward compat : Colaig sans auth)
        2. workspace.public=True → True (accès public déclaré)
        3. user_id in workspace.user_ids → True (accès explicite)
        4. Sinon → False

        Args:
            workspace: WorkspaceConfig.
            user_id: Identifiant de l'utilisateur courant.
            auth_enabled: Authentification MCP activée (config COLAIG_MCP_AUTH_ENABLED).
        """
        if not auth_enabled:
            return True
        if not user_id:
            return getattr(workspace, "public", False)
        if getattr(workspace, "public", False):
            return True
        user_ids = getattr(workspace, "user_ids", None) or []
        return user_id in user_ids

    @staticmethod
    def assert_can_access(workspace, user_id: str, auth_enabled: bool) -> None:
        """Lève WorkspaceAccessDenied si l'utilisateur n'a pas accès.

        Le message d'erreur ne révèle pas si le workspace existe (anti-énumération).

        Args:
            workspace: WorkspaceConfig.
            user_id: Identifiant de l'utilisateur courant.
            auth_enabled: Authentification MCP activée.

        Raises:
            WorkspaceAccessDenied: Si accès refusé.
        """
        if not WorkspaceACL.can_access(workspace, user_id, auth_enabled):
            from colaig.exceptions import WorkspaceAccessDenied
            logger.debug(
                "acl: accès refusé — user=%s workspace=%s",
                user_id,
                getattr(workspace, "workspace_id", "?"),
            )
            raise WorkspaceAccessDenied(
                f"'{user_id}' n'a pas accès à ce workspace"
            )

    @staticmethod
    def filter_accessible(
        workspaces: list,
        user_id: str,
        auth_enabled: bool,
    ) -> list:
        """Filtre la liste pour ne retourner que les workspaces accessibles.

        Utilisé par colaig_list_workspaces, find_workspace, colaig_search.

        Args:
            workspaces: Liste de WorkspaceConfig.
            user_id: Identifiant de l'utilisateur courant.
            auth_enabled: Authentification MCP activée.

        Returns:
            Sous-liste des workspaces accessibles à user_id.
        """
        return [
            ws for ws in workspaces
            if WorkspaceACL.can_access(ws, user_id, auth_enabled)
        ]

    @staticmethod
    def validate_task_workspace(
        task,
        target_workspace_id: str,
        all_workspaces: list,
    ) -> None:
        """Vérifie que task.user_id peut accéder à target_workspace_id.

        Double vérification :
        1. Le workspace cible est dans la liste des workspaces connus
        2. task.user_id a accès à ce workspace (user_ids ou public)
        3. Si task.workspace_ids_allowed est défini, target doit y figurer

        Args:
            task: TaskDefinition — fournit user_id et workspace_ids_allowed.
            target_workspace_id: ID du workspace cible (run_subtask).
            all_workspaces: Liste complète des WorkspaceConfig.

        Raises:
            WorkspaceNotFound: Si le workspace cible est inconnu.
            WorkspaceAccessDenied: Si accès refusé.
            ValueError: Si target_workspace_id n'est pas dans workspace_ids_allowed.
        """
        from colaig.exceptions import WorkspaceNotFound, WorkspaceAccessDenied

        target_ws = next(
            (ws for ws in all_workspaces if ws.workspace_id == target_workspace_id),
            None,
        )
        if target_ws is None:
            raise WorkspaceNotFound(target_workspace_id)

        WorkspaceACL.assert_can_access(target_ws, task.user_id, auth_enabled=True)

        allowed = getattr(task, "workspace_ids_allowed", None) or []
        if allowed and target_workspace_id not in allowed:
            raise ValueError(
                f"Workspace '{target_workspace_id}' non autorisé pour cette tâche"
            )

    @staticmethod
    def validate_delivery_target(
        delivery_type: str,
        delivery_target: str,
        user_id: str = "",
        personal_workspace_path: str = "",
    ) -> str:
        """Valide et normalise delivery_target pour Mode C.

        Args:
            delivery_type: "messaging", "conversation" ou "document".
            delivery_target: Cible (conversation_id ou chemin fichier).
            user_id: Identifiant user (pour les logs).
            personal_workspace_path: Chemin workspace personnel pour valider les documents.

        Returns:
            delivery_target normalisé.

        Raises:
            ValueError: Si invalide.
        """
        if not delivery_target or not delivery_target.strip():
            raise ValueError("delivery_target ne peut pas être vide")

        delivery_target = delivery_target.strip()

        if delivery_type in ("messaging", "conversation"):
            if len(delivery_target) > 256:
                raise ValueError("delivery_target trop long (max 256 chars)")
            # conversation_id / room_id : alphanumériques, tirets, underscores, points, !:
            if not re.match(r'^[a-zA-Z0-9_\-\.!:#@]+$', delivery_target):
                raise ValueError(
                    f"delivery_target invalide pour type '{delivery_type}': caractères non autorisés"
                )
            return delivery_target

        if delivery_type == "document":
            from colaig.security.path_validator import validate_storage_path, is_subpath
            validated = validate_storage_path(
                delivery_target,
                allow_dotcolaig=False,
                context=f"delivery_target user={user_id}",
            )
            if personal_workspace_path and not is_subpath(validated, personal_workspace_path):
                raise ValueError(
                    f"delivery_target '{validated}' hors du workspace personnel"
                )
            return validated

        # Type inconnu — retourner tel quel après vérification basique
        if len(delivery_target) > 512:
            raise ValueError("delivery_target trop long")
        return delivery_target
