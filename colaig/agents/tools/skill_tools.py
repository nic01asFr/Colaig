"""
Colaig — Tool search_skill pour l'Orchestrateur (N1)

Permet à l'Orchestrateur de requêter sémantiquement les procédures et connaissances
métier stockées dans .colaig/skills/*.md du workspace.

Deux modes :
- Sémantique : si albert (embed) + index_registry (FaissIndexRegistry) → cosine similarity
- Fallback keyword : scan .colaig/skills/*.md avec correspondance de mots-clés

Handler retourne un JSON string : {"found": bool, "skills": [{"name": str, "content": str}]}
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from colaig.models import ToolDefinition, ToolParameter
from colaig.rag.colaig_index import ColaigIndex

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


SEARCH_SKILL_DEFINITION = ToolDefinition(
    name="search_skill",
    description=(
        "Cherche une procédure ou connaissance métier dans les skills du workspace. "
        "Utilise cet outil avant de répondre à une question procédurale ou métier "
        "pour récupérer les instructions pertinentes (ex: formulaire, étape, politique). "
        "Retourne les skills les plus proches de la requête."
    ),
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Description de la procédure ou connaissance recherchée.",
            required=True,
        ),
        ToolParameter(
            name="k",
            type="integer",
            description="Nombre maximum de skills à retourner (défaut : 3).",
            required=False,
        ),
    ],
    category="knowledge",
)


def create_search_skill_handler(
    storage,
    workspace_path: str,
    albert=None,
    index_registry=None,
) -> Callable:
    """Construit le handler search_skill.

    Args:
        storage: StorageProtocol pour lire .colaig/skills/*.md.
        workspace_path: Chemin du workspace (ex: "/espace-rh/").
        albert: AlbertClientProtocol pour embed() — requis pour mode sémantique.
        index_registry: FaissIndexRegistry — requis pour mode sémantique.

    Returns:
        Handler async compatible ToolRegistry.
    """
    _storage = storage
    _ws_path = workspace_path.rstrip("/")
    _albert = albert
    _registry = index_registry

    async def _semantic_search(query: str, k: int) -> list[dict[str, str]]:
        """Recherche sémantique via FaissIndexRegistry."""
        query_emb = await _albert.embed(query)
        key = ColaigIndex.skills_key(_ws_path)
        hits = await _registry.search(key, query_emb, k=k)
        # hits → list[tuple[score, metadata_dict]] ou list[ChunkResult]
        results = []
        for hit in hits:
            if isinstance(hit, tuple):
                _score, meta = hit
                results.append({
                    "name": meta.get("source", "skill"),
                    "content": meta.get("text", ""),
                })
            else:
                # ChunkResult / SearchResult avec .text et .source
                results.append({
                    "name": getattr(hit, "source", "skill"),
                    "content": getattr(hit, "text", ""),
                })
        return [r for r in results if r["content"]]

    async def _keyword_fallback(query: str, k: int) -> list[dict[str, str]]:
        """Fallback : scan .colaig/skills/*.md avec correspondance de mots-clés."""
        skills_path = f"{_ws_path}/.colaig/skills"
        try:
            files = await _storage.list_files(skills_path)
        except Exception:
            return []

        query_words = set(query.lower().split())
        scored: list[tuple[int, str, str]] = []

        for f in files:
            path = getattr(f, "path", str(f))
            if not path.endswith(".md"):
                continue
            try:
                content_bytes = await _storage.download(path)
                content = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            content_lower = content.lower()
            score = sum(1 for w in query_words if w in content_lower)
            if score > 0:
                name = path.split("/")[-1].replace(".md", "")
                scored.append((score, name, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"name": name, "content": content}
            for _, name, content in scored[:k]
        ]

    async def handler(query: str = "", k: Any = 3, **kwargs) -> str:
        try:
            k_int = int(k) if k else 3
        except (TypeError, ValueError):
            k_int = 3

        if not query:
            return json.dumps({"found": False, "skills": [], "error": "query vide"})

        results: list[dict[str, str]] = []

        # Tentative sémantique
        if _albert is not None and _registry is not None:
            try:
                results = await _semantic_search(query, k_int)
            except Exception as e:
                logger.debug("search_skill: semantic search échoué, fallback keyword: %s", e)

        # Fallback keyword si semantic a échoué ou n'est pas configuré
        if not results:
            results = await _keyword_fallback(query, k_int)

        if results:
            logger.debug("search_skill: %d skill(s) trouvé(s) pour '%s'", len(results), query)
        else:
            logger.debug("search_skill: aucun skill trouvé pour '%s'", query)

        return json.dumps({
            "found": bool(results),
            "skills": results,
        }, ensure_ascii=False)

    return handler
