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


def create_search_handler(retriever, workspace_id: str = "", store=None, bm25_store=None,
                          question_posee: str = "") -> Callable:
    """Crée un handler async pour l'outil search_documents.

    Args:
        retriever: Implémentation de RetrieverProtocol.
        workspace_id: ID du workspace pour le logging (optionnel).
        store: VectorStore spécifique au workspace (optionnel, pour isolation).
        bm25_store: Index lexical BM25 du workspace (optionnel). Sans lui,
            `retrieve()` reste purement vectoriel : la fusion RRF n'a pas lieu.
        question_posee: La question de l'usager, cherchee EN PLUS de ce que le
            modele demande. Voir la note dans le corps du handler.

    Returns:
        Callable async(query, k=5, threshold=0.3) -> str (JSON)
    """
    _store = store  # fermeture sur le store workspace-spécifique
    _bm25_store = bm25_store  # idem pour l'index lexical (recherche hybride)
    _question = (question_posee or '').strip()

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

        # `bm25_store` n'est passe que s'il existe : `RetrieverProtocol.retrieve`
        # ne le declare pas, et une implementation conforme au contrat le refuserait.
        kwargs: dict = dict(k=k_val, score_threshold=threshold_val, store=_store)
        if _bm25_store is not None:
            kwargs["bm25_store"] = _bm25_store

        # LA QUESTION POSEE EST TOUJOURS CHERCHEE, EN PLUS DE CE QUE LE MODELE DEMANDE.
        #
        # `query` est ecrite par le modele a chaque appel. Tant qu'elle etait la seule,
        # la recherche heritait de son instabilite : sur six campagnes du service, au
        # grain du passage, 51 cas voyaient l'article attendu TOUJOURS servi, 9 JAMAIS,
        # et 53 UNE FOIS SUR DEUX. La question de l'usager, elle, ne bouge pas.
        #
        # Elle ne remplace pas la requete du modele — celle-ci porte le vocabulaire du
        # domaine la ou l'usager emploie le sien — elle lui ajoute un socle.
        if _question and _question != query:
            lots = await retriever.retrieve_many([query, _question], **kwargs)
            resultats, vus = [], set()
            for lot in lots:
                for r in lot:
                    cle = (r.chunk.source_path, r.chunk.position)
                    if cle in vus:
                        continue
                    vus.add(cle)
                    resultats.append(r)
            results = resultats
        else:
            results = await retriever.retrieve(query, **kwargs)

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
