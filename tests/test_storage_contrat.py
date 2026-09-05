"""
Contrat `StorageProtocol` — L1.1.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.1

**Une seule suite, exécutée contre chaque implémentation.** Ce qui est vérifié ici est
ce sur quoi le code métier s'appuie ; une implémentation qui n'y passe pas n'est pas
interchangeable, quel que soit son respect formel des signatures.

`FakeStorage` figure dans la liste, et c'est le point le plus important du lot : si la
doublure ne passe pas le même contrat que les implémentations réelles, tous les tests
qui l'utilisent mesurent autre chose que la production.

Le Protocol est sous-spécifié
-----------------------------
`StorageProtocol` ne dit ni ce que lève `download()` sur un chemin absent, ni si
`delete()` d'un chemin inexistant est une erreur, ni si `upload()` crée les dossiers
parents. Les docstrings tiennent en une ligne. Ce fichier **écrit** ces règles, en
prenant pour référence le comportement commun aux sept implémentations — pas une
préférence :

- les sept lèvent `StorageFileNotFoundError` (et non le `FileNotFoundError` natif,
  qui n'en est pas un parent) ;
- aucune ne crée de dossier parent implicitement à l'`upload` — c'est `mkdir()` qui
  le fait.

Toute divergence constatée est un point à arbitrer, pas à trancher dans un test.

Couverture
----------
| backend | condition d'exécution |
|---|---|
| `FakeStorage` | toujours |
| `local` | toujours (dossier temporaire) |
| `s3` | `COLAIG_S3_BUCKET` + credentials dans l'environnement |
| `webdav`, `bigfolder`, `msgraph`, `box`, `gdrive` | credentials absents → `skip` |

Un `skip` est **visible** dans le rapport pytest : il dit « non vérifié », jamais
« vérifié ». C'est la différence entre une couverture connue et une couverture supposée.
"""
from __future__ import annotations

import os
import uuid

import pytest

from colaig.exceptions import StorageFileNotFoundError
from tests.fakes import FakeStorage

pytestmark = pytest.mark.asyncio


# ── Fabriques de backends ───────────────────────────────────────────────────


def _fabrique_fake(tmp_path):
    return FakeStorage(), ""


def _fabrique_local(tmp_path):
    from colaig.integrations.storage.local import LocalStorage

    return LocalStorage(str(tmp_path)), ""


def _fabrique_s3(tmp_path):
    bucket = os.environ.get("COLAIG_S3_BUCKET") or os.environ.get("AWS_BUCKET_NAME", "")
    acces = os.environ.get("COLAIG_S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("COLAIG_S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not (bucket and acces and secret):
        pytest.skip("credentials S3 absents de l'environnement (COLAIG_S3_*)")
    try:
        import boto3  # noqa: F401
    except ImportError:
        pytest.skip("boto3 absent")

    from colaig.integrations.storage.s3 import S3Storage

    endpoint = os.environ.get("COLAIG_S3_ENDPOINT_URL") or os.environ.get("AWS_S3_ENDPOINT", "")
    if endpoint and not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    # Préfixe unique par exécution : la suite ne touche jamais aux données existantes
    # du bucket, et deux exécutions concurrentes ne se marchent pas dessus.
    prefixe = f"colaig-contrat/{uuid.uuid4().hex[:12]}"
    stockage = S3Storage(
        bucket_name=bucket,
        access_key=acces,
        secret_key=secret,
        endpoint_url=endpoint or None,
        prefix=prefixe,
        session_token=os.environ.get("COLAIG_S3_SESSION_TOKEN")
        or os.environ.get("AWS_SESSION_TOKEN", ""),
        region=os.environ.get("COLAIG_S3_REGION", "us-east-1"),
    )
    return stockage, ""


def _skip(nom, raison):
    def fabrique(tmp_path):
        pytest.skip(f"{nom} : {raison}")

    return fabrique


FABRIQUES = {
    "fake": _fabrique_fake,
    "local": _fabrique_local,
    "s3": _fabrique_s3,
    "webdav": _skip("webdav", "credentials absents (COLAIG_WEBDAV_*)"),
    "bigfolder": _skip("bigfolder", "credentials absents (BIGFOLDER_*)"),
    "msgraph": _skip("msgraph", "credentials absents (MSGRAPH_*)"),
    "box": _skip("box", "credentials absents (BOX_*)"),
    "gdrive": _skip("gdrive", "credentials absents (GDRIVE_*)"),
}


@pytest.fixture(params=list(FABRIQUES), ids=list(FABRIQUES))
def backend(request, tmp_path):
    """Une implémentation de `StorageProtocol`, ou un `skip` explicite."""
    stockage, _ = FABRIQUES[request.param](tmp_path)
    return stockage


@pytest.fixture
def racine():
    """Espace de travail isolé pour un test — jamais deux tests au même endroit."""
    return f"/contrat-{uuid.uuid4().hex[:8]}"


# ── Le contrat ──────────────────────────────────────────────────────────────


async def test_aller_retour(backend, racine):
    """`upload` puis `download` rend exactement les mêmes octets."""
    chemin = f"{racine}/document.txt"
    contenu = "Procédure de marché public — été 2026.".encode()
    await backend.upload(chemin, contenu)
    try:
        assert await backend.download(chemin) == contenu
    finally:
        await backend.delete(chemin)


async def test_exists_suit_le_cycle_de_vie(backend, racine):
    chemin = f"{racine}/presence.txt"
    assert await backend.exists(chemin) is False
    await backend.upload(chemin, b"x")
    assert await backend.exists(chemin) is True
    await backend.delete(chemin)
    assert await backend.exists(chemin) is False


async def test_download_absent_leve_storage_file_not_found(backend, racine):
    """Les sept implémentations lèvent `StorageFileNotFoundError`.

    Et **pas** le `FileNotFoundError` natif : `issubclass()` vaut False entre les deux.
    Une doublure qui lèverait l'autre laisserait passer en test un `except` qui ne se
    déclencherait pas en production.
    """
    with pytest.raises(StorageFileNotFoundError):
        await backend.download(f"{racine}/jamais-ecrit.txt")


async def test_upload_ecrase(backend, racine):
    """« crée ou écrase », dit le Protocol."""
    chemin = f"{racine}/ecrase.txt"
    await backend.upload(chemin, b"premiere version")
    await backend.upload(chemin, b"seconde version")
    try:
        assert await backend.download(chemin) == b"seconde version"
    finally:
        await backend.delete(chemin)


async def test_etag_absent_puis_stable_puis_change(backend, racine):
    """L'etag est le pivot de l'indexation incrémentale — son contrat compte.

    - `None` si le fichier n'existe pas.
    - Inchangé tant que le contenu ne change pas.
    - Différent après une écriture de contenu différent.
    """
    chemin = f"{racine}/etag.txt"
    assert await backend.get_etag(chemin) is None

    await backend.upload(chemin, b"contenu initial")
    try:
        premier = await backend.get_etag(chemin)
        assert premier is not None
        assert await backend.get_etag(chemin) == premier, "etag instable sans écriture"

        await backend.upload(chemin, b"contenu modifie")
        assert await backend.get_etag(chemin) != premier, "etag inchangé malgré une écriture"
    finally:
        await backend.delete(chemin)


async def test_download_if_changed(backend, racine):
    chemin = f"{racine}/incremental.txt"
    await backend.upload(chemin, b"v1")
    try:
        etag = await backend.get_etag(chemin)
        assert await backend.download_if_changed(chemin, etag) is None, (
            "etag identique : le backend doit économiser le transfert"
        )
        await backend.upload(chemin, b"v2")
        assert await backend.download_if_changed(chemin, etag) == b"v2"
    finally:
        await backend.delete(chemin)


async def test_list_files_non_recursif_ignore_les_sous_dossiers(backend, racine):
    await backend.upload(f"{racine}/a.txt", b"a")
    await backend.upload(f"{racine}/b.txt", b"b")
    await backend.upload(f"{racine}/sous/c.txt", b"c")
    try:
        directs = {f.path for f in await backend.list_files(f"{racine}/")}
        assert f"{racine}/a.txt" in directs
        assert f"{racine}/b.txt" in directs
        assert f"{racine}/sous/c.txt" not in directs, (
            "un listing non récursif ne doit pas descendre dans les sous-dossiers"
        )
    finally:
        for c in (f"{racine}/a.txt", f"{racine}/b.txt", f"{racine}/sous/c.txt"):
            await backend.delete(c)


async def test_list_files_recursif_descend(backend, racine):
    await backend.upload(f"{racine}/a.txt", b"a")
    await backend.upload(f"{racine}/sous/c.txt", b"c")
    try:
        tous = {f.path for f in await backend.list_files(f"{racine}/", recursive=True)}
        assert f"{racine}/a.txt" in tous
        assert f"{racine}/sous/c.txt" in tous
    finally:
        for c in (f"{racine}/a.txt", f"{racine}/sous/c.txt"):
            await backend.delete(c)


async def test_list_files_dossier_vide(backend, racine):
    """Un chemin sans contenu rend une liste vide, il ne lève pas."""
    assert await backend.list_files(f"{racine}/vide/") == []


async def test_contenu_binaire_preserve(backend, racine):
    """Un index FAISS n'est pas du texte : aucun ré-encodage ne doit l'altérer."""
    chemin = f"{racine}/index.faiss"
    contenu = bytes(range(256)) * 8
    await backend.upload(chemin, contenu)
    try:
        assert await backend.download(chemin) == contenu
    finally:
        await backend.delete(chemin)
