"""
Tests pour colaig/integrations/storage/msgraph.py — MicrosoftGraphStorage.

Stratégie : mock de _request (niveau HTTP) et _ensure_token pour isoler
chaque opération sans appel réseau réel.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.integrations.storage.msgraph import _GRAPH_BASE, MicrosoftGraphStorage

# =============================================================================
# Fixtures
# =============================================================================


def _make_storage(root_path: str = "") -> MicrosoftGraphStorage:
    return MicrosoftGraphStorage(
        tenant_id="tenant-123",
        client_id="client-abc",
        client_secret="secret-xyz",
        drive_user_id="colaig@org.onmicrosoft.com",
        drive_id="",
        root_path=root_path,
    )


def _mock_resp(data: dict | list, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.content = b"file-bytes"
    return resp


# =============================================================================
# Construction + URL helpers
# =============================================================================


class TestUrlHelpers:
    def test_drive_root_no_drive_id(self):
        s = _make_storage()
        assert s._drive_root() == f"{_GRAPH_BASE}/users/colaig@org.onmicrosoft.com/drive"

    def test_drive_root_with_drive_id(self):
        s = MicrosoftGraphStorage(
            tenant_id="t", client_id="c", client_secret="s",
            drive_user_id="user", drive_id="driveXYZ",
        )
        assert s._drive_root() == f"{_GRAPH_BASE}/drives/driveXYZ"

    def test_full_path_no_root(self):
        s = _make_storage()
        assert s._full_path("/docs/guide.pdf") == "docs/guide.pdf"

    def test_full_path_with_root(self):
        s = _make_storage(root_path="ColaigShared")
        assert s._full_path("/docs/guide.pdf") == "ColaigShared/docs/guide.pdf"

    def test_full_path_empty_with_root(self):
        s = _make_storage(root_path="ColaigShared")
        assert s._full_path("/") == "ColaigShared"

    def test_item_url_root(self):
        s = _make_storage()
        url = s._item_url("/")
        assert url.endswith("/root")

    def test_item_url_non_root(self):
        s = _make_storage()
        url = s._item_url("/docs/guide.pdf")
        assert "root:/docs/guide.pdf" in url

    def test_strip_root(self):
        s = _make_storage(root_path="ColaigShared")
        result = s._strip_root("/ColaigShared/docs/guide.pdf")
        assert result == "/docs/guide.pdf"


# =============================================================================
# Token auth
# =============================================================================


class TestEnsureToken:
    @pytest.mark.asyncio
    async def test_fetches_token_on_first_call(self):
        s = _make_storage()
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "tok123", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post.return_value = token_resp
        # _get_http doit retourner notre mock directement
        s._get_http = AsyncMock(return_value=mock_http)

        token = await s._ensure_token()
        assert token == "tok123"
        assert s._access_token == "tok123"

    @pytest.mark.asyncio
    async def test_reuses_cached_token(self):
        s = _make_storage()
        s._access_token = "cached-tok"
        s._token_expires_at = time.monotonic() + 1000  # bien dans le futur

        token = await s._ensure_token()
        assert token == "cached-tok"

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(self):
        s = _make_storage()
        s._access_token = "old-tok"
        s._token_expires_at = time.monotonic() - 1  # expiré

        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "new-tok", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post.return_value = token_resp
        s._get_http = AsyncMock(return_value=mock_http)

        token = await s._ensure_token()
        assert token == "new-tok"


# =============================================================================
# list_files
# =============================================================================


class TestListFiles:
    @pytest.fixture
    def storage(self):
        return _make_storage(root_path="ColaigShared")

    @pytest.mark.asyncio
    async def test_list_returns_files_and_dirs(self, storage):
        page = {
            "value": [
                {
                    "name": "guide.pdf",
                    "size": 12345,
                    "eTag": '"etag1"',
                    "lastModifiedDateTime": "2025-01-01T12:00:00Z",
                    "file": {"mimeType": "application/pdf"},
                    "parentReference": {"path": "/drive/root:/ColaigShared/docs"},
                },
                {
                    "name": "subfolder",
                    "folder": {},
                    "size": 0,
                    "eTag": '"etag2"',
                    "parentReference": {"path": "/drive/root:/ColaigShared/docs"},
                },
            ],
        }
        storage._request = AsyncMock(return_value=_mock_resp(page))
        storage._ensure_token = AsyncMock(return_value="tok")

        files = await storage.list_files("/docs")

        assert len(files) == 2
        names = {f.name for f in files}
        assert "guide.pdf" in names
        assert "subfolder" in names

        pdf = next(f for f in files if f.name == "guide.pdf")
        assert pdf.is_directory is False
        assert pdf.size == 12345
        assert pdf.content_type == "application/pdf"

        subdir = next(f for f in files if f.name == "subfolder")
        assert subdir.is_directory is True

    @pytest.mark.asyncio
    async def test_list_paginated(self, storage):
        page1 = {
            "value": [{"name": "a.txt", "size": 1, "eTag": '"e1"',
                        "parentReference": {"path": "/drive/root:/ColaigShared"}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
        }
        page2 = {
            "value": [{"name": "b.txt", "size": 2, "eTag": '"e2"',
                        "parentReference": {"path": "/drive/root:/ColaigShared"}}],
        }

        call_count = 0

        async def _request_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_resp(page1)
            return _mock_resp(page2)

        storage._request = _request_side_effect
        storage._ensure_token = AsyncMock(return_value="tok")

        files = await storage.list_files("/")
        assert len(files) == 2
        assert call_count == 2


# =============================================================================
# download
# =============================================================================


class TestDownload:
    @pytest.mark.asyncio
    async def test_download_returns_content(self):
        s = _make_storage()
        resp = _mock_resp({})
        resp.content = b"PDF content"
        s._request = AsyncMock(return_value=resp)
        s._ensure_token = AsyncMock(return_value="tok")

        result = await s.download("/docs/guide.pdf")
        assert result == b"PDF content"

    @pytest.mark.asyncio
    async def test_download_not_found_raises(self):
        s = _make_storage()
        s._request = AsyncMock(side_effect=StorageFileNotFoundError("not found"))
        s._ensure_token = AsyncMock(return_value="tok")

        with pytest.raises(StorageFileNotFoundError):
            await s.download("/docs/missing.pdf")


# =============================================================================
# download_if_changed
# =============================================================================


class TestDownloadIfChanged:
    @pytest.mark.asyncio
    async def test_returns_none_when_etag_matches(self):
        s = _make_storage()

        meta_resp = _mock_resp({"eTag": '"abc123"'})

        async def _req(method, url, **kwargs):
            return meta_resp

        s._request = _req
        s._ensure_token = AsyncMock(return_value="tok")

        result = await s.download_if_changed("/docs/guide.pdf", "abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bytes_when_etag_differs(self):
        s = _make_storage()
        meta_resp = _mock_resp({"eTag": '"new-etag"'})
        content_resp = _mock_resp({})
        content_resp.content = b"new content"

        call_count = 0

        async def _req(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return meta_resp
            return content_resp

        s._request = _req
        s._ensure_token = AsyncMock(return_value="tok")

        result = await s.download_if_changed("/docs/guide.pdf", "old-etag")
        assert result == b"new content"


# =============================================================================
# upload + mkdir
# =============================================================================


class TestUploadMkdir:
    @pytest.mark.asyncio
    async def test_upload_calls_put(self):
        s = _make_storage()
        s._request = AsyncMock(return_value=_mock_resp({}))
        s._ensure_token = AsyncMock(return_value="tok")

        await s.upload("/docs/out.txt", b"content")

        call_args = s._request.call_args
        assert call_args[0][0] == "PUT"

    @pytest.mark.asyncio
    async def test_mkdir_calls_post(self):
        s = _make_storage()
        s._request = AsyncMock(return_value=_mock_resp({}))
        s._ensure_token = AsyncMock(return_value="tok")

        await s.mkdir("/docs/new-folder")

        call_args = s._request.call_args
        assert call_args[0][0] == "POST"
        body = call_args[1].get("json", {})
        assert body["name"] == "new-folder"

    @pytest.mark.asyncio
    async def test_mkdir_ignores_already_exists(self):
        s = _make_storage()
        s._request = AsyncMock(
            side_effect=StorageError("nameAlreadyExists bla bla")
        )
        s._ensure_token = AsyncMock(return_value="tok")

        # Ne doit pas lever d'exception
        await s.mkdir("/docs/existing")


# =============================================================================
# exists + get_etag + delete
# =============================================================================


class TestExistsEtagDelete:
    @pytest.mark.asyncio
    async def test_exists_true(self):
        s = _make_storage()
        s._request = AsyncMock(return_value=_mock_resp({"id": "item-1"}))
        s._ensure_token = AsyncMock(return_value="tok")

        assert await s.exists("/docs/guide.pdf") is True

    @pytest.mark.asyncio
    async def test_exists_false_when_not_found(self):
        s = _make_storage()
        s._request = AsyncMock(side_effect=StorageFileNotFoundError("not found"))
        s._ensure_token = AsyncMock(return_value="tok")

        assert await s.exists("/docs/missing.pdf") is False

    @pytest.mark.asyncio
    async def test_get_etag_returns_stripped(self):
        s = _make_storage()
        s._request = AsyncMock(return_value=_mock_resp({"eTag": '"abc123"'}))
        s._ensure_token = AsyncMock(return_value="tok")

        etag = await s.get_etag("/docs/guide.pdf")
        assert etag == "abc123"

    @pytest.mark.asyncio
    async def test_get_etag_returns_none_on_error(self):
        s = _make_storage()
        s._request = AsyncMock(side_effect=StorageFileNotFoundError("not found"))
        s._ensure_token = AsyncMock(return_value="tok")

        etag = await s.get_etag("/docs/missing.pdf")
        assert etag is None

    @pytest.mark.asyncio
    async def test_delete_calls_delete(self):
        s = _make_storage()
        s._request = AsyncMock(return_value=_mock_resp({}))
        s._ensure_token = AsyncMock(return_value="tok")

        await s.delete("/docs/old.txt")

        call_args = s._request.call_args
        assert call_args[0][0] == "DELETE"


# =============================================================================
# list_shared_with_me (auto-discovery)
# =============================================================================


class TestListSharedWithMe:
    @pytest.mark.asyncio
    async def test_returns_shared_folders(self):
        s = _make_storage()
        resp_data = {
            "value": [
                {"name": "UserFolder", "folder": {}, "eTag": '"etag1"', "remoteItem": {}},
                {"name": "doc.pdf"},  # fichier — ignoré
            ]
        }
        s._request = AsyncMock(return_value=_mock_resp(resp_data))
        s._ensure_token = AsyncMock(return_value="tok")

        shared = await s.list_shared_with_me()
        assert len(shared) == 1
        assert shared[0].name == "UserFolder"
        assert shared[0].is_directory is True
        assert shared[0].path == "/shared/UserFolder"

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        s = _make_storage()
        s._request = AsyncMock(side_effect=StorageError("forbidden"))
        s._ensure_token = AsyncMock(return_value="tok")

        shared = await s.list_shared_with_me()
        assert shared == []


# =============================================================================
# _request — error mapping
# =============================================================================


class TestRequestErrorMapping:
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self):
        s = _make_storage()
        s._ensure_token = AsyncMock(return_value="tok")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_http.request.return_value = mock_resp
        s._get_http = AsyncMock(return_value=mock_http)

        with pytest.raises(StorageAuthError):
            await s._request("GET", "https://graph.microsoft.com/v1.0/test")

    @pytest.mark.asyncio
    async def test_404_raises_file_not_found(self):
        s = _make_storage()
        s._ensure_token = AsyncMock(return_value="tok")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_http.request.return_value = mock_resp
        s._get_http = AsyncMock(return_value=mock_http)

        with pytest.raises(StorageFileNotFoundError):
            await s._request("GET", "https://graph.microsoft.com/v1.0/test")

    @pytest.mark.asyncio
    async def test_500_raises_storage_error(self):
        s = _make_storage()
        s._ensure_token = AsyncMock(return_value="tok")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": {"message": "internal error"}}
        mock_http.request.return_value = mock_resp
        s._get_http = AsyncMock(return_value=mock_http)

        with pytest.raises(StorageError):
            await s._request("GET", "https://graph.microsoft.com/v1.0/test")
