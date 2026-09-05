"""
Colaig — WebDAVStorage (Nextcloud/Bnum)

Implémente StorageProtocol via le protocole WebDAV.
Utilise httpx pour les requêtes HTTP brutes (PROPFIND, GET, PUT, MKCOL, DELETE).
Aucune bibliothèque WebDAV externe — parsing XML direct.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import unquote

import httpx

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)

# Namespace WebDAV
DAV_NS = "DAV:"
_NS = {"d": DAV_NS}


class WebDAVStorage:
    """Backend de stockage WebDAV pour Nextcloud/Bnum.

    Implémente StorageProtocol.

    Args:
        base_url: URL WebDAV (ex: https://bnum.din.gouv.fr/remote.php/dav/files/colaig/).
        username: Nom d'utilisateur WebDAV.
        password: Mot de passe WebDAV.
        timeout: Timeout HTTP en secondes.
        max_retries: Nombre maximal de retries sur 429/503/timeout.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(username, password)
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                auth=self._auth,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Ferme le client HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        """Construit l'URL complète pour un chemin."""
        if path.startswith("http"):
            return path
        from colaig.security.path_validator import validate_storage_path
        path = validate_storage_path(path, allow_dotcolaig=True, context="webdav")

        # UN NOM DE FICHIER N'EST PAS UNE URL.
        #
        # Le chemin arrive ici sous sa forme LOGIQUE — produit par `paths.py`, ou rendu
        # par `_parse_propfind`, qui applique `unquote()` sur le `href`. Le recoller tel
        # quel dans une URL cassait l'aller-retour :
        #
        #   « note #2.pdf » etait liste correctement, puis redemande avec un « # » qui
        #   ouvre un FRAGMENT — le serveur ne recevait que « note » et rendait 404.
        #
        # Idem pour « ? », qui ouvre une chaine de requete, et « % », qui amorce une
        # sequence d'echappement. Ces trois-la ne changent pas les octets du chemin :
        # ils changent la GRAMMAIRE de l'URL, donc ce que le serveur croit qu'on lui
        # demande. C'est pourquoi le defaut ne se voit qu'avec un vrai nom de fichier,
        # ecrit par un humain — accents et espaces, eux, passaient deja.
        #
        # `safe="/"` : les separateurs restent des separateurs. Les encoder
        # transformerait une arborescence en un seul nom de fichier.
        from urllib.parse import quote
        return f"{self._base_url}/{quote(path.lstrip('/'), safe='/')}"

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Exécute une requête avec retry et backoff exponentiel."""
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.monotonic()
            try:
                response = await client.request(method, url, **kwargs)
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.debug(
                    "storage webdav %s %s → %s (%dms)",
                    method, url, response.status_code, duration_ms,
                )

                if response.status_code in (429, 503):
                    if attempt < self._max_retries:
                        delay = _backoff_delay(attempt)
                        logger.warning(
                            "storage webdav %s retry %d/%d après %ds",
                            method, attempt + 1, self._max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise StorageError(f"WebDAV {response.status_code} après {self._max_retries} retries: {url}")

                if response.status_code == 401:
                    raise StorageAuthError(f"WebDAV 401 Unauthorized: {url}")
                if response.status_code == 403:
                    raise StorageAuthError(f"WebDAV 403 Forbidden: {url}")
                if response.status_code == 404:
                    raise StorageFileNotFoundError(f"WebDAV 404 Not Found: {url}")

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "storage webdav %s timeout/connect retry %d/%d après %ds",
                        method, attempt + 1, self._max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

        raise StorageError(f"WebDAV {method} échoué après {self._max_retries} retries: {last_error}")

    # ── Opérations StorageProtocol ────────────────────────────────────

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les fichiers à un chemin WebDAV."""
        depth = "infinity" if recursive else "1"
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop>"
            "<d:resourcetype/><d:getcontentlength/><d:getetag/>"
            "<d:getlastmodified/><d:getcontenttype/><d:displayname/>"
            "</d:prop>"
            "</d:propfind>"
        )

        response = await self._request_with_retry(
            "PROPFIND",
            self._url(path),
            content=propfind_body.encode("utf-8"),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        )

        return _parse_propfind(response.text, self._url(path))

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier."""
        response = await self._request_with_retry("GET", self._url(path))
        return response.content

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        """Télécharge seulement si etag a changé. None si inchangé."""
        try:
            response = await self._request_with_retry(
                "GET",
                self._url(path),
                headers={"If-None-Match": known_etag},
            )
            if response.status_code == 304:
                return None
            return response.content
        except StorageFileNotFoundError:
            return None

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier (crée ou écrase)."""
        response = await self._request_with_retry("PUT", self._url(path), content=content)
        if response.status_code not in (200, 201, 204):
            raise StorageError(f"WebDAV PUT {path} → {response.status_code}")

    async def mkdir(self, path: str) -> None:
        """Crée un répertoire."""
        try:
            response = await self._request_with_retry("MKCOL", self._url(path))
            if response.status_code not in (200, 201, 405):
                # 405 = déjà existant sur certains serveurs
                raise StorageError(f"WebDAV MKCOL {path} → {response.status_code}")
        except StorageError:
            # Le dossier existe peut-être déjà
            if await self.exists(path):
                return
            raise

    async def exists(self, path: str) -> bool:
        """Vérifie si un chemin existe."""
        try:
            client = await self._get_client()
            response = await client.request("HEAD", self._url(path))
            return response.status_code < 400
        except (httpx.TimeoutException, httpx.ConnectError):
            return False

    async def get_etag(self, path: str) -> str | None:
        """Retourne l'etag d'un fichier (None si inexistant)."""
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop><d:getetag/></d:prop>"
            "</d:propfind>"
        )
        try:
            response = await self._request_with_retry(
                "PROPFIND",
                self._url(path),
                content=propfind_body.encode("utf-8"),
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            )
            files = _parse_propfind(response.text, self._url(path))
            return files[0].etag if files else None
        except (StorageFileNotFoundError, StorageError):
            return None

    async def delete(self, path: str) -> None:
        """Supprime un fichier ou répertoire."""
        response = await self._request_with_retry("DELETE", self._url(path))
        if response.status_code not in (200, 204, 404):
            raise StorageError(f"WebDAV DELETE {path} → {response.status_code}")


# Alias de rétrocompatibilité
WebDAVClient = WebDAVStorage


# ── Helpers ──────────────────────────────────────────────────────────


def _backoff_delay(attempt: int, base: float = 1.0, maximum: float = 60.0) -> float:
    """Calcule un délai de backoff exponentiel avec jitter."""
    delay = min(base * (2 ** attempt), maximum)
    return delay + random.uniform(0, delay * 0.1)


def _parse_propfind(xml_text: str, base_url: str) -> list[StorageFile]:
    """Parse la réponse XML PROPFIND en liste de StorageFile."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("erreur parsing PROPFIND XML: %s", e)
        return []

    files: list[StorageFile] = []

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue

        href = href_el.text

        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue

        # Déterminer si c'est un répertoire
        resourcetype = prop.find(f"{{{DAV_NS}}}resourcetype")
        is_directory = False
        if resourcetype is not None:
            is_directory = resourcetype.find(f"{{{DAV_NS}}}collection") is not None

        # Nom du fichier
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue

        # Taille
        length_el = prop.find(f"{{{DAV_NS}}}getcontentlength")
        size = int(length_el.text) if length_el is not None and length_el.text else 0

        # Etag
        etag_el = prop.find(f"{{{DAV_NS}}}getetag")
        etag = etag_el.text.strip('"') if etag_el is not None and etag_el.text else ""

        # Last modified
        lastmod_el = prop.find(f"{{{DAV_NS}}}getlastmodified")
        last_modified = None
        if lastmod_el is not None and lastmod_el.text:
            try:
                last_modified = parsedate_to_datetime(lastmod_el.text)
            except (ValueError, TypeError):
                pass

        # Content type
        ct_el = prop.find(f"{{{DAV_NS}}}getcontenttype")
        content_type = ct_el.text if ct_el is not None and ct_el.text else ""

        # Extraire le chemin relatif depuis l'URL
        path = unquote(href)

        files.append(StorageFile(
            path=path,
            name=unquote(name),
            is_directory=is_directory,
            size=size,
            etag=etag,
            last_modified=last_modified,
            content_type=content_type,
        ))

    return files
