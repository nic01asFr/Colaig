# SPDX-License-Identifier: MIT
"""
Registre d'outils internes Colaig pour la boucle agent.

Chaque outil est un wrapper léger autour d'un service existant.
Les outils MCP sont ajoutés dynamiquement depuis le MCPRegistry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.matrix_bot.config import logger


@dataclass
class ToolDef:
    """Définition d'un outil pour le system prompt et l'exécution."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    require_confirmation: bool = False
    handler: Optional[Callable[..., Coroutine]] = field(default=None, repr=False)

    def to_prompt_block(self) -> str:
        """Ligne de description pour le system prompt."""
        params_desc = ""
        props = self.parameters.get("properties", {})
        if props:
            parts = []
            required = set(self.parameters.get("required", []))
            for pname, pschema in props.items():
                req = " (requis)" if pname in required else ""
                parts.append(f"    - {pname}: {pschema.get('description', pschema.get('type', ''))}{req}")
            params_desc = "\n" + "\n".join(parts)
        confirm = "  [confirmation requise]" if self.require_confirmation else ""
        return f"### {self.name}{confirm}\n{self.description}{params_desc}"

    def to_openai_tool(self) -> Dict[str, Any]:
        """Format OpenAI tool_use natif."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


class ToolRegistry:
    """Registre qui agrège les outils internes + MCP."""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    @property
    def all_tools(self) -> List[ToolDef]:
        return list(self._tools.values())

    def to_prompt(self) -> str:
        return "\n\n".join(t.to_prompt_block() for t in self._tools.values())

    def filter_in_place(self, keep_names) -> None:
        """Garde uniquement les outils dont le nom est dans keep_names."""
        keep_set = set(keep_names)
        self._tools = {
            name: tool for name, tool in self._tools.items()
            if name in keep_set
        }


# ─── Outils internes ──────────────────────────────────────────────────────────
#
# Signature uniforme : async def handler(args: dict, ctx: dict) -> str
# - args : arguments fournis par le LLM (validés contre le schéma JSON)
# - ctx  : contexte injecté par la boucle agent (config, services, etc.)
# Cette séparation évite les collisions de noms entre args LLM et contexte.

async def _handle_search_documents(args: dict, ctx: dict) -> str:
    """Recherche sémantique dans les documents indexés.

    Vérifie que l'index FAISS est prêt avant de lancer une recherche.
    Si pas d'index : retourne un message clair plutôt que de déclencher
    une réindexation synchrone (qui prendrait 30-60 secondes et bloquerait
    la boucle agent).
    """
    query = args.get("query", "")
    behavior_manager = ctx.get("behavior_manager")
    session_dict = ctx.get("session_dict", {})
    room_dict = ctx.get("room_dict", {})
    config = ctx.get("config")

    if not query:
        return "Erreur : paramètre 'query' requis."
    if not behavior_manager:
        return "Erreur : aucun gestionnaire de comportement disponible."

    # Vérifier si l'index documentaire est prêt avant de chercher.
    # Évite de déclencher un rebuild synchrone (téléchargement de tous les
    # documents WebDAV) qui bloquerait l'agent pendant 30-60 secondes.
    try:
        from app.services.index_service import get_index_service
        if config:
            index_svc = await get_index_service(config)
            if (not hasattr(index_svc, 'document_index')
                    or index_svc.document_index is None
                    or index_svc.document_index.faiss_index.index.ntotal == 0):
                return (
                    "Aucun document n'est indexé dans cet espace de travail. "
                    "Utilisez la commande !index rebuild pour indexer les documents."
                )
    except Exception:
        pass  # En cas d'erreur, on tente quand même la recherche

    try:
        result = await behavior_manager.execute_action(
            intent_name="standard_rag",
            query=query,
            context={**session_dict, **room_dict},
        )
        if result.get("success") and result.get("response"):
            return result["response"]
        return "Aucun résultat pertinent trouvé dans les documents indexés."
    except Exception as e:
        logger.warning(f"search_documents error: {e}")
        return f"Erreur lors de la recherche : {e}"


async def _handle_synthesize(args: dict, ctx: dict) -> str:
    """Génère une synthèse documentaire."""
    subject = args.get("subject", "")
    config = ctx.get("config")
    session_context = ctx.get("session_context_obj")
    room_context = ctx.get("room_context_obj")

    if not subject:
        return "Erreur : paramètre 'subject' requis."
    if not config:
        return "Erreur : configuration manquante."

    try:
        from app.actions.synthesis_rag_action import SynthesisRagAction
        async with SynthesisRagAction(config) as svc:
            result = await svc.process_synthesis_request(
                query=subject,
                session_context=session_context,
                room_context=room_context,
            )
            if isinstance(result, dict):
                return result.get("synthesis", "Synthèse vide.")
            return result or "Synthèse vide."
    except Exception as e:
        logger.warning(f"synthesize error: {e}")
        return f"Erreur lors de la synthèse : {e}"


async def _handle_manage_index(args: dict, ctx: dict) -> str:
    """Gère l'index documentaire."""
    action = args.get("action", "")
    config = ctx.get("config")

    if not action:
        return "Erreur : paramètre 'action' requis."
    if not config:
        return "Erreur : configuration manquante."

    try:
        from app.services.index_service import get_index_service
        index_service = await get_index_service(config)

        if action == "status":
            status = await index_service.get_status()
            return json.dumps(status, default=str, ensure_ascii=False, indent=2)
        elif action == "verify":
            is_fresh = await index_service.verify()
            return "Index à jour." if is_fresh else "Index obsolète — rebuild recommandé."
        elif action == "rebuild":
            await index_service.rebuild()
            return "Index reconstruit avec succès."
        elif action == "clean":
            await index_service.clean()
            return "Index nettoyé."
        return f"Action inconnue : {action}"
    except Exception as e:
        logger.warning(f"manage_index error: {e}")
        return f"Erreur index : {e}"


# ─── Construction du registre ─────────────────────────────────────────────────

def build_internal_tools() -> List[ToolDef]:
    """Retourne les outils internes Colaig."""
    return [
        ToolDef(
            name="search_documents",
            description=(
                "Recherche sémantique dans les documents indexés du workspace. "
                "Retourne les passages les plus pertinents avec leurs sources."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Question en langage naturel"},
                },
                "required": ["query"],
            },
            handler=_handle_search_documents,
        ),
        ToolDef(
            name="synthesize_documents",
            description=(
                "Génère une synthèse structurée à partir des documents indexés. "
                "Pour un sujet précis, appeler search_documents d'abord."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Sujet de la synthèse"},
                },
                "required": ["subject"],
            },
            handler=_handle_synthesize,
        ),
        ToolDef(
            name="manage_index",
            description="Gère l'index documentaire : consulter le statut, vérifier, reconstruire ou nettoyer.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "verify", "rebuild", "clean"],
                        "description": "Action à effectuer",
                    },
                },
                "required": ["action"],
            },
            require_confirmation=True,  # rebuild/clean sont destructifs
            handler=_handle_manage_index,
        ),
    ]


def build_registry(
    mcp_tools: list = None,
    has_doc_index: bool = False,
) -> ToolRegistry:
    """Construit le registre complet pour une session agent.

    Args:
        mcp_tools: Liste de MCPTool depuis le MCPRegistry.
        has_doc_index: True si le workspace a un index documentaire.
    """
    registry = ToolRegistry()

    # Outils internes : toujours exposés
    for tool in build_internal_tools():
        registry.register(tool)

    # Exposer chaque outil MCP individuellement avec son vrai schéma
    if mcp_tools:
        for mt in mcp_tools:
            # Skip si l'outil est destiné à l'UI uniquement
            if hasattr(mt, "annotations") and "assistant" not in mt.annotations.audience:
                continue
            registry.register(_make_mcp_tool_wrapper(mt))

    return registry


def _is_list_tool(tool_name: str) -> bool:
    """Heuristique : un outil est compactable s'il retourne une liste.

    Charge les hints depuis le YAML config (dynamique).
    """
    from app.agent.config_loader import get_compaction_config
    hints = get_compaction_config().get(
        "list_tool_hints",
        ["search_", "list_", "find_", "_search", "_list"],
    )
    short_name = tool_name.split("__", 1)[-1]  # 'datagouv__search_X' -> 'search_X'
    return any(hint in short_name for hint in hints)


def _make_mcp_tool_wrapper(mcp_tool) -> ToolDef:
    """Crée un ToolDef pour un MCPTool spécifique avec son vrai schéma.

    Le wrapper :
    - Délègue le cache au MCPRegistry (qui applique cache_scope du serveur)
    - Compacte les résultats des outils LISTE >1500 chars (P1)
    - Préserve intégralement les résultats des outils GET ciblés
    """
    qualified = mcp_tool.qualified_name  # ex: "datagouv__search_datasets"
    is_compactable = _is_list_tool(qualified)
    # Si l'annotation MCP `destructive` est présente, on désactive le cache
    # par défense (les outils destructifs ne doivent jamais être cachés).
    # Sinon, on laisse le registry décider selon cache_scope du serveur.
    is_destructive = (
        getattr(mcp_tool.annotations, "destructive", False)
        if hasattr(mcp_tool, "annotations") else False
    )

    async def handler(args: dict, ctx: dict) -> str:
        """Wrapper qualifié pour un outil MCP donné.

        Signature `(args, ctx)` : args = paramètres LLM purs (transmis au
        serveur MCP tels quels), ctx = contexte injecté par la boucle agent.
        Pas de risque de collision de noms.

        Le cache des résultats est géré par MCPRegistry selon le cache_scope
        déclaré pour chaque serveur (workspace par défaut, server pour les
        données publiques partagées).
        """
        from app.agent.compact import (
            compact_tool_result,
            compact_tool_result_async,
        )
        from app.agent.config_loader import get_compaction_config

        mcp_registry = ctx.get("mcp_registry")
        workspace_root = ctx.get("workspace_root", "")
        user_query = ctx.get("_user_query")

        if not mcp_registry:
            return "Erreur : registre MCP non disponible."

        try:
            # Le cache MCP est appliqué automatiquement par le registry
            # selon le cache_scope déclaré dans MCPServerConfig.
            # use_cache=False uniquement pour les outils marqués destructifs.
            result = await mcp_registry.call_tool(
                workspace_root, qualified, args,
                use_cache=not is_destructive,
            )
            if result.is_error:
                return f"Erreur MCP : {result.text}"

            raw_text = result.text or "Résultat vide."

            # P1 — Compaction conditionnelle :
            # - get_* / query_* (data ciblée) : préservés intégralement
            # - search_* / list_* (listings) : compactés
            #   * petit listing : troncature structurée
            #   * gros listing : résumé LLM si activé (préserve la diversité)
            comp_cfg = get_compaction_config()
            llm_summary_enabled = comp_cfg.get("llm_summary_enabled", False)
            llm_threshold = comp_cfg.get("llm_summary_threshold", 4000)
            config_obj = ctx.get("config")

            if (is_compactable and llm_summary_enabled and config_obj
                    and len(raw_text) > llm_threshold):
                logger.info(
                    f"[COMPACT] LLM summary pour {qualified} "
                    f"({len(raw_text)} chars)"
                )
                return await compact_tool_result_async(
                    raw_text,
                    config=config_obj,
                    use_llm_summary=True,
                    context_query=user_query,
                )
            return compact_tool_result(raw_text, compactable=is_compactable)
        except Exception as e:
            return f"Erreur MCP : {e}"

    # Utiliser le schéma de l'outil MCP directement
    schema = mcp_tool.input_schema or {"type": "object", "properties": {}}

    return ToolDef(
        name=qualified,
        description=mcp_tool.description or f"Outil MCP {qualified}",
        parameters=schema,
        require_confirmation=getattr(mcp_tool.annotations, "destructive", False) if hasattr(mcp_tool, "annotations") else False,
        handler=handler,
    )
