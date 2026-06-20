"""
Test du rattachement contextuel des rooms à un workspace.

Modèle actuel : un workspace est un dossier de premier niveau dans le stockage
BigFolder (ex: "Archivist"). Plusieurs rooms peuvent partager le même workspace.
Le rattachement d'une room se fait via :
- !space link <workspace> (explicite, préservé)
- Config.default_workspace (implicite)
- sinon webdav_context="" (en attente d'un !space link)

L'ancien auto-mapping par room ("rooms/{room_id}") est obsolète : il est
activement migré vers le workspace par défaut.

Vérifie que :
1. Une room nouvelle est rattachée à Config.default_workspace
2. Sans default_workspace ni root path, pas de rattachement (webdav_context="")
3. Un webdav_context explicite (via !space link) n'est pas réécrit
4. Un webdav_context au format obsolète "rooms/..." est migré

Ce test peut être lancé sans bot Tchap actif. Il mocke le stockage.

Usage :
    PYTHONPATH=. python tests/test_workspace_isolation.py
"""
import asyncio
import sys
from unittest.mock import MagicMock


def _make_config(default_workspace="", webdav_root_path="/documents"):
    """Config minimale pour les tests.

    `default_workspace` et `webdav_root_path` sont posés explicitement : sur un
    MagicMock nu ils auto-résoudraient en MagicMock (truthy), faussant la logique
    de rattachement qui les lit via getattr.
    """
    cfg = MagicMock()
    cfg.default_workspace = default_workspace
    cfg.webdav_root_path = webdav_root_path
    cfg.webdav_url = "http://fake-webdav/dav"
    cfg.webdav_username = "test"
    cfg.webdav_password = "test"
    cfg.behavior_path = ".albert/behavior"
    cfg.embedding_dimension = 1024
    cfg.systemd_logging = False
    cfg.loglevel = "WARNING"
    return cfg


def _make_manager(config):
    """Instancie un ContextManager nu (sans toucher au stockage) avec des
    get/create/update mockés."""
    from app.services.context.manager import ContextManager
    from app.services.context.models import RoomContext

    cm = ContextManager.__new__(ContextManager)
    cm.config = config
    cm._room_contexts = {}
    cm._cache = MagicMock()
    cm._cache.get = MagicMock(return_value=None)
    cm._cache.set = MagicMock()
    cm._local_cache = {}

    async def fake_get(rid, ctype):
        return None

    async def fake_create(rid, ctype, data):
        return RoomContext(
            room_id=data["room_id"],
            name=data["name"],
            is_direct=data.get("is_direct", False),
        )

    async def fake_update(rid, ctype, data, immediate_save=False):
        return True

    cm.get_context = fake_get
    cm.create_context = fake_create
    cm.update_context = fake_update
    return cm


async def test_room_context_default_workspace():
    """Une room nouvelle est rattachée au workspace par défaut configuré."""
    config = _make_config(default_workspace="Archivist")
    cm = _make_manager(config)

    rc_a = await cm.get_or_create_room_context("!ROOMa", "Room A", False)
    rc_b = await cm.get_or_create_room_context("!ROOMb", "Room B", False)

    # Plusieurs rooms partagent le même workspace par défaut.
    assert rc_a.webdav_context == "Archivist", rc_a.webdav_context
    assert rc_b.webdav_context == "Archivist", rc_b.webdav_context

    # Les RoomContext restent des objets distincts en cache mémoire.
    assert cm._room_contexts["!ROOMa"] is rc_a
    assert cm._room_contexts["!ROOMb"] is rc_b
    assert rc_a is not rc_b
    print("OK rattachement au workspace par défaut")


async def test_room_context_no_default():
    """Sans default_workspace ni root path : pas de rattachement automatique."""
    config = _make_config(default_workspace="", webdav_root_path="")
    cm = _make_manager(config)

    rc = await cm.get_or_create_room_context("!ROOM", "Room", False)
    # webdav_context vide = en attente d'un !space link explicite.
    assert rc.webdav_context == "", f"Attendu '' (aucun défaut), obtenu {rc.webdav_context!r}"
    print("OK aucun défaut → pas de rattachement automatique")


async def test_room_context_preserve_existing():
    """Si webdav_context déjà défini (via !space link), ne pas le réécrire."""
    from app.services.context.models import RoomContext

    config = _make_config(default_workspace="Archivist")
    cm = _make_manager(config)

    existing = RoomContext(room_id="!ROOM", name="Room", is_direct=False)
    existing.set_documentation_space("custom/workspace")

    async def fake_get(rid, ctype):
        return existing

    cm.get_context = fake_get

    rc = await cm.get_or_create_room_context("!ROOM", "Room", False)
    assert rc.webdav_context == "custom/workspace", \
        f"webdav_context écrasé : {rc.webdav_context}"
    print("OK webdav_context préexistant préservé")


async def test_room_context_migrates_obsolete_format():
    """Un webdav_context au format obsolète 'rooms/...' est migré vers le défaut."""
    from app.services.context.models import RoomContext

    config = _make_config(default_workspace="Archivist")
    cm = _make_manager(config)

    obsolete = RoomContext(room_id="!ROOM", name="Room", is_direct=False)
    obsolete.set_documentation_space("rooms/!ROOM")

    async def fake_get(rid, ctype):
        return obsolete

    cm.get_context = fake_get

    rc = await cm.get_or_create_room_context("!ROOM", "Room", False)
    assert rc.webdav_context == "Archivist", \
        f"Format obsolète non migré : {rc.webdav_context!r}"
    print("OK migration du format obsolète rooms/...")


def main():
    print("=" * 60)
    print("Test de rattachement room → workspace")
    print("=" * 60)

    tests = [
        ("Rattachement au workspace par défaut", test_room_context_default_workspace),
        ("Aucun défaut → pas de rattachement", test_room_context_no_default),
        ("Préservation de webdav_context existant", test_room_context_preserve_existing),
        ("Migration du format obsolète", test_room_context_migrates_obsolete_format),
    ]

    failed = 0
    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            asyncio.run(fn())
        except AssertionError as e:
            print(f"FAIL : {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERREUR : {e}")
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    if failed == 0:
        print("Tous les tests de rattachement passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
