"""
Tests — WorkspaceSpecializer (auto-spécialisation depuis le corpus).
"""

import json

import pytest

from colaig.context.workspace import create_workspace, load_workspace
from colaig.integrations.storage.local import LocalStorage
from colaig.rag.colaig_index import ColaigIndex
from colaig.rag.specializer import WorkspaceSpecializer, _parse_persona


class _Chunk:
    def __init__(self, text):
        self.text = text


class _Store:
    def __init__(self, texts):
        self._chunks = [_Chunk(t) for t in texts]

    def get_all_active_chunks(self):
        return self._chunks


class _FakeAlbert:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    async def chat(self, messages, model=None, temperature=0.1, **kw):
        self.calls += 1
        return self._reply


_PERSONA_JSON = json.dumps({
    "domain": "marchés publics",
    "sub_domains": ["contrats"],
    "vocabulary": ["appel d'offres", "CCAP"],
    "tone": "formal",
    "expertise_level": "expert",
    "system_prompt": "Tu es un expert en marchés publics.",
    "confidence": 0.9,
})


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(base_path=str(tmp_path))


class TestParsePersona:
    def test_plain_json(self):
        assert _parse_persona('{"domain": "x"}')["domain"] == "x"

    def test_json_in_markdown_fence(self):
        assert _parse_persona('```json\n{"domain": "y"}\n```')["domain"] == "y"

    def test_invalid_returns_none(self):
        assert _parse_persona("pas du json") is None

    def test_vocabulary_capped(self):
        big = json.dumps({"vocabulary": [str(i) for i in range(100)]})
        assert len(_parse_persona(big)["vocabulary"]) == 50


class TestDerive:

    async def test_empty_corpus(self, storage):
        spec = WorkspaceSpecializer(storage, _FakeAlbert(_PERSONA_JSON))
        out = await spec.derive("/ws/", _Store([]))
        assert out["success"] is False

    async def test_dry_run_writes_knowledge_not_config(self, storage):
        await create_workspace(storage, "/ws/", "WS")
        spec = WorkspaceSpecializer(storage, _FakeAlbert(_PERSONA_JSON))
        out = await spec.derive("/ws/", _Store(["doc A", "doc B"]), dry_run=True)
        assert out["success"] is True and out["applied"] is False
        # knowledge.json écrit
        assert await storage.exists(ColaigIndex.knowledge_json_path("/ws/"))
        # config inchangée (system_prompt vide)
        ws = await load_workspace(storage, "/ws/")
        assert ws.system_prompt == ""

    async def test_apply_writes_config(self, storage):
        await create_workspace(storage, "/ws/", "WS")
        spec = WorkspaceSpecializer(storage, _FakeAlbert(_PERSONA_JSON))
        out = await spec.derive("/ws/", _Store(["doc A"]), dry_run=False)
        assert out["applied"] is True
        ws = await load_workspace(storage, "/ws/")
        assert ws.system_prompt == "Tu es un expert en marchés publics."
        assert ws.tone == "formal" and ws.expertise_level == "expert"

    async def test_preserve_manual_prompt(self, storage):
        await create_workspace(storage, "/ws/", "WS", system_prompt="PROMPT MANUEL")
        spec = WorkspaceSpecializer(storage, _FakeAlbert(_PERSONA_JSON))
        await spec.derive("/ws/", _Store(["doc A"]), dry_run=False, preserve_manual=True)
        ws = await load_workspace(storage, "/ws/")
        # le prompt manuel n'est PAS écrasé ; ton/expertise dérivés OK
        assert ws.system_prompt == "PROMPT MANUEL"
        assert ws.tone == "formal"

    async def test_invalid_llm_reply_no_write(self, storage):
        await create_workspace(storage, "/ws/", "WS")
        spec = WorkspaceSpecializer(storage, _FakeAlbert("pas du json"))
        out = await spec.derive("/ws/", _Store(["doc A"]), dry_run=False)
        assert out["success"] is False and out["applied"] is False
