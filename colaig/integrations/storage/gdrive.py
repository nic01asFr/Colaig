"""
Colaig — GoogleDriveStorage (Google Drive)

Implémente StorageProtocol via l'API Google Drive v3.
Auth : Service Account avec JWT RS256 (server-to-server, aucune interaction utilisateur).
Optionnel : domain-wide delegation pour impersoner un utilisateur Google Workspace.

Google Drive est orienté "files by ID" : chaque fichier/dossier a un identifiant
opaque. GoogleDriveStorage maintient un cache chemin → ID pour simuler l'adressage
par chemin attendu par StorageProtocol.

Dépendances :
    httpx (déjà dans le projet)
    cryptography (optionnelle — pip install cryptography)
        Requis pour signer le JWT RS256 du Service Account.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime

import httpx

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)

# URLs Google APIs
_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3/files"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

# MIME type dossier Google Drive
_FOLDER_MIME = "application/vnd.google-apps.folder"

# Marge avant expiration du token (secondes)
_TOKEN_EXPIRY_MARGIN = 60

# Champs demandés lors des listings
_FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,parents"


def _require_cryptography():
    """Importe cryptography, lève ImportError avec message clair si absent."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        return hashes, serialization, padding
    except ImportError:
        raise ImportError(
            "cryptography est requis pour GoogleDriveStorage (signature JWT RS256). "
            "Installez-le : pip install cryptography"
        )


def _b64url(data: bytes) -> str:
    """Encode en base64 URL-safe sans padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_jwt(client_email: str, private_key_pem: str, impersonate_user: str = "") -> str:
    """Construit et signe un JWT RS256 pour l'échange contre un access token Google.

    Args:
        client_email: Email du service account (champ `client_email` du JSON).
        private_key_pem: Clé privée RSA au format PEM.
        impersonate_user: Email de l'utilisateur à impersoner (domain-wide delegation).

    Returns:
        JWT signé sous forme de string.
    """
    hashes, serialization, padding = _require_cryptography()

    now = int(time.time())

    # En-tête JWT
    header = {"alg": "RS256", "typ": "JWT"}

    # Payload JWT
    payload: dict = {
        "iss": client_email,
        "scope": _DRIVE_SCOPE,
        "aud": _TOKEN_URL,
        "exp": now + 3600,
        "iat": now,
    }
    if impersonate_user:
        payload["sub"] = impersonate_user

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    # Chargement de la clé privée
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )

    # Signature RS256
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signature_b64 = _b64url(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


class GoogleDriveStorage:
    """Backend de stockage Google Drive pour Colaig.

    Implémente StorageProtocol.

    Auth via Service Account JWT RS256 — aucune interaction utilisateur requise.
    Domain-wide delegation supportée via `impersonate_user`.

    Google Drive est orienté ID : un cache chemin → file_id évite de traverser
    l'arbre à chaque opération.

    Args:
        service_account_json: Contenu JSON du service account (string, pas chemin).
        root_folder_id: ID du dossier racine Drive (défaut "root" = racine du drive).
            Fournir l'ID d'un dossier partagé pour restreindre la vue de Colaig.
        impersonate_user: Email de l'utilisateur à impersoner via domain-wide delegation.
            Vide = agir comme le service account directement.
        timeout: Timeout HTTP en secondes.
    """

    def __init__(
        self,
        service_account_json: str,
        root_folder_id: str = "root",
        impersonate_user: str = "",
        timeout: float = 30.0,
    ) -> None:
        # Parsing du JSON service account
        try:
            sa = json.loads(service_account_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "GoogleDriveStorage: service_account_json invalide (JSON attendu)"
            ) from exc

        self._client_email: str = sa.get("client_email", "")
        self._private_key_pem: str = sa.get("private_key", "")

        if not self._client_email or not self._private_key_pem:
            raise ValueError(
                "GoogleDriveStorage: le JSON service account doit contenir "
                "'client_email' et 'private_key'"
            )

        self._root_folder_id = root_folder_id or "root"
        self._impersonate_user = impersonate_user
        self._timeout = timeout

        # Cache token OAuth2
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        # Cache chemin Colaig → file_id Google Drive
        self._path_cache: dict[str, str] = {}

        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        """Ferme le client HTTP."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Auth : JWT RS256 → OAuth2 Bearer token

    async def _ensure_token(self) -> str:
        """Retourne un token valide, le renouvelle si nécessaire."""
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - _TOKEN_EXPIRY_MARGIN:
            return self._access_token

        jwt = _build_jwt(self._client_email, self._private_key_pem, self._impersonate_user)
        http = await self._get_http()

        try:
            resp = await http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": jwt,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise StorageAuthError(
                f"Google Drive OAuth2 échoué ({e.response.status_code}): {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise StorageError(f"Google Drive OAuth2 connexion impossible: {e}") from e

        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = now + data.get("expires_in", 3600)
        logger.debug(
            "gdrive token renouvelé pour %s (expire dans %ds)",
            self._client_email,
            data.get("expires_in", 3600),
        )
        return self._access_token

    # ------------------------------------------------------------------
    # Requêtes Drive génériques

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Exécute une requête Drive API avec token Bearer."""
        token = await self._ensure_token()
        http = await self._get_http()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        try:
            resp = await http.request(method, url, headers=headers, **kwargs)
        except httpx.RequestError as e:
            raise StorageError(f"Google Drive connexion impossible: {e}") from e

        if resp.status_code == 401:
            raise StorageAuthError(f"Google Drive 401 Unauthorized: {url}")
        if resp.status_code == 403:
            raise StorageAuthError(f"Google Drive 403 Forbidden: {url}")
        if resp.status_code == 404:
            raise StorageFileNotFoundError(f"Google Drive 404 Not Found: {url}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                detail = resp.text
            raise StorageError(f"Google Drive {resp.status_code}: {detail}")

        return resp

    # ------------------------------------------------------------------
    # Résolution des chemins Colaig → file_id Google Drive

    def _full_key(self, path: str) -> str:
        """Applique le root_folder_id et normalise le chemin pour le cache.

        Retourne la clé de cache (chemin nettoyé sans slash de début/fin).
        La racine retourne "" (chaîne vide) → correspond à root_folder_id.
        """
        # Normaliser avant la validation : "/" ou "" = racine
        stripped = path.strip("/")
        if not stripped:
            return ""
        from colaig.security.path_validator import validate_storage_path
        path = validate_storage_path(path, allow_dotcolaig=True, context="gdrive")
        return path.strip("/")

    def _strip_root(self, gdrive_path: str) -> str:
        """Retire les slashes de début pour normaliser un chemin Drive."""
        return "/" + gdrive_path.strip("/")

    def _invalidate_cache(self, path: str) -> None:
        """Invalide le cache pour un chemin et tous ses enfants."""
        key = path.strip("/")
        keys_to_del = [
            k for k in self._path_cache
            if k == key or k.startswith(key + "/")
        ]
        for k in keys_to_del:
            del self._path_cache[k]

    async def _resolve_path(self, path: str) -> str:
        """Résout un chemin Colaig en file_id Google Drive.

        Traverse l'arbre Drive depuis root_folder_id, composant par composant.
        Utilise et alimente le cache chemin → file_id.

        Args:
            path: Chemin Colaig (ex: "/workspace/docs/guide.pdf").

        Returns:
            file_id Google Drive correspondant.

        Raises:
            StorageFileNotFoundError: Si le chemin n'existe pas dans Drive.
        """
        key = self._full_key(path)

        # Racine = root_folder_id
        if not key:
            return self._root_folder_id

        # Cache hit ?
        if key in self._path_cache:
            return self._path_cache[key]

        # Traversée composant par composant
        parts = key.split("/")
        current_id = self._root_folder_id
        accumulated = ""

        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part

            if accumulated in self._path_cache:
                current_id = self._path_cache[accumulated]
                continue

            # Recherche dans le dossier courant
            child_id = await self._find_child(current_id, part)
            self._path_cache[accumulated] = child_id
            current_id = child_id

        return current_id

    async def _find_child(self, parent_id: str, name: str) -> str:
        """Cherche un enfant par nom dans un dossier Drive.

        Args:
            parent_id: ID du dossier parent.
            name: Nom de l'item cherché (fichier ou dossier).

        Returns:
            file_id de l'item trouvé.

        Raises:
            StorageFileNotFoundError: Si aucun item de ce nom dans ce dossier.
        """
        # Échapper les apostrophes dans le nom pour la query Drive
        name_escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name='{name_escaped}' and '{parent_id}' in parents and trashed=false"

        resp = await self._request(
            "GET",
            f"{_DRIVE_BASE}/files",
            params={"q": q, "fields": "files(id,name,mimeType)", "pageSize": 10},
        )
        data = resp.json()
        files = data.get("files", [])
        if not files:
            raise StorageFileNotFoundError(
                f"Google Drive: '{name}' introuvable dans le dossier {parent_id}"
            )
        return files[0]["id"]

    async def _ensure_folder(self, path: str) -> str:
        """S'assure qu'un dossier existe, le crée si nécessaire (mkdir -p).

        Args:
            path: Chemin Colaig du dossier.

        Returns:
            file_id du dossier.
        """
        key = self._full_key(path)
        if not key:
            return self._root_folder_id

        if key in self._path_cache:
            return self._path_cache[key]

        parts = key.split("/")
        current_id = self._root_folder_id
        accumulated = ""

        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part

            if accumulated in self._path_cache:
                current_id = self._path_cache[accumulated]
                continue

            try:
                child_id = await self._find_child(current_id, part)
            except StorageFileNotFoundError:
                child_id = await self._create_folder(current_id, part)
                logger.debug("gdrive: dossier créé %s (id=%s)", accumulated, child_id)

            self._path_cache[accumulated] = child_id
            current_id = child_id

        return current_id

    async def _create_folder(self, parent_id: str, name: str) -> str:
        """Crée un dossier dans un parent Drive.

        Args:
            parent_id: ID du dossier parent.
            name: Nom du nouveau dossier.

        Returns:
            file_id du dossier créé.
        """
        resp = await self._request(
            "POST",
            f"{_DRIVE_BASE}/files",
            json={
                "name": name,
                "mimeType": _FOLDER_MIME,
                "parents": [parent_id],
            },
            headers={"Content-Type": "application/json"},
        )
        return resp.json()["id"]

    # ------------------------------------------------------------------
    # StorageProtocol

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les items d'un dossier Google Drive."""
        try:
            folder_id = await self._resolve_path(path)
        except StorageFileNotFoundError:
            return []

        files: list[StorageFile] = []
        page_token: str | None = None

        while True:
            params: dict = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": f"nextPageToken,files({_FILE_FIELDS})",
                "pageSize": 1000,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await self._request("GET", f"{_DRIVE_BASE}/files", params=params)
            data = resp.json()

            for item in data.get("files", []):
                is_dir = item.get("mimeType") == _FOLDER_MIME
                name = item.get("name", "")
                item_path = (path.rstrip("/") + "/" + name)

                # Mettre en cache l'ID de chaque item listé
                cache_key = self._full_key(item_path)
                if cache_key:
                    self._path_cache[cache_key] = item["id"]

                # Etag = md5Checksum pour les fichiers, vide pour les dossiers
                etag = item.get("md5Checksum", "") if not is_dir else ""

                last_mod: datetime | None = None
                if mod_str := item.get("modifiedTime"):
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
                    size=int(item.get("size", 0) or 0),
                    etag=etag,
                    last_modified=last_mod,
                    content_type=item.get("mimeType", "") if not is_dir else "",
                ))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Récursion async sur les sous-dossiers
        if recursive:
            subdirs = [f for f in files if f.is_directory]
            for subdir in subdirs:
                sub = await self.list_files(subdir.path, recursive=True)
                files.extend(sub)

        return files

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier Google Drive."""
        try:
            file_id = await self._resolve_path(path)
        except StorageFileNotFoundError:
            raise StorageFileNotFoundError(f"Google Drive: fichier introuvable {path}")

        resp = await self._request(
            "GET",
            f"{_DRIVE_BASE}/files/{file_id}",
            params={"alt": "media"},
        )
        return resp.content

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        """Télécharge seulement si l'etag (md5Checksum) a changé."""
        try:
            file_id = await self._resolve_path(path)
        except StorageFileNotFoundError:
            return None

        try:
            resp = await self._request(
                "GET",
                f"{_DRIVE_BASE}/files/{file_id}",
                params={"fields": "md5Checksum"},
            )
            current_etag = resp.json().get("md5Checksum", "")
            if current_etag and current_etag == known_etag:
                return None
        except (StorageFileNotFoundError, StorageError):
            return None

        return await self.download(path)

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier Google Drive (crée ou met à jour).

        Utilise l'upload multipart pour envoyer métadonnées + contenu en une requête.
        """
        # Séparer le chemin parent et le nom du fichier
        parent_path, filename = _split_path(path)
        parent_id = await self._ensure_folder(parent_path)

        # Vérifier si le fichier existe déjà (pour update vs create)
        existing_id: str | None = None
        cache_key = self._full_key(path)
        if cache_key in self._path_cache:
            existing_id = self._path_cache[cache_key]
        else:
            try:
                existing_id = await self._find_child(parent_id, filename)
                self._path_cache[cache_key] = existing_id
            except StorageFileNotFoundError:
                pass

        # Construire le corps multipart
        boundary = f"colaig_{uuid.uuid4().hex}"
        metadata = json.dumps(
            {"name": filename} if existing_id else {"name": filename, "parents": [parent_id]},
            separators=(",", ":"),
        ).encode("utf-8")

        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode()
        body += metadata
        body += (
            f"\r\n--{boundary}\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        body += content
        body += f"\r\n--{boundary}--".encode()

        if existing_id:
            # Mise à jour d'un fichier existant
            resp = await self._request(
                "PATCH",
                f"{_UPLOAD_BASE}/{existing_id}",
                params={"uploadType": "multipart"},
                content=body,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )
            logger.debug("gdrive: fichier mis à jour %s (id=%s)", path, existing_id)
        else:
            # Création d'un nouveau fichier
            resp = await self._request(
                "POST",
                _UPLOAD_BASE,
                params={"uploadType": "multipart"},
                content=body,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )
            new_id = resp.json().get("id", "")
            if new_id and cache_key:
                self._path_cache[cache_key] = new_id
            logger.debug("gdrive: fichier créé %s (id=%s)", path, new_id)

    async def mkdir(self, path: str) -> None:
        """Crée un dossier Google Drive (et ses parents si nécessaire). Idempotent."""
        await self._ensure_folder(path)

    async def exists(self, path: str) -> bool:
        """Vérifie si un item Google Drive existe."""
        try:
            await self._resolve_path(path)
            return True
        except StorageFileNotFoundError:
            return False
        except (StorageError, StorageAuthError):
            return False

    async def get_etag(self, path: str) -> str | None:
        """Retourne le md5Checksum d'un fichier Drive (None pour les dossiers)."""
        try:
            file_id = await self._resolve_path(path)
        except StorageFileNotFoundError:
            return None

        try:
            resp = await self._request(
                "GET",
                f"{_DRIVE_BASE}/files/{file_id}",
                params={"fields": "md5Checksum,mimeType"},
            )
            data = resp.json()
            # Les dossiers n'ont pas de md5Checksum
            if data.get("mimeType") == _FOLDER_MIME:
                return None
            return data.get("md5Checksum") or None
        except (StorageFileNotFoundError, StorageError):
            return None

    async def delete(self, path: str) -> None:
        """Supprime un item Google Drive (fichier ou dossier). Idempotent."""
        try:
            file_id = await self._resolve_path(path)
        except StorageFileNotFoundError:
            return  # déjà absent — idempotent

        try:
            await self._request("DELETE", f"{_DRIVE_BASE}/files/{file_id}")
            logger.debug("gdrive: supprimé %s (id=%s)", path, file_id)
        except StorageFileNotFoundError:
            pass  # race condition — idempotent

        self._invalidate_cache(path)


# =============================================================================
# Helpers
# =============================================================================

def _split_path(path: str) -> tuple[str, str]:
    """Sépare un chemin en (parent_path, filename).

    Exemple: "/a/b/c.pdf" → ("/a/b", "c.pdf")
    Exemple: "/file.pdf" → ("/", "file.pdf")
    """
    path = path.rstrip("/")
    if "/" not in path.lstrip("/"):
        return "/", path.lstrip("/")
    parent = path.rsplit("/", 1)[0] or "/"
    name = path.rsplit("/", 1)[1]
    return parent, name
