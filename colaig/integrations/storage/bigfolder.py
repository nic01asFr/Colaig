"""
Colaig — BigfolderStorage (API Archivist)

Implémente StorageProtocol via les endpoints /storage/* de l'API Archivist.
Toutes les opérations sont path-based — pas de cache local, pas d'ID document.

Auth : X-API-Key header (user api_key ou service token).

Mapping chemins :
    Colaig path    : /documents/guide.pdf
    BigFolder path : {bigfolder_root}/documents/guide.pdf
                     ex: /MonEspace/documents/guide.pdf
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
import time
from datetime import datetime

import httpx

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)


class BigfolderStorage:
    """Backend de stockage Bigfolder (API Archivist) via endpoints /storage/*.

    Implémente StorageProtocol.

    Args:
        api_url: URL de base de l'API Archivist (ex: http://localhost:8002).
        service_token: Service token Archivist (format: svc_xxxxx).
        bigfolder_root: Chemin racine BigFolder mappé à "/" Colaig.
            Ex: "/MonEspace" → Colaig "/docs/guide.pdf" → BigFolder "/MonEspace/docs/guide.pdf"
        timeout: Timeout HTTP en secondes.
        max_retries: Nombre max de retries sur 429/503/timeout.
    """

    def __init__(
        self,
        api_url: str,
        service_token: str,
        bigfolder_root: str = "/",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._service_token = service_token
        # Normalisation : "" ou "/MonEspace" (sans slash final)
        self._root = bigfolder_root.rstrip("/") if bigfolder_root != "/" else ""
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    # ── Client HTTP ──────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._service_token}"},
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Ferme le client HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Exécute une requête API avec retry et backoff exponentiel."""
        client = await self._get_client()
        url = f"{self._api_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = await client.request(method, url, **kwargs)
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.debug(
                    "bigfolder %s %s → %d (%dms)",
                    method, path, resp.status_code, duration_ms,
                )

                if resp.status_code in (429, 503):
                    if attempt < self._max_retries:
                        await asyncio.sleep(_backoff_delay(attempt))
                        continue
                    raise StorageError(
                        f"Bigfolder {resp.status_code} après {self._max_retries} retries: {path}"
                    )

                if resp.status_code == 401:
                    raise StorageAuthError("Bigfolder 401 — service token invalide")
                if resp.status_code == 403:
                    raise StorageAuthError(f"Bigfolder 403 — accès refusé: {path}")
                if resp.status_code == 404:
                    raise StorageFileNotFoundError(f"Bigfolder 404: {path}")

                return resp

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    await asyncio.sleep(_backoff_delay(attempt))
                    continue

        raise StorageError(
            f"Bigfolder {method} {path} échoué après {self._max_retries} retries: {last_error}"
        )

    # ── Mapping chemins ──────────────────────────────────────────────

    def _to_bfpath(self, colaig_path: str) -> str:
        """Colaig path → BigFolder absolute path."""
        from colaig.security.path_validator import validate_storage_path
        colaig_path = validate_storage_path(colaig_path, allow_dotcolaig=True, context="bigfolder")
        return self._root + "/" + colaig_path.lstrip("/")

    def _to_colaig_path(self, bf_path: str) -> str:
        """BigFolder absolute path → Colaig path."""
        if self._root and bf_path.startswith(self._root):
            return bf_path[len(self._root):] or "/"
        return "/" + bf_path.lstrip("/")

    # ── StorageProtocol ──────────────────────────────────────────────

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les fichiers via GET /storage/ls."""
        bf_path = self._to_bfpath(path)
        return await self._ls_recursive(bf_path, recursive=recursive)

    async def _ls_recursive(self, bf_path: str, recursive: bool) -> list[StorageFile]:
        """Appel /storage/ls + récursion sur les sous-dossiers si recursive=True."""
        try:
            resp = await self._request("GET", "/storage/ls", params={"path": bf_path})
        except StorageFileNotFoundError:
            return []

        entries = resp.json().get("entries", [])
        files: list[StorageFile] = []
        subdir_tasks: list = []

        for entry in entries:
            entry_path = entry.get("path", "")
            entry_name = entry.get("name", "")
            entry_type = entry.get("type", "file")

            if entry_type in ("folder", "directory", "workspace"):
                if recursive:
                    subdir_tasks.append(self._ls_recursive(entry_path, recursive=True))
                else:
                    files.append(StorageFile(
                        path=self._to_colaig_path(entry_path),
                        name=entry_name,
                        is_directory=True,
                    ))
            else:
                # Extraire l'ID du document_uri comme etag (stable par contenu via dédup MD5)
                doc_uri = entry.get("document_uri", "")
                etag = doc_uri.split("/")[-1] if doc_uri else ""
                files.append(StorageFile(
                    path=self._to_colaig_path(entry_path),
                    name=entry_name,
                    is_directory=False,
                    size=entry.get("size", 0),
                    etag=etag,
                    last_modified=_parse_iso_dt(entry.get("updated_at")),
                    content_type="",
                ))

        if subdir_tasks:
            sub_results = await asyncio.gather(*subdir_tasks)
            for sub_files in sub_results:
                files.extend(sub_files)

        return files

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu binaire via GET /storage/download."""
        bf_path = self._to_bfpath(path)
        resp = await self._request("GET", "/storage/download", params={"path": bf_path})
        return resp.content

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        """Télécharge seulement si le document a changé depuis known_etag.

        Archivist ne supporte pas If-None-Match sur /storage/download.
        On compare l'etag (document_id) via /storage/exists avant de télécharger.
        """
        current_etag = await self.get_etag(path)
        if current_etag is None:
            return None
        if current_etag == known_etag:
            return None
        return await self.download(path)

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier via POST /storage/store.

        Bigfolder gère la déduplication et le remplacement côté serveur.
        """
        bf_path = self._to_bfpath(path)
        filename = bf_path.split("/")[-1]
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        resp = await self._request(
            "POST", "/storage/store",
            data={"path": bf_path},
            files={"file": (filename, content, mime_type)},
        )
        if resp.status_code not in (200, 201):
            raise StorageError(
                f"Bigfolder upload {path} → {resp.status_code}: {resp.text}"
            )
        logger.debug("bigfolder upload OK: %s", bf_path)

    async def delete(self, path: str) -> None:
        """Supprime un fichier via DELETE /storage/delete."""
        bf_path = self._to_bfpath(path)
        try:
            await self._request("DELETE", "/storage/delete", params={"path": bf_path})
        except StorageFileNotFoundError:
            pass  # Déjà supprimé

    async def exists(self, path: str) -> bool:
        """Vérifie si un chemin existe via GET /storage/exists."""
        bf_path = self._to_bfpath(path)
        try:
            resp = await self._request("GET", "/storage/exists", params={"path": bf_path})
            return resp.json().get("exists", False)
        except StorageFileNotFoundError:
            return False

    async def mkdir(self, path: str) -> None:
        """Crée un dossier via POST /storage/mkdir (non-bloquant)."""
        bf_path = self._to_bfpath(path)
        try:
            await self._request("POST", "/storage/mkdir", params={"path": bf_path})
        except (StorageError, StorageFileNotFoundError):
            pass  # Non-bloquant — Bigfolder crée les dossiers à l'upload si absent

    async def get_etag(self, path: str) -> str | None:
        """Retourne l'ID du document comme etag (None si introuvable).

        L'ID est stable par contenu (déduplication MD5 côté Archivist).
        """
        bf_path = self._to_bfpath(path)
        try:
            resp = await self._request("GET", "/storage/exists", params={"path": bf_path})
            data = resp.json()
            if not data.get("exists"):
                return None
            doc_uri = data.get("document_uri", "")
            return doc_uri.split("/")[-1] if doc_uri else None
        except StorageFileNotFoundError:
            return None


# ── Fonctions utilitaires ─────────────────────────────────────────────


def _backoff_delay(attempt: int, base: float = 1.0, maximum: float = 60.0) -> float:
    """Calcule un délai de backoff exponentiel avec jitter."""
    delay = min(base * (2 ** attempt), maximum)
    return delay + random.uniform(0, delay * 0.1)


def _parse_iso_dt(value: str | None) -> datetime | None:
    """Parse une date ISO 8601 (avec ou sans suffixe Z) en datetime aware."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
