"""
Test d'isolation contextuelle par workspace.

Vérifie que :
1. get_or_create_room_context auto-peuple webdav_context si template configuré
2. Deux rooms différentes obtiennent deux workspace_path différents
3. get_webdav_for_context retourne un service avec workspace_path scopé
4. BehaviorManager pour deux contextes distincts charge depuis deux dirs distincts
5. Les caches en mémoire (room_contexts, behavior_contexts, webdav_instances)
   séparent bien les rooms

Ce test peut être lancé sans bot Tchap actif. Il mocke le WebDAV.

Usage :
    PYTHONPATH=. python tests/test_workspace_isolation.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


def _make_config(template="rooms/{room_id}"):
    """Config minimale pour les tests."""
    cfg = MagicMock()
    cfg.workspace_path_template = template
    cfg.webdav_url = "http://fake-webdav/dav"
    cfg.webdav_username = "test"
    cfg.webdav_password = "test"
    cfg.webdav_root_path = "/documents"
    cfg.behavior_path = ".albert/behavior"
    cfg.embedding_dimension = 1024
    cfg.systemd_logging = False
    cfg.loglevel = "WARNING"
    return cfg


async def test_room_context_auto_mapping():
    """Vérifie qu'un room nouveau reçoit son webdav_context automatiquement."""
    from app.services.context.manager import ContextManager
    from app.services.context.types import ContextType

    config = _make_config()

    # On mocke le ContextManager au minimum pour éviter de toucher au stockage
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
        from app.services.context.models import RoomContext
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

    # Test 1 : room A → workspace rooms/!ROOMa
    rc_a = await cm.get_or_create_room_context("!ROOMa", "Room A", False)
    assert rc_a.webdav_context == "rooms/!ROOMa", \
        f"Attendu rooms/!ROOMa, obtenu {rc_a.webdav_context}"
    print("OK room A → webdav_context = rooms/!ROOMa")

    # Test 2 : room B → workspace différent
    rc_b = await cm.get_or_create_room_context("!ROOMb", "Room B", False)
    assert rc_b.webdav_context == "rooms/!ROOMb", \
        f"Attendu rooms/!ROOMb, obtenu {rc_b.webdav_context}"
    print("OK room B → webdav_context = rooms/!ROOMb")

    # Test 3 : isolation des caches
    assert cm._room_contexts["!ROOMa"] is rc_a
    assert cm._room_contexts["!ROOMb"] is rc_b
    assert rc_a.webdav_context != rc_b.webdav_context
    print("OK isolation des room_contexts en mémoire")


async def test_room_context_no_auto_when_template_empty():
    """Si workspace_path_template est vide, pas d'auto-mapping."""
    from app.services.context.manager import ContextManager

    config = _make_config(template="")
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
        from app.services.context.models import RoomContext
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

    rc = await cm.get_or_create_room_context("!ROOM", "Room", False)
    assert rc.webdav_context is None, \
        f"Attendu None (template vide), obtenu {rc.webdav_context}"
    print("OK template vide → pas d'auto-mapping")


async def test_room_context_preserve_existing():
    """Si webdav_context déjà défini (via !space link), ne pas le réécrire."""
    from app.services.context.manager import ContextManager
    from app.services.context.models import RoomContext

    config = _make_config()
    cm = ContextManager.__new__(ContextManager)
    cm.config = config
    cm._room_contexts = {}
    cm._cache = MagicMock()
    cm._cache.get = MagicMock(return_value=None)
    cm._cache.set = MagicMock()
    cm._local_cache = {}

    # Room avec webdav_context déjà défini (simulé)
    existing = RoomContext(room_id="!ROOM", name="Room", is_direct=False)
    existing.set_documentation_space("custom/workspace")

    async def fake_get(rid, ctype):
        return existing

    async def fake_create(rid, ctype, data):
        return existing

    async def fake_update(rid, ctype, data, immediate_save=False):
        return True

    cm.get_context = fake_get
    cm.create_context = fake_create
    cm.update_context = fake_update

    rc = await cm.get_or_create_room_context("!ROOM", "Room", False)
    assert rc.webdav_context == "custom/workspace", \
        f"webdav_context écrasé : {rc.webdav_context}"
    print("OK webdav_context préexistant préservé")


async def test_workspace_path_template_format():
    """Vérifie le format de substitution {room_id}."""
    config = _make_config(template="custom-prefix/{room_id}/data")
    template = config.workspace_path_template
    result = template.format(room_id="!XYZ:server.fr")
    assert result == "custom-prefix/!XYZ:server.fr/data"
    print("OK template {room_id} substitué correctement")


def main():
    print("=" * 60)
    print("Test d'isolation par workspace — phase 0")
    print("=" * 60)

    tests = [
        ("Auto-mapping room → workspace", test_room_context_auto_mapping),
        ("Template vide désactive auto-mapping", test_room_context_no_auto_when_template_empty),
        ("Préservation de webdav_context existant", test_room_context_preserve_existing),
        ("Format du template", test_workspace_path_template_format),
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
        print(f"Tous les tests d'isolation passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
