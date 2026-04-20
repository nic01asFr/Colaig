"""
Colaig — MicrosoftGraphStorage (OneDrive / SharePoint)

Implémente StorageProtocol via Microsoft Graph API.
Auth : client_credentials (app-only) — Cas 2 : Colaig a son propre compte
Azure AD app, les utilisateurs partagent leurs dossiers OneDrive avec ce compte.

Endpoints Graph utilisés :
    GET  /v1.0/users/{user_id}/drive/root:/{path}:/children  → list_files
    GET  /v1.0/users/{user_id}/drive/root:/{path}:/content   → download
    PUT  /v1.0/users/{user_id}/drive/root:/{path}:/content   → upload
    DELETE /v1.0/users/{user_id}/drive/root:/{path}          → delete
    POST /v1.0/users/{user_id}/drive/root:/{path}/children   → mkdir

Dépendance : httpx (déjà dans le projet)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from datetime import datetime, timezone

import httpx

from colaig.exceptions import StorageError, StorageFileNotFoundError, StorageAuthError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Token expiré si moins de 60s restantes
_TOKEN_EXPIRY_MARGIN = 60


class MicrosoftGraphStorage:
    """Backend de stockage OneDrive/SharePoint via Microsoft Graph API.

    Implémente StorageProtocol.

    Cas 2 — compte propre Colaig : l'instance Colaig est enregistrée comme
    Azure AD Application (client_credentials). Les utilisateurs partagent
    leurs dossiers OneDrive avec l'UPN de cette application.

    Args:
        tenant_id: ID du tenant Azure AD.
        client_id: ID de l'application Azure AD enregistrée.
        client_secret: Secret de l'application.
        drive_user_id: UPN ou object-id de l'utilisateur dont on accède au drive
            (ex: "colaig@monorg.onmicrosoft.com").
        drive_id: ID du drive spécifique (vide = drive par défaut de l'utilisateur).
        root_path: Chemin racine dans le drive (vide = racine absolue).
            Exemple : "ColaigShared" pour ne travailler que dans ce dossier.
        timeout: Timeout HTTP en secondes.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        drive_user_id: str,
        drive_id: str = "",
        root_path: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._drive_user_id = drive_user_id
        self._drive_id = drive_id
        self._root_path = root_path.strip("/")
        self._timeout = timeout

        # Token cache
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Auth : client_credentials flow

    async def _ensure_token(self) -> str:
        """Retourne un token valide, le renouvelle si nécessaire."""
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - _TOKEN_EXPIRY_MARGIN:
            return self._access_token

        url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        http = await self._get_http()

        try:
            resp = await http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": _GRAPH_SCOPE,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise StorageAuthError(
                f"Graph OAuth2 échoué ({e.response.status_code}): {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise StorageError(f"Graph OAuth2 connexion impossible: {e}") from e

        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = now + data.get("expires_in", 3600)
        logger.debug("graph token renouvelé (expire dans %ds)", data.get("expires_in", 3600))
        return self._access_token

    # ------------------------------------------------------------------
    # Construction des URLs Graph

    def _drive_root(self) -> str:
        """Retourne l'URL de base du drive."""
        if self._drive_id:
            return f"{_GRAPH_BASE}/drives/{self._drive_id}"
        return f"{_GRAPH_BASE}/users/{self._drive_user_id}/drive"

    def _item_url(self, path: str, suffix: str = "") -> str:
        """Construit l'URL Graph pour un chemin Colaig.

        Path "" ou "/" → racine du drive (root).
        Path non vide → /root:/{full_path}
        """
        full = self._full_path(path)
        base = self._drive_root()
        if not full or full == "/":
            url = f"{base}/root"
        else:
            url = f"{base}/root:/{full.lstrip('/')}"
        if suffix:
            if not full or full == "/":
                url = f"{url}/{suffix.lstrip('/')}"
            else:
                url = f"{url}:/{suffix.lstrip('/')}"
        return url

    def _full_path(self, path: str) -> str:
        """Applique le root_path configuré au chemin Colaig."""
        from colaig.security.path_validator import validate_storage_path
        path = validate_storage_path(path, allow_dotcolaig=True, context="msgraph")
        p = path.strip("/")
        if self._root_path:
            return f"{self._root_path}/{p}" if p else self._root_path
        return p

    def _strip_root(self, graph_path: str) -> str:
        """Retire le root_path d'un chemin Graph pour retrouver le chemin Colaig."""
        p = graph_path.lstrip("/")
        if self._root_path and p.startswith(self._root_path.lstrip("/")):
            p = p[len(self._root_path.lstrip("/")):]
        return "/" + p.lstrip("/")

    # ------------------------------------------------------------------
    # Requêtes Graph génériques

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Exécute une requête Graph avec token Bearer."""
        token = await self._ensure_token()
        http = await self._get_http()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        try:
            resp = await http.request(method, url, headers=headers, **kwargs)
        except httpx.RequestError as e:
            raise StorageError(f"Graph connexion impossible: {e}") from e

        if resp.status_code == 401:
            raise StorageAuthError(f"Graph 401 Unauthorized: {url}")
        if resp.status_code == 403:
            raise StorageAuthError(f"Graph 403 Forbidden: {url}")
        if resp.status_code == 404:
            raise StorageFileNotFoundError(f"Graph 404 Not Found: {url}")

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                detail = resp.text
            raise StorageError(f"Graph {resp.status_code}: {detail}")

        return resp

    # ------------------------------------------------------------------
    # StorageProtocol

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les enfants d'un dossier OneDrive."""
        url = self._item_url(path, suffix="/children")
        files: list[StorageFile] = []

        while url:
            resp = await self._request("GET", url, params={"$top": 200})
            data = resp.json()

            for item in data.get("value", []):
                is_dir = "folder" in item
                name = item.get("name", "")
                item_path = self._strip_root(
                    item.get("parentReference", {}).get("path", "").split(":")[-1]
                    + "/" + name
                )
                last_mod = None
                if mod_str := item.get("lastModifiedDateTime"):
                    try:
                        last_mod = datetime.fromisoformat(
                            mod_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                files.append(StorageFile(
                    path=item_path,
                    name=name,
                    is_directory=is_dir,
                    size=item.get("size", 0),
                    etag=item.get("eTag", "").strip('"'),
                    last_modified=last_mod,
                    content_type=item.get("file", {}).get("mimeType", ""),
                ))

                # Récursion
                if recursive and is_dir:
                    sub = await self.list_files(item_path, recursive=True)
                    files.extend(sub)

            url = data.get("@odata.nextLink")

        return files

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier OneDrive."""
        url = self._item_url(path, suffix="/content")
        # /content redirige vers l'URL de téléchargement — httpx suit la redirection
        resp = await self._request("GET", url)
        return resp.content

    async def download_if_changed(self, path: str, known_etag: str) -> Optional[bytes]:
        """Télécharge seulement si l'etag a changé."""
        # Graph ne supporte pas If-None-Match sur /content directement.
        # On fait un HEAD sur l'item pour comparer l'etag.
        try:
            meta_url = self._item_url(path)
            resp = await self._request("GET", meta_url, params={"$select": "eTag,id"})
            current_etag = resp.json().get("eTag", "").strip('"')
            if current_etag and current_etag == known_etag.strip('"'):
                return None
        except StorageFileNotFoundError:
            return None

        return await self.download(path)

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier OneDrive (crée ou écrase)."""
        url = self._item_url(path, suffix="/content")
        await self._request(
            "PUT",
            url,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    async def mkdir(self, path: str) -> None:
        """Crée un dossier OneDrive (et ses parents si nécessaire)."""
        parts = path.strip("/").split("/")
        if not parts or not parts[-1]:
            return

        name = parts[-1]
        parent_path = "/".join(parts[:-1])

        parent_url = self._item_url(parent_path, suffix="/children")
        try:
            await self._request(
                "POST",
                parent_url,
                json={
                    "name": name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "replace",
                },
                headers={"Content-Type": "application/json"},
            )
        except StorageError as e:
            if "nameAlreadyExists" in str(e) or "conflict" in str(e).lower():
                return  # Déjà existant
            raise

    async def exists(self, path: str) -> bool:
        """Vérifie si un item OneDrive existe."""
        try:
            url = self._item_url(path)
            await self._request("GET", url, params={"$select": "id"})
            return True
        except StorageFileNotFoundError:
            return False
        except (StorageError, StorageAuthError):
            return False

    async def get_etag(self, path: str) -> Optional[str]:
        """Retourne l'etag d'un item OneDrive."""
        try:
            url = self._item_url(path)
            resp = await self._request("GET", url, params={"$select": "eTag"})
            return resp.json().get("eTag", "").strip('"') or None
        except (StorageFileNotFoundError, StorageError):
            return None

    async def delete(self, path: str) -> None:
        """Supprime un item OneDrive."""
        url = self._item_url(path)
        await self._request("DELETE", url)

    # ------------------------------------------------------------------
    # Méthode bonus : dossiers partagés (pour auto-discovery Cas 2)

    async def list_shared_with_me(self) -> list[StorageFile]:
        """Retourne la liste des dossiers partagés avec ce compte.

        Utilisé pour l'auto-discovery : quand un utilisateur partage son dossier
        OneDrive avec le compte Colaig, cette méthode le détecte.

        Returns:
            Liste de StorageFile (is_directory=True) pour les dossiers partagés.
        """
        url = f"{_GRAPH_BASE}/users/{self._drive_user_id}/drive/sharedWithMe"
        try:
            resp = await self._request("GET", url)
        except StorageError:
            return []

        files: list[StorageFile] = []
        for item in resp.json().get("value", []):
            if "folder" not in item:
                continue
            name = item.get("name", "")
            # remoteItem contient les métadonnées originales
            remote = item.get("remoteItem", item)
            files.append(StorageFile(
                path=f"/shared/{name}",
                name=name,
                is_directory=True,
                size=0,
                etag=item.get("eTag", "").strip('"'),
            ))
        return files
