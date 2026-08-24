"""
Tests pour run_workspace_discovery_loop() — découverte automatique des workspaces.

Couvre :
- Nouveau dossier sans .colaig/ → scaffold + register_workspace
- Dossier avec .colaig/config.yaml non connu → load + register_workspace
- Dossier avec .colaig-ignore → ignoré (opt-out)
- Dossier caché (commence par .) → ignoré
- Workspace déjà connu du resolver → aucun appel
- StorageError lors du scaffold → skip permanent (pas de retry au cycle suivant)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from colaig.exceptions import StorageError
from colaig.main import run_workspace_discovery_loop
from colaig.models import StorageFile, WorkspaceConfig

# =============================================================================
# Helpers
# =============================================================================


def _make_dir_entry(name: str, path: str) -> StorageFile:
    return StorageFile(path=path, name=name, is_directory=True)


def _make_workspace(storage_path: str, workspace_id: str = "ws-test") -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id=workspace_id,
        name="Test WS",
        storage_path=storage_path,
    )


def _make_resolver(initial_workspaces: list | None = None):
    """Resolver mock avec workspaces dynamiques : register_workspace met à jour la liste."""
    known: list = list(initial_workspaces or [])
    resolver = MagicMock()

    # workspaces retourne toujours la liste courante (propriété lue chaque cycle)
    type(resolver).workspaces = property(lambda self: known)

    async def _register(ws: WorkspaceConfig) -> None:
        known.append(ws)

    resolver.register_workspace = AsyncMock(side_effect=_register)
    return resolver, known


async def _run_discovery(storage, resolver, *, cycles: int = 1, interval: float = 0) -> None:
    """Lance la boucle et l'arrête après `cycles` cycles complets."""
    shutdown = asyncio.Event()

    async def _stopper():
        # On laisse `cycles` itérations se produire puis on shutdown
        await asyncio.sleep(0.05 * cycles + 0.02)
        shutdown.set()

    await asyncio.gather(
        run_workspace_discovery_loop(storage, resolver, interval=interval, shutdown_event=shutdown),
        _stopper(),
    )


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_new_folder_scaffolded():
    """Nouveau dossier sans .colaig/ → create_workspace appelé + register_workspace."""
    entries = [_make_dir_entry("mon-projet", "/mon-projet/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock(side_effect=lambda path: {
        "/mon-projet/.colaig-ignore": False,
        "/mon-projet/.colaig/config.yaml": False,
    }.get(path, False))

    resolver, known = _make_resolver()
    ws = _make_workspace("/mon-projet/", "mon-projet")

    with (
        patch("colaig.context.workspace.create_workspace", AsyncMock(return_value=ws)),
        patch("colaig.context.workspace.load_workspace", AsyncMock()),
    ):
        await _run_discovery(storage, resolver)

    assert len(known) == 1
    assert known[0].storage_path == "/mon-projet/"
    resolver.register_workspace.assert_called_once_with(ws)


@pytest.mark.asyncio
async def test_existing_config_loaded():
    """Dossier avec .colaig/config.yaml non encore connu → load_workspace + register_workspace."""
    entries = [_make_dir_entry("rh", "/rh/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock(side_effect=lambda path: {
        "/rh/.colaig-ignore": False,
        "/rh/.colaig/config.yaml": True,
    }.get(path, False))

    resolver, known = _make_resolver()
    ws = _make_workspace("/rh/", "rh")

    with (
        patch("colaig.context.workspace.load_workspace", AsyncMock(return_value=ws)),
        patch("colaig.context.workspace.create_workspace", AsyncMock()),
    ):
        await _run_discovery(storage, resolver)

    assert len(known) == 1
    assert known[0].storage_path == "/rh/"
    resolver.register_workspace.assert_called_once_with(ws)


@pytest.mark.asyncio
async def test_ignore_marker_skipped():
    """Dossier avec .colaig-ignore → ni scaffold ni register."""
    entries = [_make_dir_entry("secret", "/secret/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock(return_value=True)  # .colaig-ignore existe

    resolver, known = _make_resolver()

    with (
        patch("colaig.context.workspace.create_workspace", AsyncMock()) as mock_create,
        patch("colaig.context.workspace.load_workspace", AsyncMock()) as mock_load,
    ):
        await _run_discovery(storage, resolver)
        mock_create.assert_not_called()
        mock_load.assert_not_called()

    assert known == []
    resolver.register_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_hidden_folder_skipped():
    """Dossier commençant par '.' → ignoré sans appel storage.exists."""
    entries = [_make_dir_entry(".hidden", "/.hidden/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock()  # ne doit jamais être appelé

    resolver, known = _make_resolver()

    with (
        patch("colaig.context.workspace.create_workspace", AsyncMock()) as mock_create,
        patch("colaig.context.workspace.load_workspace", AsyncMock()) as mock_load,
    ):
        await _run_discovery(storage, resolver)
        mock_create.assert_not_called()
        mock_load.assert_not_called()

    assert known == []
    resolver.register_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_already_known_skipped():
    """Workspace déjà dans resolver → aucun scaffold ni load même si config.yaml existe."""
    ws = _make_workspace("/connu/", "connu")
    entries = [_make_dir_entry("connu", "/connu/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock(side_effect=lambda path: {
        "/connu/.colaig-ignore": False,
        "/connu/.colaig/config.yaml": True,
    }.get(path, False))

    resolver, known = _make_resolver(initial_workspaces=[ws])

    with (
        patch("colaig.context.workspace.create_workspace", AsyncMock()) as mock_create,
        patch("colaig.context.workspace.load_workspace", AsyncMock()) as mock_load,
    ):
        await _run_discovery(storage, resolver)
        mock_create.assert_not_called()
        mock_load.assert_not_called()

    # Toujours 1 workspace (le workspace initial, pas de doublon)
    assert len(known) == 1
    resolver.register_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_storage_error_permanent_skip():
    """StorageError lors du scaffold → skip permanent : pas de retry au cycle suivant."""
    entries = [_make_dir_entry("readonly", "/readonly/")]

    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=entries)
    storage.exists = AsyncMock(side_effect=lambda path: {
        "/readonly/.colaig-ignore": False,
        "/readonly/.colaig/config.yaml": False,
    }.get(path, False))

    resolver, known = _make_resolver()

    call_count = 0

    async def failing_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise StorageError("Permission refusée")

    with (
        patch("colaig.context.workspace.create_workspace", AsyncMock(side_effect=failing_create)),
        patch("colaig.context.workspace.load_workspace", AsyncMock()),
    ):
        # 2 cycles (interval=0, on attend 0.12s pour laisser 2 itérations)
        await _run_discovery(storage, resolver, cycles=2)

    # create_workspace appelé UNE SEULE fois : skip permanent après StorageError
    assert call_count == 1
    assert known == []
    resolver.register_workspace.assert_not_called()
