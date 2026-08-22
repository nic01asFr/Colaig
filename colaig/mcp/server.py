"""
Colaig — MCP Server

ColaigMCPServer encapsule un FastMCP("colaig") et enregistre tools, resources
et prompts. Produit un http_app() montable sur FastAPI.

Architecture MCP :
    Tools    → actions (ask, search, upload, list_docs, get_doc, reindex)
    Resources → données lisibles (workspaces, index docs, config)
    Prompts   → templates contextuels (workspace_assistant)
    Sampling  → délégation d'inférence au client LLM (analyse documents à l'upload)

Sampling :
    Quand un fichier est uploadé via colaig_upload_file, le serveur MCP peut
    demander au client LLM (Claude Desktop, Cursor…) d'analyser le document et
    retourner les métadonnées structurées (résumé, catégorie, entités, mots-clés).
    Si le client ne supporte pas le sampling (ex : Matrix bridge), le pipeline
    Albert prend le relais via analyze_pending().

Usage dans main.py :
    mcp_server = ColaigMCPServer(resolver, retriever, indexer, storage, config,
                                  document_index=document_index, ...)
    app.mount("/mcp", mcp_server.http_app())
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime

from mcp import types as mcp_types
from mcp.server.fastmcp import Context, FastMCP

from colaig.context.workspace import (
    add_conversation_to_workspace,
    create_workspace,
    update_workspace_config,
)
from colaig.models import (
    ConversationType,
    DocumentRecord,
    DocumentStatus,
    IncomingMessage,
)
from colaig.rag.document_index import _parse_json_from_response
from colaig import paths

logger = logging.getLogger(__name__)

# Schéma JSON attendu pour l'analyse de document (envoyé au client via sampling)
_ANALYSIS_SCHEMA = json.dumps({
    "summary": "2-3 phrases résumant le document",
    "category": "procédure|rapport|guide|formulaire|compte-rendu|circulaire|note|courrier|autre",
    "entities": ["entité1", "entité2"],
    "keywords": ["mot-clé1", "mot-clé2", "mot-clé3"],
    "language": "fr",
    "doc_type": "type de contenu en quelques mots",
}, ensure_ascii=False)


class ColaigMCPServer:
    """Serveur MCP pour Colaig.

    Enregistre 7 tools, 3+ resources, 1 prompt.
    Supporte le sampling MCP pour l'analyse de documents à l'upload.

    Args:
        resolver: ContextResolverProtocol.
        retriever: RetrieverProtocol.
        indexer: Indexer (pour reindex).
        storage: StorageProtocol.
        config: ColaigConfig.
        document_index: DocumentIndex (optionnel — activé si COLAIG_DOCUMENT_INDEX_ENABLED).
        generator: Generator (Phase 1 fallback).
        analyser: Analyser (Phase 2).
        orchestrator: Orchestrator (Phase 2).
        synthesiser: Synthesiser (Phase 2).
    """

    def __init__(
        self,
        resolver,
        retriever,
        indexer,
        storage,
        config,
        document_index=None,
        generator=None,
        analyser=None,
        orchestrator=None,
        synthesiser=None,
        workspace_stores=None,
        config_store=None,
        conversation_memory=None,
        user_memory=None,
        token_manager=None,
    ) -> None:
        self._resolver = resolver
        self._retriever = retriever
        self._indexer = indexer
        self._storage = storage
        self._config = config
        self._document_index = document_index
        self._generator = generator
        self._analyser = analyser
        self._orchestrator = orchestrator
        self._synthesiser = synthesiser
        self._workspace_stores = workspace_stores if workspace_stores is not None else {}
        self._config_store = config_store
        self._conversation_memory = conversation_memory
        self._user_memory = user_memory
        self._token_manager = token_manager

        self.mcp = FastMCP(
            "colaig",
            streamable_http_path="/",
            instructions="x-dual-mode: standalone\nColaig est un assistant RAG documentaire. Utilisez colaig_ask pour interroger les documents, colaig_search pour la recherche seule, colaig_list_workspaces pour lister les espaces disponibles.",
        )

        self._register_tools()
        self._register_resources()
        self._register_prompts()

    def http_app(self, path: str = "/mcp"):
        """Retourne l'application ASGI montable sur FastAPI."""
        return self.mcp.streamable_http_app()

    # =========================================================================
    # Tools
    # =========================================================================

    def _register_tools(self) -> None:
        resolver = self._resolver
        retriever = self._retriever
        indexer = self._indexer
        storage = self._storage
        generator = self._generator
        analyser = self._analyser
        orchestrator = self._orchestrator
        synthesiser = self._synthesiser
        document_index = self._document_index
        workspace_stores = self._workspace_stores
        config_store = self._config_store
        conversation_memory = self._conversation_memory
        user_memory = self._user_memory
        token_manager = self._token_manager
        config = self._config
        mcp = self.mcp

        # ── colaig_ask ────────────────────────────────────────────────

        @mcp.tool()
        async def colaig_ask(
            question: str,
            workspace_id: str = "",
            conversation_id: str = "mcp-default",
            user_id: str = "mcp-client",
        ) -> str:
            """Pose une question à Colaig. Pipeline complet (analyse → recherche → réponse).

            La session MCP est persistée comme une conversation Colaig standard :
            l'historique est sauvegardé dans {workspace}/.colaig/conversations/{conversation_id}.json,
            identique aux conversations Tchap/Matrix. Passer le même conversation_id entre
            appels maintient la continuité de la session.

            Si un token Bearer est fourni dans les headers HTTP (Authorization: Bearer colaig_...),
            user_id et les droits d'accès sont déterminés automatiquement par le token.
            scope="*" → workspace personnel (PERSONAL, ask_workspace disponible)
            scope="ws-id" → workspace spécifique uniquement (ASSISTANT)

            Args:
                question: La question à poser.
                workspace_id: ID du workspace (optionnel — ignoré si token scope restreint).
                conversation_id: ID de session MCP — sert de clé d'historique persistant.
                    Utiliser un ID stable (ex: "claude-desktop-projet-xyz") pour maintenir
                    la continuité entre sessions.
                user_id: Identifiant du client LLM appelant. Ignoré si token présent.

            Returns:
                JSON avec answer, sources, confidence, context_card.
            """
            from colaig.auth.tokens import get_current_token

            # Auth token — prend la priorité sur les paramètres (identique à un DM Tchap)
            token_ctx = get_current_token()
            if token_ctx:
                user_id = token_ctx.user_id
                if token_ctx.scope != "*":
                    # Token workspace-scoped : contraint workspace_id
                    if workspace_id and workspace_id != token_ctx.scope:
                        return json.dumps({
                            "error": f"workspace '{workspace_id}' non autorisé par ce token (scope={token_ctx.scope})"
                        }, ensure_ascii=False)
                    workspace_id = token_ctx.scope

            # Déterminer le type de conversation :
            # - Token global (scope=*) sans workspace_id → DM → resolver → PERSONAL
            #   → même comportement qu'un DM Tchap : workspace personnel + ask_workspace
            # - Workspace explicite → PRIVATE → resolver → ASSISTANT
            is_personal = token_ctx is not None and token_ctx.scope == "*" and not workspace_id
            conv_type = ConversationType.DM if is_personal else ConversationType.PRIVATE

            message = IncomingMessage(
                user_id=user_id,
                conversation_id=conversation_id,
                body=question,
                conversation_type=conv_type,
                platform="mcp",
            )
            # Si workspace_id explicite → résolution directe sans passer par le resolver
            # (évite un log trompeur "workspace=_default" et un appel async inutile)
            if workspace_id:
                from colaig.context.layers import build_context
                from colaig.models import ContextMode
                ws = _find_workspace(resolver, workspace_id)
                if ws:
                    # Contrôle d'accès : workspace public ou user membre
                    if not _user_can_access_workspace(ws, user_id, config.mcp_auth_enabled if config else False):
                        return json.dumps({
                            "error": f"Accès non autorisé au workspace '{workspace_id}'. "
                                     "Ce workspace requiert un token d'authentification valide."
                        }, ensure_ascii=False)
                    context = build_context(ws, message, ContextMode.ASSISTANT)
                else:
                    context = await resolver.resolve(message)
            else:
                context = await resolver.resolve(message)

            # Store workspace-specific si disponible (index chargé par initial_indexation)
            ws_store = None
            if workspace_id and workspace_id in workspace_stores:
                ws_store = workspace_stores[workspace_id]
            elif context.workspace and context.workspace.workspace_id in workspace_stores:
                ws_store = workspace_stores[context.workspace.workspace_id]

            # Charger l'historique de conversation (même mécanique que handlers.py Phase 5)
            if conversation_memory and context.workspace and context.workspace.storage_path:
                try:
                    history = await conversation_memory.load_relevant_history(
                        workspace_path=context.workspace.storage_path,
                        conversation_id=conversation_id,
                        current_query=question,
                    )
                    context.conversation_history = history
                except Exception:
                    logger.warning("MCP: impossible de charger l'historique conv=%s", conversation_id)

            if analyser and orchestrator and synthesiser:
                intent = await analyser.analyse(message, context)
                plan = await orchestrator.execute(intent, context)
                response = await synthesiser.synthesise(plan, context, message=message)
            elif generator:
                search_results = []
                if context.workspace and context.workspace.rag_enabled:
                    search_results = await retriever.retrieve(query=question, store=ws_store)
                response = await generator.generate(
                    query=question, context=context,
                    search_results=search_results,
                )
            else:
                return json.dumps({"error": "Aucun générateur configuré"})

            # Persister le tour de conversation (idem handlers.py)
            if conversation_memory and context.workspace and context.workspace.storage_path:
                try:
                    await conversation_memory.save_turn(
                        workspace_path=context.workspace.storage_path,
                        conversation_id=conversation_id,
                        user_message=question,
                        assistant_response=response.text,
                        existing_history=context.conversation_history,
                    )
                except Exception:
                    logger.warning("MCP: impossible de sauvegarder l'historique conv=%s", conversation_id)

            # UserMemory — extraction fire-and-forget (ne bloque pas la réponse)
            if user_memory and context.workspace and context.workspace.storage_path:
                user_memory.schedule_extract(
                    user_id=user_id,
                    workspace_path=context.workspace.storage_path,
                    user_msg=question,
                    assistant_msg=response.text,
                    conversation_id=conversation_id,
                )

            result = {
                "answer": response.text,
                "sources": response.sources,
                "confidence": response.confidence,
                "model_used": response.model_used,
            }
            if response.context_card:
                result["context_card"] = asdict(response.context_card)
            return json.dumps(result, ensure_ascii=False)

        # ── colaig_search ─────────────────────────────────────────────

        @mcp.tool()
        async def colaig_search(
            query: str,
            k: int = 5,
            score_threshold: float = 0.3,
            workspace_id: str = "",
        ) -> str:
            """Recherche documentaire RAG dans les index Colaig.

            Args:
                query: Requête de recherche.
                k: Nombre de résultats max.
                score_threshold: Seuil de score minimum.
                workspace_id: Restreindre la recherche à un workspace spécifique (optionnel).

            Returns:
                JSON avec la liste des résultats (texte, source, score).
            """
            # Recherche dans tous les workspace_stores chargés (union des index)
            # Si workspace_id fourni, restreindre à ce workspace uniquement
            stores = workspace_stores or {}
            if workspace_id:
                stores = {ws_id: ws_store for ws_id, ws_store in stores.items() if ws_id == workspace_id}
            if stores:
                all_results = []
                seen_stores: set[int] = set()
                for ws_id, ws_store in stores.items():
                    if id(ws_store) in seen_stores:
                        continue
                    seen_stores.add(id(ws_store))
                    try:
                        partial = await retriever.retrieve(
                            query=query, k=k, score_threshold=score_threshold, store=ws_store,
                        )
                        all_results.extend(partial)
                    except Exception:
                        pass
                results = sorted(all_results, key=lambda r: r.score, reverse=True)[:k]
            else:
                results = await retriever.retrieve(
                    query=query, k=k, score_threshold=score_threshold,
                )
            output = [
                {
                    "text": r.chunk.text[:500],
                    "source": r.chunk.source_name,
                    "source_path": r.chunk.source_path,
                    "score": round(r.score, 3),
                }
                for r in results
            ]
            return json.dumps(output, ensure_ascii=False)

        # ── colaig_upload_file ────────────────────────────────────────

        @mcp.tool()
        async def colaig_upload_file(
            path: str,
            content_base64: str,
            workspace_id: str = "",
            analyze: bool = True,
        ) -> str:
            """Upload un fichier dans un workspace Colaig.

            Si analyze=True et que le client MCP supporte le sampling,
            demande au client LLM d'analyser le document et retourne les
            métadonnées structurées. Sinon, le fichier est mis en file
            d'attente pour analyse via Albert (analyze_pending).

            Args:
                path: Chemin relatif dans le workspace (ex: "documents/guide.pdf").
                content_base64: Contenu du fichier encodé en base64.
                workspace_id: ID du workspace cible (premier workspace si vide).
                analyze: Déclencher l'analyse IA après upload.

            Returns:
                JSON avec path, size, status, et analysis si disponible.
            """
            # Décoder le contenu
            try:
                content = base64.b64decode(content_base64)
            except Exception:
                return json.dumps({"error": "content_base64 invalide"})

            # Trouver le workspace
            ws = _find_workspace(resolver, workspace_id)
            if ws is None:
                return json.dumps({"error": f"workspace '{workspace_id}' introuvable"})

            # Valider le chemin d'upload (anti path traversal, interdit .colaig/)
            try:
                from colaig.security.path_validator import validate_storage_path
                path = validate_storage_path(path, allow_dotcolaig=False, context="colaig_upload_file")
            except Exception as e:
                return json.dumps({"error": f"chemin invalide: {e}"})

            # Construire le chemin complet
            ws_path = ws.storage_path.rstrip("/") + "/"
            full_path = ws_path + path.lstrip("/")
            filename = path.split("/")[-1]

            # Upload
            try:
                await storage.upload(full_path, content)
            except Exception as e:
                return json.dumps({"error": f"upload échoué: {e}"})

            result: dict = {
                "path": full_path,
                "size": len(content),
                "status": "uploaded",
            }

            if analyze and document_index is not None:
                # Créer un record initial
                record = DocumentRecord(
                    path=full_path,
                    name=filename,
                    size=len(content),
                    status=DocumentStatus.PENDING,
                    indexed_at=datetime.now(UTC),
                )

                # Tenter l'analyse via sampling MCP
                analysis = await _try_sampling_analysis(mcp, content, filename)

                if analysis:
                    # Le client LLM a analysé → inject direct (bypass Albert)
                    record.ai_summary = analysis.get("summary", "")
                    record.ai_category = analysis.get("category", "")
                    record.ai_entities = analysis.get("entities", [])
                    record.ai_keywords = analysis.get("keywords", [])
                    record.ai_language = analysis.get("language", "fr")
                    record.ai_doc_type = analysis.get("doc_type", "")
                    await document_index.inject_analyzed_record(ws.storage_path, record)
                    result["status"] = "uploaded_analyzed"
                    result["analysis"] = analysis
                    logger.info(
                        "colaig_upload_file: sampling OK pour %s (catégorie=%s)",
                        filename, record.ai_category,
                    )
                else:
                    # Pas de sampling → ajouter en PENDING pour la boucle Albert
                    state = await document_index._ensure_loaded(ws.storage_path)
                    state.registry[full_path] = record
                    state.dirty = True
                    result["status"] = "uploaded_pending_analysis"
                    logger.info(
                        "colaig_upload_file: %s en attente d'analyse Albert", filename
                    )

            return json.dumps(result, ensure_ascii=False)

        # ── colaig_list_documents ─────────────────────────────────────

        @mcp.tool()
        async def colaig_list_documents(
            workspace_id: str = "",
            category: str = "",
            status: str = "",
            name_contains: str = "",
            limit: int = 20,
        ) -> str:
            """Liste les documents du DocumentIndex d'un workspace.

            Args:
                workspace_id: ID du workspace (premier workspace si vide).
                category: Filtrer par catégorie IA (guide, rapport, formulaire…).
                status: Filtrer par statut (pending, analyzed, error).
                name_contains: Filtrer par nom de fichier (case-insensitive).
                limit: Nombre max de résultats.

            Returns:
                JSON avec la liste de documents et leurs métadonnées IA.
            """
            if document_index is None:
                return json.dumps({"error": "DocumentIndex non activé (COLAIG_DOCUMENT_INDEX_ENABLED)"})

            ws = _find_workspace(resolver, workspace_id)
            if ws is None:
                return json.dumps({"error": f"workspace '{workspace_id}' introuvable"})

            filters: dict = {}
            if category:
                filters["ai_category"] = category
            if status:
                filters["status"] = status
            if name_contains:
                filters["name_contains"] = name_contains

            docs = await document_index.list_documents(
                ws.storage_path, filters=filters or None, limit=limit
            )
            output = [_doc_to_dict(d) for d in docs]
            return json.dumps(output, ensure_ascii=False)

        # ── colaig_get_document ───────────────────────────────────────

        @mcp.tool()
        async def colaig_get_document(
            path: str,
            workspace_id: str = "",
        ) -> str:
            """Récupère les métadonnées complètes d'un document du DocumentIndex.

            Args:
                path: Chemin complet du document.
                workspace_id: ID du workspace (premier workspace si vide).

            Returns:
                JSON avec toutes les métadonnées IA du document, ou error si absent.
            """
            if document_index is None:
                return json.dumps({"error": "DocumentIndex non activé (COLAIG_DOCUMENT_INDEX_ENABLED)"})

            ws = _find_workspace(resolver, workspace_id)
            if ws is None:
                return json.dumps({"error": f"workspace '{workspace_id}' introuvable"})

            record = await document_index.get_document(ws.storage_path, path)
            if record is None:
                return json.dumps({"error": f"document introuvable: {path}"})
            return json.dumps(_doc_to_dict_full(record), ensure_ascii=False)

        # ── colaig_list_workspaces ────────────────────────────────────

        @mcp.tool()
        async def colaig_list_workspaces() -> str:
            """Liste les workspaces configurés dans Colaig.

            Sans token : seuls les workspaces publics (public=true) sont listés.
            Avec token : les workspaces publics + ceux dont l'utilisateur est membre.

            Returns:
                JSON avec la liste des workspaces (id, name, description, tools).
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            current_user_id = token_ctx.user_id if token_ctx else ""

            workspaces = resolver.workspaces
            output = [
                {
                    "workspace_id": ws.workspace_id,
                    "name": ws.name,
                    "description": ws.description,
                    "topics": getattr(ws, "topics", []),
                    "rag_enabled": ws.rag_enabled,
                    "tools_enabled": ws.tools_enabled,
                    "public": ws.public,
                    "visibility": getattr(ws, "visibility", "private"),
                }
                for ws in workspaces
                if _user_can_access_workspace(ws, current_user_id, config.mcp_auth_enabled if config else False)
            ]
            return json.dumps(output, ensure_ascii=False)

        # ── colaig_create_token / colaig_list_tokens / colaig_revoke_token ──
        # Gestion des tokens MCP utilisateur.
        # Ces tools nécessitent un token valide (user_id résolu depuis le token)
        # ou sont désactivés si token_manager est None.

        @mcp.tool()
        async def colaig_create_token(
            name: str,
            scope: str = "*",
            expires_in_days: int = 0,
        ) -> str:
            """Crée un token MCP pour accéder à Colaig depuis un client LLM.

            Le token est stocké dans le workspace personnel de l'utilisateur.
            Un fichier de config MCP prêt à l'emploi est généré automatiquement
            dans .colaig/mcp-configs/ du workspace personnel.

            Args:
                name: Nom lisible du token (ex: "Claude Desktop", "Cursor Pro").
                scope: "*" → accès complet (workspace personnel + ask_workspace).
                       "workspace-id" → accès restreint à ce workspace uniquement.
                expires_in_days: 0 = pas d'expiration. Sinon, nombre de jours.

            Returns:
                JSON avec le token brut (affiché une seule fois) et le chemin
                du fichier de config MCP à récupérer depuis le workspace.
            """
            if token_manager is None:
                return json.dumps({"error": "Gestion des tokens non activée"})

            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise pour créer un token"})

            expires_at = None
            if expires_in_days and expires_in_days > 0:
                from datetime import datetime as dt
                from datetime import timedelta
                expires_at = (dt.now(UTC) + timedelta(days=expires_in_days)).isoformat()

            raw_token = await token_manager.create(
                user_id=token_ctx.user_id,
                name=name,
                scope=scope,
                expires_at=expires_at,
            )

            from colaig.auth.tokens import _mcp_config_path, _personal_ws_slug, _safe_name
            slug = _personal_ws_slug(token_ctx.user_id)
            sname = _safe_name(name)
            cfg_path = _mcp_config_path(slug, sname)

            return json.dumps({
                "token": raw_token,
                "scope": scope,
                "expires_at": expires_at,
                "mcp_config_path": cfg_path,
                "note": (
                    "Conservez ce token en lieu sûr — il ne sera plus affiché. "
                    f"Récupérez la config MCP dans votre workspace personnel : {cfg_path}"
                ),
            }, ensure_ascii=False)

        @mcp.tool()
        async def colaig_list_tokens() -> str:
            """Liste les tokens MCP de l'utilisateur courant.

            Retourne les tokens sans exposer les secrets.

            Returns:
                JSON avec la liste des tokens (name, scope, created_at, expires_at).
            """
            if token_manager is None:
                return json.dumps({"error": "Gestion des tokens non activée"})

            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            tokens = await token_manager.list_tokens(token_ctx.user_id)
            return json.dumps({"tokens": tokens, "count": len(tokens)}, ensure_ascii=False)

        @mcp.tool()
        async def colaig_revoke_token(name: str) -> str:
            """Révoque un token MCP par son nom.

            Supprime le fichier token et la config MCP associée du workspace personnel.
            Le token devient immédiatement invalide.

            Args:
                name: Nom du token à révoquer (tel qu'il apparaît dans colaig_list_tokens).

            Returns:
                JSON avec le résultat de la révocation.
            """
            if token_manager is None:
                return json.dumps({"error": "Gestion des tokens non activée"})

            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            revoked = await token_manager.revoke(token_ctx.user_id, name)
            if revoked:
                return json.dumps({"revoked": True, "name": name}, ensure_ascii=False)
            return json.dumps(
                {"revoked": False, "error": f"Token '{name}' introuvable"},
                ensure_ascii=False,
            )

        # ── colaig_create_task / colaig_list_tasks / colaig_delete_task / colaig_run_task_now ──
        # Gestion des tâches autonomes planifiées (Mode C).
        # Ces tools nécessitent un token valide (auth requise).

        @mcp.tool()
        async def colaig_create_task(
            name: str,
            query: str,
            schedule_type: str,
            schedule_value: str,
            delivery_type: str = "messaging",
            delivery_target: str = "",
            workspace_id: str = "",
        ) -> str:
            """Crée une tâche autonome planifiée.

            La tâche s'exécutera en arrière-plan selon la planification définie.
            Le résultat sera livré via messaging ou sauvegardé comme document.

            Nécessite un token d'authentification valide (Authorization: Bearer colaig_...).

            Args:
                name           : Nom lisible de la tâche (ex: "Veille hebdomadaire RH").
                query          : Instruction complète décrivant ce que doit faire la tâche.
                schedule_type  : "once" | "interval" | "cron".
                schedule_value : Pour interval : "7d", "24h", "30m". Pour cron : "0 8 * * 1".
                delivery_type  : "messaging" (DM Tchap), "document" (fichier), ou "conversation"
                               (écriture dans une conversation MCP, pull-based).
                               Vide depuis MCP = "conversation" automatique.
                delivery_target: conversation_id (messaging/conversation) ou path (document).
                               Vide = auto-généré selon le type détecté.
                workspace_id   : Workspace cible principal (optionnel).

            Returns:
                JSON avec task_id, schedule, next_run_at.
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise pour créer une tâche"})

            from colaig.agents.tasks import (
                TaskDefinition,
                compute_next_run,
                generate_task_id,
                save_task,
            )
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            task_id = generate_task_id()
            next_run = compute_next_run(schedule_type, schedule_value)

            # Auto-détection MCP : si delivery_type/target non précisés,
            # utiliser "conversation" (pull-based) avec un conversation_id stable par user.
            # Le résultat sera récupérable via colaig_ask(conversation_id=delivery_target).
            if not delivery_target and delivery_type == "messaging":
                delivery_type = "conversation"
                delivery_target = f"task-results-{personal_ws.workspace_id}"
            delivery_resolved = delivery_target or f"task-results-{personal_ws.workspace_id}"

            # Valider delivery_target avant création de la tâche
            try:
                from colaig.security.acl import WorkspaceACL
                WorkspaceACL.validate_delivery_target(
                    delivery_type,
                    delivery_resolved,
                    user_id=token_ctx.user_id,
                    personal_workspace_path=personal_ws.storage_path if delivery_type == "document" else "",
                )
            except ValueError as exc:
                return json.dumps({"error": f"delivery_target invalide: {exc}"}, ensure_ascii=False)

            task = TaskDefinition(
                task_id=task_id,
                user_id=token_ctx.user_id,
                source_conversation_id=delivery_resolved,
                workspace_path=personal_ws.storage_path,
                name=name,
                query=query,
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                delivery_type=delivery_type,
                delivery_target=delivery_resolved,
                next_run_at=next_run,
                status="pending",
                enabled=True,
            )

            try:
                await save_task(storage, task)
            except Exception as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

            return json.dumps({
                "task_id": task_id,
                "name": name,
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "next_run_at": next_run,
                "delivery_type": delivery_type,
                "delivery_target": delivery_resolved,
                "status": "pending",
            }, ensure_ascii=False)

        @mcp.tool()
        async def colaig_list_tasks() -> str:
            """Liste les tâches autonomes de l'utilisateur courant.

            Nécessite un token d'authentification valide.

            Returns:
                JSON avec la liste des tâches (id, name, status, schedule, next_run_at).
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            from colaig.agents.tasks import list_tasks
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            tasks = await list_tasks(storage, personal_ws.storage_path)

            output = [
                {
                    "task_id": t.task_id,
                    "name": t.name,
                    "status": t.status,
                    "enabled": t.enabled,
                    "schedule_type": t.schedule_type,
                    "schedule_value": t.schedule_value,
                    "next_run_at": t.next_run_at,
                    "last_run_at": t.last_run_at,
                    "last_run_status": t.last_run_status,
                    "created_at": t.created_at,
                }
                for t in tasks
                if t.status != "archived"
            ]
            return json.dumps({"tasks": output, "count": len(output)}, ensure_ascii=False)

        @mcp.tool()
        async def colaig_delete_task(task_id: str) -> str:
            """Supprime (désactive) une tâche autonome.

            Marque la tâche enabled=False et status=archived.
            Ne supprime pas les archives runs/ (audit conservé).

            Nécessite un token d'authentification valide.

            Args:
                task_id: Identifiant de la tâche (depuis colaig_list_tasks).

            Returns:
                JSON avec le résultat.
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            from colaig.agents.tasks import load_task, save_task
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            task = await load_task(storage, personal_ws.storage_path, task_id)
            if task is None:
                return json.dumps({"error": f"Tâche '{task_id}' introuvable"}, ensure_ascii=False)

            task.enabled = False
            task.status = "archived"
            await save_task(storage, task)

            return json.dumps({"deleted": True, "task_id": task_id}, ensure_ascii=False)

        @mcp.tool()
        async def colaig_run_task_now(task_id: str) -> str:
            """Déclenche immédiatement l'exécution d'une tâche planifiée.

            Force next_run_at=None et status=pending — le scheduler la prendra
            lors du prochain cycle de polling.

            Nécessite un token d'authentification valide.

            Args:
                task_id: Identifiant de la tâche.

            Returns:
                JSON avec la confirmation.
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            from colaig.agents.tasks import load_task, save_task
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            task = await load_task(storage, personal_ws.storage_path, task_id)
            if task is None:
                return json.dumps({"error": f"Tâche '{task_id}' introuvable"}, ensure_ascii=False)
            if not task.enabled:
                return json.dumps({"error": f"Tâche '{task_id}' désactivée"}, ensure_ascii=False)

            task.next_run_at = None  # Force exécution immédiate au prochain cycle
            task.status = "pending"
            await save_task(storage, task)

            return json.dumps({
                "triggered": True,
                "task_id": task_id,
                "name": task.name,
                "message": "La tâche sera exécutée au prochain cycle du planificateur.",
            }, ensure_ascii=False)

        # ── colaig_get_task_status / colaig_get_task_history ─────────

        @mcp.tool()
        async def colaig_get_task_status(task_id: str) -> str:
            """Retourne le statut détaillé d'une tâche planifiée.

            Si une session est en cours (current/session.json présent), retourne
            l'état live (step courant, heartbeat, plan). Sinon, retourne le résumé
            du dernier run archivé.

            Nécessite un token d'authentification valide.

            Args:
                task_id: Identifiant de la tâche.

            Returns:
                JSON avec statut détaillé.
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            from colaig.agents.tasks import (
                load_plan,
                load_session_state,
                load_task,
                run_summary_path,
            )
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            task = await load_task(storage, personal_ws.storage_path, task_id)
            if task is None:
                return json.dumps({"error": f"Tâche '{task_id}' introuvable"}, ensure_ascii=False)

            result: dict = {
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status,
                "enabled": task.enabled,
                "schedule_type": task.schedule_type,
                "schedule_value": task.schedule_value,
                "next_run_at": task.next_run_at,
                "last_run_at": task.last_run_at,
                "last_run_status": task.last_run_status,
                "error_count": task.error_count,
            }

            # Session live ?
            session = await load_session_state(storage, task)
            if session is not None:
                result["live_session"] = {
                    "conversation_id": session.conversation_id,
                    "session_status": session.status,
                    "started_at": session.started_at,
                    "last_heartbeat": session.last_heartbeat,
                    "current_step": session.current_step,
                    "current_step_description": session.current_step_description,
                    "subtasks_done": session.subtasks_done,
                }
                plan = await load_plan(storage, task)
                if plan:
                    result["live_plan"] = plan
            else:
                # Dernier run archivé
                last_summary = None
                try:
                    run_dir = paths.task_runs_dir(task.workspace_path, task_id)
                    run_entries = await storage.list_files(run_dir, recursive=False)
                    run_dirs = sorted(
                        [e for e in run_entries if e.is_directory],
                        key=lambda e: e.name,
                        reverse=True,
                    )
                    if run_dirs:
                        latest = run_dirs[0].name
                        summary_path = run_summary_path(task.workspace_path, task_id, latest)
                        raw = await storage.download(summary_path)
                        last_summary = json.loads(raw.decode("utf-8"))
                except Exception:
                    pass
                result["last_run"] = last_summary

            return json.dumps(result, ensure_ascii=False, indent=2)

        @mcp.tool()
        async def colaig_get_task_history(task_id: str, limit: int = 10) -> str:
            """Retourne l'historique des exécutions d'une tâche planifiée.

            Liste les N derniers runs archivés avec leur statut, durée, et aperçu
            du résultat.

            Nécessite un token d'authentification valide.

            Args:
                task_id: Identifiant de la tâche.
                limit  : Nombre max de runs à retourner (défaut 10).

            Returns:
                JSON avec la liste des runs triés du plus récent au plus ancien.
            """
            from colaig.auth.tokens import get_current_token
            token_ctx = get_current_token()
            if token_ctx is None:
                return json.dumps({"error": "Authentification requise"})

            from colaig.agents.tasks import load_task
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(storage, token_ctx.user_id)
            task = await load_task(storage, personal_ws.storage_path, task_id)
            if task is None:
                return json.dumps({"error": f"Tâche '{task_id}' introuvable"}, ensure_ascii=False)

            runs = []
            try:
                run_dir = paths.task_runs_dir(task.workspace_path, task_id)
                run_entries = await storage.list_files(run_dir, recursive=False)
                run_dirs = sorted(
                    [e for e in run_entries if e.is_directory],
                    key=lambda e: e.name,
                    reverse=True,
                )[:max(1, limit)]

                for entry in run_dirs:
                    from colaig.agents.tasks import run_summary_path
                    try:
                        summary_path = run_summary_path(task.workspace_path, task_id, entry.name)
                        raw = await storage.download(summary_path)
                        runs.append(json.loads(raw.decode("utf-8")))
                    except Exception:
                        runs.append({"run_id": entry.name, "error": "summary introuvable"})
            except Exception:
                pass  # Pas encore de runs = liste vide

            return json.dumps({
                "task_id": task_id,
                "task_name": task.name,
                "runs_count": len(runs),
                "runs": runs,
            }, ensure_ascii=False, indent=2)

        # ── colaig_reindex ────────────────────────────────────────────

        @mcp.tool()
        async def colaig_reindex(workspace_id: str = "") -> str:
            """Relance l'indexation des documents d'un workspace.

            Args:
                workspace_id: ID du workspace à réindexer. Vide = tous.

            Returns:
                JSON avec le résultat de l'indexation.
            """
            results = []
            for ws in resolver.workspaces:
                if workspace_id and ws.workspace_id != workspace_id:
                    continue
                if not ws.rag_enabled or not ws.storage_path:
                    continue
                try:
                    count = await indexer.index_workspace(ws.storage_path)
                    if count > 0:
                        await indexer.save_to_storage(ws.index_path)
                    results.append({
                        "workspace_id": ws.workspace_id,
                        "indexed": count,
                        "status": "ok",
                    })
                except Exception as e:
                    results.append({
                        "workspace_id": ws.workspace_id,
                        "status": "error",
                        "error": str(e),
                    })
            return json.dumps(results, ensure_ascii=False)

        # ── colaig_create_workspace ───────────────────────────────────

        @mcp.tool()
        async def colaig_create_workspace(
            storage_path: str,
            name: str,
            workspace_id: str = "",
            description: str = "",
            conversations: str = "",
            system_prompt: str = "",
            tone: str = "professional",
            language: str = "fr",
            rag_enabled: bool = True,
        ) -> str:
            """Crée un nouveau workspace Colaig avec le scaffold .colaig/ complet.

            Accès restreint aux administrateurs (COLAIG_ADMIN_USER_IDS).

            Crée le dossier .colaig/ dans storage_path avec config.yaml, indexes/
            et conversations/. Enregistre immédiatement le workspace dans Colaig
            sans nécessiter de redémarrage.

            Args:
                storage_path: Chemin du dossier dans le storage (ex: /espace-rh/).
                name: Nom affiché du workspace.
                workspace_id: Slug unique (auto-généré depuis name si vide).
                description: Description du workspace.
                conversations: IDs de salons liés séparés par des virgules.
                system_prompt: Prompt système personnalisé (vide = défaut Colaig).
                tone: Ton de l'assistant (professional, casual, formal, technical).
                language: Langue principale (fr, en…).
                rag_enabled: Activer la recherche documentaire RAG.

            Returns:
                JSON avec le WorkspaceConfig créé, ou error si échec.
            """
            from colaig.auth.tokens import get_current_token, require_admin
            if err := require_admin(get_current_token()):
                return err

            conv_list = [c.strip() for c in conversations.split(",") if c.strip()]

            try:
                ws = await create_workspace(
                    storage=storage,
                    storage_path=storage_path,
                    name=name,
                    workspace_id=workspace_id or None,
                    description=description,
                    conversations=conv_list,
                    system_prompt=system_prompt,
                    tone=tone,
                    language=language,
                    rag_enabled=rag_enabled,
                )
                # Enregistrer immédiatement dans le resolver (sans restart)
                await resolver.register_workspace(ws)
                logger.info("MCP: workspace créé: %s @ %s", ws.workspace_id, storage_path)
                return json.dumps({
                    "workspace_id": ws.workspace_id,
                    "name": ws.name,
                    "storage_path": ws.storage_path,
                    "conversations": ws.conversations,
                    "status": "created",
                }, ensure_ascii=False)
            except Exception as e:
                logger.error("MCP colaig_create_workspace: %s", e)
                return json.dumps({"error": str(e)})

        # ── colaig_link_conversation ──────────────────────────────────

        @mcp.tool()
        async def colaig_link_conversation(
            workspace_id: str,
            conversation_id: str,
        ) -> str:
            """Lie un salon/conversation à un workspace Colaig existant.

            Ajoute conversation_id à la liste du workspace et met à jour
            immédiatement le cache du resolver (sans redémarrage).

            Args:
                workspace_id: ID du workspace cible.
                conversation_id: Identifiant du salon/canal (room Matrix, channel Slack…).

            Returns:
                JSON confirmant la liaison, ou error si workspace introuvable.
            """
            ws = _find_workspace(resolver, workspace_id)
            if ws is None:
                return json.dumps({"error": f"workspace '{workspace_id}' introuvable"})

            try:
                updated_ws = await add_conversation_to_workspace(
                    storage, ws.storage_path, conversation_id
                )
                # Mettre à jour le cache du resolver
                await resolver.register_workspace(updated_ws)
                return json.dumps({
                    "workspace_id": updated_ws.workspace_id,
                    "conversation_id": conversation_id,
                    "conversations": updated_ws.conversations,
                    "status": "linked",
                }, ensure_ascii=False)
            except Exception as e:
                logger.error("MCP colaig_link_conversation: %s", e)
                return json.dumps({"error": str(e)})

        # ── colaig_update_workspace ───────────────────────────────────

        @mcp.tool()
        async def colaig_update_workspace(
            workspace_id: str,
            name: str = "",
            description: str = "",
            system_prompt: str = "",
            tone: str = "",
            language: str = "",
            rag_enabled: bool | None = None,
            similarity_threshold: float | None = None,
            max_results: int | None = None,
        ) -> str:
            """Met à jour la configuration d'un workspace Colaig existant.

            Seuls les champs fournis (non vides/non None) sont modifiés.
            La mise à jour est immédiatement prise en compte par le resolver.

            Args:
                workspace_id: ID du workspace à mettre à jour.
                name: Nouveau nom affiché.
                description: Nouvelle description.
                system_prompt: Nouveau prompt système.
                tone: Nouveau ton (professional, casual, formal, technical).
                language: Nouvelle langue (fr, en…).
                rag_enabled: Activer/désactiver le RAG.
                similarity_threshold: Seuil de similarité RAG (0.0–1.0).
                max_results: Nombre max de résultats RAG.

            Returns:
                JSON avec la config mise à jour, ou error si workspace introuvable.
            """
            ws = _find_workspace(resolver, workspace_id)
            if ws is None:
                return json.dumps({"error": f"workspace '{workspace_id}' introuvable"})

            # Construire le dict des champs à modifier (seulement ceux fournis)
            updates: dict = {}
            if name:
                updates["name"] = name
            if description:
                updates["description"] = description
            if system_prompt:
                updates["system_prompt"] = system_prompt
            if tone:
                updates["tone"] = tone
            if language:
                updates["language"] = language
            if rag_enabled is not None:
                updates["rag_enabled"] = rag_enabled
            if similarity_threshold is not None:
                updates["similarity_threshold"] = similarity_threshold
            if max_results is not None:
                updates["max_results"] = max_results

            if not updates:
                return json.dumps({"warning": "aucun champ à mettre à jour"})

            try:
                updated_ws = await update_workspace_config(storage, ws.storage_path, **updates)
                await resolver.register_workspace(updated_ws)
                return json.dumps({
                    "workspace_id": updated_ws.workspace_id,
                    "updated_fields": list(updates.keys()),
                    "status": "updated",
                }, ensure_ascii=False)
            except Exception as e:
                logger.error("MCP colaig_update_workspace: %s", e)
                return json.dumps({"error": str(e)})

        # ── colaig_get_config ─────────────────────────────────────────

        @mcp.tool()
        async def colaig_get_config() -> str:
            """Retourne la configuration actuelle du déploiement Colaig.

            Accès restreint aux administrateurs (COLAIG_ADMIN_USER_IDS) —
            la réponse contient des informations sensibles (backends, credentials).

            Returns:
                JSON avec storage, messaging, llm (backend + statut) et workspaces.
            """
            from colaig.auth.tokens import get_current_token, require_admin
            if err := require_admin(get_current_token()):
                return err

            cfg = self._config
            runtime = config_store.get_all() if config_store else {}

            # Infos storage
            storage_info: dict = {
                "backend": cfg.storage_backend,
                "runtime_override": "storage" in runtime,
            }
            if cfg.storage_backend == "webdav":
                storage_info["url"] = cfg.webdav_url
                storage_info["username"] = cfg.webdav_username
            elif cfg.storage_backend == "local":
                storage_info["path"] = cfg.local_storage_path
            elif cfg.storage_backend == "s3":
                storage_info["endpoint_url"] = cfg.s3_endpoint_url
                storage_info["bucket"] = cfg.s3_bucket_name
            elif cfg.storage_backend == "msgraph":
                storage_info["tenant_id"] = cfg.msgraph_tenant_id
                storage_info["drive_user_id"] = cfg.msgraph_drive_user_id

            # Infos messaging
            messaging_info: dict = {
                "backend": cfg.messaging_backend,
                "runtime_override": "messaging" in runtime,
            }
            if cfg.messaging_backend == "matrix":
                messaging_info["homeserver"] = cfg.matrix_homeserver
                messaging_info["username"] = cfg.matrix_username

            # Infos LLM
            llm_backend = cfg.llm_backend or "albert"
            llm_info: dict = {
                "backend": llm_backend,
                "runtime_override": "llm" in runtime,
            }
            if llm_backend == "albert":
                llm_info["api_url"] = cfg.albert_api_url
                llm_info["model_chat"] = cfg.albert_model_chat
                llm_info["model_embed"] = cfg.albert_model_embed
            else:
                llm_info["api_url"] = cfg.llm_api_url
                llm_info["model_chat"] = cfg.llm_model_chat

            # Infos workspaces
            workspaces = resolver.workspaces
            total_vectors = sum(
                ws_store.vector_count
                for ws_store in workspace_stores.values()
                if hasattr(ws_store, "vector_count")
            )
            ws_info = {
                "count": len(workspaces),
                "ids": [ws.workspace_id for ws in workspaces],
                "total_vectors": total_vectors,
            }

            result = {
                "storage": storage_info,
                "messaging": messaging_info,
                "llm": llm_info,
                "workspaces": ws_info,
                "mcp_enabled": cfg.mcp_enabled,
                "agents_enabled": cfg.agents_enabled,
                "runtime_yml": str(config_store.path) if config_store else None,
            }
            return json.dumps(result, ensure_ascii=False, default=str)

        # ── colaig_test_backend ───────────────────────────────────────

        @mcp.tool()
        async def colaig_test_backend(
            type: str,
            backend: str,
            credentials: str,
        ) -> str:
            """Teste les credentials d'un backend Colaig sans sauvegarder.

            Args:
                type: Type de backend : "storage" | "messaging" | "llm".
                backend: Nom du backend (webdav, local, s3, msgraph, matrix, albert, openai, ollama…).
                credentials: JSON des credentials spécifiques au provider.
                    storage webdav   : {"url": "...", "username": "...", "password": "..."}
                    storage local    : {"path": "/data/storage"}
                    storage s3       : {"endpoint_url": "...", "access_key": "...", "secret_key": "...", "bucket": "..."}
                    storage msgraph  : {"tenant_id": "...", "client_id": "...", "client_secret": "...", "drive_user_id": "..."}
                    messaging matrix : {"homeserver": "...", "username": "...", "password": "..."}
                    messaging telegram : {"bot_token": "..."}
                    llm albert/openai : {"api_url": "...", "api_key": "...", "model_chat": "..."}
                    llm ollama        : {"api_url": "http://localhost:11434", "model_chat": "llama3"}

            Returns:
                JSON avec status ("ok"/"error"), latency_ms, error si échec.
            """
            try:
                creds = json.loads(credentials) if credentials else {}
            except json.JSONDecodeError as e:
                return json.dumps({"status": "error", "error": f"credentials JSON invalide: {e}"})

            ok, error, latency = await _test_backend(type, backend, creds)
            result: dict = {"status": "ok" if ok else "error", "latency_ms": latency}
            if not ok:
                result["error"] = error
            return json.dumps(result, ensure_ascii=False)

        # ── colaig_set_backend ────────────────────────────────────────

        @mcp.tool()
        async def colaig_set_backend(
            type: str,
            backend: str,
            credentials: str,
        ) -> str:
            """Configure un backend Colaig (storage, messaging ou llm).

            Accès restreint aux administrateurs (COLAIG_ADMIN_USER_IDS).

            Teste la connexion AVANT de sauvegarder. Si le test échoue,
            rien n'est persisté. Sauvegarde dans config/runtime.yml, qui
            est chargé au prochain démarrage avec priorité maximale sur .env.

            Args:
                type: Type de backend : "storage" | "messaging" | "llm".
                backend: Nom du backend.
                credentials: JSON des credentials (voir colaig_test_backend pour le format).

            Returns:
                JSON avec status, tested (ok/error), saved (bool), restart_required (bool).
            """
            from colaig.auth.tokens import get_current_token, require_admin
            if err := require_admin(get_current_token()):
                return err

            if config_store is None:
                return json.dumps({"status": "error", "error": "config_store non disponible"})

            try:
                creds = json.loads(credentials) if credentials else {}
            except json.JSONDecodeError as e:
                return json.dumps({"status": "error", "error": f"credentials JSON invalide: {e}"})

            # Test de connexion
            ok, error, latency = await _test_backend(type, backend, creds)
            if not ok:
                return json.dumps({
                    "status": "error",
                    "tested": "error",
                    "error": error,
                    "latency_ms": latency,
                    "saved": False,
                })

            # Sauvegarde dans runtime.yml
            section_data = {"backend": backend, **creds}
            config_store.set(type, section_data)

            # Messaging nécessite un redémarrage (boucle asyncio en cours)
            restart_required = type == "messaging"

            logger.info(
                "colaig_set_backend: %s backend '%s' configuré et sauvegardé",
                type, backend,
            )
            return json.dumps({
                "status": "ok",
                "tested": "ok",
                "latency_ms": latency,
                "saved": True,
                "saved_to": str(config_store.path),
                "restart_required": restart_required,
                "note": "Redémarrage requis pour activer le nouveau canal de messagerie." if restart_required
                        else "Configuration appliquée au prochain démarrage.",
            }, ensure_ascii=False)

        # ── colaig_onboard ────────────────────────────────────────

        @mcp.tool()
        async def colaig_onboard(ctx: Context) -> str:
            """Onboarding interactif Colaig — configure storage, messaging et LLM pas à pas.

            Accès restreint aux administrateurs (COLAIG_ADMIN_USER_IDS).

            Si le client MCP supporte les élicitations (Claude Desktop, Cursor…),
            affiche des formulaires natifs pour saisir les credentials.
            Sinon, retourne des instructions textuelles pour utiliser colaig_set_backend.

            Étapes :
              1. Backend de stockage (webdav, local, s3, msgraph, bigfolder, box)
              2. Canal de messagerie (matrix, telegram, none)
              3. LLM (albert, openai, ollama)

            Chaque étape teste la connexion avant de sauvegarder dans config/runtime.yml.
            """
            from colaig.auth.tokens import get_current_token, require_admin
            if err := require_admin(get_current_token()):
                return err

            # Vérifier le support des élicitations
            has_elicitation = False
            try:
                has_elicitation = ctx.session.check_client_capability(
                    mcp_types.ClientCapabilities(
                        elicitation=mcp_types.ElicitationCapability()
                    )
                )
            except Exception:
                pass

            if not has_elicitation:
                return _onboarding_text_instructions(config_store)

            results: dict = {}

            # ── Étape 1 : Storage ──────────────────────────────────
            r = await ctx.session.elicit(
                message=(
                    "Étape 1/3 — Stockage de documents\n\n"
                    "Quel backend souhaitez-vous utiliser ?"
                ),
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "backend": {
                            "type": "string",
                            "description": "webdav | local | s3 | msgraph | bigfolder | box",
                        }
                    },
                    "required": ["backend"],
                },
            )
            if r.action != "accept" or not r.content:
                return json.dumps({"status": "annulé", "step": "storage"})

            storage_backend = r.content.get("backend", "").strip()
            creds_schema = _get_storage_schema(storage_backend)

            if creds_schema:
                rc = await ctx.session.elicit(
                    message=f"Credentials pour {storage_backend}",
                    requestedSchema=creds_schema,
                )
                if rc.action != "accept" or not rc.content:
                    return json.dumps({"status": "annulé", "step": "storage_creds"})

                ok, err, latency = await _test_backend("storage", storage_backend, rc.content)
                if not ok:
                    return json.dumps({"status": "error", "step": "storage", "error": err, "latency_ms": latency})

                if config_store:
                    config_store.set("storage", {"backend": storage_backend, **rc.content})
                results["storage"] = {"backend": storage_backend, "tested": "ok", "latency_ms": latency}

            # ── Étape 2 : Messaging ────────────────────────────────
            r2 = await ctx.session.elicit(
                message=(
                    "Étape 2/3 — Canal de messagerie\n\n"
                    "Quel canal souhaitez-vous connecter ?"
                ),
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "backend": {
                            "type": "string",
                            "description": "matrix (Tchap) | telegram | none (MCP seulement)",
                        },
                        "skip": {
                            "type": "boolean",
                            "description": "true pour passer cette étape",
                        },
                    },
                    "required": ["backend"],
                },
            )
            if r2.action != "accept" or not r2.content:
                return json.dumps({"status": "annulé", "step": "messaging"})

            msg_backend = r2.content.get("backend", "").strip()
            msg_skip = r2.content.get("skip", False)

            if not msg_skip and msg_backend and msg_backend != "none":
                msg_schema = _get_messaging_schema(msg_backend)
                if msg_schema:
                    rc2 = await ctx.session.elicit(
                        message=f"Credentials pour {msg_backend}",
                        requestedSchema=msg_schema,
                    )
                    if rc2.action == "accept" and rc2.content:
                        ok, err, latency = await _test_backend("messaging", msg_backend, rc2.content)
                        if not ok:
                            return json.dumps({"status": "error", "step": "messaging", "error": err})
                        if config_store:
                            config_store.set("messaging", {"backend": msg_backend, **rc2.content})
                        results["messaging"] = {"backend": msg_backend, "tested": "ok", "latency_ms": latency}

            # ── Étape 3 : LLM ─────────────────────────────────────
            r3 = await ctx.session.elicit(
                message=(
                    "Étape 3/3 — Modèle de langage\n\n"
                    "Quel backend LLM souhaitez-vous utiliser ?"
                ),
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "backend": {
                            "type": "string",
                            "description": "albert (Etalab — recommandé) | openai | ollama (local)",
                        },
                        "skip": {
                            "type": "boolean",
                            "description": "true pour conserver la config LLM actuelle",
                        },
                    },
                    "required": ["backend"],
                },
            )
            if r3.action != "accept" or not r3.content:
                return json.dumps({"status": "annulé", "step": "llm"})

            llm_backend = r3.content.get("backend", "").strip()
            llm_skip = r3.content.get("skip", False)

            if not llm_skip and llm_backend:
                llm_schema = _get_llm_schema(llm_backend)
                if llm_schema:
                    rc3 = await ctx.session.elicit(
                        message=f"Credentials pour {llm_backend}",
                        requestedSchema=llm_schema,
                    )
                    if rc3.action == "accept" and rc3.content:
                        ok, err, latency = await _test_backend("llm", llm_backend, rc3.content)
                        if not ok:
                            return json.dumps({"status": "error", "step": "llm", "error": err})
                        if config_store:
                            config_store.set("llm", {"backend": llm_backend, **rc3.content})
                        results["llm"] = {"backend": llm_backend, "tested": "ok", "latency_ms": latency}

            return json.dumps({
                "status": "ok",
                "configured": results,
                "saved_to": str(config_store.path) if config_store else None,
                "message": "Configuration sauvegardée. Redémarrez Colaig pour l'activer.",
            }, ensure_ascii=False)

    # =========================================================================
    # Resources
    # =========================================================================

    def _register_resources(self) -> None:
        resolver = self._resolver
        document_index = self._document_index
        config_store = self._config_store
        config = self._config

        @self.mcp.resource("colaig://workspaces")
        async def workspaces_resource() -> str:
            """Liste des workspaces Colaig."""
            output = [
                {"workspace_id": ws.workspace_id, "name": ws.name}
                for ws in resolver.workspaces
            ]
            return json.dumps(output, ensure_ascii=False)

        @self.mcp.resource("colaig://onboarding/status")
        async def onboarding_status_resource() -> str:
            """État de la configuration Colaig — ce qui est configuré, ce qui manque, prochaine étape."""
            cfg = config
            configured = []
            missing = []

            # Storage
            storage_ok = (
                cfg.storage_backend == "webdav" and cfg.webdav_url
                or cfg.storage_backend == "local" and cfg.local_storage_path
                or cfg.storage_backend == "s3" and cfg.s3_endpoint_url
                or cfg.storage_backend == "msgraph" and cfg.msgraph_tenant_id
                or cfg.storage_backend == "bigfolder" and cfg.bigfolder_api_url
                or cfg.storage_backend == "box" and (cfg.box_config_file or cfg.box_client_id)
            )
            if storage_ok:
                configured.append({"type": "storage", "backend": cfg.storage_backend})
            else:
                missing.append({"type": "storage", "description": "Backend de stockage non configuré"})

            # Messaging
            messaging_ok = (
                cfg.messaging_backend == "matrix" and cfg.matrix_homeserver
                or cfg.messaging_backend == "telegram" and cfg.telegram_bot_token
            )
            if messaging_ok:
                configured.append({"type": "messaging", "backend": cfg.messaging_backend})
            else:
                missing.append({"type": "messaging", "description": "Canal de messagerie non configuré (optionnel si MCP only)"})

            # LLM
            llm_ok = (
                cfg.llm_backend == "albert" and cfg.albert_api_key
                or cfg.llm_backend in ("openai", "azure", "ollama") and cfg.llm_api_url
            )
            if llm_ok:
                configured.append({"type": "llm", "backend": cfg.llm_backend})
            else:
                missing.append({"type": "llm", "description": "LLM non configuré"})

            critical_missing = [m for m in missing if m["type"] != "messaging"]
            if critical_missing:
                m = critical_missing[0]
                next_step = (
                    f"Appelez colaig_onboard (interactif) ou "
                    f"colaig_set_backend(type='{m['type']}', ...) pour configurer le {m['type']}."
                )
            elif missing:
                next_step = (
                    "Appelez colaig_onboard pour configurer la messagerie, "
                    "ou passez directement à colaig_create_workspace."
                )
            else:
                next_step = "Configuration complète. Créez votre premier workspace avec colaig_create_workspace."

            return json.dumps({
                "configured": configured,
                "missing": missing,
                "ready": len(critical_missing) == 0,
                "next_step": next_step,
                "workspaces_count": len(resolver.workspaces),
                "runtime_yml": str(config_store.path) if config_store else None,
            }, ensure_ascii=False)

        @self.mcp.resource("colaig://onboarding/backends")
        async def onboarding_backends_resource() -> str:
            """Backends disponibles pour storage, messaging et LLM avec champs requis et exemples."""
            backends = {
                "storage": {
                    "webdav": {
                        "description": "WebDAV (Nextcloud, Bnum, tout serveur WebDAV)",
                        "fields": ["url", "username", "password"],
                        "example": {
                            "url": "https://nextcloud.example.fr/remote.php/dav/files/colaig/",
                            "username": "colaig",
                            "password": "xxx",
                        },
                    },
                    "local": {
                        "description": "Stockage local sur le filesystem (dev/tests)",
                        "fields": ["path"],
                        "example": {"path": "/app/data/storage"},
                    },
                    "s3": {
                        "description": "S3 compatible (MinIO, AWS S3, OVH Object Storage…)",
                        "fields": ["endpoint_url", "access_key", "secret_key", "bucket", "region"],
                        "example": {
                            "endpoint_url": "https://s3.example.fr",
                            "access_key": "xxx",
                            "secret_key": "xxx",
                            "bucket": "colaig",
                            "region": "us-east-1",
                        },
                    },
                    "msgraph": {
                        "description": "Microsoft Graph (OneDrive, SharePoint)",
                        "fields": ["tenant_id", "client_id", "client_secret", "drive_user_id"],
                        "example": {
                            "tenant_id": "xxx",
                            "client_id": "xxx",
                            "client_secret": "xxx",
                            "drive_user_id": "user@example.com",
                        },
                    },
                    "bigfolder": {
                        "description": "Bigfolder / Archivist API (multi-provider)",
                        "fields": ["api_url", "api_key", "workspace_id"],
                        "example": {
                            "api_url": "http://localhost:8002",
                            "api_key": "ark_xxx",
                            "workspace_id": "xxx",
                        },
                    },
                    "box": {
                        "description": "Box (JWT Server Authentication — entreprise)",
                        "fields": ["config_file"],
                        "example": {"config_file": "/app/secrets/box_config.json"},
                    },
                },
                "messaging": {
                    "matrix": {
                        "description": "Matrix / Tchap (administration française)",
                        "fields": ["homeserver", "username", "password"],
                        "example": {
                            "homeserver": "https://matrix.agent.tchap.gouv.fr",
                            "username": "@colaig:agent.tchap.gouv.fr",
                            "password": "xxx",
                        },
                    },
                    "telegram": {
                        "description": "Telegram Bot API",
                        "fields": ["bot_token"],
                        "example": {"bot_token": "1234567890:AAH..."},
                    },
                    "none": {
                        "description": "Sans messagerie (accès MCP uniquement)",
                        "fields": [],
                        "example": {},
                    },
                },
                "llm": {
                    "albert": {
                        "description": "Albert API (Etalab/DINUM — recommandé pour l'administration française)",
                        "fields": ["api_url", "api_key", "model_chat", "model_embed"],
                        "example": {
                            "api_url": "https://albert.api.etalab.gouv.fr",
                            "api_key": "sk-xxx",
                            "model_chat": "openai/gpt-oss-120b",
                            "model_embed": "BAAI/bge-m3",
                        },
                    },
                    "openai": {
                        "description": "OpenAI ou compatible (Mistral, Groq, Together…)",
                        "fields": ["api_url", "api_key", "model_chat", "model_embed"],
                        "example": {
                            "api_url": "https://api.openai.com",
                            "api_key": "sk-xxx",
                            "model_chat": "gpt-4o",
                            "model_embed": "text-embedding-3-small",
                        },
                    },
                    "ollama": {
                        "description": "Ollama (inférence locale, air-gap)",
                        "fields": ["api_url", "model_chat", "model_embed"],
                        "example": {
                            "api_url": "http://localhost:11434",
                            "model_chat": "llama3",
                            "model_embed": "nomic-embed-text",
                        },
                    },
                },
            }
            return json.dumps(backends, ensure_ascii=False, indent=2)

        # Resources par workspace
        for ws in resolver.workspaces:
            self._register_workspace_resources(ws, document_index)

    def _register_workspace_resources(self, ws, document_index) -> None:
        """Enregistre les resources MCP pour un workspace."""
        ws_id = ws.workspace_id
        ws_data = {
            "workspace_id": ws_id,
            "name": ws.name,
            "description": ws.description,
            "rag_enabled": ws.rag_enabled,
            "tone": ws.tone,
            "language": ws.language,
            "tools_enabled": ws.tools_enabled,
        }
        ws_path = ws.storage_path

        @self.mcp.resource(f"colaig://workspace/{ws_id}/config")
        async def workspace_config_resource() -> str:
            """Configuration d'un workspace Colaig."""
            return json.dumps(ws_data, ensure_ascii=False)

        if document_index is not None:
            @self.mcp.resource(f"colaig://workspace/{ws_id}/documents")
            async def workspace_documents_resource() -> str:
                """DocumentIndex d'un workspace — liste les documents analysés."""
                docs = await document_index.list_documents(ws_path, limit=200)
                return json.dumps(
                    [_doc_to_dict(d) for d in docs],
                    ensure_ascii=False,
                )

    # =========================================================================
    # Prompts
    # =========================================================================

    def _register_prompts(self) -> None:

        @self.mcp.prompt()
        async def workspace_assistant(workspace_id: str = "", question: str = "") -> str:
            """Prompt contextuel pour un assistant workspace Colaig.

            Args:
                workspace_id: ID du workspace pour contextualiser.
                question: Question optionnelle à inclure dans le prompt.
            """
            ws = None
            for w in self._resolver.workspaces:
                if w.workspace_id == workspace_id:
                    ws = w
                    break

            if ws:
                prompt = (
                    f"Tu es Colaig, un assistant IA pour le workspace '{ws.name}'.\n"
                    f"Description : {ws.description}\n"
                    f"Langue : {ws.language}\n"
                    f"Ton : {ws.tone}\n"
                )
                if ws.system_prompt:
                    prompt += f"\nConsignes : {ws.system_prompt}\n"
            else:
                prompt = "Tu es Colaig, un assistant IA généraliste.\n"

            if question:
                prompt += f"\nQuestion : {question}"

            return prompt

        @self.mcp.prompt()
        async def onboarding_guide() -> str:
            """Guide d'onboarding Colaig — contextualise le LLM pour guider l'utilisateur.

            Lit l'état de configuration actuel et retourne un prompt système qui
            oriente le LLM vers les bonnes actions selon ce qui est manquant.
            """
            cfg = self._config
            configured = []
            missing = []

            storage_ok = bool(
                (cfg.storage_backend == "webdav" and cfg.webdav_url)
                or (cfg.storage_backend == "local" and cfg.local_storage_path)
                or (cfg.storage_backend == "s3" and cfg.s3_endpoint_url)
                or (cfg.storage_backend == "msgraph" and cfg.msgraph_tenant_id)
                or (cfg.storage_backend == "bigfolder" and cfg.bigfolder_api_url)
                or (cfg.storage_backend == "box" and (cfg.box_config_file or cfg.box_client_id))
            )
            if storage_ok:
                configured.append(f"storage ({cfg.storage_backend})")
            else:
                missing.append("storage")

            llm_ok = bool(
                (cfg.llm_backend == "albert" and cfg.albert_api_key)
                or (cfg.llm_backend in ("openai", "azure", "ollama") and cfg.llm_api_url)
            )
            if llm_ok:
                configured.append(f"llm ({cfg.llm_backend})")
            else:
                missing.append("llm")

            messaging_ok = bool(
                (cfg.messaging_backend == "matrix" and cfg.matrix_homeserver)
                or (cfg.messaging_backend == "telegram" and cfg.telegram_bot_token)
            )
            if messaging_ok:
                configured.append(f"messaging ({cfg.messaging_backend})")
            else:
                missing.append("messaging (optionnel)")

            ws_count = len(self._resolver.workspaces)
            configured_str = ", ".join(configured) if configured else "aucun"
            missing_str = ", ".join(missing) if missing else "aucun"

            if missing and missing[0] != "messaging (optionnel)":
                action = (
                    "Propose d'utiliser `colaig_onboard` pour un onboarding guidé pas à pas, "
                    "ou `colaig_set_backend` pour configurer directement chaque composant."
                )
            elif ws_count == 0:
                action = (
                    "La config technique est complète. "
                    "Propose de créer le premier workspace avec `colaig_create_workspace`."
                )
            else:
                action = (
                    f"Colaig est opérationnel avec {ws_count} workspace(s). "
                    "Tu peux répondre aux questions via `colaig_ask` ou explorer les documents."
                )

            return (
                "Tu es l'assistant de configuration et d'onboarding de Colaig.\n\n"
                f"État de la configuration :\n"
                f"  Configuré : {configured_str}\n"
                f"  Manquant   : {missing_str}\n"
                f"  Workspaces : {ws_count}\n\n"
                f"Prochaine action recommandée : {action}\n\n"
                "Outils disponibles :\n"
                "  colaig_onboard         — Onboarding interactif (élicitations natives)\n"
                "  colaig_get_config      — Voir la configuration actuelle\n"
                "  colaig_test_backend    — Tester des credentials sans sauvegarder\n"
                "  colaig_set_backend     — Configurer storage / messaging / llm\n"
                "  colaig_create_workspace — Créer un espace documentaire\n"
                "  colaig_list_workspaces  — Lister les workspaces existants\n"
                "  colaig_ask             — Poser une question à Colaig (pipeline RAG)\n\n"
                "Ressources disponibles :\n"
                "  colaig://onboarding/status   — État de configuration détaillé\n"
                "  colaig://onboarding/backends — Backends disponibles et leurs champs\n"
                "  colaig://workspaces          — Liste des workspaces\n\n"
                "Guide l'utilisateur avec bienveillance et précision. "
                "Si une information manque, utilise les outils pour la récupérer avant de répondre."
            )


# =============================================================================
# Helpers module-level
# =============================================================================


def _find_workspace(resolver, workspace_id: str):
    """Retourne le workspace correspondant à l'ID, ou None."""
    for ws in resolver.workspaces:
        if ws.workspace_id == workspace_id:
            return ws
    return None


def _user_can_access_workspace(ws, user_id: str, auth_enabled: bool) -> bool:
    """Vérifie si l'utilisateur a accès au workspace.

    Délègue à WorkspaceACL — source unique de vérité pour les contrôles d'accès.
    """
    from colaig.security.acl import WorkspaceACL
    return WorkspaceACL.can_access(ws, user_id, auth_enabled)


def _doc_to_dict(record: DocumentRecord) -> dict:
    """Sérialise un DocumentRecord en dict pour les listings MCP."""
    return {
        "path": record.path,
        "name": record.name,
        "size": record.size,
        "status": record.status.value,
        "category": record.ai_category,
        "summary": record.ai_summary,
        "keywords": record.ai_keywords,
        "language": record.ai_language,
        "analyzed_at": record.analyzed_at.isoformat() if record.analyzed_at else None,
    }


def _doc_to_dict_full(record: DocumentRecord) -> dict:
    """Sérialise un DocumentRecord complet (toutes les métadonnées IA)."""
    return {
        "path": record.path,
        "name": record.name,
        "size": record.size,
        "mime_type": record.mime_type,
        "checksum": record.checksum,
        "status": record.status.value,
        "ai_summary": record.ai_summary,
        "ai_category": record.ai_category,
        "ai_entities": record.ai_entities,
        "ai_keywords": record.ai_keywords,
        "ai_language": record.ai_language,
        "ai_doc_type": record.ai_doc_type,
        "chunk_count": record.chunk_count,
        "faiss_doc_id": record.faiss_doc_id,
        "indexed_at": record.indexed_at.isoformat() if record.indexed_at else None,
        "analyzed_at": record.analyzed_at.isoformat() if record.analyzed_at else None,
        "error_message": record.error_message,
    }


async def _test_backend(
    type: str, backend: str, creds: dict
) -> tuple[bool, str, int]:
    """Teste la connexion à un backend. Retourne (ok, error_msg, latency_ms)."""
    import time
    t0 = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    try:
        if type == "storage":
            return await _test_storage(backend, creds, _ms)
        elif type == "messaging":
            return await _test_messaging(backend, creds, _ms)
        elif type == "llm":
            return await _test_llm(backend, creds, _ms)
        else:
            return False, f"type inconnu: {type} (attendu: storage|messaging|llm)", _ms()
    except Exception as e:
        return False, str(e), _ms()


async def _test_storage(backend: str, creds: dict, ms) -> tuple[bool, str, int]:
    """Teste un backend storage en listant la racine."""
    try:
        if backend == "webdav":
            from colaig.integrations.storage.webdav import WebDAVStorage
            tmp = WebDAVStorage(
                base_url=creds.get("url", ""),
                username=creds.get("username", ""),
                password=creds.get("password", ""),
            )
            await tmp.list_files("/")

        elif backend == "local":
            from pathlib import Path as _Path

            from colaig.integrations.storage.local import LocalStorage
            p = creds.get("path", "")
            if not p:
                return False, "path requis pour le backend local", ms()
            _Path(p).mkdir(parents=True, exist_ok=True)
            tmp = LocalStorage(base_path=p)
            await tmp.list_files("/")

        elif backend == "s3":
            from colaig.integrations.storage.s3 import S3Storage
            tmp = S3Storage(
                endpoint_url=creds.get("endpoint_url"),
                access_key=creds.get("access_key"),
                secret_key=creds.get("secret_key"),
                bucket_name=creds.get("bucket"),
                prefix=creds.get("prefix", ""),
                region=creds.get("region", "us-east-1"),
            )
            await tmp.list_files("/")

        elif backend == "msgraph":
            from colaig.integrations.storage.msgraph import MicrosoftGraphStorage
            tmp = MicrosoftGraphStorage(
                tenant_id=creds.get("tenant_id", ""),
                client_id=creds.get("client_id", ""),
                client_secret=creds.get("client_secret", ""),
                drive_user_id=creds.get("drive_user_id", ""),
                drive_id=creds.get("drive_id", ""),
                root_path=creds.get("root_path", ""),
            )
            await tmp.list_files("/")

        elif backend == "bigfolder":
            from colaig.integrations.storage.bigfolder import BigfolderStorage
            tmp = BigfolderStorage(
                api_url=creds.get("api_url", ""),
                api_key=creds.get("api_key", ""),
                workspace_id=creds.get("workspace_id", ""),
            )
            await tmp.list_files("/")

        else:
            return False, f"backend storage inconnu: {backend}", ms()

        return True, "", ms()

    except Exception as e:
        return False, str(e), ms()


async def _test_messaging(backend: str, creds: dict, ms) -> tuple[bool, str, int]:
    """Teste un backend messaging via une authentification minimale."""
    import httpx

    try:
        if backend == "matrix":
            homeserver = creds.get("homeserver", "").rstrip("/")
            username = creds.get("username", "")
            password = creds.get("password", "")
            if not homeserver:
                return False, "homeserver requis", ms()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{homeserver}/_matrix/client/v3/login",
                    json={"type": "m.login.password", "user": username, "password": password},
                )
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}", ms()

        elif backend == "telegram":
            token = creds.get("bot_token", "")
            if not token:
                return False, "bot_token requis", ms()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}", ms()
                data = resp.json()
                if not data.get("ok"):
                    return False, data.get("description", "token invalide"), ms()

        elif backend == "none":
            pass  # NoopMessaging — toujours OK

        else:
            return False, f"backend messaging inconnu: {backend}", ms()

        return True, "", ms()

    except Exception as e:
        return False, str(e), ms()


async def _test_llm(backend: str, creds: dict, ms) -> tuple[bool, str, int]:
    """Teste un backend LLM via l'endpoint /v1/models ou /api/tags (Ollama)."""
    import httpx

    try:
        api_url = creds.get("api_url", "").rstrip("/")
        api_key = creds.get("api_key", "")

        if not api_url:
            return False, "api_url requis", ms()

        headers: dict = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if backend == "ollama":
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{api_url}/api/tags")
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}", ms()
        else:
            # OpenAI-compatible (albert, openai, azure, mistral...)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{api_url}/v1/models", headers=headers)
                # 404 = endpoint /v1/models non implémenté mais API accessible → OK
                if resp.status_code not in (200, 404):
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}", ms()

        return True, "", ms()

    except Exception as e:
        return False, str(e), ms()


# =============================================================================
# Onboarding helpers
# =============================================================================


def _get_storage_schema(backend: str) -> dict | None:
    """Retourne le JSON Schema des credentials pour un backend storage."""
    schemas: dict[str, dict] = {
        "webdav": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL WebDAV (ex: https://nextcloud.example.fr/remote.php/dav/files/colaig/)"},
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["url", "username", "password"],
        },
        "local": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du dossier de stockage"},
            },
            "required": ["path"],
        },
        "s3": {
            "type": "object",
            "properties": {
                "endpoint_url": {"type": "string", "description": "URL S3/MinIO"},
                "access_key": {"type": "string"},
                "secret_key": {"type": "string"},
                "bucket": {"type": "string"},
                "region": {"type": "string", "description": "Région (ex: us-east-1)"},
            },
            "required": ["endpoint_url", "access_key", "secret_key", "bucket"],
        },
        "msgraph": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "client_id": {"type": "string"},
                "client_secret": {"type": "string"},
                "drive_user_id": {"type": "string", "description": "Email ou ID utilisateur OneDrive"},
            },
            "required": ["tenant_id", "client_id", "client_secret", "drive_user_id"],
        },
        "bigfolder": {
            "type": "object",
            "properties": {
                "api_url": {"type": "string"},
                "api_key": {"type": "string"},
                "workspace_id": {"type": "string"},
            },
            "required": ["api_url", "api_key", "workspace_id"],
        },
        "box": {
            "type": "object",
            "properties": {
                "config_file": {"type": "string", "description": "Chemin vers le fichier JSON Box (ex: /app/secrets/box_config.json)"},
            },
            "required": ["config_file"],
        },
    }
    return schemas.get(backend)


def _get_messaging_schema(backend: str) -> dict | None:
    """Retourne le JSON Schema des credentials pour un backend messaging."""
    schemas: dict[str, dict] = {
        "matrix": {
            "type": "object",
            "properties": {
                "homeserver": {"type": "string", "description": "URL homeserver Matrix (ex: https://matrix.agent.tchap.gouv.fr)"},
                "username": {"type": "string", "description": "MXID (ex: @colaig:agent.tchap.gouv.fr)"},
                "password": {"type": "string"},
            },
            "required": ["homeserver", "username", "password"],
        },
        "telegram": {
            "type": "object",
            "properties": {
                "bot_token": {"type": "string", "description": "Token Telegram (ex: 1234567890:AAH...)"},
            },
            "required": ["bot_token"],
        },
    }
    return schemas.get(backend)


def _get_llm_schema(backend: str) -> dict | None:
    """Retourne le JSON Schema des credentials pour un backend LLM."""
    schemas: dict[str, dict] = {
        "albert": {
            "type": "object",
            "properties": {
                "api_url": {"type": "string", "description": "URL Albert API (ex: https://albert.api.etalab.gouv.fr)"},
                "api_key": {"type": "string", "description": "Clé API Albert"},
                "model_chat": {"type": "string", "description": "Modèle de chat (ex: openai/gpt-oss-120b)"},
                "model_embed": {"type": "string", "description": "Modèle d'embeddings (ex: BAAI/bge-m3)"},
            },
            "required": ["api_url", "api_key"],
        },
        "openai": {
            "type": "object",
            "properties": {
                "api_url": {"type": "string", "description": "URL API (ex: https://api.openai.com)"},
                "api_key": {"type": "string"},
                "model_chat": {"type": "string", "description": "Modèle chat (ex: gpt-4o)"},
                "model_embed": {"type": "string", "description": "Modèle embeddings (ex: text-embedding-3-small)"},
            },
            "required": ["api_url", "api_key"],
        },
        "ollama": {
            "type": "object",
            "properties": {
                "api_url": {"type": "string", "description": "URL Ollama (ex: http://localhost:11434)"},
                "model_chat": {"type": "string", "description": "Modèle Ollama (ex: llama3)"},
                "model_embed": {"type": "string", "description": "Modèle embeddings (ex: nomic-embed-text)"},
            },
            "required": ["api_url", "model_chat"],
        },
    }
    return schemas.get(backend)


def _onboarding_text_instructions(config_store) -> str:
    """Guidance JSON structuré pour onboarding sans élicitations.

    Retourne un JSON qui indique au LLM quelles étapes restent à compléter,
    quels champs collecter auprès de l'utilisateur, et quel tool appeler.
    Le LLM guide l'utilisateur pas à pas dans la conversation.
    """
    runtime_path = str(config_store.path) if config_store else "config/runtime.yml"

    # Lecture de l'état actuel
    storage_cfg: dict = config_store.get("storage") if config_store else {}
    messaging_cfg: dict = config_store.get("messaging") if config_store else {}
    llm_cfg: dict = config_store.get("llm") if config_store else {}

    already_configured: list[str] = []
    steps_remaining: list[dict] = []

    # ── Étape 1 : Storage ──────────────────────────────────────────────────
    if storage_cfg.get("backend"):
        already_configured.append(f"storage={storage_cfg['backend']}")
    else:
        steps_remaining.append({
            "step": "1/3 — Stockage",
            "ask_user": "Quel backend de stockage souhaitez-vous utiliser ?",
            "options": {
                "webdav": {
                    "description": "Nextcloud / Bnum (administration)",
                    "fields": {
                        "url": "URL WebDAV (ex: https://nextcloud.example.fr/remote.php/dav/files/colaig/)",
                        "username": "Identifiant",
                        "password": "Mot de passe",
                    },
                },
                "local": {
                    "description": "Filesystem local (développement / tests)",
                    "fields": {"path": "Chemin absolu (ex: /app/data/storage)"},
                },
                "s3": {
                    "description": "MinIO / S3",
                    "fields": {
                        "endpoint_url": "URL S3 (ex: http://minio:9000)",
                        "access_key": "Access key",
                        "secret_key": "Secret key",
                        "bucket": "Nom du bucket",
                    },
                },
                "msgraph": {
                    "description": "Microsoft OneDrive (via Graph API)",
                    "fields": {
                        "tenant_id": "Tenant ID Azure AD",
                        "client_id": "Client ID (app registration)",
                        "client_secret": "Client secret",
                        "drive_user_id": "Email ou ID utilisateur OneDrive",
                    },
                },
                "bigfolder": {
                    "description": "API Archivist (multi-provider)",
                    "fields": {
                        "api_url": "URL API Bigfolder",
                        "api_key": "Clé API (ark_...)",
                        "workspace_id": "ID workspace Bigfolder",
                    },
                },
                "box": {
                    "description": "Box.com (JWT service account)",
                    "fields": {
                        "config_file": "Chemin vers le JSON Box (ex: /app/secrets/box_config.json)",
                    },
                },
            },
            "tool_call": 'colaig_set_backend(type="storage", backend=<choix>, credentials=<dict JSON>)',
        })

    # ── Étape 2 : Messaging (optionnelle) ──────────────────────────────────
    if messaging_cfg.get("backend"):
        already_configured.append(f"messaging={messaging_cfg['backend']}")
    else:
        steps_remaining.append({
            "step": "2/3 — Messagerie (optionnelle)",
            "ask_user": (
                "Quel canal de messagerie souhaitez-vous connecter ? "
                "Répondez 'none' pour utiliser Colaig via MCP/web uniquement."
            ),
            "options": {
                "matrix": {
                    "description": "Matrix / Tchap (administration française)",
                    "fields": {
                        "homeserver": "URL homeserver (ex: https://matrix.agent.tchap.gouv.fr)",
                        "username": "MXID (ex: @colaig:agent.tchap.gouv.fr)",
                        "password": "Mot de passe",
                    },
                },
                "telegram": {
                    "description": "Bot Telegram",
                    "fields": {"bot_token": "Token BotFather (ex: 1234567890:AAH...)"},
                },
                "none": {
                    "description": "Pas de messagerie — interface MCP/web uniquement",
                    "fields": {},
                },
            },
            "tool_call": 'colaig_set_backend(type="messaging", backend=<choix>, credentials=<dict JSON ou {}>)',
        })

    # ── Étape 3 : LLM ──────────────────────────────────────────────────────
    if llm_cfg.get("backend"):
        already_configured.append(f"llm={llm_cfg['backend']}")
    else:
        steps_remaining.append({
            "step": "3/3 — Modèle de langage",
            "ask_user": "Quel backend LLM souhaitez-vous utiliser ?",
            "options": {
                "albert": {
                    "description": "Albert API — Etalab/DINUM (recommandé pour l'administration française)",
                    "fields": {
                        "api_url": "URL (ex: https://albert.api.etalab.gouv.fr)",
                        "api_key": "Clé API Albert",
                        "model_chat": "Modèle chat (optionnel, ex: openai/gpt-oss-120b)",
                        "model_embed": "Modèle embeddings (optionnel, ex: BAAI/bge-m3)",
                    },
                },
                "openai": {
                    "description": "OpenAI / Mistral / Groq (tout provider compatible OpenAI)",
                    "fields": {
                        "api_url": "URL API (ex: https://api.openai.com)",
                        "api_key": "Clé API",
                        "model_chat": "Modèle chat (ex: gpt-4o)",
                        "model_embed": "Modèle embeddings (ex: text-embedding-3-small)",
                    },
                },
                "ollama": {
                    "description": "Ollama — LLM local (aucune clé requise)",
                    "fields": {
                        "api_url": "URL Ollama (ex: http://localhost:11434)",
                        "model_chat": "Modèle (ex: llama3)",
                        "model_embed": "Modèle embeddings (ex: nomic-embed-text)",
                    },
                },
            },
            "tool_call": 'colaig_set_backend(type="llm", backend=<choix>, credentials=<dict JSON>)',
        })

    # ── Résumé ─────────────────────────────────────────────────────────────
    if not steps_remaining:
        return json.dumps({
            "status": "already_configured",
            "configured": already_configured,
            "next_action": "Appelez colaig_create_workspace pour créer un premier espace documentaire.",
        }, ensure_ascii=False)

    return json.dumps({
        "mode": "conversational",
        "instruction": (
            "Votre client MCP ne supporte pas les formulaires natifs (élicitations). "
            "Guidez l'utilisateur pas à pas : pour chaque étape, posez la question indiquée, "
            "collectez les informations, puis appelez le tool indiqué. "
            "Passez ensuite à l'étape suivante."
        ),
        "already_configured": already_configured,
        "steps_remaining": steps_remaining,
        "tip": "Utilisez colaig_test_backend pour tester des credentials sans les sauvegarder.",
        "runtime_config": runtime_path,
    }, ensure_ascii=False, indent=2)


async def _try_sampling_analysis(mcp: FastMCP, content: bytes, filename: str) -> dict | None:
    """Tente d'analyser un document via MCP sampling (client LLM).

    Le serveur MCP demande au client connecté (Claude Desktop, Cursor…)
    d'analyser le document et de retourner des métadonnées structurées JSON.

    Retourne None si :
    - Le client ne supporte pas le sampling
    - Le document n'a pas de texte extractible
    - Toute autre erreur

    Le fallback vers Albert est géré par l'appelant.
    """
    from colaig.utils.text import extract_text, is_supported

    if not is_supported(filename):
        return None

    text = extract_text(content, filename)
    if not text.strip():
        return None

    prompt = (
        f"Analyse ce document et retourne UNIQUEMENT un JSON valide selon ce schéma :\n"
        f"{_ANALYSIS_SCHEMA}\n\n"
        f"Document ({filename}) :\n{text[:4000]}"
    )

    try:
        # FastMCP expose le contexte de la requête courante via get_context()
        ctx = mcp.get_context()
        # Accès à la session MCP sous-jacente (ServerSession)
        session = getattr(getattr(ctx, "request_context", None), "session", None)
        if session is None:
            return None

        # Vérifier les capacités du client
        client_caps = getattr(
            getattr(session, "client_params", None), "capabilities", None
        )
        if client_caps is None or not getattr(client_caps, "sampling", None):
            logger.debug("sampling: client ne supporte pas la capacité sampling")
            return None

        # Envoyer la requête de sampling au client
        from mcp.types import CreateMessageRequestParams, SamplingMessage, TextContent

        result = await session.create_message(
            CreateMessageRequestParams(
                messages=[
                    SamplingMessage(
                        role="user",
                        content=TextContent(type="text", text=prompt),
                    )
                ],
                max_tokens=512,
            )
        )

        if result and hasattr(result, "content"):
            text_result = getattr(result.content, "text", None) or str(result.content)
            parsed = _parse_json_from_response(text_result)
            logger.debug(
                "sampling OK pour %s → catégorie=%s",
                filename, parsed.get("category", "?"),
            )
            return parsed

    except Exception as e:
        logger.debug("sampling non disponible ou échoué pour %s: %s", filename, e)

    return None
