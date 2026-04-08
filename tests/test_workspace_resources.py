"""
Test de l'index sémantique unifié des ressources workspace (phase 4).

Couvre :
- Création/réutilisation d'instances par workspace_root
- populate_index : construction de l'inventaire (outils internes, MCP, skills)
- Inventory etag : pas de rebuild si inchangé
- search avec filtre par kind
- Isolation entre workspaces
- Invalidation de cache

Note : ces tests mockent les embeddings pour rester rapides.

Usage :
    PYTHONPATH=. python tests/test_workspace_resources.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


def _reset_cache():
    from app.agent import workspace_resources
    workspace_resources._cache.clear()


def _mock_tool(name, description=""):
    t = MagicMock()
    t.name = name
    t.description = description
    return t


def _mock_skill(name, description="", body=""):
    from app.agent.skills import Skill
    return Skill(name=name, description=description, body=body)


async def _fake_embeddings(texts, config=None):
    """Embeddings déterministes basés sur la longueur — pour tests rapides."""
    import math
    out = []
    for t in texts:
        # Vecteur simpliste basé sur le hash et la longueur
        h = hash(t) % 1000
        vec = [
            math.sin(h + i) for i in range(8)
        ]
        out.append(vec)
    return out


async def test_create_index():
    """get_or_create_index renvoie une instance par workspace."""
    from app.agent.workspace_resources import get_or_create_index
    _reset_cache()

    idx_a = get_or_create_index("rooms/!A")
    idx_b = get_or_create_index("rooms/!B")
    idx_a2 = get_or_create_index("rooms/!A")

    assert idx_a is idx_a2, "Même workspace doit renvoyer la même instance"
    assert idx_a is not idx_b, "Workspaces différents = instances distinctes"
    assert idx_a.workspace_root == "rooms/!A"
    print("OK création/réutilisation par workspace")


async def test_populate_inventory():
    """populate_index construit l'inventaire à partir des sources."""
    from app.agent.workspace_resources import populate_index
    _reset_cache()

    tools = [
        _mock_tool("search_documents", "Recherche dans les docs"),
        _mock_tool("synthesize_documents", "Synthèse"),
    ]
    skills = [
        _mock_skill("instruction_opah", "OPAH", body="## Procédure\nÉtape 1"),
    ]

    idx = populate_index(
        "rooms/!POP",
        internal_tools=tools,
        skills=skills,
    )

    assert len(idx.entries) == 3
    kinds = {e.kind for e in idx.entries}
    assert kinds == {"tool_internal", "skill"}
    names = {e.name for e in idx.entries}
    assert "search_documents" in names
    assert "instruction_opah" in names
    print("OK inventory construit")


async def test_inventory_etag_stable():
    """Si l'inventaire est identique, populate_index ne reconstruit pas."""
    from app.agent.workspace_resources import populate_index
    _reset_cache()

    tools = [_mock_tool("a", "desc a"), _mock_tool("b", "desc b")]

    idx1 = populate_index("rooms/!ETAG", internal_tools=tools)
    etag1 = idx1.inventory_etag()

    idx2 = populate_index("rooms/!ETAG", internal_tools=tools)
    etag2 = idx2.inventory_etag()

    assert idx1 is idx2
    assert etag1 == etag2
    print("OK etag stable si inventaire identique")


async def test_inventory_etag_changes():
    """Etag change si l'inventaire change."""
    from app.agent.workspace_resources import populate_index
    _reset_cache()

    tools_v1 = [_mock_tool("a", "desc a")]
    idx_v1 = populate_index("rooms/!CHG", internal_tools=tools_v1)
    etag_v1 = idx_v1.inventory_etag()

    tools_v2 = [_mock_tool("a", "desc a"), _mock_tool("b", "desc b")]
    idx_v2 = populate_index("rooms/!CHG", internal_tools=tools_v2)
    etag_v2 = idx_v2.inventory_etag()

    assert idx_v1 is idx_v2  # même cache
    assert etag_v1 != etag_v2  # mais etag différent
    assert len(idx_v2.entries) == 2
    print("OK etag change si inventaire change")


async def test_isolation_between_workspaces():
    """Deux workspaces ont des inventaires séparés."""
    from app.agent.workspace_resources import populate_index, get_or_create_index
    _reset_cache()

    populate_index("rooms/!ISOA", internal_tools=[_mock_tool("tool_a")])
    populate_index("rooms/!ISOB", internal_tools=[_mock_tool("tool_b")])

    idx_a = get_or_create_index("rooms/!ISOA")
    idx_b = get_or_create_index("rooms/!ISOB")

    names_a = {e.name for e in idx_a.entries}
    names_b = {e.name for e in idx_b.entries}
    assert names_a == {"tool_a"}
    assert names_b == {"tool_b"}
    print("OK isolation des inventaires entre workspaces")


async def test_invalidate_workspace():
    """invalidate_workspace_index vide bien le cache."""
    from app.agent.workspace_resources import (
        populate_index, invalidate_workspace_index, _cache,
    )
    _reset_cache()

    populate_index("rooms/!INV", internal_tools=[_mock_tool("a")])
    assert "rooms/!INV" in _cache

    invalidate_workspace_index("rooms/!INV")
    assert "rooms/!INV" not in _cache
    print("OK invalidation par workspace")

    populate_index("rooms/!INV2", internal_tools=[_mock_tool("a")])
    populate_index("rooms/!INV3", internal_tools=[_mock_tool("a")])
    invalidate_workspace_index(None)
    assert len(_cache) == 0
    print("OK invalidation globale")


async def test_search_with_mocked_embeddings():
    """search() trouve les ressources par similarité (avec embeddings mockés)."""
    from app.agent.workspace_resources import populate_index, get_or_create_index
    _reset_cache()

    # Embeddings constants pour rendre le test déterministe
    async def constant_embeddings(texts, config=None):
        return [[1.0, 0.0, 0.0] for _ in texts]

    with patch("app.agent.workspace_resources._compute_embeddings", side_effect=constant_embeddings):
        populate_index(
            "rooms/!SEARCH",
            internal_tools=[
                _mock_tool("search_documents", "recherche dans documents indexés"),
                _mock_tool("manage_index", "gestion de l'index FAISS"),
            ],
        )
        idx = get_or_create_index("rooms/!SEARCH")
        await idx.build(config=MagicMock())

        assert idx._ready, "Index doit être prêt après build"
        assert len(idx._embeddings) == 2, f"2 embeddings attendus, {len(idx._embeddings)} obtenus"

        results = await idx.search(
            query="trouve un document",
            config=MagicMock(),
            top_k=5,
        )
        assert len(results) == 2, f"2 résultats attendus, {len(results)} obtenus"
        # Avec des embeddings constants, tous ont score 1.0
        for entry, score in results:
            assert score == 1.0, f"score attendu 1.0, obtenu {score}"
    print("OK search avec embeddings mockés")


def main():
    print("=" * 60)
    print("Test workspace resources index — phase 4")
    print("=" * 60)

    tests = [
        ("Création/réutilisation par workspace", test_create_index),
        ("Inventory construction", test_populate_inventory),
        ("Etag stable", test_inventory_etag_stable),
        ("Etag change si modif", test_inventory_etag_changes),
        ("Isolation entre workspaces", test_isolation_between_workspaces),
        ("Invalidation cache", test_invalidate_workspace),
        ("Search avec embeddings mockés", test_search_with_mocked_embeddings),
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
        print("Tous les tests workspace resources passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
