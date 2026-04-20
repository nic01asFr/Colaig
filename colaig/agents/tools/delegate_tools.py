"""
agents/tools/delegate_tools.py — Outil de délégation inter-workspace.

Outil : ask_workspace
Permet à l'orchestrateur agentique d'interroger un workspace cible au nom
de l'utilisateur (disponible quand user_id et all_workspaces sont connus).

Mécanisme :
- Utilise run_rag_delegate() : RAG cross-workspace + ACL check
- Retourne les chunks pertinents du workspace cible en JSON
- Le synthétiseur du workspace appelant intègre ces résultats
- Zéro pipeline agents dans le workspace cible (RAG direct)

ACL : l'utilisateur doit figurer dans workspace.user_ids du workspace cible.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from colaig.models import ToolDefinition, ToolParameter


# =============================================================================
# find_workspace — découverte sémantique de workspaces
# =============================================================================

FIND_WORKSPACE_DEFINITION = ToolDefinition(
    name="find_workspace",
    description=(
        "Trouve les workspaces Colaig les plus pertinents pour une requête, "
        "sans connaître leurs identifiants a priori. "
        "Utile pour router une question vers le bon espace de travail. "
        "Retourne les workspace_ids à utiliser ensuite avec ask_workspace."
    ),
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Requête ou sujet pour lequel trouver le workspace le plus pertinent.",
            required=True,
        ),
        ToolParameter(
            name="k",
            type="integer",
            description="Nombre maximum de workspaces candidats à retourner (défaut : 3).",
            required=False,
        ),
    ],
    category="delegation",
)


def create_find_workspace_handler(
    workspace_directory,
    user_id: str = "",
    all_workspaces: Optional[list] = None,
    auth_enabled: bool = True,
) -> Callable:
    """Crée un handler async pour l'outil find_workspace.

    Les résultats sont filtrés par ACL : seuls les workspaces accessibles à
    user_id sont retournés (défense en profondeur — même si ask_workspace
    valide l'ACL à la récupération).

    Args:
        workspace_directory: WorkspaceDirectory — répertoire vectoriel des workspaces.
        user_id: Identifiant de l'utilisateur courant (pour le filtrage ACL).
        all_workspaces: Liste complète des WorkspaceConfig (pour la vérification ACL).
        auth_enabled: Authentification MCP activée.

    Returns:
        Callable async(query, k=3) -> str (JSON)
    """
    _directory = workspace_directory
    _user_id = user_id
    _all_workspaces = all_workspaces or []
    _auth_enabled = auth_enabled

    # Index ACL rapide : workspace_id → WorkspaceConfig
    _ws_index = {ws.workspace_id: ws for ws in _all_workspaces}

    async def find_workspace_handler(query: str, k: Optional[int] = 3) -> str:
        """Trouve les workspaces les plus pertinents pour une requête.

        Returns:
            JSON string :
            {"workspaces": [{"workspace_id": ..., "name": ..., "description": ..., "score": ...}]}
        """
        from colaig.security.acl import WorkspaceACL

        try:
            # Demander plus de résultats pour compenser le filtrage ACL
            fetch_k = (k or 3) * 3
            entries = await _directory.search(query, k=fetch_k)
        except Exception as exc:
            return json.dumps({"workspaces": [], "error": str(exc)}, ensure_ascii=False)

        # Filtrer par ACL si user_id et index de workspaces disponibles
        visible = []
        for e in entries:
            ws = _ws_index.get(e.workspace_id)
            if ws is not None:
                # Workspace local connu : vérifier ACL
                if not WorkspaceACL.can_access(ws, _user_id, _auth_enabled):
                    continue
            # Workspace distant (mcp_url non vide) ou non indexé : inclure
            # (l'ACL sera vérifiée par ask_workspace lors de la récupération)
            visible.append(e)
            if len(visible) >= (k or 3):
                break

        return json.dumps(
            {
                "workspaces": [
                    {
                        "workspace_id": e.workspace_id,
                        "name": e.workspace_name,
                        "description": e.description,
                        "score": e.score,
                    }
                    for e in visible
                ]
            },
            ensure_ascii=False,
        )

    return find_workspace_handler


# =============================================================================
# ask_workspace — délégation RAG vers un workspace cible
# =============================================================================

# Définition de l'outil — schéma JSON OpenAI-compatible
ASK_WORKSPACE_DEFINITION = ToolDefinition(
    name="ask_workspace",
    description=(
        "Recherche des documents dans un workspace Colaig spécifique au nom de l'utilisateur. "
        "Retourne les passages pertinents trouvés dans l'espace de travail cible. "
        "Utile pour croiser des informations provenant de plusieurs workspaces. "
        "L'utilisateur doit avoir accès au workspace cible (user_id dans workspace.user_ids)."
    ),
    parameters=[
        ToolParameter(
            name="workspace_id",
            type="string",
            description=(
                "Identifiant du workspace cible (ex: 'espace-rh', 'conception-routiere'). "
                "Doit correspondre à un workspace auquel l'utilisateur a accès."
            ),
            required=True,
        ),
        ToolParameter(
            name="query",
            type="string",
            description="Question ou requête de recherche à effectuer dans le workspace cible.",
            required=True,
        ),
        ToolParameter(
            name="k",
            type="integer",
            description="Nombre maximum de résultats à retourner (défaut : 5).",
            required=False,
        ),
    ],
    category="delegation",
)


def create_ask_workspace_handler(
    user_id: str,
    all_workspaces: list,
    retriever,
    workspace_stores: Optional[dict] = None,
    bm25_stores: Optional[dict] = None,
    calling_workspace=None,   # WorkspaceConfig — niveau 3 inter-fédération (mcp_connectors)
    index_registry=None,      # FaissIndexRegistry — niveau 2 intra-fédération (auto-discovery)
    workspace_directory=None, # WorkspaceDirectory — niveau 4 fédération décentralisée
) -> Callable:
    """Crée un handler async pour l'outil ask_workspace.

    Utilise run_rag_delegate() — RAG cross-workspace avec vérification ACL.
    Les résultats sont retournés en JSON pour que le synthétiseur
    du workspace appelant les intègre dans sa réponse finale.

    Résolution en 4 niveaux (transparente pour l'appelant) :
    1. FAISS local déjà chargé (workspace_stores)
    2. Auto-discovery intra-fédération via index_registry (même storage, visibility="federation")
    3. Inter-fédération via calling_workspace.mcp_connectors (storage différent)
    4. Fédération décentralisée via workspace_directory (peers.yaml + connectors agrégés)

    Args:
        user_id: Identifiant de l'utilisateur courant (pour ACL).
        all_workspaces: Liste complète des workspaces disponibles.
        retriever: Service RAG (RetrieverProtocol).
        workspace_stores: Dict workspace_id → FaissStore.
        bm25_stores: Dict workspace_id → BM25Store (hybrid search, optionnel).
        calling_workspace: Workspace source — fournit les mcp_connectors pour niveau 3.
        index_registry: FaissIndexRegistry partagé — auto-discovery intra-fédération.
        workspace_directory: WorkspaceDirectory — lookup mcp_url pour niveau 4.

    Returns:
        Callable async(workspace_id, query, k=5) -> str (JSON)
    """
    _user_id = user_id
    _all_workspaces = all_workspaces
    _retriever = retriever
    _workspace_stores = workspace_stores
    _bm25_stores = bm25_stores
    _calling_workspace = calling_workspace
    _index_registry = index_registry
    _workspace_directory = workspace_directory

    async def ask_workspace_handler(
        workspace_id: str,
        query: str,
        k: Optional[int] = 5,
    ) -> str:
        """Effectue une recherche RAG dans un workspace cible.

        Returns:
            JSON string avec les chunks pertinents :
            {"success": true, "workspace": ..., "chunks": [...], "count": N}
        """
        from colaig.agents.workspace_delegate import (
            WorkspaceAccessDenied,
            WorkspaceNotFound,
            run_rag_delegate,
        )

        try:
            delegate_result = await run_rag_delegate(
                workspace_id=workspace_id,
                query=query,
                user_id=_user_id,
                all_workspaces=_all_workspaces,
                workspace_stores=_workspace_stores,
                index_registry=_index_registry,
                calling_workspace=_calling_workspace,
                retriever=_retriever,
                k=k or 5,
                bm25_stores=_bm25_stores,
                workspace_directory=_workspace_directory,
            )
        except WorkspaceNotFound as exc:
            accessible = [
                ws.workspace_id
                for ws in _all_workspaces
                if _user_id in ws.user_ids
            ]
            return json.dumps({
                "success": False,
                "error": str(exc),
                "accessible_workspaces": accessible,
            }, ensure_ascii=False)
        except WorkspaceAccessDenied as exc:
            return json.dumps({
                "success": False,
                "error": str(exc),
            }, ensure_ascii=False)

        output: dict = {
            "success": delegate_result.success,
            "workspace": delegate_result.workspace_name,
            "workspace_id": delegate_result.workspace_id,
            "count": len(delegate_result.chunks),
            "chunks": delegate_result.chunks,
        }
        if delegate_result.error:
            output["error"] = delegate_result.error
        return json.dumps(output, ensure_ascii=False)

    return ask_workspace_handler
