"""
agents/tools/document_index_tools.py — Outils d'accès au DocumentIndex pour l'Orchestrateur.

Trois outils :
- search_document_index  : recherche sémantique sur documents entiers (≠ chunks RAG)
- list_document_index    : liste avec filtres structurés (catégorie, status, nom)
- get_document_metadata  : métadonnées IA complètes d'un fichier par son path
"""

from __future__ import annotations

import json
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter

# ── Définitions des outils (schémas JSON OpenAI-compatible) ──────────

SEARCH_DOCUMENT_INDEX_DEFINITION = ToolDefinition(
    name="search_document_index",
    description=(
        "Recherche des documents dans l'index documentaire enrichi du workspace. "
        "Contrairement à search_documents (recherche dans les passages/chunks), cet outil "
        "retourne des documents entiers avec leurs métadonnées IA (catégorie, entités, résumé, "
        "mots-clés). Utile pour découvrir quels documents traitent d'un sujet avant de les "
        "lire en détail avec fetch_document."
    ),
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="La requête de recherche sémantique.",
            required=True,
        ),
        ToolParameter(
            name="k",
            type="integer",
            description="Nombre maximum de résultats (défaut : 5).",
            required=False,
        ),
        ToolParameter(
            name="category",
            type="string",
            description=(
                "Filtrer par catégorie IA détectée. "
                "Valeurs courantes : procédure, rapport, guide, formulaire, "
                "compte-rendu, circulaire, note, courrier, autre."
            ),
            required=False,
        ),
        ToolParameter(
            name="status",
            type="string",
            description="Filtrer par statut d'analyse.",
            required=False,
            enum=["analyzed", "pending", "error"],
        ),
        ToolParameter(
            name="virtual_path_contains",
            type="string",
            description=(
                "Filtrer les documents dont le virtual_path contient cette chaîne. "
                "Exemple : '/Factures/' pour ne voir que les factures classées."
            ),
            required=False,
        ),
        ToolParameter(
            name="entity",
            type="string",
            description=(
                "Filtrer par entité IA au format 'cle:valeur'. "
                "Exemple : 'supplier:EDF' ou 'date:2024'. "
                "Matching case-insensitive avec containment."
            ),
            required=False,
        ),
    ],
    category="rag",
)

LIST_DOCUMENT_INDEX_DEFINITION = ToolDefinition(
    name="list_document_index",
    description=(
        "Liste les documents du workspace avec leurs métadonnées IA. "
        "Permet des filtres structurés par catégorie, statut ou nom. "
        "Utile pour explorer l'inventaire des documents sans requête sémantique."
    ),
    parameters=[
        ToolParameter(
            name="category",
            type="string",
            description="Filtrer par catégorie IA (ex: guide, rapport, formulaire).",
            required=False,
        ),
        ToolParameter(
            name="status",
            type="string",
            description="Filtrer par statut d'analyse.",
            required=False,
            enum=["analyzed", "pending", "error"],
        ),
        ToolParameter(
            name="name_contains",
            type="string",
            description="Filtrer les documents dont le nom contient cette chaîne (insensible à la casse).",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Nombre maximum de résultats (défaut : 20).",
            required=False,
        ),
    ],
    category="rag",
)

GET_DOCUMENT_METADATA_DEFINITION = ToolDefinition(
    name="get_document_metadata",
    description=(
        "Récupère les métadonnées IA complètes d'un document par son chemin exact. "
        "Retourne le résumé, la catégorie, les entités, les mots-clés et le statut d'analyse. "
        "Utile après search_document_index pour obtenir tous les détails d'un document spécifique."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Chemin complet du document dans le storage (ex: /espace-rh/guide.pdf).",
            required=True,
        ),
    ],
    category="rag",
)


# ── Factories de handlers ─────────────────────────────────────────────

def create_search_document_index_handler(
    document_index,
    workspace_path: str,
) -> Callable:
    """Crée le handler async pour search_document_index.

    Args:
        document_index: Implémentation de DocumentIndexProtocol.
        workspace_path: Chemin racine du workspace courant.

    Returns:
        Callable async(query, k=5, category=None, status=None) -> str (JSON)
    """

    async def handler(
        query: str,
        k: int | None = 5,
        category: str | None = None,
        status: str | None = None,
        virtual_path_contains: str | None = None,
        entity: str | None = None,
    ) -> str:
        k_val = k if k is not None else 5

        filters: dict = {}
        if category:
            filters["ai_category"] = category
        if status:
            filters["status"] = status
        if virtual_path_contains:
            filters["virtual_path_contains"] = virtual_path_contains
        if entity and ":" in entity:
            key, _, val = entity.partition(":")
            filters[f"entity.{key.strip()}"] = val.strip()

        results = await document_index.search(
            query=query,
            workspace_path=workspace_path,
            k=k_val,
            filters=filters or None,
        )

        serialized = []
        for r in results:
            rec = r.record
            serialized.append({
                "path": rec.path,
                "name": rec.name,
                "summary": rec.ai_summary,
                "category": rec.ai_category,
                "keywords": rec.ai_keywords,
                "entities": rec.ai_entities,
                "virtual_path": rec.virtual_path,
                "rule_applied": rec.rule_applied,
                "classification_confidence": rec.classification_confidence,
                "score": round(r.score, 3),
                "status": rec.status.value,
                "chunk_count": rec.chunk_count,
            })

        return json.dumps(serialized, ensure_ascii=False)

    return handler


def create_list_document_index_handler(
    document_index,
    workspace_path: str,
) -> Callable:
    """Crée le handler async pour list_document_index.

    Returns:
        Callable async(category=None, status=None, name_contains=None, limit=20) -> str (JSON)
    """

    async def handler(
        category: str | None = None,
        status: str | None = None,
        name_contains: str | None = None,
        limit: int | None = 20,
    ) -> str:
        filters: dict = {}
        if category:
            filters["ai_category"] = category
        if status:
            filters["status"] = status
        if name_contains:
            filters["name_contains"] = name_contains

        limit_val = limit if limit is not None else 20

        records = await document_index.list_documents(
            workspace_path=workspace_path,
            filters=filters or None,
            limit=limit_val,
        )

        serialized = []
        for rec in records:
            serialized.append({
                "path": rec.path,
                "name": rec.name,
                "category": rec.ai_category,
                "summary": rec.ai_summary,
                "keywords": rec.ai_keywords,
                "status": rec.status.value,
                "size": rec.size,
                "analyzed_at": rec.analyzed_at.isoformat() if rec.analyzed_at else None,
            })

        return json.dumps(serialized, ensure_ascii=False)

    return handler


GET_CLASSIFIED_DOCUMENTS_DEFINITION = ToolDefinition(
    name="get_classified_documents",
    description=(
        "Liste les documents qui ont été classifiés automatiquement avec un virtual_path. "
        "Permet de voir comment les documents seraient organisés selon les règles de "
        "classification ou les suggestions IA. Utile pour auditer l'organisation proposée "
        "ou trouver les documents d'un dossier virtuel donné."
    ),
    parameters=[
        ToolParameter(
            name="virtual_folder",
            type="string",
            description=(
                "Filtrer par dossier virtuel (ex: '/Factures/', '/RH/Circulaires/'). "
                "Laisse vide pour tous les documents classifiés."
            ),
            required=False,
        ),
        ToolParameter(
            name="rule_applied",
            type="string",
            description=(
                "Filtrer par règle appliquée. "
                "Valeurs : nom exact d'une règle, 'ai_suggestion' (suggestion IA sans règle), "
                "ou laisser vide pour tous."
            ),
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Nombre maximum de résultats (défaut : 20).",
            required=False,
        ),
    ],
    category="rag",
)


def create_get_classified_documents_handler(
    document_index,
    workspace_path: str,
) -> Callable:
    """Crée le handler async pour get_classified_documents.

    Returns:
        Callable async(virtual_folder=None, rule_applied=None, limit=20) -> str (JSON)
    """

    async def handler(
        virtual_folder: str | None = None,
        rule_applied: str | None = None,
        limit: int | None = 20,
    ) -> str:
        filters: dict = {"has_virtual_path": True}
        if virtual_folder:
            filters["virtual_path_contains"] = virtual_folder
        if rule_applied:
            filters["rule_applied"] = rule_applied

        records = await document_index.list_documents(
            workspace_path=workspace_path,
            filters=filters,
            limit=limit or 20,
        )

        serialized = []
        for rec in records:
            serialized.append({
                "path": rec.path,
                "name": rec.name,
                "virtual_path": rec.virtual_path,
                "virtual_filename": rec.virtual_filename,
                "rule_applied": rec.rule_applied,
                "classification_confidence": rec.classification_confidence,
                "category": rec.ai_category,
                "entities": rec.ai_entities,
                "analyzed_at": rec.analyzed_at.isoformat() if rec.analyzed_at else None,
            })

        return json.dumps(serialized, ensure_ascii=False)

    return handler


def create_get_document_metadata_handler(
    document_index,
    workspace_path: str,
) -> Callable:
    """Crée le handler async pour get_document_metadata.

    Returns:
        Callable async(path) -> str (JSON)
    """

    async def handler(path: str) -> str:
        record = await document_index.get_document(
            workspace_path=workspace_path,
            doc_path=path,
        )

        if record is None:
            return json.dumps(
                {"error": f"document non trouvé dans l'index: {path}"},
                ensure_ascii=False,
            )

        return json.dumps({
            "path": record.path,
            "name": record.name,
            "size": record.size,
            "mime_type": record.mime_type,
            "status": record.status.value,
            "ai_summary": record.ai_summary,
            "ai_category": record.ai_category,
            "ai_entities": record.ai_entities,
            "ai_keywords": record.ai_keywords,
            "ai_language": record.ai_language,
            "ai_doc_type": record.ai_doc_type,
            "chunk_count": record.chunk_count,
            "analyzed_at": record.analyzed_at.isoformat() if record.analyzed_at else None,
            "indexed_at": record.indexed_at.isoformat() if record.indexed_at else None,
            "error_message": record.error_message or None,
        }, ensure_ascii=False)

    return handler
