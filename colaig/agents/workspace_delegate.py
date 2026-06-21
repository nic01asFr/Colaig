"""
Colaig — Délégation inter-workspace

Module partagé pour l'exécution de tâches dans un workspace cible.

Utilisé par :
- L'outil interne `ask_workspace` (orchestrateur agentique en mode DM)
- Le planificateur Mode C (tâches autonomes en arrière-plan)

Principe d'isolation : chaque tâche s'exécute dans le contexte complet
du workspace cible (system_prompt, RAG, outils, langue).

ACL : un utilisateur peut accéder à un workspace si son user_id figure
dans workspace.user_ids. Cette liste est configurée par l'administrateur
du workspace dans .colaig/config.yaml (champ `user_ids`).

Design : ask_workspace fonctionne comme un search_documents cross-workspace —
il retourne les chunks pertinents du workspace cible. Le synthétiseur du
workspace personnel intègre ensuite ces résultats dans sa réponse finale.
Pour un pipeline complet dans le workspace cible, utiliser run_workspace_task()
(Mode C uniquement).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from colaig.models import WorkspaceConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================

# Importer depuis colaig.exceptions (source de vérité).
# WorkspaceAccessDenied supporte la signature legacy (user_id, workspace_id)
# ET la signature simple (message string) — backward compat complet.
from colaig.exceptions import WorkspaceAccessDenied, WorkspaceNotFound  # noqa: F401

# =============================================================================
# Résultats
# =============================================================================

@dataclass
class WorkspaceDelegateResult:
    """Résultat d'une délégation RAG vers un workspace cible.

    Retourné par run_rag_delegate() — contient les chunks pertinents
    sous forme sérialisable pour le synthétiseur du workspace appelant.
    """
    workspace_id: str
    workspace_name: str
    query: str
    chunks: list = field(default_factory=list)  # [{text, source, score, page, section, workspace}]
    success: bool = True
    error: str = ""


@dataclass
class WorkspaceTaskResult:
    """Résultat d'une tâche complète exécutée dans un workspace cible.

    Retourné par run_workspace_task() — contient la réponse textuelle finale.
    Utilisé principalement par le Mode C (tâches autonomes planifiées).
    """
    workspace_id: str
    workspace_name: str
    query: str
    response: str = ""
    sources: list = field(default_factory=list)
    confidence: float = 0.0
    success: bool = True
    error: str = ""
    pipeline_used: str = ""  # "agents" | "generator" | "error"


# =============================================================================
# ACL
# =============================================================================

def find_accessible_workspaces(
    all_workspaces: list[WorkspaceConfig], user_id: str
) -> list[WorkspaceConfig]:
    """Retourne les workspaces accessibles à un utilisateur (user_id in ws.user_ids).

    Les workspaces personnels ont user_ids=[owner_id].
    Les workspaces partagés peuvent être enrichis d'user_ids par l'admin.
    """
    return [ws for ws in all_workspaces if user_id in ws.user_ids]


def check_workspace_access(workspace: WorkspaceConfig, user_id: str) -> None:
    """Vérifie qu'un utilisateur a accès à un workspace.

    Raises:
        WorkspaceAccessDenied: Si l'utilisateur n'a pas accès.
    """
    if user_id not in workspace.user_ids:
        raise WorkspaceAccessDenied(user_id, workspace.workspace_id)


# =============================================================================
# Délégation légère — ask_workspace (RAG cross-workspace)
# =============================================================================

async def run_rag_delegate(
    *,
    workspace_id: str,
    query: str,
    user_id: str,
    all_workspaces: list[WorkspaceConfig],
    workspace_stores: dict | None = None,
    index_registry=None,          # FaissIndexRegistry — niveau 2 intra-fédération
    calling_workspace: WorkspaceConfig | None = None,  # niveau 3 inter-fédération
    retriever,
    k: int = 5,
    score_threshold: float = 0.3,
    bm25_stores: dict | None = None,
    workspace_directory=None,     # WorkspaceDirectory — niveau 4 fédération décentralisée
) -> WorkspaceDelegateResult:
    """Effectue une recherche RAG dans un workspace cible avec vérification ACL.

    Résolution en 4 niveaux :
    1. Intra-instance — FAISS local déjà chargé dans workspace_stores (O(1))
    2. Intra-fédération — même storage, FAISS chargé via index_registry (visibilité "federation"/"public")
    3. Inter-fédération déclarée — appel MCP via mcp_connectors du workspace appelant
    4. Fédération décentralisée — mcp_url résolu depuis workspace_directory (peers.yaml + connectors)

    Utilisé par l'outil ask_workspace dans l'orchestrateur agentique.
    Retourne les chunks pertinents, pas une réponse textuelle complète.

    Raises:
        WorkspaceNotFound, WorkspaceAccessDenied
    """
    target_ws = next((ws for ws in all_workspaces if ws.workspace_id == workspace_id), None)

    # ── Niveau 1 & 2 : workspace connu localement (même instance ou même storage) ──
    if target_ws is not None:
        check_workspace_access(target_ws, user_id)

        # Niveau 1 : FAISS déjà chargé dans workspace_stores
        store = (workspace_stores or {}).get(workspace_id)

        # Niveau 2 : auto-discovery intra-fédération — si pas de store local et
        # visibility="federation"/"public", charger depuis index_registry (même storage)
        if store is None and target_ws.visibility in ("federation", "public") and index_registry is not None:
            from colaig.rag.colaig_index import ColaigIndex
            index_key = ColaigIndex.docs_key(target_ws.storage_path)
            try:
                store = await index_registry.get_or_load(index_key)
            except Exception:
                store = None

        return await _run_local_rag(
            target_ws=target_ws,
            query=query,
            store=store,  # None → retriever utilise son index par défaut
            bm25_store=(bm25_stores or {}).get(workspace_id),
            retriever=retriever,
            k=k,
            score_threshold=score_threshold,
        )

    # ── Niveau 3 : inter-fédération via mcp_connectors ──────────────────────
    # Le workspace n'est pas dans all_workspaces (storage différent).
    # Si le workspace appelant déclare un connector pour cet id, appel MCP distant.
    if calling_workspace is not None:
        connector = next(
            (
                c for c in calling_workspace.mcp_connectors
                if c.name == workspace_id and c.enabled
            ),
            None,
        )
        if connector is not None:
            return await _call_peer_search(connector, workspace_id, query, k, score_threshold)

    # ── Niveau 4 : fédération décentralisée via workspace_directory ──────────
    # Workspace découvert via find_workspace (peers.yaml + connectors agrégés).
    # Le répertoire vectoriel a résolu le mcp_url du peer lors du build.
    if workspace_directory is not None:
        entry = workspace_directory.find_by_id(workspace_id)
        if entry is not None and entry.mcp_url:
            # Valider l'URL peer avant d'appeler (anti-SSRF)
            from colaig.security.federation_guard import validate_peer_url
            try:
                validate_peer_url(entry.mcp_url)
            except ValueError as exc:
                logger.warning(
                    "run_rag_delegate: URL peer invalide pour '%s': %s", workspace_id, exc
                )
                raise WorkspaceNotFound(workspace_id)

            # Synthétiser un connector-like compatible avec _call_peer_search
            class _PeerConnector:
                def __init__(self, url: str, auth_token: str, name: str) -> None:
                    self.url = url
                    self.auth_token = auth_token
                    self.name = name
                    self.enabled = True

            return await _call_peer_search(
                _PeerConnector(entry.mcp_url, entry.auth_token, workspace_id),
                workspace_id,
                query,
                k,
                score_threshold,
            )

    raise WorkspaceNotFound(workspace_id)


async def _run_local_rag(
    *,
    target_ws: WorkspaceConfig,
    query: str,
    store,
    bm25_store,
    retriever,
    k: int,
    score_threshold: float,
) -> WorkspaceDelegateResult:
    """Exécute un RAG local sur un FaissStore déjà chargé."""
    result = WorkspaceDelegateResult(
        workspace_id=target_ws.workspace_id, workspace_name=target_ws.name, query=query
    )
    try:
        retrieve_kwargs: dict = dict(query=query, k=k, score_threshold=score_threshold)
        if store is not None:
            retrieve_kwargs["store"] = store
        if bm25_store is not None:
            retrieve_kwargs["bm25_store"] = bm25_store

        search_results = await retriever.retrieve(**retrieve_kwargs)
        result.chunks = [
            {
                "text": r.chunk.text[:1000],
                "source": f"[{target_ws.name}] {r.chunk.source_name or r.chunk.source_path}",
                "score": round(r.score, 3),
                "page": r.chunk.page,
                "section": r.chunk.section,
                "workspace": target_ws.name,
            }
            for r in search_results
        ]
    except Exception as exc:
        logger.exception(
            "workspace_delegate: erreur RAG workspace=%s", target_ws.workspace_id
        )
        result.success = False
        result.error = str(exc)
    return result


async def _call_peer_search(
    connector,
    workspace_id: str,
    query: str,
    k: int,
    score_threshold: float,
) -> WorkspaceDelegateResult:
    """Appelle colaig_search sur une instance distante via MCP JSON-RPC.

    Utilisé pour l'inter-fédération (storage différent) — le connector MCP
    est déclaré dans calling_workspace.mcp_connectors.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "colaig_search",
            "arguments": {
                "query": query,
                "workspace_id": workspace_id,
                "k": k,
                "score_threshold": score_threshold,
            },
        },
    }
    result = WorkspaceDelegateResult(
        workspace_id=workspace_id,
        workspace_name=connector.name,
        query=query,
    )
    try:
        headers = {}
        if connector.auth_token:
            headers["Authorization"] = f"Bearer {connector.auth_token}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(connector.url, json=payload, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        # Format MCP : result.content[0].text = JSON string de la liste de chunks
        content = data.get("result", {}).get("content", [])
        raw_chunks = []
        if content:
            import json as _json
            try:
                raw_chunks = _json.loads(content[0].get("text", "[]"))
            except Exception:
                pass
        # Valider les chunks reçus (anti-injection depuis peer compromis)
        from colaig.security.federation_guard import validate_peer_chunks
        safe_chunks = validate_peer_chunks(raw_chunks, connector.name)
        result.chunks = [
            {
                "text": c["text"][:1000],
                "source": f"[{connector.name}] {c.get('source', '')}",
                "score": c.get("score", 0.0),
                "page": None,
                "section": None,
                "workspace": connector.name,
            }
            for c in safe_chunks
        ]
    except Exception as exc:
        logger.exception(
            "workspace_delegate: erreur peer_search connector=%s workspace=%s",
            connector.name, workspace_id,
        )
        result.success = False
        result.error = str(exc)
    return result


# =============================================================================
# Tâche complète — Mode C (pipeline analyser+orchestrateur+synthétiseur)
# =============================================================================

async def run_workspace_task(
    *,
    workspace_id: str,
    query: str,
    user_id: str,
    all_workspaces: list[WorkspaceConfig],
    workspace_stores: dict | None = None,
    analyser=None,
    orchestrator=None,
    synthesiser=None,
    retriever=None,
    generator=None,
    conversation_memory=None,
    conversation_id: str = "",
    bm25_stores: dict | None = None,
) -> WorkspaceTaskResult:
    """Exécute une tâche complète dans un workspace cible avec vérification ACL.

    Utilisé principalement par le Mode C (tâches autonomes planifiées).
    Pour la délégation interactive depuis un DM, préférer run_rag_delegate().

    Raises:
        WorkspaceNotFound, WorkspaceAccessDenied
    """
    target_ws = next((ws for ws in all_workspaces if ws.workspace_id == workspace_id), None)
    if target_ws is None:
        raise WorkspaceNotFound(workspace_id)
    check_workspace_access(target_ws, user_id)

    result = WorkspaceTaskResult(
        workspace_id=workspace_id, workspace_name=target_ws.name, query=query
    )

    from colaig.context.layers import build_context
    from colaig.models import ContextMode, ConversationType, IncomingMessage

    fake_message = IncomingMessage(
        user_id=user_id,
        conversation_id=conversation_id or f"delegate_{workspace_id}",
        body=query,
        conversation_type=ConversationType.DM,
        message_id=f"delegate_{workspace_id}_{hash(query) & 0xFFFFFF}",
        platform="internal",
    )
    context = build_context(target_ws, fake_message, ContextMode.ASSISTANT)

    try:
        if analyser is not None and orchestrator is not None and synthesiser is not None:
            await _run_agents_pipeline(
                query=query, fake_message=fake_message, context=context,
                workspace_id=workspace_id, conversation_id=conversation_id,
                analyser=analyser, orchestrator=orchestrator, synthesiser=synthesiser,
                conversation_memory=conversation_memory, result=result,
            )
        elif retriever is not None and generator is not None:
            await _run_generator_pipeline(
                query=query, context=context, workspace_id=workspace_id,
                workspace_stores=workspace_stores, bm25_stores=bm25_stores,
                retriever=retriever, generator=generator, result=result,
            )
        else:
            result.success = False
            result.error = "Aucun pipeline disponible"
            result.pipeline_used = "error"
    except Exception as exc:
        logger.exception(
            "workspace_delegate: erreur pipeline workspace=%s user=%s", workspace_id, user_id
        )
        result.success = False
        result.error = str(exc)
        result.pipeline_used = "error"
    return result


# =============================================================================
# Pipelines internes (Mode C)
# =============================================================================

async def _run_agents_pipeline(
    *, query, fake_message, context, workspace_id, conversation_id,
    analyser, orchestrator, synthesiser, conversation_memory, result,
) -> None:
    """Pipeline Phase 2+ : Analyseur → Orchestrateur → Synthétiseur."""
    history: list[dict] = []
    if conversation_memory and conversation_id and context.workspace:
        try:
            history = await conversation_memory.load_relevant_history(
                context.workspace.storage_path, conversation_id, query
            )
        except Exception:
            pass
    intent = await analyser.analyse(fake_message, context)
    plan = await orchestrator.execute(intent, context)
    response = await synthesiser.synthesise(plan, context, conversation_history=history)
    result.response = response.text
    result.sources = list(response.sources) if response.sources else []
    result.confidence = float(response.confidence) if response.confidence else 0.0
    result.pipeline_used = "agents"


async def _run_generator_pipeline(
    *, query, context, workspace_id, workspace_stores, bm25_stores, retriever, generator, result,
) -> None:
    """Pipeline Phase 1 : RAG + Générateur (fallback sans agents)."""
    store = (workspace_stores or {}).get(workspace_id)
    bm25_store = (bm25_stores or {}).get(workspace_id)
    ws = context.workspace
    k = ws.max_results if ws else 5
    threshold = ws.similarity_threshold if ws else 0.3
    retrieve_kwargs: dict = dict(query=query, k=k, score_threshold=threshold)
    if store is not None:
        retrieve_kwargs["store"] = store
    if bm25_store is not None:
        retrieve_kwargs["bm25_store"] = bm25_store
    search_results = await retriever.retrieve(**retrieve_kwargs)
    response = await generator.generate(query=query, context=context, search_results=search_results)
    result.response = response.text if hasattr(response, "text") else str(response)
    result.sources = [r.chunk.source_name for r in search_results if r.chunk.source_name]
    result.confidence = (
        sum(r.score for r in search_results) / len(search_results) if search_results else 0.0
    )
    result.pipeline_used = "generator"
