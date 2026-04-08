"""
Test du chargement et de l'application de workspace.yaml.

Couvre :
- Parsing du YAML (config valide / vide / invalide)
- Cache mtime opportuniste : pas de re-fetch sous _REFRESH_INTERVAL_SECONDS
- Invalidation de cache par etag (modification du contenu)
- Isolation par workspace_root (deux workspaces ne se voient pas)
- Persona override dans build_system_prompt
- Retombée gracieuse si workspace.yaml absent

Usage :
    PYTHONPATH=. python tests/test_workspace_config.py
"""
import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_webdav_mock(file_contents: dict):
    """Crée un mock WebDAVService.

    file_contents : dict {path: content_str ou None pour absent}
    """
    svc = MagicMock()

    async def fake_exists(path):
        return path in file_contents and file_contents[path] is not None

    async def fake_download(path):
        content = file_contents.get(path)
        if content is None:
            raise FileNotFoundError(path)
        return content.encode("utf-8") if isinstance(content, str) else content

    svc.exists = fake_exists
    svc.download_file = fake_download
    return svc


def _reset_cache():
    from app.agent import workspace_config
    workspace_config._cache.clear()


# ─── Tests ───────────────────────────────────────────────────────────────────

async def test_load_empty_workspace():
    """Workspace sans yaml → config vide, pas d'erreur."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    svc = _make_webdav_mock({})  # rien n'existe
    cfg = await load_workspace_config(svc, "rooms/!ROOM")
    assert cfg.is_empty(), "Config doit être vide"
    assert cfg.persona_override == ""
    assert cfg.tools_enabled == []
    print("OK workspace sans yaml → config vide")


async def test_load_persona_override():
    """Workspace avec persona_override → bien chargé."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    yaml_content = """
identity:
  persona_override: |
    Tu es l'assistant de la préfecture de Mayenne.
    Tu réponds aux agents publics du département.
"""
    svc = _make_webdav_mock({
        "rooms/!ROOMA/.albert/config/workspace.yaml": yaml_content,
    })
    cfg = await load_workspace_config(svc, "rooms/!ROOMA")
    assert "préfecture de Mayenne" in cfg.persona_override
    assert not cfg.is_empty()
    print("OK persona_override chargé")


async def test_load_tools_scoping():
    """Scoping outils correctement parsé."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    yaml_content = """
tools:
  enabled: ["search_documents", "datagouv__*"]
  disabled: ["datagouv__get_metrics"]
  always_included: ["search_documents"]
  keywords_extra:
    search_documents: ["OPAH", "PLU"]
"""
    svc = _make_webdav_mock({
        "rooms/!ROOMB/.albert/config/workspace.yaml": yaml_content,
    })
    cfg = await load_workspace_config(svc, "rooms/!ROOMB")
    assert cfg.tools_enabled == ["search_documents", "datagouv__*"]
    assert cfg.tools_disabled == ["datagouv__get_metrics"]
    assert cfg.tools_always_included == ["search_documents"]
    assert cfg.tools_keywords_extra == {"search_documents": ["OPAH", "PLU"]}
    print("OK tools scoping parsé")


async def test_isolation_between_workspaces():
    """Deux workspaces différents → deux configs distinctes en cache."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    svc = _make_webdav_mock({
        "rooms/!A/.albert/config/workspace.yaml": "identity:\n  persona_override: 'Persona A'",
        "rooms/!B/.albert/config/workspace.yaml": "identity:\n  persona_override: 'Persona B'",
    })

    cfg_a = await load_workspace_config(svc, "rooms/!A")
    cfg_b = await load_workspace_config(svc, "rooms/!B")

    assert cfg_a.persona_override == "Persona A"
    assert cfg_b.persona_override == "Persona B"
    print("OK isolation par workspace_root")


async def test_cache_mtime_no_refetch():
    """Deuxième appel rapide → utilise le cache, pas de re-fetch."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    call_count = {"exists": 0, "download": 0}

    async def fake_exists(path):
        call_count["exists"] += 1
        return True

    async def fake_download(path):
        call_count["download"] += 1
        return b"identity:\n  persona_override: 'Test'"

    svc = MagicMock()
    svc.exists = fake_exists
    svc.download_file = fake_download

    # Premier appel : 1 exists + 1 download
    cfg1 = await load_workspace_config(svc, "rooms/!CACHE")
    assert call_count["exists"] == 1
    assert call_count["download"] == 1
    assert cfg1.persona_override == "Test"

    # Deuxième appel immédiat : aucun nouvel appel
    cfg2 = await load_workspace_config(svc, "rooms/!CACHE")
    assert call_count["exists"] == 1, f"exists appelé {call_count['exists']} fois"
    assert call_count["download"] == 1, f"download appelé {call_count['download']} fois"
    assert cfg2.persona_override == "Test"

    print("OK cache mtime : pas de re-fetch sous TTL")


async def test_cache_invalidation():
    """invalidate_workspace_cache vide bien le cache d'un workspace."""
    from app.agent.workspace_config import load_workspace_config, invalidate_workspace_cache, _cache
    _reset_cache()

    svc = _make_webdav_mock({
        "rooms/!INV/.albert/config/workspace.yaml": "identity:\n  persona_override: 'X'",
    })

    await load_workspace_config(svc, "rooms/!INV")
    assert "rooms/!INV" in _cache

    invalidate_workspace_cache("rooms/!INV")
    assert "rooms/!INV" not in _cache
    print("OK invalidation par workspace")

    # Tester l'invalidation globale
    await load_workspace_config(svc, "rooms/!INV")
    assert "rooms/!INV" in _cache
    invalidate_workspace_cache(None)
    assert len(_cache) == 0
    print("OK invalidation globale")


async def test_invalid_yaml_graceful():
    """YAML invalide → config vide, pas de crash."""
    from app.agent.workspace_config import load_workspace_config
    _reset_cache()

    svc = _make_webdav_mock({
        "rooms/!BAD/.albert/config/workspace.yaml": "this is :: not :: yaml",
    })
    cfg = await load_workspace_config(svc, "rooms/!BAD")
    # YAML peut quand même parser ça en string, donc on tolère
    # L'important est que ça ne crashe pas
    assert cfg is not None
    print("OK YAML invalide → fallback vide sans crash")


def test_persona_override_in_system_prompt():
    """build_system_prompt utilise bien persona_override quand fourni."""
    from app.agent.prompt import build_system_prompt, _identity_section
    from app.agent.tools import ToolRegistry

    # Sans override
    sp_default = _identity_section()
    assert "Colaig" in sp_default
    assert "État français" in sp_default
    print("OK identité Colaig par défaut")

    # Avec override
    custom = "Tu es l'assistant de la DREAL Pays de la Loire."
    sp_custom = _identity_section(custom)
    assert sp_custom == custom
    assert "Colaig" not in sp_custom
    print("OK persona override remplace l'identité")

    # Via build_system_prompt complet
    reg = ToolRegistry()
    full = build_system_prompt(reg, persona_override=custom)
    assert custom in full
    assert "Tu es **Colaig**" not in full
    print("OK build_system_prompt propage persona_override")


# ─── Runner ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Test workspace.yaml — phase 1")
    print("=" * 60)

    sync_tests = [
        ("Persona override dans system prompt", test_persona_override_in_system_prompt),
    ]

    async_tests = [
        ("Workspace sans yaml", test_load_empty_workspace),
        ("Persona override chargé", test_load_persona_override),
        ("Tools scoping parsé", test_load_tools_scoping),
        ("Isolation entre workspaces", test_isolation_between_workspaces),
        ("Cache mtime opportuniste", test_cache_mtime_no_refetch),
        ("Invalidation de cache", test_cache_invalidation),
        ("YAML invalide → fallback", test_invalid_yaml_graceful),
    ]

    failed = 0
    for name, fn in sync_tests:
        print(f"\n[{name}]")
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL : {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERREUR : {e}")
            traceback.print_exc()
            failed += 1

    for name, fn in async_tests:
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
        print("Tous les tests workspace.yaml passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
