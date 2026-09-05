"""
Tests pour colaig/integrations/storage/bigfolder.py — BigfolderStorage.

Stratégie : mock de _request (niveau HTTP) — pas de cache à manipuler.
Tous les endpoints sont path-based via /storage/*.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.exceptions import StorageAuthError, StorageError, StorageFileNotFoundError
from colaig.integrations.storage.bigfolder import (
    BigfolderStorage,
    _backoff_delay,
    _parse_iso_dt,
)
from colaig.models import StorageFile

# =============================================================================
# Constantes de test
# =============================================================================

API_URL = "http://localhost:8002"
SERVICE_TOKEN = "svc_testtoken64hexcharactersxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
BF_ROOT = "/MonEspace"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def storage():
    return BigfolderStorage(
        api_url=API_URL,
        service_token=SERVICE_TOKEN,
        bigfolder_root=BF_ROOT,
    )


@pytest.fixture
def storage_root():
    """BigfolderStorage avec bigfolder_root='/' (racine)."""
    return BigfolderStorage(
        api_url=API_URL,
        service_token=SERVICE_TOKEN,
        bigfolder_root="/",
    )


def make_resp(status_code: int, data=None, content: bytes = b""):
    """Crée un mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data if data is not None else {}
    resp.content = content or (json.dumps(data).encode() if data else b"")
    resp.text = json.dumps(data) if data else ""
    return resp


def make_ls_entry(name: str, path: str, is_dir: bool = False, size: int = 1024,
                  doc_id: str = "doc-uuid-1"):
    """Crée une entrée de réponse /storage/ls."""
    if is_dir:
        return {"name": name, "path": path, "type": "folder"}
    return {
        "name": name,
        "path": path,
        "type": "file",
        "size": size,
        "status": "analyzed",
        "document_uri": f"http://localhost:8002/documents/{doc_id}",
        "updated_at": "2026-02-25T11:00:00Z",
    }


def make_ls_resp(entries: list) -> MagicMock:
    return make_resp(200, {"path": "/", "entries": entries, "count": len(entries)})


def make_exists_resp(exists: bool, path: str = "", doc_id: str = "doc-uuid-1"):
    if exists:
        return make_resp(200, {
            "exists": True,
            "path": path,
            "type": "file",
            "document_id": doc_id,
            "document_uri": f"http://localhost:8002/documents/{doc_id}",
            "size": 1024,
        })
    return make_resp(200, {"exists": False, "path": path})


# =============================================================================
# TestPathMapping
# =============================================================================


class TestPathMapping:

    def test_to_bfpath_prepends_root(self, storage):
        assert storage._to_bfpath("/documents/guide.pdf") == "/MonEspace/documents/guide.pdf"

    def test_to_bfpath_dotcolaig(self, storage):
        assert storage._to_bfpath("/.colaig/indexes/index.faiss") == "/MonEspace/.colaig/indexes/index.faiss"

    def test_to_bfpath_root_storage(self, storage_root):
        assert storage_root._to_bfpath("/documents/guide.pdf") == "/documents/guide.pdf"

    def test_to_colaig_path_strips_root(self, storage):
        assert storage._to_colaig_path("/MonEspace/documents/guide.pdf") == "/documents/guide.pdf"

    def test_to_colaig_path_root_storage(self, storage_root):
        assert storage_root._to_colaig_path("/documents/guide.pdf") == "/documents/guide.pdf"

    def test_roundtrip(self, storage):
        original = "/documents/guide.pdf"
        assert storage._to_colaig_path(storage._to_bfpath(original)) == original

    def test_roundtrip_nested(self, storage):
        original = "/.colaig/indexes/documents/index.faiss"
        assert storage._to_colaig_path(storage._to_bfpath(original)) == original


# =============================================================================
# TestListFiles
# =============================================================================


class TestListFiles:

    async def test_returns_storage_files(self, storage):
        entries = [
            make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf", doc_id="d1"),
            make_ls_entry("note.md", "/MonEspace/documents/note.md", doc_id="d2"),
        ]
        storage._request = AsyncMock(return_value=make_ls_resp(entries))

        files = await storage.list_files("/documents/")
        assert len(files) == 2
        assert all(isinstance(f, StorageFile) for f in files)

    async def test_files_have_colaig_paths(self, storage):
        entries = [make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf")]
        storage._request = AsyncMock(return_value=make_ls_resp(entries))

        files = await storage.list_files("/documents/")
        assert files[0].path == "/documents/guide.pdf"

    async def test_files_have_correct_name(self, storage):
        entries = [make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf")]
        storage._request = AsyncMock(return_value=make_ls_resp(entries))

        files = await storage.list_files("/documents/")
        assert files[0].name == "guide.pdf"

    async def test_etag_uses_document_id(self, storage):
        entries = [make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf", doc_id="my-doc-uuid")]
        storage._request = AsyncMock(return_value=make_ls_resp(entries))

        files = await storage.list_files("/documents/")
        assert files[0].etag == "my-doc-uuid"

    async def test_non_recursive_emits_subdir_entry(self, storage):
        entries = [
            make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf"),
            make_ls_entry("sub", "/MonEspace/documents/sub/", is_dir=True),
        ]
        storage._request = AsyncMock(return_value=make_ls_resp(entries))

        files = await storage.list_files("/documents/", recursive=False)
        dirs = [f for f in files if f.is_directory]
        assert len(dirs) == 1
        assert dirs[0].name == "sub"

    async def test_recursive_calls_ls_for_subdirs(self, storage):
        root_entries = [
            make_ls_entry("guide.pdf", "/MonEspace/documents/guide.pdf"),
            make_ls_entry("sub", "/MonEspace/documents/sub/", is_dir=True),
        ]
        sub_entries = [
            make_ls_entry("note.pdf", "/MonEspace/documents/sub/note.pdf", doc_id="d2"),
        ]

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            bf_path = kwargs.get("params", {}).get("path", "")
            if "sub" in bf_path:
                return make_ls_resp(sub_entries)
            return make_ls_resp(root_entries)

        storage._request = mock_request

        files = await storage.list_files("/documents/", recursive=True)
        file_paths = [f.path for f in files if not f.is_directory]
        assert "/documents/guide.pdf" in file_paths
        assert "/documents/sub/note.pdf" in file_paths
        assert call_count == 2

    async def test_empty_returns_empty(self, storage):
        storage._request = AsyncMock(return_value=make_ls_resp([]))
        files = await storage.list_files("/documents/")
        assert files == []

    async def test_not_found_returns_empty(self, storage):
        storage._request = AsyncMock(side_effect=StorageFileNotFoundError("404"))
        files = await storage.list_files("/nonexistent/")
        assert files == []


# =============================================================================
# TestDownload
# =============================================================================


class TestDownload:

    async def test_download_success(self, storage):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"contenu du guide"
        storage._request = AsyncMock(return_value=mock_resp)

        content = await storage.download("/documents/guide.pdf")
        assert content == b"contenu du guide"

    async def test_download_calls_correct_endpoint(self, storage):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"data"
        storage._request = AsyncMock(return_value=mock_resp)

        await storage.download("/documents/guide.pdf")

        call_args = storage._request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "/storage/download"
        assert call_args[1]["params"]["path"] == "/MonEspace/documents/guide.pdf"

    async def test_download_not_found_raises(self, storage):
        storage._request = AsyncMock(side_effect=StorageFileNotFoundError("404"))
        with pytest.raises(StorageFileNotFoundError):
            await storage.download("/nonexistent.pdf")


# =============================================================================
# TestDownloadIfChanged
# =============================================================================


class TestDownloadIfChanged:

    async def test_returns_none_when_etag_unchanged(self, storage):
        storage._request = AsyncMock(
            return_value=make_exists_resp(True, doc_id="same-etag")
        )
        result = await storage.download_if_changed("/documents/guide.pdf", "same-etag")
        assert result is None

    async def test_returns_content_when_etag_changed(self, storage):
        exists_resp = make_exists_resp(True, doc_id="new-etag")
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"nouveau contenu"

        storage._request = AsyncMock(side_effect=[exists_resp, download_resp])
        result = await storage.download_if_changed("/documents/guide.pdf", "old-etag")
        assert result == b"nouveau contenu"

    async def test_returns_none_when_file_not_found(self, storage):
        storage._request = AsyncMock(return_value=make_exists_resp(False))
        result = await storage.download_if_changed("/nonexistent.pdf", "any-etag")
        assert result is None


# =============================================================================
# TestUpload
# =============================================================================


class TestUpload:

    async def test_upload_success(self, storage):
        storage._request = AsyncMock(return_value=make_resp(201, {"id": "new-id"}))
        await storage.upload("/documents/new.txt", b"contenu")
        storage._request.assert_called_once()

    async def test_upload_calls_store_endpoint(self, storage):
        storage._request = AsyncMock(return_value=make_resp(201, {}))

        await storage.upload("/documents/sub/guide.pdf", b"contenu")

        call_args = storage._request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/storage/store"
        assert call_args[1]["data"]["path"] == "/MonEspace/documents/sub/guide.pdf"

    async def test_upload_bad_status_raises(self, storage):
        storage._request = AsyncMock(return_value=make_resp(500))
        with pytest.raises(StorageError):
            await storage.upload("/docs/new.txt", b"contenu")

    async def test_upload_no_delete_needed(self, storage):
        """Bigfolder gère le remplacement côté serveur — pas de DELETE préalable."""
        storage._request = AsyncMock(return_value=make_resp(201, {}))
        await storage.upload("/documents/guide.pdf", b"nouveau contenu")

        # Un seul appel : POST /storage/store
        assert storage._request.call_count == 1
        assert storage._request.call_args[0][0] == "POST"


# =============================================================================
# TestDelete
# =============================================================================


class TestDelete:

    async def test_delete_success(self, storage):
        storage._request = AsyncMock(return_value=make_resp(200, {"deleted": True}))
        await storage.delete("/documents/guide.pdf")

        call_args = storage._request.call_args
        assert call_args[0][0] == "DELETE"
        assert call_args[0][1] == "/storage/delete"
        assert call_args[1]["params"]["path"] == "/MonEspace/documents/guide.pdf"

    async def test_delete_nonexistent_is_noop(self, storage):
        storage._request = AsyncMock(side_effect=StorageFileNotFoundError("404"))
        # Ne doit pas lever d'exception
        await storage.delete("/nonexistent.pdf")


# =============================================================================
# TestExists
# =============================================================================


class TestExists:

    async def test_exists_true(self, storage):
        storage._request = AsyncMock(
            return_value=make_exists_resp(True, "/MonEspace/documents/guide.pdf")
        )
        assert await storage.exists("/documents/guide.pdf") is True

    async def test_exists_false(self, storage):
        storage._request = AsyncMock(
            return_value=make_exists_resp(False, "/MonEspace/nonexistent.pdf")
        )
        assert await storage.exists("/nonexistent.pdf") is False

    async def test_exists_calls_correct_endpoint(self, storage):
        storage._request = AsyncMock(
            return_value=make_exists_resp(True, "/MonEspace/documents/guide.pdf")
        )
        await storage.exists("/documents/guide.pdf")

        call_args = storage._request.call_args
        assert call_args[0][1] == "/storage/exists"
        assert call_args[1]["params"]["path"] == "/MonEspace/documents/guide.pdf"

    async def test_exists_404_returns_false(self, storage):
        storage._request = AsyncMock(side_effect=StorageFileNotFoundError("404"))
        assert await storage.exists("/nonexistent.pdf") is False


# =============================================================================
# TestMkdir
# =============================================================================


class TestMkdir:

    async def test_mkdir_calls_endpoint(self, storage):
        storage._request = AsyncMock(return_value=make_resp(201, {"created": True}))
        await storage.mkdir("/new-folder/")

        call_args = storage._request.call_args
        assert call_args[0][1] == "/storage/mkdir"
        assert call_args[1]["params"]["path"].startswith("/MonEspace/new-folder")

    async def test_mkdir_error_is_noop(self, storage):
        storage._request = AsyncMock(side_effect=StorageError("mkdir failed"))
        # Ne doit pas lever d'exception
        await storage.mkdir("/new-folder/")


# =============================================================================
# TestGetEtag
# =============================================================================


class TestGetEtag:

    async def test_get_etag_returns_document_id(self, storage):
        storage._request = AsyncMock(
            return_value=make_exists_resp(True, doc_id="my-stable-uuid")
        )
        etag = await storage.get_etag("/documents/guide.pdf")
        assert etag == "my-stable-uuid"

    async def test_get_etag_not_found_returns_none(self, storage):
        storage._request = AsyncMock(return_value=make_exists_resp(False))
        assert await storage.get_etag("/nonexistent.pdf") is None

    async def test_get_etag_404_returns_none(self, storage):
        storage._request = AsyncMock(side_effect=StorageFileNotFoundError("404"))
        assert await storage.get_etag("/nonexistent.pdf") is None


# =============================================================================
# TestAuthentication
# =============================================================================


class TestAuthentication:

    async def test_request_uses_bearer_token(self, storage):
        """Vérifie que le client httpx utilise Authorization: Bearer."""
        client = await storage._get_client()
        auth_header = client.headers.get("authorization")
        assert auth_header == f"Bearer {SERVICE_TOKEN}"

    async def test_auth_error_raised_on_401(self, storage):
        storage._request = AsyncMock(side_effect=StorageAuthError("401"))
        with pytest.raises(StorageAuthError):
            await storage.download("/docs/test.txt")


# =============================================================================
# TestHelpers
# =============================================================================


class TestHelpers:

    def test_parse_iso_dt_valid(self):
        result = _parse_iso_dt("2026-02-25T10:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_parse_iso_dt_z_suffix(self):
        result = _parse_iso_dt("2026-02-25T10:00:00Z")
        assert isinstance(result, datetime)

    def test_parse_iso_dt_none(self):
        assert _parse_iso_dt(None) is None

    def test_parse_iso_dt_empty(self):
        assert _parse_iso_dt("") is None

    def test_parse_iso_dt_invalid(self):
        assert _parse_iso_dt("pas-une-date") is None

    def test_backoff_delay_increases_with_attempt(self):
        d0 = _backoff_delay(0)
        d1 = _backoff_delay(1)
        d2 = _backoff_delay(2)
        assert d1 > d0
        assert d2 > d1

    def test_backoff_delay_has_maximum(self):
        d_big = _backoff_delay(100, maximum=60.0)
        assert d_big <= 66.0

    def test_backoff_delay_positive(self):
        for i in range(5):
            assert _backoff_delay(i) > 0
