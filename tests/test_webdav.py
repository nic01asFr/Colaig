"""Tests pour colaig/integrations/storage/webdav.py — Client WebDAV."""

from unittest.mock import MagicMock, patch

import pytest

from colaig.integrations.storage.webdav import WebDAVStorage, _parse_propfind


@pytest.fixture
def client() -> WebDAVStorage:
    return WebDAVStorage(
        base_url="https://nextcloud.test.local/remote.php/dav/files/colaig/",
        username="colaig",
        password="secret",
        max_retries=0,
    )


# ── Parsing XML PROPFIND ─────────────────────────────────────────────

PROPFIND_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/colaig/docs/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:displayname>docs</d:displayname>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/colaig/docs/guide.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>12345</d:getcontentlength>
        <d:getetag>"abc123"</d:getetag>
        <d:getlastmodified>Wed, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>application/pdf</d:getcontenttype>
        <d:displayname>guide.pdf</d:displayname>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/colaig/docs/notes.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>500</d:getcontentlength>
        <d:getetag>"def456"</d:getetag>
        <d:getcontenttype>text/plain</d:getcontenttype>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""


class TestParsePropfind:
    """Tests du parsing XML PROPFIND."""

    def test_parse_files_and_directories(self):
        files = _parse_propfind(PROPFIND_RESPONSE, "https://test")
        assert len(files) == 3

    def test_directory_detected(self):
        files = _parse_propfind(PROPFIND_RESPONSE, "https://test")
        dirs = [f for f in files if f.is_directory]
        assert len(dirs) == 1
        assert dirs[0].name == "docs"

    def test_file_metadata(self):
        files = _parse_propfind(PROPFIND_RESPONSE, "https://test")
        pdf = [f for f in files if f.name == "guide.pdf"][0]
        assert pdf.size == 12345
        assert pdf.etag == "abc123"
        assert pdf.content_type == "application/pdf"
        assert not pdf.is_directory

    def test_empty_xml(self):
        assert _parse_propfind("", "https://test") == []

    def test_malformed_xml(self):
        assert _parse_propfind("<broken", "https://test") == []


class TestWebDAVStorage:
    """Tests du client WebDAV avec mock httpx."""

    async def test_list_files(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 207
        mock_response.text = PROPFIND_RESPONSE

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            files = await client.list_files("/docs/")
        assert len(files) == 3

    async def test_download(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"file content"

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            content = await client.download("/docs/guide.pdf")
        assert content == b"file content"

    async def test_download_if_changed_304(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.content = b""

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            result = await client.download_if_changed("/docs/guide.pdf", '"abc123"')
        assert result is None

    async def test_download_if_changed_new_content(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"updated content"

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            result = await client.download_if_changed("/docs/guide.pdf", '"old_etag"')
        assert result == b"updated content"

    async def test_upload(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            await client.upload("/docs/new.txt", b"content")

    async def test_url_construction(self, client):
        url = client._url("/docs/test.txt")
        assert url == "https://nextcloud.test.local/remote.php/dav/files/colaig/docs/test.txt"

    async def test_url_no_double_slash(self, client):
        url = client._url("docs/test.txt")
        assert "//" not in url.replace("https://", "")
