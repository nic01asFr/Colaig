"""
Colaig — LocalStorage (filesystem)

Implémente StorageProtocol sur le système de fichiers local.
Idéal pour le développement, les tests, et les déploiements minimalistes.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from colaig.exceptions import StorageError, StorageFileNotFoundError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)


class LocalStorage:
    """Backend de stockage sur filesystem local.

    Implémente StorageProtocol.

    Args:
        base_path: Répertoire racine pour le stockage.
    """

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        """Résout un chemin relatif en chemin absolu dans le base_path."""
        clean = path.lstrip("/")
        resolved = (self._base / clean).resolve()
        # Sécurité : empêcher la traversée de répertoire
        if not str(resolved).startswith(str(self._base.resolve())):
            raise StorageError(f"Chemin interdit (traversée): {path}")
        return resolved

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les fichiers à un chemin."""
        target = self._resolve(path)
        if not target.exists():
            return []
        if not target.is_dir():
            return []

        results: list[StorageFile] = []
        if recursive:
            entries = list(target.rglob("*"))
        else:
            entries = list(target.iterdir())

        for entry in entries:
            rel = "/" + str(entry.relative_to(self._base)).replace("\\", "/")
            if entry.is_dir():
                rel = rel.rstrip("/") + "/"
            stat = entry.stat()
            etag = ""
            if entry.is_file():
                content = entry.read_bytes()
                etag = hashlib.sha256(content).hexdigest()[:16]

            results.append(StorageFile(
                path=rel,
                name=entry.name,
                is_directory=entry.is_dir(),
                size=stat.st_size if entry.is_file() else 0,
                etag=etag,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
                content_type=_guess_content_type(entry.name),
            ))

        return results

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier."""
        target = self._resolve(path)
        if not target.exists() or not target.is_file():
            raise StorageFileNotFoundError(f"Fichier introuvable: {path}")
        return target.read_bytes()

    async def download_if_changed(self, path: str, known_etag: str) -> Optional[bytes]:
        """Télécharge seulement si le fichier a changé."""
        target = self._resolve(path)
        if not target.exists() or not target.is_file():
            return None
        content = target.read_bytes()
        current_etag = hashlib.sha256(content).hexdigest()[:16]
        if current_etag == known_etag:
            return None
        return content

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier (crée ou écrase)."""
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def mkdir(self, path: str) -> None:
        """Crée un répertoire (et parents si nécessaire)."""
        target = self._resolve(path)
        target.mkdir(parents=True, exist_ok=True)

    async def exists(self, path: str) -> bool:
        """Vérifie si un chemin existe."""
        return self._resolve(path).exists()

    async def get_etag(self, path: str) -> Optional[str]:
        """Retourne l'etag d'un fichier (None si inexistant)."""
        target = self._resolve(path)
        if not target.exists() or not target.is_file():
            return None
        content = target.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]

    async def delete(self, path: str) -> None:
        """Supprime un fichier ou répertoire."""
        target = self._resolve(path)
        if not target.exists():
            return
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()


def _guess_content_type(filename: str) -> str:
    """Devine le content-type d'un fichier par son extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "odt": "application/vnd.oasis.opendocument.text",
        "txt": "text/plain",
        "md": "text/markdown",
        "yaml": "text/yaml",
        "yml": "text/yaml",
        "json": "application/json",
        "csv": "text/csv",
        "html": "text/html",
        "faiss": "application/octet-stream",
        "pkl": "application/octet-stream",
    }.get(ext, "application/octet-stream")
