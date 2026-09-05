"""
Tests pour Step 1 — Modèles Tools + ToolRegistry

Couvre :
- ToolParameter, ToolDefinition, ToolCall, ToolResult, ChatCompletionResult (models.py)
- ToolRegistry (agents/tool_registry.py)
- Tools built-in : rag_tools, storage_tools, summarize_tools
- build_tool_registry (context_builder.py)
"""

import pytest

from colaig.agents.context_builder import build_tool_registry
from colaig.agents.tool_registry import ToolRegistry
from colaig.agents.tools.rag_tools import SEARCH_DOCUMENTS_DEFINITION
from colaig.agents.tools.storage_tools import (
    FETCH_DOCUMENT_DEFINITION,
    LIST_DOCUMENTS_DEFINITION,
)
from colaig.agents.tools.summarize_tools import SUMMARIZE_TEXT_DEFINITION
from colaig.models import (
    ChatCompletionResult,
    ToolCall,
    ToolDefinition,
    ToolParameter,
    ToolResult,
)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def simple_tool():
    return ToolDefinition(
        name="my_tool",
        description="Un outil de test",
        parameters=[
            ToolParameter(name="query", type="string", description="La requête", required=True),
            ToolParameter(name="k", type="integer", description="Nombre de résultats", required=False),
        ],
    )


@pytest.fixture
def registry():
    return ToolRegistry()


# =============================================================================
# ToolParameter
# =============================================================================

def test_tool_parameter_defaults():
    p = ToolParameter(name="q", type="string")
    assert p.required is False
    assert p.enum == []
    assert p.description == ""


def test_tool_parameter_with_enum():
    p = ToolParameter(name="lang", type="string", enum=["fr", "en"], required=True)
    assert "fr" in p.enum
    assert p.required is True


# =============================================================================
# ToolDefinition.to_openai_schema
# =============================================================================

def test_to_openai_schema_structure(simple_tool):
    schema = simple_tool.to_openai_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "my_tool"
    assert fn["description"] == "Un outil de test"
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert "k" in params["properties"]


def test_to_openai_schema_required(simple_tool):
    schema = simple_tool.to_openai_schema()
    required = schema["function"]["parameters"]["required"]
    assert "query" in required
    assert "k" not in required


def test_to_openai_schema_enum():
    tool = ToolDefinition(
        name="t",
        description="test",
        parameters=[
            ToolParameter(name="lang", type="string", enum=["fr", "en"], required=True)
        ],
    )
    schema = tool.to_openai_schema()
    prop = schema["function"]["parameters"]["properties"]["lang"]
    assert prop["enum"] == ["fr", "en"]


def test_to_openai_schema_empty_params():
    tool = ToolDefinition(name="simple", description="no params")
    schema = tool.to_openai_schema()
    assert schema["function"]["parameters"]["properties"] == {}
    assert schema["function"]["parameters"]["required"] == []


# =============================================================================
# ToolCall, ToolResult, ChatCompletionResult
# =============================================================================

def test_tool_call_defaults():
    tc = ToolCall(tool_name="search_documents")
    assert tc.arguments == {}
    assert tc.call_id == ""


def test_tool_result_defaults():
    tr = ToolResult(tool_name="search_documents")
    assert tr.success is True
    assert tr.error == ""


def test_chat_completion_result_has_tool_calls_true():
    result = ChatCompletionResult(
        tool_calls=[ToolCall(tool_name="search_documents", arguments={"query": "test"})]
    )
    assert result.has_tool_calls is True


def test_chat_completion_result_has_tool_calls_false():
    result = ChatCompletionResult(content="Voici la réponse.")
    assert result.has_tool_calls is False


# =============================================================================
# ToolRegistry
# =============================================================================

@pytest.mark.asyncio
async def test_registry_register_and_get(registry, simple_tool):
    async def handler(query, k=5):
        return "result"

    registry.register(simple_tool, handler)
    entry = registry.get("my_tool")
    assert entry is not None
    defn, fn = entry
    assert defn.name == "my_tool"


def test_registry_get_unknown(registry):
    assert registry.get("nonexistent") is None


def test_registry_list_definitions(registry, simple_tool):
    async def h(**kw): return ""
    registry.register(simple_tool, h)
    defns = registry.list_definitions()
    assert len(defns) == 1
    assert defns[0].name == "my_tool"


def test_registry_list_openai_schemas(registry, simple_tool):
    async def h(**kw): return ""
    registry.register(simple_tool, h)
    schemas = registry.list_openai_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"


def test_registry_filter_by_names(registry):
    async def h(**kw): return ""
    t1 = ToolDefinition(name="t1", description="tool 1")
    t2 = ToolDefinition(name="t2", description="tool 2")
    t3 = ToolDefinition(name="t3", description="tool 3")
    registry.register(t1, h)
    registry.register(t2, h)
    registry.register(t3, h)

    filtered = registry.filter_by_names(["t1", "t3"])
    assert "t1" in filtered
    assert "t3" in filtered
    assert "t2" not in filtered
    assert len(filtered) == 2


def test_registry_filter_by_names_unknown_ignored(registry, simple_tool):
    async def h(**kw): return ""
    registry.register(simple_tool, h)
    filtered = registry.filter_by_names(["my_tool", "nonexistent"])
    assert len(filtered) == 1


@pytest.mark.asyncio
async def test_registry_execute_success(registry, simple_tool):
    async def handler(query, k=5):
        return f"results for {query}"

    registry.register(simple_tool, handler)
    tool_call = ToolCall(tool_name="my_tool", arguments={"query": "test", "k": 3})
    result = await registry.execute(tool_call)
    assert result.success is True
    assert "results for test" in result.result


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool(registry):
    tool_call = ToolCall(tool_name="nonexistent")
    result = await registry.execute(tool_call)
    assert result.success is False
    assert "nonexistent" in result.error


@pytest.mark.asyncio
async def test_registry_execute_handler_error(registry, simple_tool):
    async def broken_handler(query, k=5):
        raise ValueError("boom")

    registry.register(simple_tool, broken_handler)
    tool_call = ToolCall(tool_name="my_tool", arguments={"query": "test"})
    result = await registry.execute(tool_call)
    assert result.success is False
    assert "boom" in result.error


# =============================================================================
# Tool built-in definitions
# =============================================================================

def test_search_documents_definition_valid():
    schema = SEARCH_DOCUMENTS_DEFINITION.to_openai_schema()
    assert schema["function"]["name"] == "search_documents"
    params = schema["function"]["parameters"]["properties"]
    assert "query" in params
    assert "k" in params
    assert "threshold" in params


def test_fetch_document_definition_valid():
    schema = FETCH_DOCUMENT_DEFINITION.to_openai_schema()
    assert schema["function"]["name"] == "fetch_document"
    assert "path" in schema["function"]["parameters"]["properties"]


def test_list_documents_definition_valid():
    schema = LIST_DOCUMENTS_DEFINITION.to_openai_schema()
    assert schema["function"]["name"] == "list_documents"


def test_summarize_text_definition_valid():
    schema = SUMMARIZE_TEXT_DEFINITION.to_openai_schema()
    assert schema["function"]["name"] == "summarize_text"
    params = schema["function"]["parameters"]["properties"]
    assert "text" in params
    assert "max_sentences" in params


# =============================================================================
# build_tool_registry
# =============================================================================

def test_build_tool_registry_contains_expected_tools(mock_retriever, mock_storage, mock_albert):
    registry = build_tool_registry(mock_retriever, mock_storage, mock_albert)
    assert "search_documents" in registry
    assert "fetch_document" in registry
    assert "list_documents" in registry
    assert "summarize_text" in registry
    assert len(registry) == 4
