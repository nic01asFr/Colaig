"""
Colaig — S3Storage (MinIO / Scaleway / OVH / AWS S3)

Implémente StorageProtocol via l'API S3.
Utilise boto3 (synchrone) wrappé dans asyncio.to_thread pour l'async.
Compatible avec tout endpoint S3 (AWS S3, MinIO, Scaleway Object Storage,
OVH Object Storage, Clever Cloud Cellar…).

Dépendance optionnelle : boto3
    pip install boto3
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timezone
from typing import Optional

from colaig.exceptions import StorageError, StorageFileNotFoundError, StorageAuthError
from colaig.models import StorageFile

logger = logging.getLogger(__name__)


def _require_boto3():
    """Importe boto3, lève ImportError avec message clair si absent."""
    try:
        import boto3
        import botocore.exceptions
        return boto3, botocore.exceptions
    except ImportError:
        raise ImportError(
            "boto3 est requis pour S3Storage. "
            "Installez-le : pip install boto3"
        )


class S3Storage:
    """Backend de stockage S3 pour Colaig.

    Implémente StorageProtocol.

    Compatible avec AWS S3, MinIO, Scaleway Object Storage, OVH Object Storage.
    Toutes les opérations sont async via asyncio.to_thread.

    Args:
        bucket_name: Nom du bucket S3.
        access_key: AWS Access Key ID.
        secret_key: AWS Secret Access Key.
        endpoint_url: URL endpoint custom (None = AWS S3 standard).
            Exemples : "http://minio.local:9000", "https://s3.nl-ams.scw.cloud"
        prefix: Préfixe optionnel appliqué à tous les chemins (ex: "colaig/").
        region: Région AWS (défaut: "us-east-1").
    """

    def __init__(
        self,
        bucket_name: str,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = None,
        prefix: str = "",
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket_name
        self._access_key = access_key
        self._secret_key = secret_key
        self._endpoint_url = endpoint_url or None
        self._prefix = prefix.strip("/")
        self._region = region
        self._client = None  # lazy init

    def _get_client(self):
        """Retourne (ou crée) le client boto3 S3."""
        if self._client is None:
            boto3, _ = _require_boto3()
            self._client = boto3.client(
                "s3",
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                endpoint_url=self._endpoint_url,
                region_name=self._region,
            )
        return self._client

    def _full_key(self, path: str) -> str:
        """Convertit un chemin Colaig en clé S3 (avec préfixe)."""
        from colaig.security.path_validator import validate_storage_path
        path = validate_storage_path(path, allow_dotcolaig=True, context="s3")
        p = path.lstrip("/")
        if self._prefix:
            return f"{self._prefix}/{p}"
        return p

    def _strip_prefix(self, key: str) -> str:
        """Retire le préfixe d'une clé S3 pour obtenir le chemin Colaig."""
        if self._prefix and key.startswith(f"{self._prefix}/"):
            return "/" + key[len(self._prefix) + 1:]
        return "/" + key

    # ------------------------------------------------------------------
    # StorageProtocol

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les objets S3 à un préfixe donné."""
        prefix = self._full_key(path) + "/"

        def _list():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            files: list[StorageFile] = []
            paginator = client.get_paginator("list_objects_v2")
            delimiter = "" if recursive else "/"

            try:
                for page in paginator.paginate(
                    Bucket=self._bucket,
                    Prefix=prefix,
                    Delimiter=delimiter,
                ):
                    # Objets (fichiers)
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        if key == prefix:
                            continue  # Ignorer le "dossier" lui-même
                        name = key.rstrip("/").split("/")[-1]
                        last_mod = obj.get("LastModified")
                        if last_mod and last_mod.tzinfo is None:
                            last_mod = last_mod.replace(tzinfo=timezone.utc)
                        files.append(StorageFile(
                            path=self._strip_prefix(key),
                            name=name,
                            is_directory=key.endswith("/"),
                            size=obj.get("Size", 0),
                            etag=obj.get("ETag", "").strip('"'),
                            last_modified=last_mod,
                        ))
                    # Préfixes communs ("sous-dossiers" en mode non-récursif)
                    for cp in page.get("CommonPrefixes", []):
                        cp_key = cp["Prefix"]
                        name = cp_key.rstrip("/").split("/")[-1]
                        files.append(StorageFile(
                            path=self._strip_prefix(cp_key),
                            name=name,
                            is_directory=True,
                        ))
            except botocore_exc.ClientError as e:
                _raise_storage_error(e)
            return files

        return await asyncio.to_thread(_list)

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un objet S3."""
        key = self._full_key(path)

        def _download():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                response = client.get_object(Bucket=self._bucket, Key=key)
                return response["Body"].read()
            except botocore_exc.ClientError as e:
                _raise_storage_error(e, path)

        return await asyncio.to_thread(_download)

    async def download_if_changed(self, path: str, known_etag: str) -> Optional[bytes]:
        """Télécharge seulement si l'etag a changé."""
        key = self._full_key(path)

        def _download_conditional():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                response = client.get_object(
                    Bucket=self._bucket,
                    Key=key,
                    IfNoneMatch=known_etag,
                )
                return response["Body"].read()
            except botocore_exc.ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "304" or "NotModified" in code:
                    return None
                _raise_storage_error(e, path)

        return await asyncio.to_thread(_download_conditional)

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un objet S3."""
        key = self._full_key(path)

        def _upload():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                client.put_object(Bucket=self._bucket, Key=key, Body=content)
            except botocore_exc.ClientError as e:
                _raise_storage_error(e, path)

        await asyncio.to_thread(_upload)

    async def mkdir(self, path: str) -> None:
        """Crée un 'dossier' S3 (objet vide avec slash terminal)."""
        key = self._full_key(path) + "/"

        def _mkdir():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                client.put_object(Bucket=self._bucket, Key=key, Body=b"")
            except botocore_exc.ClientError as e:
                _raise_storage_error(e, path)

        await asyncio.to_thread(_mkdir)

    async def exists(self, path: str) -> bool:
        """Vérifie si un objet ou préfixe existe."""
        key = self._full_key(path)

        def _head():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            # Essayer d'abord comme objet exact
            try:
                client.head_object(Bucket=self._bucket, Key=key)
                return True
            except botocore_exc.ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "404":
                    pass
                elif code in ("403", "401"):
                    return False
                else:
                    return False

            # Essayer comme préfixe (dossier)
            try:
                prefix = key.rstrip("/") + "/"
                response = client.list_objects_v2(
                    Bucket=self._bucket, Prefix=prefix, MaxKeys=1
                )
                return bool(response.get("Contents") or response.get("CommonPrefixes"))
            except botocore_exc.ClientError:
                return False

        return await asyncio.to_thread(_head)

    async def get_etag(self, path: str) -> Optional[str]:
        """Retourne l'etag d'un objet S3."""
        key = self._full_key(path)

        def _head():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                response = client.head_object(Bucket=self._bucket, Key=key)
                return response.get("ETag", "").strip('"') or None
            except botocore_exc.ClientError:
                return None

        return await asyncio.to_thread(_head)

    async def delete(self, path: str) -> None:
        """Supprime un objet S3."""
        key = self._full_key(path)

        def _delete():
            client = self._get_client()
            _, botocore_exc = _require_boto3()
            try:
                client.delete_object(Bucket=self._bucket, Key=key)
            except botocore_exc.ClientError as e:
                _raise_storage_error(e, path)

        await asyncio.to_thread(_delete)


# =============================================================================
# Helpers
# =============================================================================

def _raise_storage_error(exc, path: str = "") -> None:
    """Convertit une exception botocore en exception Colaig."""
    _, botocore_exc = _require_boto3()
    code = exc.response.get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
    msg = str(exc)

    if code in ("404", "NoSuchKey", "NoSuchBucket"):
        raise StorageFileNotFoundError(f"S3 objet introuvable: {path}") from exc
    if code in ("403", "AccessDenied", "401"):
        raise StorageAuthError(f"S3 accès refusé: {path}") from exc
    raise StorageError(f"S3 erreur [{code}]: {msg}") from exc
