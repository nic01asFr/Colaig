"""
Tests pour colaig/integrations/storage/s3.py — S3Storage.

Stratégie : mock de boto3 (import optionnel) et mock du client boto3 pour
isoler chaque opération sans connexion S3 réelle.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.integrations.storage.s3 import S3Storage, _raise_storage_error


# =============================================================================
# Fake exception class (remplace botocore.exceptions.ClientError)
# =============================================================================

class FakeClientError(Exception):
    """Substitut de botocore.exceptions.ClientError pour les tests."""
    def __init__(self, code: str, message: str = "error"):
        self.response = {"Error": {"Code": code, "Message": message}}
        super().__init__(message)


def _fake_botocore():
    """Retourne un module botocore simulé avec ClientError = FakeClientError."""
    bc = MagicMock()
    bc.ClientError = FakeClientError
    return bc


def _make_s3(prefix: str = "") -> S3Storage:
    return S3Storage(
        bucket_name="my-bucket",
        access_key="AKID",
        secret_key="SECRET",
        endpoint_url="http://minio.local:9000",
        prefix=prefix,
        region="us-east-1",
    )


# =============================================================================
# Construction + _full_key / _strip_prefix
# =============================================================================


class TestKeyHelpers:
    def test_full_key_no_prefix(self):
        s3 = _make_s3()
        assert s3._full_key("/docs/guide.pdf") == "docs/guide.pdf"

    def test_full_key_with_prefix(self):
        s3 = _make_s3(prefix="colaig")
        assert s3._full_key("/docs/guide.pdf") == "colaig/docs/guide.pdf"

    def test_full_key_root(self):
        s3 = _make_s3(prefix="colaig")
        assert s3._full_key("/") == "colaig/"

    def test_strip_prefix_no_prefix(self):
        s3 = _make_s3()
        assert s3._strip_prefix("docs/guide.pdf") == "/docs/guide.pdf"

    def test_strip_prefix_with_prefix(self):
        s3 = _make_s3(prefix="colaig")
        assert s3._strip_prefix("colaig/docs/guide.pdf") == "/docs/guide.pdf"

    def test_strip_prefix_no_match(self):
        s3 = _make_s3(prefix="colaig")
        assert s3._strip_prefix("other/docs/guide.pdf") == "/other/docs/guide.pdf"


# =============================================================================
# _require_boto3 — import manquant
# =============================================================================


class TestRequireBoto3:
    def test_import_error_when_missing(self):
        """Si boto3 n'est pas installé, lève ImportError avec message clair."""
        from colaig.integrations.storage import s3 as s3_mod
        with patch.object(s3_mod, "_require_boto3", side_effect=ImportError("boto3 est requis")):
            s3 = _make_s3()
            with pytest.raises(ImportError, match="boto3"):
                s3._get_client()


# =============================================================================
# list_files
# =============================================================================


class TestListFiles:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_list_files_returns_files(self, s3):
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_page = {
            "Contents": [
                {
                    "Key": "colaig/docs/guide.pdf",
                    "Size": 12345,
                    "ETag": '"abc123"',
                    "LastModified": datetime(2025, 1, 1, tzinfo=timezone.utc),
                },
            ],
            "CommonPrefixes": [],
        }
        mock_paginator.paginate.return_value = [mock_page]
        mock_client.get_paginator.return_value = mock_paginator

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                files = await s3.list_files("/docs")

        assert len(files) == 1
        assert files[0].name == "guide.pdf"
        assert files[0].size == 12345
        assert files[0].etag == "abc123"
        assert files[0].is_directory is False

    @pytest.mark.asyncio
    async def test_list_files_includes_subdirs(self, s3):
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_page = {
            "Contents": [],
            "CommonPrefixes": [{"Prefix": "colaig/docs/subdir/"}],
        }
        mock_paginator.paginate.return_value = [mock_page]
        mock_client.get_paginator.return_value = mock_paginator

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                files = await s3.list_files("/docs")

        assert len(files) == 1
        assert files[0].name == "subdir"
        assert files[0].is_directory is True

    @pytest.mark.asyncio
    async def test_list_files_skips_prefix_itself(self, s3):
        """L'objet dont la clé == le préfixe doit être ignoré."""
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_page = {
            "Contents": [
                {"Key": "colaig/docs/", "Size": 0, "ETag": "", "LastModified": None},
            ],
            "CommonPrefixes": [],
        }
        mock_paginator.paginate.return_value = [mock_page]
        mock_client.get_paginator.return_value = mock_paginator

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                files = await s3.list_files("/docs")

        assert files == []


# =============================================================================
# download
# =============================================================================


class TestDownload:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_download_returns_bytes(self, s3):
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"hello pdf")}

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                result = await s3.download("/docs/guide.pdf")

        assert result == b"hello pdf"
        mock_client.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="colaig/docs/guide.pdf"
        )

    @pytest.mark.asyncio
    async def test_download_not_found(self, s3):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = FakeClientError("NoSuchKey")

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                with pytest.raises(StorageFileNotFoundError):
                    await s3.download("/docs/missing.pdf")


# =============================================================================
# upload + mkdir
# =============================================================================


class TestUploadMkdir:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_upload_calls_put_object(self, s3):
        mock_client = MagicMock()

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                await s3.upload("/docs/out.txt", b"content")

        mock_client.put_object.assert_called_once_with(
            Bucket="my-bucket", Key="colaig/docs/out.txt", Body=b"content"
        )

    @pytest.mark.asyncio
    async def test_mkdir_creates_slash_terminated_key(self, s3):
        mock_client = MagicMock()

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                await s3.mkdir("/new-dir")

        mock_client.put_object.assert_called_once_with(
            Bucket="my-bucket", Key="colaig/new-dir/", Body=b""
        )


# =============================================================================
# exists
# =============================================================================


class TestExists:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_exists_true_via_head(self, s3):
        mock_client = MagicMock()
        mock_client.head_object.return_value = {}

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                result = await s3.exists("/docs/guide.pdf")

        assert result is True

    @pytest.mark.asyncio
    async def test_exists_false_when_not_found(self, s3):
        mock_client = MagicMock()
        mock_client.head_object.side_effect = FakeClientError("404")
        mock_client.list_objects_v2.return_value = {}

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                result = await s3.exists("/docs/missing.pdf")

        assert result is False


# =============================================================================
# get_etag + delete
# =============================================================================


class TestGetEtagDelete:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_get_etag_returns_value(self, s3):
        mock_client = MagicMock()
        mock_client.head_object.return_value = {"ETag": '"abc123"'}

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                etag = await s3.get_etag("/docs/guide.pdf")

        assert etag == "abc123"

    @pytest.mark.asyncio
    async def test_delete_calls_delete_object(self, s3):
        mock_client = MagicMock()

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                await s3.delete("/docs/old.txt")

        mock_client.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="colaig/docs/old.txt"
        )


# =============================================================================
# _raise_storage_error
# =============================================================================


class TestRaiseStorageError:
    def test_not_found_codes(self):
        for code in ("404", "NoSuchKey", "NoSuchBucket"):
            exc = FakeClientError(code)
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                with pytest.raises(StorageFileNotFoundError):
                    _raise_storage_error(exc, "/test")

    def test_auth_codes(self):
        for code in ("403", "AccessDenied", "401"):
            exc = FakeClientError(code)
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                with pytest.raises(StorageAuthError):
                    _raise_storage_error(exc, "/test")

    def test_generic_error(self):
        exc = FakeClientError("InternalError")
        with patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            with pytest.raises(StorageError):
                _raise_storage_error(exc, "/test")


# =============================================================================
# download_if_changed
# =============================================================================


class TestDownloadIfChanged:
    @pytest.fixture
    def s3(self):
        return _make_s3(prefix="colaig")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_modified(self, s3):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = FakeClientError("304")

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                result = await s3.download_if_changed("/docs/guide.pdf", '"abc123"')

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bytes_when_changed(self, s3):
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"new content")}

        with patch.object(s3, "_get_client", return_value=mock_client):
            with patch("colaig.integrations.storage.s3._require_boto3",
                       return_value=(MagicMock(), _fake_botocore())):
                result = await s3.download_if_changed("/docs/guide.pdf", '"old-etag"')

        assert result == b"new content"
