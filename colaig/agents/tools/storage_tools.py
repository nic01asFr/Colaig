"""
agents/tools/storage_tools.py — Outils d'accès au stockage pour l'Orchestrateur.

Outils :
- fetch_document : télécharge et lit un document spécifique
- list_documents  : liste les fichiers d'un répertoire du workspace
"""

from __future__ import annotations

import json
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter, WorkspaceConfig

# ---------------------------------------------------------------------------
# Définitions

FETCH_DOCUMENT_DEFINITION = ToolDefinition(
    name="fetch_document",
    description=(
        "Télécharge et lit le contenu d'un document spécifique depuis le workspace. "
        "Utiliser quand la recherche sémantique identifie un document précis à lire "
        "en entier ou quand l'utilisateur mentionne un fichier par son nom."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description=(
                "Chemin du document dans le storage (relatif à la racine du workspace). "
                "Exemple : 'documents/guide-procedures.pdf'"
            ),
            required=True,
        ),
        ToolParameter(
            name="max_chars",
            type="integer",
            description="Nombre maximum de caractères à retourner (défaut : 3000).",
            required=False,
        ),
    ],
    category="storage",
)

LIST_DOCUMENTS_DEFINITION = ToolDefinition(
    name="list_documents",
    description=(
        "Liste les fichiers disponibles dans un répertoire du workspace. "
        "Utile pour explorer la structure des documents avant de les récupérer."
    ),
    parameters=[
        ToolParameter(
            name="directory",
            type="string",
            description=(
                "Répertoire à lister (relatif à la racine du workspace). "
                "Laisser vide ou '/' pour la racine."
            ),
            required=False,
        ),
    ],
    category="storage",
)


# ---------------------------------------------------------------------------
# Factories

def create_fetch_handler(storage, workspace: WorkspaceConfig | None = None) -> Callable:
    """Crée un handler async pour fetch_document.

    Args:
        storage: Implémentation de StorageProtocol.
        workspace: Configuration du workspace (pour résoudre le chemin racine).
    """
    workspace_root = workspace.storage_path if workspace else ""

    async def fetch_handler(path: str, max_chars: int | None = 3000) -> str:
        """Télécharge et retourne le contenu d'un document.

        Returns:
            JSON string : {"path": ..., "content": ..., "size": ...}
            ou {"error": ...} si le fichier n'existe pas.
        """
        # Résolution du chemin absolu
        if workspace_root and not path.startswith("/"):
            full_path = f"{workspace_root.rstrip('/')}/{path.lstrip('/')}"
        else:
            full_path = path

        try:
            exists = await storage.exists(full_path)
            if not exists:
                return json.dumps({"error": f"Fichier introuvable : {path}"}, ensure_ascii=False)

            content_bytes = await storage.download(full_path)
            # Décodage UTF-8 avec fallback latin-1 pour les PDF/DOCX bruts
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = content_bytes.decode("latin-1", errors="replace")

            max_c = max_chars if max_chars is not None else 3000
            truncated = len(content) > max_c
            return json.dumps({
                "path": path,
                "content": content[:max_c],
                "size": len(content_bytes),
                "truncated": truncated,
            }, ensure_ascii=False)

        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return fetch_handler


def create_list_handler(storage, workspace: WorkspaceConfig | None = None) -> Callable:
    """Crée un handler async pour list_documents.

    Args:
        storage: Implémentation de StorageProtocol.
        workspace: Configuration du workspace (pour résoudre le chemin racine).
    """
    workspace_root = workspace.storage_path if workspace else ""

    async def list_handler(directory: str | None = "") -> str:
        """Liste les fichiers d'un répertoire.

        Returns:
            JSON string : {"directory": ..., "files": [{"name": ..., "size": ..., "type": ...}]}
        """
        dir_path = directory or ""

        # Résolution du chemin absolu
        if workspace_root and not dir_path.startswith("/"):
            full_path = f"{workspace_root.rstrip('/')}/{dir_path.lstrip('/')}" if dir_path else workspace_root
        else:
            full_path = dir_path or "/"

        try:
            files = await storage.list_files(full_path)
            serialized = [
                {
                    "name": f.name,
                    "path": f.path,
                    "size": f.size,
                    "is_directory": f.is_directory,
                    "type": f.content_type or ("directory" if f.is_directory else "file"),
                }
                for f in files
            ]
            return json.dumps({
                "directory": dir_path or "/",
                "files": serialized,
                "count": len(serialized),
            }, ensure_ascii=False)

        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return list_handler
