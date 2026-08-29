"""
Tests pour colaig/integrations/storage/s3.py — S3Storage.

Stratégie : mock de boto3 (import optionnel) et mock du client boto3 pour
isoler chaque opération sans connexion S3 réelle.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
                    "LastModified": datetime(2025, 1, 1, tzinfo=UTC),
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


# =============================================================================
# La racine du seau — deploiement du 29/08/2026
# =============================================================================

class TestRacineDuSeau:
    """`exists("/")` doit repondre, y compris sur un seau vide.

    LE DEFAUT TROUVE EN PRODUCTION. La sonde de disponibilite appelle
    `storage.exists("/")` ; sur S3 cela donnait :

        Parameter validation failed: Invalid length for parameter Key,
        value: 0, valid min length: 1

    `_full_key("/")` rend une chaine vide — legitime, la racine n'a pas de cle — et
    `head_object(Key="")` est refuse par boto3 AVANT tout appel reseau. L'erreur est une
    `ParamValidationError`, pas une `ClientError` : aucune des deux branches de garde ne
    l'attrapait, et elle remontait telle quelle jusqu'a la sonde. Le pod ne devenait
    jamais pret.

    Le second chemin aurait ete faux aussi : `"".rstrip("/") + "/"` interroge le prefixe
    `"/"`, qui ne designe rien, et un seau vide aurait donc repondu « la racine n'existe
    pas ». Or la racine d'un seau joignable existe toujours — c'est le seau.
    """

    @pytest.mark.asyncio
    async def test_la_racine_existe_sur_un_seau_vide(self):
        s3 = _make_s3()
        client = MagicMock()
        client.head_bucket.return_value = {}
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            assert await s3.exists("/") is True
        client.head_bucket.assert_called_once_with(Bucket="my-bucket")
        client.head_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_la_racine_n_existe_pas_si_le_seau_est_injoignable(self):
        """Un seau absent ou refuse doit rendre False, pas lever.

        C'est ce qui donne son sens a la sonde : elle doit pouvoir dire « non ».
        """
        s3 = _make_s3()
        client = MagicMock()
        client.head_bucket.side_effect = FakeClientError("404")
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            assert await s3.exists("/") is False

    @pytest.mark.asyncio
    async def test_la_racine_avec_prefixe_ne_teste_pas_le_seau(self):
        """Avec un prefixe, la racine de l'instance est ce prefixe, pas le seau.

        Repondre `head_bucket` ici dirait « la racine existe » alors que le prefixe
        peut n'avoir jamais ete cree.
        """
        s3 = _make_s3(prefix="colaig")
        client = MagicMock()
        client.head_object.side_effect = FakeClientError("404")
        client.list_objects_v2.return_value = {"Contents": [{"Key": "colaig/x"}]}
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            assert await s3.exists("/") is True
        client.head_bucket.assert_not_called()

    @pytest.mark.asyncio
    async def test_lister_la_racine_interroge_le_prefixe_vide(self):
        """LE defaut qui rendait le seau invisible.

        `list_files` faisait `_full_key(path) + "/"`. A la racine sans prefixe,
        `_full_key("/")` rend "" et l'on interrogeait donc le prefixe `"/"` — qui ne
        correspond a AUCUNE cle, puisqu'une cle S3 ne commence pas par un slash.

        Consequence observee le 29/08/2026 : un seau contenant 63 objets rendait
        `entrees a la racine : 0`. `load_all_workspaces` et la boucle de decouverte
        voyaient donc un stockage vide, et Colaig restait sans aucun espace — sans la
        moindre erreur, ce qui est le pire des cas.
        """
        s3 = _make_s3()
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{
            "CommonPrefixes": [{"Prefix": "mesure-sst/"}],
        }]
        client.get_paginator.return_value = paginator
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            entrees = await s3.list_files("/")

        appel = paginator.paginate.call_args.kwargs
        assert appel["Prefix"] == "", (
            f'la racine doit interroger le prefixe vide, pas {appel["Prefix"]!r}'
        )
        assert [e.name for e in entrees] == ["mesure-sst"]

    @pytest.mark.asyncio
    async def test_lister_la_racine_avec_prefixe_interroge_le_prefixe(self):
        """Avec un prefixe configure, la racine de l'instance EST ce prefixe."""
        s3 = _make_s3(prefix="colaig")
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{}]
        client.get_paginator.return_value = paginator
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            await s3.list_files("/")

        assert paginator.paginate.call_args.kwargs["Prefix"] == "colaig/"

    @pytest.mark.asyncio
    async def test_creer_la_racine_ne_pose_pas_d_objet_slash(self):
        """`mkdir("/")` ecrivait un objet dont la cle est un simple slash.

        Meme cause. Un tel objet n'est pas un dossier : c'est un dechet a la racine du
        seau, que les listings suivants rendent comme une entree sans nom.
        """
        s3 = _make_s3()
        client = MagicMock()
        with patch.object(s3, "_get_client", return_value=client), \
             patch("colaig.integrations.storage.s3._require_boto3",
                   return_value=(MagicMock(), _fake_botocore())):
            await s3.mkdir("/")
        client.put_object.assert_not_called()
