"""
Test unitaire de la boucle agent Colaig.

Simule des réponses LLM via generate_fn injectable pour vérifier :
- Réponse directe (sans outil)
- Appel d'un outil -> résultat -> réponse
- Multi-tour (outil inconnu -> fallback)
- Limite de tours
"""
import asyncio
from app.agent.parser import parse_tool_calls, ToolCall
from app.agent.tools import ToolDef, ToolRegistry
from app.agent.loop import agent_loop
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
    async def mock_search(query: str, **ctx):
        return f"Résultat pour '{query}': Document A, Document B."

    reg = ToolRegistry()
    reg.register(ToolDef(
        name="search_documents",
        description="Recherche dans les documents.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=mock_search,
    ))
    return reg


# ─── Tests de la boucle agent ────────────────────────────────────────────────

async def test_direct_response():
    """Le LLM répond directement sans appeler d'outil."""
    registry = _make_registry()

    async def mock_gen(config, messages):
        return "Voici ma réponse directe."

    result = await agent_loop(
        message="Bonjour",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
        generate_fn=mock_gen,
    )
    assert result == "Voici ma réponse directe."


async def test_one_tool_call():
    """Le LLM appelle un outil, puis répond avec le résultat."""
    registry = _make_registry()
    call_count = 0

    async def mock_gen(config, messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return '<tool_call>{"name": "search_documents", "arguments": {"query": "recrutement"}}</tool_call>'
        else:
            last_msg = messages[-1]["content"]
            assert "Résultat pour" in last_msg
            return "Selon les documents, le recrutement fonctionne ainsi..."

    result = await agent_loop(
        message="procédures de recrutement ?",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
        generate_fn=mock_gen,
    )
    assert "recrutement" in result
    assert call_count == 2


async def test_max_turns_reached():
    """La boucle s'arrête après max_turns."""
    registry = _make_registry()
    call_count = 0

    async def mock_gen(config, messages):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return '<tool_call>{"name": "search_documents", "arguments": {"query": "boucle"}}</tool_call>'
        return "Réponse forcée après limite."

    result = await agent_loop(
        message="test boucle",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
        generate_fn=mock_gen,
        max_turns=3,
    )
    assert "Réponse forcée" in result


async def test_unknown_tool():
    """Un outil inconnu retourne un message d'erreur sans crasher."""
    registry = _make_registry()
    call_count = 0

    async def mock_gen(config, messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return '<tool_call>{"name": "outil_inexistant", "arguments": {}}</tool_call>'
        return "Je n'ai pas pu utiliser cet outil."

    result = await agent_loop(
        message="test",
        history=[],
        system_prompt="Tu es Colaig.",
        registry=registry,
        config=None,
        generate_fn=mock_gen,
    )
    assert call_count == 2


# ─── Tests du prompt ─────────────────────────────────────────────────────────

def test_system_prompt_contains_tools():
    registry = _make_registry()
    prompt = build_system_prompt(registry)
    assert "Colaig" in prompt
    assert "search_documents" in prompt
    assert "tool_call" in prompt


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

    asyncio.run(test_direct_response())
    print("test_direct_response OK")
    asyncio.run(test_one_tool_call())
    print("test_one_tool_call OK")
    asyncio.run(test_max_turns_reached())
    print("test_max_turns_reached OK")
    asyncio.run(test_unknown_tool())
    print("test_unknown_tool OK")

    print("\nTous les tests agent passent.")
