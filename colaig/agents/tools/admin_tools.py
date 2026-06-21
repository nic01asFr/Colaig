"""
Colaig — Outils d'administration réflexive (méta-tools)

Permettent à l'agent, dans le contexte approprié (DM + utilisateur admin), d'opérer
les fonctionnalités Colaig depuis la conversation : créer/configurer un workspace,
lier un salon, définir le prompt système, lister les espaces.

Principe réflexif : ces handlers réutilisent les MÊMES fonctions que le serveur MCP
et les routes web (source unique de vérité — context/workspace.py + resolver).

Garde : injectés uniquement si WorkspaceACL.can_manage(context, admin_user_ids) est vrai
(voir orchestrator._execute_agentic). Default-deny côté ACL.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)


# =============================================================================
# Définitions (schémas exposés au LLM)
# =============================================================================

MANAGE_WORKSPACE_DEFINITION = ToolDefinition(
    name="manage_workspace",
    description=(
        "Crée ou met à jour un espace de travail (workspace) Colaig. "
        "action='create' : crée le workspace + scaffold .colaig/ (idempotent). "
        "action='update' : met à jour la config d'un workspace existant. "
        "Réservé à l'administration (DM admin). Le workspace devient immédiatement actif."
    ),
    parameters=[
        ToolParameter(name="action", type="string",
                      description="'create' ou 'update'.", required=True),
        ToolParameter(name="name", type="string",
                      description="Nom affiché du workspace (ex: 'Équipe RH').", required=True),
        ToolParameter(name="storage_path", type="string",
                      description="Chemin racine dans le storage (create uniquement, ex: /espace-rh/).",
                      required=False),
        ToolParameter(name="workspace_id", type="string",
                      description="Identifiant du workspace cible (update uniquement).", required=False),
        ToolParameter(name="description", type="string",
                      description="Description du domaine du workspace.", required=False),
        ToolParameter(name="system_prompt", type="string",
                      description="Prompt système / persona de l'assistant pour cet espace.", required=False),
        ToolParameter(name="tone", type="string",
                      description="Ton : professional | casual | formal | technical.", required=False),
        ToolParameter(name="language", type="string",
                      description="Langue principale (fr, en…).", required=False),
    ],
    category="admin",
)

LINK_CONVERSATION_DEFINITION = ToolDefinition(
    name="link_conversation",
    description=(
        "Lie un salon/conversation à un workspace existant : les messages de ce salon "
        "seront traités avec la config et les documents du workspace. "
        "Réservé à l'administration (DM admin)."
    ),
    parameters=[
        ToolParameter(name="workspace_id", type="string",
                      description="Identifiant du workspace cible.", required=True),
        ToolParameter(name="conversation_id", type="string",
                      description="Identifiant du salon à lier.", required=True),
    ],
    category="admin",
)

SET_WORKSPACE_PROMPT_DEFINITION = ToolDefinition(
    name="set_workspace_prompt",
    description=(
        "Définit le prompt système (persona/consignes) d'un workspace existant. "
        "Spécialise l'agent pour cet espace. Réservé à l'administration (DM admin)."
    ),
    parameters=[
        ToolParameter(name="workspace_id", type="string",
                      description="Identifiant du workspace cible.", required=True),
        ToolParameter(name="system_prompt", type="string",
                      description="Nouveau prompt système.", required=True),
    ],
    category="admin",
)

MANAGE_WORKSPACE_OWNERS_DEFINITION = ToolDefinition(
    name="manage_workspace_owners",
    description=(
        "Ajoute ou retire un owner (administrateur délégué) d'un workspace. "
        "Réservé à l'administration GLOBALE (jamais un owner — anti-escalade). "
        "Un owner peut ensuite configurer son espace via les autres outils."
    ),
    parameters=[
        ToolParameter(name="action", type="string",
                      description="'add' ou 'remove'.", required=True),
        ToolParameter(name="workspace_id", type="string",
                      description="Identifiant du workspace cible.", required=True),
        ToolParameter(name="target_user_id", type="string",
                      description="user_id à ajouter/retirer des owners.", required=True),
    ],
    category="admin",
)

LIST_MANAGEABLE_WORKSPACES_DEFINITION = ToolDefinition(
    name="list_manageable_workspaces",
    description=(
        "Liste les workspaces connus (id, nom, chemin, nombre de salons liés) "
        "pour les administrer. Réservé à l'administration (DM admin)."
    ),
    parameters=[],
    category="admin",
)


# =============================================================================
# Handlers (closures sur storage + resolver)
# =============================================================================


def _find_ws(resolver, workspace_id: str):
    """Retrouve un WorkspaceConfig par id dans le resolver (liste vivante)."""
    for ws in getattr(resolver, "workspaces", []) or []:
        if ws.workspace_id == workspace_id:
            return ws
    return None


def create_manage_workspace_handler(storage, resolver, user_id="", admin_user_ids=None) -> Callable:
    """Handler pour manage_workspace (create/update).

    create → l'utilisateur courant devient owner (config réflexive scopée).
    update → garde fine can_manage_workspace sur la cible.
    """
    from colaig.security.acl import WorkspaceACL
    _admins = admin_user_ids or []

    async def _handler(action: str, name: str, storage_path: str = "",
                       workspace_id: str = "", description: str = "",
                       system_prompt: str = "", tone: str = "",
                       language: str = "", **kwargs) -> str:
        from colaig.context.workspace import create_workspace, update_workspace_config

        action = (action or "").strip().lower()
        try:
            if action == "create":
                if not storage_path:
                    return json.dumps({"success": False,
                                       "error": "storage_path requis pour action=create"},
                                      ensure_ascii=False)
                # Le créateur devient owner → il pourra ensuite administrer cet espace.
                ws = await create_workspace(
                    storage, storage_path=storage_path, name=name,
                    description=description, system_prompt=system_prompt,
                    tone=tone or "professional", language=language or "fr",
                    owners=[user_id] if user_id else [],
                )
                await resolver.register_workspace(ws)
                return json.dumps({
                    "success": True, "action": "create",
                    "workspace_id": ws.workspace_id, "storage_path": ws.storage_path,
                    "message": f"Workspace '{ws.name}' créé (id={ws.workspace_id}) et actif.",
                }, ensure_ascii=False)

            if action == "update":
                ws = _find_ws(resolver, workspace_id)
                if ws is None:
                    return json.dumps({"success": False,
                                       "error": f"Workspace '{workspace_id}' introuvable"},
                                      ensure_ascii=False)
                if not WorkspaceACL.can_manage_workspace(user_id, ws, _admins):
                    return json.dumps({"success": False,
                                       "error": f"Droits insuffisants sur '{workspace_id}'"},
                                      ensure_ascii=False)
                fields = {}
                if name:
                    fields["name"] = name
                if description:
                    fields["description"] = description
                if system_prompt:
                    fields["system_prompt"] = system_prompt
                if tone:
                    fields["tone"] = tone
                if language:
                    fields["language"] = language
                updated = await update_workspace_config(storage, ws.storage_path, **fields)
                await resolver.register_workspace(updated)
                return json.dumps({
                    "success": True, "action": "update",
                    "workspace_id": updated.workspace_id,
                    "updated_fields": list(fields.keys()),
                    "message": f"Workspace '{updated.name}' mis à jour.",
                }, ensure_ascii=False)

            return json.dumps({"success": False,
                               "error": "action doit être 'create' ou 'update'"},
                              ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — surface l'erreur au LLM
            logger.warning("manage_workspace: échec (%s)", e, exc_info=True)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    return _handler


def create_link_conversation_handler(storage, resolver, user_id="", admin_user_ids=None) -> Callable:
    """Handler pour link_conversation (garde fine sur le workspace cible)."""
    from colaig.security.acl import WorkspaceACL
    _admins = admin_user_ids or []

    async def _handler(workspace_id: str, conversation_id: str, **kwargs) -> str:
        from colaig.context.workspace import add_conversation_to_workspace

        try:
            ws = _find_ws(resolver, workspace_id)
            if ws is None:
                return json.dumps({"success": False,
                                   "error": f"Workspace '{workspace_id}' introuvable"},
                                  ensure_ascii=False)
            if not WorkspaceACL.can_manage_workspace(user_id, ws, _admins):
                return json.dumps({"success": False,
                                   "error": f"Droits insuffisants sur '{workspace_id}'"},
                                  ensure_ascii=False)
            updated = await add_conversation_to_workspace(
                storage, ws.storage_path, conversation_id)
            await resolver.register_workspace(updated)
            return json.dumps({
                "success": True, "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "message": f"Salon lié au workspace '{updated.name}'.",
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("link_conversation: échec (%s)", e, exc_info=True)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    return _handler


def create_set_workspace_prompt_handler(storage, resolver, user_id="", admin_user_ids=None) -> Callable:
    """Handler pour set_workspace_prompt (garde fine sur le workspace cible)."""
    from colaig.security.acl import WorkspaceACL
    _admins = admin_user_ids or []

    async def _handler(workspace_id: str, system_prompt: str, **kwargs) -> str:
        from colaig.context.workspace import update_workspace_config

        try:
            ws = _find_ws(resolver, workspace_id)
            if ws is None:
                return json.dumps({"success": False,
                                   "error": f"Workspace '{workspace_id}' introuvable"},
                                  ensure_ascii=False)
            if not WorkspaceACL.can_manage_workspace(user_id, ws, _admins):
                return json.dumps({"success": False,
                                   "error": f"Droits insuffisants sur '{workspace_id}'"},
                                  ensure_ascii=False)
            updated = await update_workspace_config(
                storage, ws.storage_path, system_prompt=system_prompt)
            await resolver.register_workspace(updated)
            return json.dumps({
                "success": True, "workspace_id": workspace_id,
                "message": f"Prompt système du workspace '{updated.name}' mis à jour.",
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("set_workspace_prompt: échec (%s)", e, exc_info=True)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    return _handler


def create_manage_workspace_owners_handler(storage, resolver, user_id="", admin_user_ids=None) -> Callable:
    """Handler pour manage_workspace_owners — ADMIN GLOBAL uniquement (anti-escalade)."""
    _admins = admin_user_ids or []

    async def _handler(action: str, workspace_id: str, target_user_id: str, **kwargs) -> str:
        from colaig.context.workspace import set_workspace_owners

        # Garde stricte : admin global seulement (jamais un owner).
        if not user_id or user_id not in _admins:
            return json.dumps({"success": False,
                               "error": "Réservé à l'administration globale"},
                              ensure_ascii=False)
        ws = _find_ws(resolver, workspace_id)
        if ws is None:
            return json.dumps({"success": False,
                               "error": f"Workspace '{workspace_id}' introuvable"},
                              ensure_ascii=False)
        action = (action or "").strip().lower()
        owners = list(getattr(ws, "owners", None) or [])
        if action == "add":
            if target_user_id not in owners:
                owners.append(target_user_id)
        elif action == "remove":
            owners = [o for o in owners if o != target_user_id]
        else:
            return json.dumps({"success": False,
                               "error": "action doit être 'add' ou 'remove'"},
                              ensure_ascii=False)
        try:
            updated = await set_workspace_owners(storage, ws.storage_path, owners)
            await resolver.register_workspace(updated)
            return json.dumps({"success": True, "workspace_id": workspace_id,
                               "owners": updated.owners,
                               "message": f"Owners de '{updated.name}' mis à jour."},
                              ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("manage_workspace_owners: échec (%s)", e, exc_info=True)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    return _handler


def create_list_manageable_workspaces_handler(resolver, user_id="", admin_user_ids=None) -> Callable:
    """Handler pour list_manageable_workspaces (filtré aux espaces administrables)."""
    from colaig.security.acl import WorkspaceACL
    _admins = admin_user_ids or []

    async def _handler(**kwargs) -> str:
        items = []
        for ws in getattr(resolver, "workspaces", []) or []:
            if not WorkspaceACL.can_manage_workspace(user_id, ws, _admins):
                continue
            items.append({
                "workspace_id": ws.workspace_id,
                "name": ws.name,
                "storage_path": ws.storage_path,
                "conversations": len(getattr(ws, "conversations", []) or []),
            })
        return json.dumps({"success": True, "count": len(items),
                           "workspaces": items}, ensure_ascii=False)

    return _handler


def register_admin_tools(registry, storage, resolver, user_id="", admin_user_ids=None) -> None:
    """Enregistre les 4 méta-tools d'administration dans un ToolRegistry.

    Appelé par l'orchestrateur UNIQUEMENT si can_manage(context, admin_user_ids, workspaces).
    Le user_id + admin_user_ids permettent la garde FINE par workspace cible
    (can_manage_workspace) dans chaque handler.
    """
    registry.register(MANAGE_WORKSPACE_DEFINITION,
                      create_manage_workspace_handler(storage, resolver, user_id, admin_user_ids))
    registry.register(LINK_CONVERSATION_DEFINITION,
                      create_link_conversation_handler(storage, resolver, user_id, admin_user_ids))
    registry.register(SET_WORKSPACE_PROMPT_DEFINITION,
                      create_set_workspace_prompt_handler(storage, resolver, user_id, admin_user_ids))
    registry.register(LIST_MANAGEABLE_WORKSPACES_DEFINITION,
                      create_list_manageable_workspaces_handler(resolver, user_id, admin_user_ids))
    # Gestion des owners : exposée uniquement aux admins GLOBAUX (anti-escalade).
    if user_id and user_id in (admin_user_ids or []):
        registry.register(MANAGE_WORKSPACE_OWNERS_DEFINITION,
                          create_manage_workspace_owners_handler(storage, resolver, user_id, admin_user_ids))
