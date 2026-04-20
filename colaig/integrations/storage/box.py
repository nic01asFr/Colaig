"""
Colaig — BoxStorage (Box.com)

Implémente StorageProtocol via l'API Box (JWT Server Authentication).
Utilise box-sdk-gen (synchrone) wrappé dans asyncio.to_thread pour l'async.

Box est un système orienté ID : chaque fichier/dossier a un identifiant
opaque. BoxStorage maintient un cache chemin → ID pour simuler l'adressage
par chemin attendu par StorageProtocol.

Auth supportée : JWT (Server Authentication — recommandée pour services background).
Le fichier de config JSON téléchargé depuis le Developer Console Box contient
tous les credentials nécessaires (clientID, clientSecret, publicKeyID,
privateKey, passphrase, enterpriseID).

Dépendance optionnelle : box-sdk-gen
    pip install box-sdk-gen
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from colaig.exceptions import StorageError, StorageFileNotFoundError, StorageAuthError
from colaig.models import StorageFile, StorageWebhookEvent

logger = logging.getLogger(__name__)

# TTL du cache chemin → ID (secondes)
_CACHE_TTL = 60.0

# Correspondance trigger Box → type d'événement générique Colaig
_BOX_TRIGGER_MAP: dict[str, str] = {
    "COLLABORATION.CREATED": "collaboration_added",
    "FILE.UPLOADED": "file_created",
    "FILE.CREATED": "file_created",
    "FILE.MODIFIED": "file_modified",
    "FILE.DELETED": "file_deleted",
    "FOLDER.CREATED": "file_created",
}


def _require_box_sdk():
    """Importe box_sdk_gen, lève ImportError avec message clair si absent."""
    try:
        import box_sdk_gen
        from box_sdk_gen import BoxAPIError
        return box_sdk_gen, BoxAPIError
    except ImportError:
        raise ImportError(
            "box-sdk-gen est requis pour BoxStorage. "
            "Installez-le : pip install box-sdk-gen"
        )


class BoxStorage:
    """Backend de stockage Box.com pour Colaig.

    Implémente StorageProtocol.

    Toutes les opérations sont async via asyncio.to_thread (le SDK box-sdk-gen
    est synchrone). Un cache chemin → ID Box évite de re-traverser l'arbre
    à chaque appel.

    Args:
        client_id: Client ID de l'application Box (Developer Console).
        client_secret: Client secret de l'application.
        enterprise_id: Enterprise ID Box (Admin Console → Enterprise Settings).
        public_key_id: ID de la clé publique JWT enregistrée dans Box.
        private_key: Clé privée RSA au format PEM (avec sauts de ligne).
        passphrase: Passphrase de la clé privée RSA.
        root_folder_id: ID du dossier racine Box (défaut: "0" = racine du
            compte service). Fournir l'ID d'un dossier partagé pour restreindre
            la vue de Colaig à ce sous-arbre.
        user_id: Si renseigné, Colaig agit comme cet utilisateur Box plutôt
            que comme le service account. L'utilisateur doit avoir accordé
            l'accès à l'application.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        enterprise_id: str,
        public_key_id: str,
        private_key: str,
        passphrase: str,
        root_folder_id: str = "0",
        user_id: str = "",
        webhook_primary_key: str = "",
        webhook_secondary_key: str = "",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._enterprise_id = enterprise_id
        self._public_key_id = public_key_id
        self._private_key = private_key
        self._passphrase = passphrase
        self._root_folder_id = root_folder_id or "0"
        self._user_id = user_id
        self._webhook_primary_key = webhook_primary_key
        self._webhook_secondary_key = webhook_secondary_key

        # Cache chemin Colaig → (box_id, item_type)
        # item_type : "file" | "folder"
        self._item_cache: dict[str, tuple[str, str]] = {}
        self._cache_ts: dict[str, float] = {}

        self._client = None  # lazy init

    # ------------------------------------------------------------------
    # Client Box (lazy init, sync)

    def _get_client(self):
        """Retourne (ou crée) le client BoxClient JWT."""
        if self._client is None:
            _, _ = _require_box_sdk()
            from box_sdk_gen import BoxClient, BoxJWTAuth, JWTConfig

            jwt_config = JWTConfig(
                client_id=self._client_id,
                client_secret=self._client_secret,
                jwt_key_id=self._public_key_id,
                private_key=self._private_key,
                private_key_passphrase=self._passphrase,
                enterprise_id=self._enterprise_id,
            )
            auth = BoxJWTAuth(config=jwt_config)

            if self._user_id:
                # Agir comme un utilisateur Box spécifique
                self._client = BoxClient(auth=auth.with_user_subject(
                    user_id=self._user_id
                ))
            else:
                self._client = BoxClient(auth=auth)

        return self._client

    # ------------------------------------------------------------------
    # Cache chemin → ID

    def _get_cached(self, path: str) -> Optional[tuple[str, str]]:
        """Retourne (id, type) depuis le cache si encore valide."""
        entry = self._item_cache.get(path)
        if entry and time.monotonic() - self._cache_ts.get(path, 0) < _CACHE_TTL:
            return entry
        return None

    def _set_cached(self, path: str, item_id: str, item_type: str) -> None:
        self._item_cache[path] = (item_id, item_type)
        self._cache_ts[path] = time.monotonic()

    def _invalidate_cache(self, path: str) -> None:
        """Invalide le cache pour un chemin (et ses enfants potentiels)."""
        path_clean = path.strip("/")
        keys_to_del = [k for k in self._item_cache if k == path_clean or k.startswith(path_clean + "/")]
        for k in keys_to_del:
            self._item_cache.pop(k, None)
            self._cache_ts.pop(k, None)

    # ------------------------------------------------------------------
    # Traversée arbre Box (sync — appelé depuis asyncio.to_thread)

    def _find_child_sync(self, client, folder_id: str, name: str) -> tuple[str, str]:
        """Cherche un enfant par nom dans un dossier Box.

        Args:
            client: Instance BoxClient.
            folder_id: ID du dossier parent.
            name: Nom de l'item cherché.

        Returns:
            (box_id, item_type) avec item_type = "file" | "folder".

        Raises:
            StorageFileNotFoundError: Si introuvable.
        """
        _, BoxAPIError = _require_box_sdk()
        try:
            offset = 0
            while True:
                items = client.folders.get_folder_items(
                    folder_id=folder_id,
                    limit=1000,
                    offset=offset,
                    fields=["id", "name", "type"],
                )
                entries = items.entries or []
                for item in entries:
                    if item.name == name:
                        return item.id, item.type
                total = getattr(items, "total_count", 0) or 0
                offset += len(entries)
                if offset >= total or not entries:
                    break
        except BoxAPIError as exc:
            _raise_box_error(exc)

        raise StorageFileNotFoundError(
            f"Box: '{name}' introuvable dans le dossier {folder_id}"
        )

    def _resolve_item_sync(self, path: str) -> tuple[str, str]:
        """Résout un chemin Colaig en (box_id, item_type).

        Traverse l'arbre Box depuis root_folder_id, composant par composant.
        Utilise et alimente le cache chemin → ID.

        Raises:
            StorageFileNotFoundError: Si le chemin n'existe pas dans Box.
        """
        from colaig.security.path_validator import validate_storage_path
        path = validate_storage_path(path, allow_dotcolaig=True, context="box")
        path_clean = path.strip("/")

        # Racine = root_folder_id
        if not path_clean:
            return self._root_folder_id, "folder"

        # Cache hit complet ?
        cached = self._get_cached(path_clean)
        if cached:
            return cached

        client = self._get_client()
        parts = path_clean.split("/")
        current_id = self._root_folder_id
        accumulated = ""

        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part

            # Cache intermédiaire ?
            cached = self._get_cached(accumulated)
            if cached:
                current_id, _ = cached
                continue

            child_id, child_type = self._find_child_sync(client, current_id, part)
            self._set_cached(accumulated, child_id, child_type)
            current_id = child_id

        return self._item_cache[path_clean]

    def _ensure_folder_sync(self, path: str) -> str:
        """S'assure qu'un dossier existe, le crée si nécessaire.

        Traverse le chemin composant par composant. Crée les dossiers
        manquants à la volée (comportement mkdir -p).

        Returns:
            ID Box du dossier correspondant au chemin.
        """
        path_clean = path.strip("/")
        if not path_clean:
            return self._root_folder_id

        client = self._get_client()
        parts = path_clean.split("/")
        current_id = self._root_folder_id
        accumulated = ""

        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part

            cached = self._get_cached(accumulated)
            if cached:
                current_id, _ = cached
                continue

            try:
                child_id, child_type = self._find_child_sync(client, current_id, part)
            except StorageFileNotFoundError:
                child_id = self._create_folder_sync(client, current_id, part)
                child_type = "folder"
                logger.debug("box: dossier créé %s (id=%s)", accumulated, child_id)

            self._set_cached(accumulated, child_id, child_type)
            current_id = child_id

        return current_id

    def _create_folder_sync(self, client, parent_id: str, name: str) -> str:
        """Crée un dossier Box dans un parent donné.

        Returns:
            ID Box du nouveau dossier.
        """
        _, BoxAPIError = _require_box_sdk()
        from box_sdk_gen import CreateFolderParent

        try:
            folder = client.folders.create_folder(
                name=name,
                parent=CreateFolderParent(id=parent_id),
            )
            return folder.id
        except BoxAPIError as exc:
            status = _box_status(exc)
            if status == 409:
                # Race condition : dossier créé entre le check et le create
                child_id, _ = self._find_child_sync(client, parent_id, name)
                return child_id
            _raise_box_error(exc)

    # ------------------------------------------------------------------
    # StorageProtocol

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les items d'un dossier Box."""

        def _list():
            _, BoxAPIError = _require_box_sdk()
            try:
                folder_id, _ = self._resolve_item_sync(path)
            except StorageFileNotFoundError:
                return []

            client = self._get_client()
            files: list[StorageFile] = []

            try:
                offset = 0
                while True:
                    items = client.folders.get_folder_items(
                        folder_id=folder_id,
                        limit=1000,
                        offset=offset,
                        fields=["id", "name", "type", "size", "etag", "modified_at"],
                    )
                    entries = items.entries or []
                    for item in entries:
                        is_dir = item.type == "folder"
                        item_path = (path.rstrip("/") + "/" + item.name)

                        # Mettre en cache l'ID de chaque item listé
                        self._set_cached(item_path.strip("/"), item.id, item.type)

                        files.append(StorageFile(
                            path=item_path,
                            name=item.name,
                            is_directory=is_dir,
                            size=getattr(item, "size", 0) or 0,
                            etag=getattr(item, "etag", "") or "",
                            last_modified=_parse_box_datetime(
                                getattr(item, "modified_at", None)
                            ),
                        ))

                    total = getattr(items, "total_count", 0) or 0
                    offset += len(entries)
                    if offset >= total or not entries:
                        break

            except BoxAPIError as exc:
                _raise_box_error(exc, path)

            return files

        files = await asyncio.to_thread(_list)

        # Récursion async (appels Box séquentiels — évite la surcharge de threads)
        if recursive:
            subdirs = [f for f in files if f.is_directory]
            for subdir in subdirs:
                sub = await self.list_files(subdir.path, recursive=True)
                files.extend(sub)

        return files

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier Box."""

        def _download():
            _, BoxAPIError = _require_box_sdk()
            try:
                file_id, _ = self._resolve_item_sync(path)
            except StorageFileNotFoundError:
                raise StorageFileNotFoundError(f"Box: fichier introuvable {path}")

            client = self._get_client()
            try:
                stream = client.downloads.download_file(file_id=file_id)
                return stream.read()
            except BoxAPIError as exc:
                _raise_box_error(exc, path)

        return await asyncio.to_thread(_download)

    async def download_if_changed(self, path: str, known_etag: str) -> Optional[bytes]:
        """Télécharge seulement si l'etag a changé."""

        def _conditional():
            _, BoxAPIError = _require_box_sdk()
            try:
                file_id, _ = self._resolve_item_sync(path)
            except StorageFileNotFoundError:
                return None

            client = self._get_client()
            try:
                file_info = client.files.get_file_by_id(
                    file_id=file_id, fields=["etag"]
                )
                current_etag = (getattr(file_info, "etag", "") or "").strip('"')
                if current_etag and current_etag == known_etag.strip('"'):
                    return None
            except BoxAPIError:
                return None

            try:
                stream = client.downloads.download_file(file_id=file_id)
                return stream.read()
            except BoxAPIError as exc:
                _raise_box_error(exc, path)

        return await asyncio.to_thread(_conditional)

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier Box (crée ou met à jour)."""
        parent_path, filename = _split_path(path)

        def _upload(parent_id: str):
            _, BoxAPIError = _require_box_sdk()
            from box_sdk_gen import (
                UploadFileAttributes,
                UploadFileAttributesParentField,
                UploadFileVersionAttributes,
            )
            client = self._get_client()

            # Chercher si le fichier existe déjà
            file_id: Optional[str] = None
            try:
                fid, ftype = self._find_child_sync(client, parent_id, filename)
                if ftype == "file":
                    file_id = fid
            except StorageFileNotFoundError:
                pass

            try:
                if file_id:
                    # Mise à jour de la version existante
                    client.uploads.upload_file_version(
                        file_id=file_id,
                        attributes=UploadFileVersionAttributes(name=filename),
                        file=io.BytesIO(content),
                    )
                    logger.debug("box: version mise à jour %s (id=%s)", path, file_id)
                else:
                    # Nouveau fichier
                    result = client.uploads.upload_file(
                        attributes=UploadFileAttributes(
                            name=filename,
                            parent=UploadFileAttributesParentField(id=parent_id),
                        ),
                        file=io.BytesIO(content),
                    )
                    # Mettre en cache l'ID du nouveau fichier
                    new_id = result.entries[0].id if result.entries else None
                    if new_id:
                        self._set_cached(path.strip("/"), new_id, "file")
                    logger.debug("box: fichier créé %s", path)
            except BoxAPIError as exc:
                _raise_box_error(exc, path)

        # S'assurer que le dossier parent existe (crée si nécessaire)
        parent_id = await asyncio.to_thread(self._ensure_folder_sync, parent_path)
        await asyncio.to_thread(_upload, parent_id)

    async def mkdir(self, path: str) -> None:
        """Crée un dossier Box (et ses parents si nécessaire). Idempotent."""
        await asyncio.to_thread(self._ensure_folder_sync, path)

    async def exists(self, path: str) -> bool:
        """Vérifie si un item Box existe."""

        def _exists():
            try:
                self._resolve_item_sync(path)
                return True
            except StorageFileNotFoundError:
                return False
            except (StorageError, StorageAuthError):
                return False

        return await asyncio.to_thread(_exists)

    async def get_etag(self, path: str) -> Optional[str]:
        """Retourne l'etag d'un fichier Box."""

        def _etag():
            _, BoxAPIError = _require_box_sdk()
            try:
                item_id, item_type = self._resolve_item_sync(path)
            except StorageFileNotFoundError:
                return None

            client = self._get_client()
            try:
                if item_type == "file":
                    info = client.files.get_file_by_id(
                        file_id=item_id, fields=["etag"]
                    )
                else:
                    info = client.folders.get_folder_by_id(
                        folder_id=item_id, fields=["etag"]
                    )
                return (getattr(info, "etag", "") or "").strip('"') or None
            except BoxAPIError:
                return None

        return await asyncio.to_thread(_etag)

    async def delete(self, path: str) -> None:
        """Supprime un item Box (fichier ou dossier)."""

        def _delete():
            _, BoxAPIError = _require_box_sdk()
            try:
                item_id, item_type = self._resolve_item_sync(path)
            except StorageFileNotFoundError:
                return  # idempotent

            client = self._get_client()
            try:
                if item_type == "folder":
                    client.folders.delete_folder_by_id(
                        folder_id=item_id, recursive=True
                    )
                else:
                    client.files.delete_file_by_id(file_id=item_id)
                logger.debug("box: supprimé %s (id=%s, type=%s)", path, item_id, item_type)
            except BoxAPIError as exc:
                if _box_status(exc) == 404:
                    return  # déjà supprimé
                _raise_box_error(exc, path)

            self._invalidate_cache(path)

        await asyncio.to_thread(_delete)

    # ------------------------------------------------------------------
    # Méthode bonus : dossiers collaborés (pour auto-discovery Cas 2)

    async def list_shared_with_me(self) -> list[StorageFile]:
        """Liste les dossiers partagés/collaborés avec le compte service.

        Dans Box, les dossiers partagés via collaboration avec le service
        account apparaissent dans son arbre racine (folder "0"). Cette
        méthode liste les items de la racine qui sont des dossiers partagés
        (collaboration externe).

        Utile pour l'auto-discovery : quand un utilisateur Box collabore
        un dossier avec le compte service Colaig, cette méthode le détecte.

        Returns:
            Liste de StorageFile (is_directory=True).
        """

        def _shared():
            _, BoxAPIError = _require_box_sdk()
            client = self._get_client()
            files: list[StorageFile] = []
            try:
                offset = 0
                while True:
                    items = client.folders.get_folder_items(
                        folder_id=self._root_folder_id,
                        limit=1000,
                        offset=offset,
                        fields=["id", "name", "type", "shared_link"],
                    )
                    entries = items.entries or []
                    for item in entries:
                        if item.type == "folder":
                            files.append(StorageFile(
                                path=f"/{item.name}",
                                name=item.name,
                                is_directory=True,
                            ))
                    total = getattr(items, "total_count", 0) or 0
                    offset += len(entries)
                    if offset >= total or not entries:
                        break
            except BoxAPIError:
                return []
            return files

        return await asyncio.to_thread(_shared)

    # ------------------------------------------------------------------
    # Webhook Box

    def _validate_box_signature(self, payload: bytes, headers: dict) -> bool:
        """Valide la signature HMAC-SHA256 d'un webhook Box.

        Box signe chaque livraison avec deux clés (primaire + secondaire) pour
        permettre la rotation sans interruption. Il suffit qu'une signature
        soit valide pour accepter l'événement.

        Algorithm Box :
            message = timestamp.encode() + payload
            signature = base64( HMAC-SHA256(key, message) )
        """
        headers_lower = {k.lower(): v for k, v in headers.items()}
        timestamp = headers_lower.get("box-delivery-timestamp", "")
        sig_primary = headers_lower.get("box-signature-primary", "")
        sig_secondary = headers_lower.get("box-signature-secondary", "")

        if not timestamp:
            logger.warning("box webhook: header Box-Delivery-Timestamp manquant")
            return False

        message = timestamp.encode("utf-8") + payload

        def _check(key: str, expected: str) -> bool:
            if not key or not expected:
                return False
            digest = hmac.new(key.encode("utf-8"), message, hashlib.sha256).digest()
            computed = base64.b64encode(digest).decode("utf-8")
            return hmac.compare_digest(computed, expected)

        return _check(self._webhook_primary_key, sig_primary) or \
               _check(self._webhook_secondary_key, sig_secondary)

    async def handle_webhook_event(
        self, payload: bytes, headers: dict
    ) -> Optional[StorageWebhookEvent]:
        """Valide et parse un événement webhook Box (HMAC-SHA256).

        Args:
            payload: Corps brut de la requête HTTP.
            headers: En-têtes HTTP (insensible à la casse).

        Returns:
            StorageWebhookEvent normalisé, ou None si trigger ignoré.

        Raises:
            StorageAuthError: Si la signature HMAC est invalide.
        """
        if not self._validate_box_signature(payload, headers):
            raise StorageAuthError("Box webhook: signature invalide")

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            logger.warning("box webhook: payload JSON invalide")
            return None

        trigger = data.get("trigger", "")
        source = data.get("source") or {}
        event_type = _BOX_TRIGGER_MAP.get(trigger)

        if event_type is None:
            logger.debug("box webhook: trigger ignoré — %s", trigger)
            return None

        folder_path = ""
        folder_id = ""
        file_path = ""
        file_id = ""

        if event_type == "collaboration_added":
            # source = { "type": "collaboration", "item": { "type": "folder", "id": "...", "name": "..." } }
            item = source.get("item") or {}
            folder_id = item.get("id", "")
            folder_name = item.get("name", "")
            folder_path = f"/{folder_name}" if folder_name else ""

        elif event_type in ("file_created", "file_modified", "file_deleted"):
            # source = { "type": "file"|"folder", "id": "...", "name": "...", "parent": {...} }
            file_id = source.get("id", "")
            file_name = source.get("name", "")
            parent = source.get("parent") or {}
            folder_id = parent.get("id", "")
            folder_name = parent.get("name", "")
            folder_path = f"/{folder_name}" if folder_name else ""
            file_path = f"{folder_path}/{file_name}".lstrip("/") if file_name else ""

            # Invalider le cache pour ce chemin (les données ont changé)
            if folder_path:
                self._invalidate_cache(folder_path)

        logger.info(
            "box webhook: %s — dossier=%s file=%s",
            event_type, folder_path or folder_id, file_path or file_id,
        )

        return StorageWebhookEvent(
            event_type=event_type,
            folder_path=folder_path,
            folder_id=folder_id,
            file_path=file_path,
            file_id=file_id,
            raw=data,
        )


# =============================================================================
# Helpers
# =============================================================================

def _split_path(path: str) -> tuple[str, str]:
    """Sépare un chemin en (parent_path, filename).

    Exemple: "/a/b/c.pdf" → ("/a/b", "c.pdf")
    """
    path = path.rstrip("/")
    if "/" not in path.lstrip("/"):
        return "/", path.lstrip("/")
    parent = path.rsplit("/", 1)[0] or "/"
    name = path.rsplit("/", 1)[1]
    return parent, name


def _box_status(exc) -> int:
    """Extrait le code HTTP d'une BoxAPIError."""
    try:
        return exc.response_info.status_code
    except AttributeError:
        return 0


def _raise_box_error(exc, path: str = "") -> None:
    """Convertit une exception Box en exception Colaig."""
    status = _box_status(exc)
    msg = str(exc)

    if status == 404:
        raise StorageFileNotFoundError(f"Box introuvable: {path or msg}") from exc
    if status in (401, 403):
        raise StorageAuthError(f"Box accès refusé [{status}]: {path or msg}") from exc
    raise StorageError(f"Box erreur [{status}]: {msg}") from exc


def _parse_box_datetime(value) -> Optional[datetime]:
    """Parse une date ISO 8601 Box en datetime aware UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
