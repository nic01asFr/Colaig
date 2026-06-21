"""
agents/tools/rag_tools.py — Outil de recherche documentaire (RAG) pour l'Orchestrateur.

Outil : search_documents
Utilise : RetrieverProtocol
"""

from __future__ import annotations

import json
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter

# Définition de l'outil — schéma JSON OpenAI-compatible
SEARCH_DOCUMENTS_DEFINITION = ToolDefinition(
    name="search_documents",
    description=(
        "Recherche des documents pertinents dans l'index vectoriel du workspace "
        "en utilisant la recherche sémantique. Retourne les passages les plus proches "
        "de la requête avec leurs scores et sources."
    ),
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="La requête de recherche, reformulée pour maximiser la pertinence.",
            required=True,
        ),
        ToolParameter(
            name="k",
            type="integer",
            description="Nombre maximum de résultats à retourner (défaut : 5).",
            required=False,
        ),
        ToolParameter(
            name="threshold",
            type="number",
            description="Score minimum de similarité (0→1, défaut : 0.3).",
            required=False,
        ),
    ],
    category="rag",
)


def create_search_handler(retriever, workspace_id: str = "", store=None) -> Callable:
    """Crée un handler async pour l'outil search_documents.

    Args:
        retriever: Implémentation de RetrieverProtocol.
        workspace_id: ID du workspace pour le logging (optionnel).
        store: VectorStore spécifique au workspace (optionnel, pour isolation).

    Returns:
        Callable async(query, k=5, threshold=0.3) -> str (JSON)
    """
    _store = store  # fermeture sur le store workspace-spécifique

    async def search_handler(
        query: str,
        k: int | None = 5,
        threshold: float | None = 0.3,
    ) -> str:
        """Exécute une recherche RAG et retourne les résultats en JSON.

        Returns:
            JSON string avec liste de résultats :
            [{"text": ..., "source": ..., "score": ..., "page": ...}]
        """
        k_val = k if k is not None else 5
        threshold_val = threshold if threshold is not None else 0.3

        results = await retriever.retrieve(query, k=k_val, score_threshold=threshold_val, store=_store)

        serialized = []
        for r in results:
            serialized.append({
                "text": r.chunk.text[:1000],  # Troncature pour token budget
                "source": r.chunk.source_name or r.chunk.source_path,
                "score": round(r.score, 3),
                "page": r.chunk.page,
                "section": r.chunk.section,
            })

        return json.dumps(serialized, ensure_ascii=False)

    return search_handler
