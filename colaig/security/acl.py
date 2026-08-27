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

logger = logging.getLogger(__name__)


class WorkspaceACL:
    """Contrôleur d'accès workspace — méthodes statiques pures, sans état."""

    @staticmethod
    def can_access(workspace, user_id: str, auth_enabled: bool) -> bool:
        """Retourne True si user_id peut accéder au workspace.

        Règles (ordre de priorité) :
        1. auth_enabled=False → True (backward compat : Colaig sans auth)
        2. workspace.public=True → True (accès public déclaré)
        3. user_id in workspace.owners → True (le propriétaire lit son espace)
        4. user_id in workspace.user_ids → True (accès explicite)
        5. Sinon → False

        POURQUOI `owners` FIGURE ICI (corrigé au lot L2.6)
        ---------------------------------------------------
        Ce prédicat ignorait `owners`. Mesuré le 27/08/2026 sur un espace créé par
        `manage_workspace(action="create")`, qui pose `owners=[createur]` et laisse
        `user_ids` vide :

            can_manage_workspace  → True    il administre
            can_link_conversation → True    il y rattache une conversation
            can_access            → False   il ne peut pas le LIRE

        Le créateur d'un espace en était donc exclu, et `filter_accessible` le lui
        cachait dans sa propre liste. Les deux autres prédicats du module consultaient
        `owners` ; celui-ci était seul à ne pas le faire.

        L'ajout **n'accorde aucun droit nouveau** : un propriétaire peut déjà s'inscrire
        lui-même dans `user_ids` par `manage_workspace(action="update")`. Il supprime un
        piège qui poussait à élargir `user_ids` pour contourner le symptôme.

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
        if user_id in (getattr(workspace, "owners", None) or []):
            return True
        user_ids = getattr(workspace, "user_ids", None) or []
        return user_id in user_ids

    @staticmethod
    def can_link_conversation(workspace, user_id: str) -> bool:
        """Cet utilisateur peut-il rattacher une conversation a cet espace ?

        POURQUOI CE PREDICAT ET PAS `can_access`
        ------------------------------------------
        `can_access` commence par « auth_enabled=False -> True », une compatibilite
        ascendante pour Colaig sans authentification MCP. Le chemin conversationnel
        Matrix n'a AUCUNE notion d'`auth_enabled` : y passer `False` rendrait la garde
        inerte, et y passer `True` mentirait sur ce qui est verifie. Un garde toujours
        vrai est pire qu'absent — on croit etre protege.

        CE QUE CE PREDICAT PROTEGE
        ---------------------------
        L'appariement salon -> espace **est** la frontiere d'acces du chemin
        conversationnel : une fois le salon rattache, tout ce qui s'y dit interroge le
        corpus de l'espace, sans autre controle. `WorkspaceACL` garde les outils
        d'administration, la delegation et les taches de fond ; il ne garde pas ce
        chemin-la, parce que l'appartenance au salon y fait foi.

        Un rattachement forgeable defait donc entierement la cloison multi-tenant.
        Mesure avant correctif : deux messages depuis n'importe quel salon suffisaient.

        REFUS PAR DEFAUT, sans exception d'environnement :
        1. espace public (accueil) -> True, c'est sa raison d'etre
        2. proprietaire declare -> True
        3. membre declare -> True
        4. sinon -> False
        """
        if not workspace:
            return False
        if getattr(workspace, "public", False):
            return True
        if not user_id:
            return False
        return (
            user_id in (getattr(workspace, "owners", None) or [])
            or user_id in (getattr(workspace, "user_ids", None) or [])
        )

    @staticmethod
    def can_manage(context, admin_user_ids: list, workspaces=None) -> bool:
        """Garde d'INJECTION : l'utilisateur reçoit-il les outils d'administration ?

        Default-deny strict (un pouvoir réflexif n'est jamais implicite) :
        1. mode != PERSONAL (DM) → False (jamais depuis un salon métier)
        2. user_id absent → False
        3. admin global (user_id ∈ admin_user_ids) → True
        4. owner d'au moins un workspace (scoping fin) → True
        5. Sinon → False

        Contrairement à can_access (allow-all si auth désactivée), l'administration
        est toujours default-deny. La garde FINE par workspace cible est
        can_manage_workspace (appliquée par appel dans les handlers).

        Args:
            context: WorkspaceContext (fournit mode + user_id).
            admin_user_ids: Liste des user_ids administrateurs globaux (config).
            workspaces: Liste des WorkspaceConfig connus (pour le check owner).
        """
        from colaig.models import ContextMode

        user_id = getattr(context, "user_id", "") or ""
        if not user_id:
            return False
        if getattr(context, "mode", None) != ContextMode.PERSONAL:
            return False
        if admin_user_ids and user_id in admin_user_ids:
            return True
        for ws in (workspaces or []):
            if user_id in (getattr(ws, "owners", None) or []):
                return True
        return False

    @staticmethod
    def can_manage_workspace(user_id: str, workspace, admin_user_ids: list) -> bool:
        """Garde FINE : l'utilisateur peut-il administrer CE workspace cible ?

        Appliquée par appel dans les handlers d'administration (update / link /
        set_prompt). Admin global → tout ; sinon owner du workspace uniquement.

        Args:
            user_id: Identifiant de l'utilisateur courant.
            workspace: WorkspaceConfig cible.
            admin_user_ids: Liste des user_ids administrateurs globaux.
        """
        if not user_id:
            return False
        if admin_user_ids and user_id in admin_user_ids:
            return True
        return user_id in (getattr(workspace, "owners", None) or [])

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
        from colaig.exceptions import WorkspaceNotFound

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
            from colaig.exceptions import StorageError
            from colaig.security.path_validator import is_subpath, validate_storage_path

            # `validate_storage_path` leve `StorageError`, qui N'EST PAS un `ValueError`.
            # La docstring de cette fonction promet `ValueError`, et l'appelant MCP ecrit
            # `except ValueError` : sans cette conversion, un refus s'echappait en erreur
            # non traitee au lieu d'un message. La tache n'etait pas creee — l'echec
            # allait donc dans le bon sens — mais rien n'etait diagnosticable.
            #
            # Un contrat annonce et non tenu est pire qu'un contrat absent : l'appelant
            # ecrit du code qui a l'air correct.
            try:
                validated = validate_storage_path(
                    delivery_target,
                    allow_dotcolaig=False,
                    context=f"delivery_target user={user_id}",
                )
            except StorageError as exc:
                raise ValueError(str(exc)) from exc
            if personal_workspace_path and not is_subpath(validated, personal_workspace_path):
                raise ValueError(
                    f"delivery_target '{validated}' hors du workspace personnel"
                )
            return validated

        # Type inconnu — retourner tel quel après vérification basique
        if len(delivery_target) > 512:
            raise ValueError("delivery_target trop long")
        return delivery_target
