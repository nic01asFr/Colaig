"""
Test du scoping d'outils par workspace (phase 2).

Couvre :
- apply_workspace_scoping : whitelist enabled (avec glob)
- apply_workspace_scoping : blacklist disabled
- apply_workspace_scoping : always_included force la présence
- apply_workspace_scoping : combinaison enabled + disabled + always_included
- merge_keywords_with_workspace : enrichissement additif
- merge_always_included_with_workspace : union des noyaux
- filter_tools_by_keywords : utilise workspace_config si fourni
- Isolation : deux workspaces différents → deux scopings différents

Usage :
    PYTHONPATH=. python tests/test_workspace_scoping.py
"""
import sys


def _make_registry(tool_names):
    """Crée un registre minimal avec des outils nommés."""
    from app.agent.tools import ToolRegistry, ToolDef

    async def noop_handler(args: dict, ctx: dict) -> str:
        return ""

    reg = ToolRegistry()
    for name in tool_names:
        reg.register(ToolDef(
            name=name,
            description=f"Test tool {name}",
            parameters={"type": "object", "properties": {}},
            handler=noop_handler,
        ))
    return reg


def test_apply_scoping_whitelist():
    """tools.enabled avec glob garde uniquement les outils matchant."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    reg = _make_registry([
        "search_documents", "synthesize_documents",
        "datagouv__search_datasets", "datagouv__get_dataset_info",
        "other_tool",
    ])

    cfg = WorkspaceConfig(tools_enabled=["search_documents", "datagouv__*"])
    apply_workspace_scoping(reg, cfg)

    kept = {t.name for t in reg.all_tools}
    assert "search_documents" in kept
    assert "datagouv__search_datasets" in kept
    assert "datagouv__get_dataset_info" in kept
    assert "synthesize_documents" not in kept, "synthesize_documents non whitelisté"
    assert "other_tool" not in kept, "other_tool non whitelisté"
    print("OK whitelist enabled (avec glob)")


def test_apply_scoping_blacklist():
    """tools.disabled retire les outils, même sans whitelist."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    reg = _make_registry([
        "search_documents",
        "datagouv__search_datasets",
        "datagouv__get_metrics",
    ])

    cfg = WorkspaceConfig(tools_disabled=["datagouv__get_metrics"])
    apply_workspace_scoping(reg, cfg)

    kept = {t.name for t in reg.all_tools}
    assert "search_documents" in kept
    assert "datagouv__search_datasets" in kept
    assert "datagouv__get_metrics" not in kept
    print("OK blacklist disabled")


def test_apply_scoping_always_included_overrides_blacklist():
    """always_included force la présence même si blacklisté (cas conflit)."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    reg = _make_registry(["search_documents", "datagouv__search_datasets"])

    cfg = WorkspaceConfig(
        tools_disabled=["search_documents"],
        tools_always_included=["search_documents"],
    )
    apply_workspace_scoping(reg, cfg)

    kept = {t.name for t in reg.all_tools}
    assert "search_documents" in kept, "always_included doit override la blacklist"
    print("OK always_included override blacklist")


def test_apply_scoping_empty_config_noop():
    """Config vide → aucun changement."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    reg = _make_registry(["a", "b", "c"])
    apply_workspace_scoping(reg, WorkspaceConfig.empty())

    kept = {t.name for t in reg.all_tools}
    assert kept == {"a", "b", "c"}, f"config vide doit être noop, reçu {kept}"
    print("OK config vide = noop")


def test_apply_scoping_combined():
    """enabled + disabled + always_included combinés."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    reg = _make_registry([
        "search_documents", "synthesize_documents",
        "datagouv__search_datasets", "datagouv__search_dataservices",
        "datagouv__get_metrics",
        "other_tool",
    ])

    cfg = WorkspaceConfig(
        tools_enabled=["search_*", "datagouv__*"],
        tools_disabled=["datagouv__get_metrics"],
        tools_always_included=["synthesize_documents"],  # Pas dans whitelist
    )
    apply_workspace_scoping(reg, cfg)

    kept = {t.name for t in reg.all_tools}
    # Whitelist matche : search_documents, datagouv__search_*, datagouv__get_metrics
    # Blacklist retire : datagouv__get_metrics
    # always_included force : synthesize_documents
    expected = {
        "search_documents",
        "datagouv__search_datasets",
        "datagouv__search_dataservices",
        "synthesize_documents",
    }
    assert kept == expected, f"Attendu {expected}, reçu {kept}"
    print("OK combinaison enabled + disabled + always_included")


def test_merge_keywords_extra():
    """merge_keywords_with_workspace ajoute sans écraser."""
    from app.agent.tool_scoping import merge_keywords_with_workspace
    from app.agent.workspace_config import WorkspaceConfig

    base = {
        "search_documents": ["document", "fichier"],
        "datagouv__search_datasets": ["data.gouv"],
    }
    cfg = WorkspaceConfig(tools_keywords_extra={
        "search_documents": ["OPAH", "PLU"],
        "new_tool": ["custom"],
    })

    merged = merge_keywords_with_workspace(base, cfg)
    assert "document" in merged["search_documents"]
    assert "OPAH" in merged["search_documents"]
    assert "PLU" in merged["search_documents"]
    assert merged["new_tool"] == ["custom"]
    assert merged["datagouv__search_datasets"] == ["data.gouv"]
    print("OK merge_keywords_extra additif sans écrasement")


def test_merge_keywords_no_duplicates():
    """Pas de doublons si keyword existe déjà."""
    from app.agent.tool_scoping import merge_keywords_with_workspace
    from app.agent.workspace_config import WorkspaceConfig

    base = {"tool": ["a", "b"]}
    cfg = WorkspaceConfig(tools_keywords_extra={"tool": ["b", "c"]})
    merged = merge_keywords_with_workspace(base, cfg)
    assert merged["tool"] == ["a", "b", "c"]
    print("OK pas de doublons dans merge_keywords")


def test_merge_always_included():
    """Union des noyaux global + workspace."""
    from app.agent.tool_scoping import merge_always_included_with_workspace
    from app.agent.workspace_config import WorkspaceConfig

    base = {"search_documents"}
    cfg = WorkspaceConfig(tools_always_included=["custom_tool", "search_documents"])
    merged = merge_always_included_with_workspace(base, cfg)
    assert merged == {"search_documents", "custom_tool"}
    print("OK merge_always_included union")


def test_isolation_two_workspaces():
    """Deux workspaces avec scopings différents → deux registres distincts."""
    from app.agent.tool_scoping import apply_workspace_scoping
    from app.agent.workspace_config import WorkspaceConfig

    base_tools = ["search_documents", "datagouv__search_datasets", "datagouv__get_metrics"]

    # Workspace A : tout activé sauf get_metrics
    reg_a = _make_registry(base_tools)
    cfg_a = WorkspaceConfig(tools_disabled=["datagouv__get_metrics"])
    apply_workspace_scoping(reg_a, cfg_a)

    # Workspace B : seulement search_documents
    reg_b = _make_registry(base_tools)
    cfg_b = WorkspaceConfig(tools_enabled=["search_documents"])
    apply_workspace_scoping(reg_b, cfg_b)

    kept_a = {t.name for t in reg_a.all_tools}
    kept_b = {t.name for t in reg_b.all_tools}

    assert kept_a == {"search_documents", "datagouv__search_datasets"}
    assert kept_b == {"search_documents"}
    assert kept_a != kept_b, "Les deux workspaces doivent être distincts"
    print("OK isolation entre workspaces")


def test_filter_with_workspace_config():
    """filter_tools_by_keywords utilise les keywords_extra du workspace."""
    from app.agent.tool_filter import filter_tools_by_keywords
    from app.agent.workspace_config import WorkspaceConfig

    tools = ["search_documents", "datagouv__search_datasets"]

    # Sans config workspace : "OPAH" ne matche aucun mot-clé global
    msg = "Cherche infos sur OPAH"
    cfg_empty = WorkspaceConfig.empty()
    kept_no_ws = filter_tools_by_keywords(msg, tools, workspace_config=cfg_empty)
    print(f"  Sans workspace : {kept_no_ws}")

    # Avec workspace.keywords_extra incluant "OPAH" → search_documents matche
    cfg_with_opah = WorkspaceConfig(tools_keywords_extra={
        "search_documents": ["OPAH", "PLU"],
    })
    kept_with_ws = filter_tools_by_keywords(msg, tools, workspace_config=cfg_with_opah)
    print(f"  Avec workspace OPAH : {kept_with_ws}")
    assert "search_documents" in kept_with_ws, \
        f"OPAH dans workspace.keywords_extra devrait matcher, reçu {kept_with_ws}"
    print("OK filter_tools_by_keywords utilise workspace_config")


def main():
    print("=" * 60)
    print("Test scoping outils par workspace — phase 2")
    print("=" * 60)

    tests = [
        ("Whitelist enabled (avec glob)", test_apply_scoping_whitelist),
        ("Blacklist disabled", test_apply_scoping_blacklist),
        ("always_included override blacklist", test_apply_scoping_always_included_overrides_blacklist),
        ("Config vide = noop", test_apply_scoping_empty_config_noop),
        ("Combinaison enabled+disabled+always", test_apply_scoping_combined),
        ("Merge keywords_extra additif", test_merge_keywords_extra),
        ("Pas de doublons dans merge", test_merge_keywords_no_duplicates),
        ("Merge always_included union", test_merge_always_included),
        ("Isolation entre workspaces", test_isolation_two_workspaces),
        ("filter_tools_by_keywords + workspace", test_filter_with_workspace_config),
    ]

    failed = 0
    for name, fn in tests:
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

    print()
    print("=" * 60)
    if failed == 0:
        print("Tous les tests scoping passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
