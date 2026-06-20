"""
Test unitaire de la boucle agent Colaig.

La boucle utilise désormais le function-calling natif (OpenAI tool_use) via
`LLMTransport`, qui délègue à `app.core_llm.generate_with_tools`. On simule donc
les réponses LLM en monkeypatchant `generate_with_tools` pour vérifier :
- Réponse directe (sans outil)
- Appel d'un outil -> résultat -> réponse
- Multi-tour (limite de tours)
- Outil inconnu -> message d'erreur sans crash
"""
import app.core_llm as core_llm
from app.agent.parser import parse_tool_calls
from app.agent.tools import ToolDef, ToolRegistry
from app.agent.loop import agent_loop
from app.agent.result import AgentResult
from app.agent.prompt import build_system_prompt


# ─── Tests du parser ─────────────────────────────────────────────────────────

def test_parse_no_tool():
    text, calls = parse_tool_calls("Réponse directe sans outil.")
    assert calls == []
    assert text == "Réponse directe sans outil."


def test_parse_one_tool():
    raw = 'Je cherche.\n<tool_call>{"name": "search_documents", "arguments": {"query": "test"}}</tool_call>'
    text, calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].name == "search_documents"
    assert calls[0].arguments == {"query": "test"}
    assert "<tool_call>" not in text


def test_parse_two_tools():
    raw = (
        '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
        'texte entre'
        '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>'
    )
    text, calls = parse_tool_calls(raw)
    assert len(calls) == 2


def test_parse_invalid_json():
    text, calls = parse_tool_calls('<tool_call>not json</tool_call>')
    assert calls == []


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_registry():
    async def mock_search(args, ctx):
        query = args.get("query", "")
        return f"Résultat pour '{query}': Document A, Document B."

    reg = ToolRegistry()
    reg.register(ToolDef(
        name="search_documents",
        description="Recherche dans les documents.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=mock_search,
    ))
    return reg


def _llm(content="", tool_calls=None):
    """Construit une réponse au format `generate_with_tools`."""
    return {"content": content, "tool_calls": tool_calls or []}


def _patch_generate(monkeypatch, responses):
    """Monkeypatch `generate_with_tools` pour renvoyer `responses` à la suite.

    Retourne un dict mutable exposant le compteur d'appels et les messages
    du dernier appel, pour les assertions.
    """
    state = {"count": 0, "last_messages": None}

    async def fake_generate_with_tools(config, messages, tools=None):
        state["last_messages"] = messages
        idx = min(state["count"], len(responses) - 1)
        state["count"] += 1
        resp = responses[idx]
        return resp(messages) if callable(resp) else resp

    monkeypatch.setattr(core_llm, "generate_with_tools", fake_generate_with_tools)
    return state


# ─── Tests de la boucle agent ────────────────────────────────────────────────

async def test_direct_response(monkeypatch):
    """Le LLM répond directement sans appeler d'outil."""
    registry = _make_registry()
    _patch_generate(monkeypatch, [_llm(content="Voici ma réponse directe.")])

    result = await agent_loop(
        message="Bonjour",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
    )
    assert isinstance(result, AgentResult)
    assert result.text == "Voici ma réponse directe."


async def test_one_tool_call(monkeypatch):
    """Le LLM appelle un outil, puis répond avec le résultat."""
    registry = _make_registry()

    def second_turn(messages):
        # Le résultat de l'outil doit être présent dans l'historique
        assert any("Résultat pour" in (m.get("content") or "") for m in messages)
        return _llm(content="Selon les documents, le recrutement fonctionne ainsi...")

    state = _patch_generate(monkeypatch, [
        _llm(tool_calls=[{"name": "search_documents", "arguments": {"query": "recrutement"}}]),
        second_turn,
    ])

    result = await agent_loop(
        message="procédures de recrutement ?",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
    )
    assert "recrutement" in result.text
    assert state["count"] == 2


async def test_max_turns_reached(monkeypatch):
    """La boucle s'arrête après max_turns et force une réponse finale."""
    registry = _make_registry()

    # Toujours rappeler un outil : la boucle doit finir par forcer une réponse.
    _patch_generate(monkeypatch, [
        _llm(tool_calls=[{"name": "search_documents", "arguments": {"query": "boucle"}}]),
        _llm(tool_calls=[{"name": "search_documents", "arguments": {"query": "boucle"}}]),
        _llm(tool_calls=[{"name": "search_documents", "arguments": {"query": "boucle"}}]),
        _llm(content="Réponse forcée après limite."),
    ])

    result = await agent_loop(
        message="test boucle",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
        max_turns=3,
    )
    assert "Réponse forcée" in result.text


async def test_unknown_tool(monkeypatch):
    """Un outil inconnu retourne un message d'erreur sans crasher."""
    registry = _make_registry()

    def second_turn(messages):
        # Le message d'erreur "Outil inconnu" doit être remonté au LLM
        assert any("inconnu" in (m.get("content") or "").lower() for m in messages)
        return _llm(content="Je n'ai pas pu utiliser cet outil.")

    state = _patch_generate(monkeypatch, [
        _llm(tool_calls=[{"name": "outil_inexistant", "arguments": {}}]),
        second_turn,
    ])

    result = await agent_loop(
        message="test",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
    )
    assert state["count"] == 2
    assert "cet outil" in result.text


# ─── Tests du prompt ─────────────────────────────────────────────────────────

def test_system_prompt_contains_tools():
    registry = _make_registry()
    prompt = build_system_prompt(registry)
    assert "Colaig" in prompt
    assert "search_documents" in prompt
    # Le function-calling est natif : le prompt décrit les outils sans balise
    # textuelle, mais doit bien mentionner la notion d'outil.
    assert "outil" in prompt.lower()


def test_system_prompt_with_workspace():
    registry = _make_registry()
    prompt = build_system_prompt(
        registry,
        workspace_info={"name": "Mon espace", "total_documents": 42, "is_fresh": True},
        behaviors_summary="- actions : standard_rag, synthesis",
    )
    assert "Mon espace" in prompt
    assert "42" in prompt
    assert "standard_rag" in prompt


# ─── Exécution directe ───────────────────────────────────────────────────────

if __name__ == "__main__":
    test_parse_no_tool()
    test_parse_one_tool()
    test_parse_two_tools()
    test_parse_invalid_json()
    test_system_prompt_contains_tools()
    test_system_prompt_with_workspace()
    print("Tests synchrones OK")
    print("Lancez `pytest tests/test_agent_loop.py` pour les tests async.")
