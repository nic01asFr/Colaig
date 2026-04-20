"""
Tests unitaires — search_skill tool (colaig/agents/tools/skill_tools.py)
"""
from __future__ import annotations

import json
import pytest

from colaig.agents.tools.skill_tools import (
    SEARCH_SKILL_DEFINITION,
    create_search_skill_handler,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeFile:
    def __init__(self, path: str):
        self.path = path


class _FakeStorage:
    """Storage minimal pour les tests : retourne des fichiers .md fictifs."""

    def __init__(self, files: dict[str, bytes]):
        self._files = files  # path → bytes

    async def list_files(self, path: str):
        return [_FakeFile(p) for p in self._files if p.startswith(path)]

    async def download(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]


# ── Tests SEARCH_SKILL_DEFINITION ────────────────────────────────────────────

def test_definition_name():
    assert SEARCH_SKILL_DEFINITION.name == "search_skill"


def test_definition_has_query_param():
    names = [p.name for p in SEARCH_SKILL_DEFINITION.parameters]
    assert "query" in names


def test_definition_category():
    assert SEARCH_SKILL_DEFINITION.category == "knowledge"


# ── Tests keyword fallback ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keyword_match_returns_skill():
    storage = _FakeStorage({
        "/ws/.colaig/skills/conges.md": b"Pour poser des cong\xc3\xa9s, remplir le formulaire RH.",
        "/ws/.colaig/skills/autre.md": b"Guide de bienvenue.",
    })
    handler = create_search_skill_handler(storage, "/ws")
    result = json.loads(await handler(query="formulaire congés"))
    assert result["found"] is True
    assert len(result["skills"]) >= 1
    assert "conges" in result["skills"][0]["name"]


@pytest.mark.asyncio
async def test_keyword_no_match_returns_not_found():
    storage = _FakeStorage({
        "/ws/.colaig/skills/conges.md": b"Formulaire de cong\xc3\xa9s.",
    })
    handler = create_search_skill_handler(storage, "/ws")
    result = json.loads(await handler(query="recrutement budget"))
    assert result["found"] is False
    assert result["skills"] == []


@pytest.mark.asyncio
async def test_empty_skills_directory():
    storage = _FakeStorage({})
    handler = create_search_skill_handler(storage, "/ws")
    result = json.loads(await handler(query="anything"))
    assert result["found"] is False


@pytest.mark.asyncio
async def test_empty_query_returns_error():
    storage = _FakeStorage({})
    handler = create_search_skill_handler(storage, "/ws")
    result = json.loads(await handler(query=""))
    assert result["found"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_k_limits_results():
    files = {
        f"/ws/.colaig/skills/skill{i}.md": f"formulaire procédure {i}".encode()
        for i in range(5)
    }
    storage = _FakeStorage(files)
    handler = create_search_skill_handler(storage, "/ws")
    result = json.loads(await handler(query="formulaire procédure", k=2))
    assert len(result["skills"]) <= 2


# ── Tests semantic path ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_fallback_on_registry_error():
    """Si le registry lève une exception, on bascule sur keyword."""
    storage = _FakeStorage({
        "/ws/.colaig/skills/conges.md": b"Proced\xc3\xbbre de cong\xc3\xa9s annuels.",
    })

    class _BadRegistry:
        async def search(self, key, emb, k):
            raise RuntimeError("index not found")

    class _FakeAlbert:
        async def embed(self, text: str):
            return [0.1] * 128

    handler = create_search_skill_handler(
        storage, "/ws", albert=_FakeAlbert(), index_registry=_BadRegistry()
    )
    result = json.loads(await handler(query="congés annuels"))
    # Dégradation gracieuse vers keyword
    assert result["found"] is True


@pytest.mark.asyncio
async def test_semantic_path_used_when_registry_available():
    """Quand le registry fonctionne, la recherche sémantique est prioritaire."""
    storage = _FakeStorage({})

    class _FakeRegistry:
        async def search(self, key, emb, k):
            # Retourne un résultat sous forme de tuple (score, metadata)
            return [(0.95, {"source": "conges.md", "text": "Procédure congés"})]

    class _FakeAlbert:
        async def embed(self, text: str):
            return [0.1] * 128

    handler = create_search_skill_handler(
        storage, "/ws", albert=_FakeAlbert(), index_registry=_FakeRegistry()
    )
    result = json.loads(await handler(query="congés"))
    assert result["found"] is True
    assert result["skills"][0]["name"] == "conges.md"
    assert "congés" in result["skills"][0]["content"]
