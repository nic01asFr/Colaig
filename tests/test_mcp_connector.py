"""
Tests unitaires — MCPConnectorClient (colaig/integrations/mcp_connector.py)
"""
from __future__ import annotations

import json
import pytest
import httpx
import respx

from colaig.models import MCPConnectorConfig
from colaig.integrations.mcp_connector import (
    MCPConnectorClient,
    _parse_tool_definition,
    _create_tool_handler,
    _json_type_to_colaig,
    _extract_mcp_content,
    _should_expose_tool,
    _TOOLS_CACHE,
    _INSTRUCTIONS_CACHE,
    _RATE_LIMITER,
)


@pytest.fixture(autouse=True)
def clear_mcp_caches():
    """Vide les caches TTL entre chaque test pour garantir l'isolation."""
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()
    _RATE_LIMITER.clear()
    yield
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()
    _RATE_LIMITER.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_connector(**kwargs) -> MCPConnectorConfig:
    defaults = dict(name="test_connector", url="http://mcp.local/mcp", enabled=True, expose_tools=True)
    defaults.update(kwargs)
    return MCPConnectorConfig(**defaults)


RAW_TOOL = {
    "name": "search",
    "description": "Recherche des documents",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Requête"},
            "k": {"type": "integer", "description": "Nombre de résultats"},
        },
        "required": ["query"],
    },
}

TOOLS_LIST_RESPONSE = {
    "jsonrpc": "2.0",
    "result": {"tools": [RAW_TOOL]},
    "id": 1,
}

TOOL_CALL_RESPONSE = {
    "jsonrpc": "2.0",
    "result": {
        "content": [{"type": "text", "text": "Résultat de la recherche"}],
    },
    "id": 1,
}


# ── Tests _parse_tool_definition ─────────────────────────────────────────────

def test_parse_tool_definition_basic():
    parsed = _parse_tool_definition(RAW_TOOL, "my_connector")
    assert parsed is not None
    defn, annotations = parsed
    assert defn.name == "my_connector__search"
    assert defn.description == "Recherche des documents"
    assert defn.category == "mcp_external"
    assert len(defn.parameters) == 2
    query_param = next(p for p in defn.parameters if p.name == "query")
    assert query_param.required is True
    assert query_param.type == "string"
    k_param = next(p for p in defn.parameters if p.name == "k")
    assert k_param.required is False
    assert k_param.type == "integer"
    assert isinstance(annotations, dict)


def test_parse_tool_definition_no_name():
    parsed = _parse_tool_definition({}, "connector")
    assert parsed is None


def test_parse_tool_definition_no_schema():
    raw = {"name": "simple_tool", "description": "Un outil simple"}
    parsed = _parse_tool_definition(raw, "connector")
    assert parsed is not None
    defn, _ = parsed
    assert defn.name == "connector__simple_tool"
    assert defn.parameters == []


def test_parse_tool_definition_alt_schema_key():
    raw = {
        "name": "alt_tool",
        "input_schema": {
            "properties": {"val": {"type": "number"}},
            "required": [],
        },
    }
    parsed = _parse_tool_definition(raw, "c")
    assert parsed is not None
    defn, _ = parsed
    assert len(defn.parameters) == 1
    assert defn.parameters[0].type == "number"


def test_parse_tool_definition_with_annotations():
    raw = {
        "name": "navigate",
        "description": "Nav",
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    }
    parsed = _parse_tool_definition(raw, "chrome")
    assert parsed is not None
    _, annotations = parsed
    assert annotations["destructiveHint"] is True
    assert annotations["readOnlyHint"] is False


# ── Tests _json_type_to_colaig ───────────────────────────────────────────────

def test_json_type_mapping():
    assert _json_type_to_colaig("string") == "string"
    assert _json_type_to_colaig("integer") == "integer"
    assert _json_type_to_colaig("number") == "number"
    assert _json_type_to_colaig("boolean") == "boolean"
    assert _json_type_to_colaig("array") == "array"
    assert _json_type_to_colaig("object") == "string"
    assert _json_type_to_colaig("unknown") == "string"


# ── Tests MCPConnectorClient.list_tools ──────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_tools_success():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json=TOOLS_LIST_RESPONSE))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()

    assert len(tools) == 1
    defn, handler = tools[0]
    assert defn.name == "test_connector__search"
    assert callable(handler)


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_network_error():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(side_effect=httpx.ConnectError("unreachable"))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()
    assert tools == []


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_json_rpc_error():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json={
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Method not found"},
        "id": 1,
    }))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()
    assert tools == []


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_http_error():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(500))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()
    assert tools == []


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_sends_auth_header():
    connector = make_connector(auth_token="my_secret_token")
    captured = {}

    def capture(request, route):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=TOOLS_LIST_RESPONSE)

    respx.post("http://mcp.local/mcp").mock(side_effect=capture)

    client = MCPConnectorClient(connector)
    await client.list_tools()
    assert captured["auth"] == "Bearer my_secret_token"


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_no_auth_header_when_empty():
    connector = make_connector(auth_token="")
    captured = {}

    def capture(request, route):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=TOOLS_LIST_RESPONSE)

    respx.post("http://mcp.local/mcp").mock(side_effect=capture)

    client = MCPConnectorClient(connector)
    await client.list_tools()
    assert captured["auth"] is None


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_empty_tools_list():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json={
        "jsonrpc": "2.0",
        "result": {"tools": []},
        "id": 1,
    }))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()
    assert tools == []


# ── Tests handler (appel outil distant) ──────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_tool_handler_success():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json=TOOL_CALL_RESPONSE))

    handler = _create_tool_handler(connector, "search")
    result = await handler(query="marchés publics", k=5)

    # Le handler retourne une string (contrat ToolRegistry)
    assert isinstance(result, str)
    assert "Résultat de la recherche" in result


@pytest.mark.asyncio
@respx.mock
async def test_tool_handler_sends_correct_payload():
    connector = make_connector()
    captured = {}

    def capture(request, route):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=TOOL_CALL_RESPONSE)

    respx.post("http://mcp.local/mcp").mock(side_effect=capture)

    handler = _create_tool_handler(connector, "search")
    await handler(query="test")

    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "search"
    assert captured["body"]["params"]["arguments"] == {"query": "test"}


@pytest.mark.asyncio
@respx.mock
async def test_tool_handler_network_failure():
    """Sur erreur réseau, le handler lève RuntimeError (capturé par ToolRegistry)."""
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(side_effect=httpx.ConnectError("timeout"))

    handler = _create_tool_handler(connector, "search")
    with pytest.raises(RuntimeError):
        await handler(query="test")


@pytest.mark.asyncio
@respx.mock
async def test_tool_handler_jsonrpc_error():
    """Sur erreur JSON-RPC, le handler lève RuntimeError avec le message d'erreur."""
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json={
        "jsonrpc": "2.0",
        "error": {"code": -32000, "message": "Internal error"},
        "id": 1,
    }))

    handler = _create_tool_handler(connector, "search")
    with pytest.raises(RuntimeError, match="Internal error"):
        await handler(query="test")


@pytest.mark.asyncio
@respx.mock
async def test_tool_handler_multi_content_blocks():
    connector = make_connector()
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json={
        "jsonrpc": "2.0",
        "result": {
            "content": [
                {"type": "text", "text": "Bloc 1"},
                {"type": "image", "url": "http://img.png"},  # ignoré
                {"type": "text", "text": "Bloc 2"},
            ],
        },
        "id": 1,
    }))

    handler = _create_tool_handler(connector, "search")
    result = await handler(query="test")

    assert isinstance(result, str)
    assert "Bloc 1" in result
    assert "Bloc 2" in result


# ── Tests _extract_mcp_content ──────────────────────────────────────────────

def test_extract_text_content():
    content = [{"type": "text", "text": "Hello"}]
    assert _extract_mcp_content(content) == "Hello"


def test_extract_image_content():
    content = [{"type": "image", "data": "aWJhc2U2NA==", "mimeType": "image/png"}]
    result = _extract_mcp_content(content)
    assert "Image capturée" in result
    assert "image/png" in result


def test_extract_mixed_content():
    content = [
        {"type": "text", "text": "Page title"},
        {"type": "image", "data": "abc", "mimeType": "image/jpeg"},
        {"type": "text", "text": "Page body"},
    ]
    result = _extract_mcp_content(content)
    assert "Page title" in result
    assert "Image capturée" in result
    assert "Page body" in result


def test_extract_truncation():
    content = [{"type": "text", "text": "x" * 20000}]
    result = _extract_mcp_content(content, max_length=100)
    assert len(result) <= 150  # 100 + truncation message
    assert "tronqué" in result


def test_extract_resource_content():
    content = [{"type": "resource", "resource": {"uri": "file://doc.md", "text": "# Title"}}]
    result = _extract_mcp_content(content)
    assert "# Title" in result
    assert "file://doc.md" in result


# ── Tests _should_expose_tool ───────────────────────────────────────────────

def test_policy_all_exposes_everything():
    connector = make_connector(tool_policy="all")
    assert _should_expose_tool({}, connector, "navigate") is True
    assert _should_expose_tool({"destructiveHint": True}, connector, "click") is True


def test_policy_read_only_blocks_destructive():
    connector = make_connector(tool_policy="read_only")
    assert _should_expose_tool({"destructiveHint": True}, connector, "click") is False
    assert _should_expose_tool({"readOnlyHint": True}, connector, "screenshot") is True


def test_policy_read_only_infers_from_name():
    connector = make_connector(tool_policy="read_only")
    # Pas d'annotation → inférer depuis le nom
    assert _should_expose_tool({}, connector, "getConsoleLog") is True
    assert _should_expose_tool({}, connector, "screenshot") is True
    assert _should_expose_tool({}, connector, "click") is False
    assert _should_expose_tool({}, connector, "type") is False


def test_policy_explicit():
    connector = make_connector(tool_policy="explicit", allowed_tools=["navigate", "screenshot"])
    assert _should_expose_tool({}, connector, "navigate") is True
    assert _should_expose_tool({}, connector, "screenshot") is True
    assert _should_expose_tool({}, connector, "click") is False


# ── Tests session_id propagation ────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_handler_injects_session_header():
    """Le handler injecte X-Session-Id si _session_id est fourni."""
    connector = make_connector(session_scope="conversation")
    captured = {}

    def capture(request, route):
        captured["session"] = request.headers.get("X-Session-Id")
        return httpx.Response(200, json=TOOL_CALL_RESPONSE)

    respx.post("http://mcp.local/mcp").mock(side_effect=capture)

    handler = _create_tool_handler(connector, "search")
    await handler(query="test", _session_id="conv-123")
    assert captured["session"] == "conv-123"


@pytest.mark.asyncio
@respx.mock
async def test_handler_no_session_when_scope_none():
    """Pas de header X-Session-Id si session_scope=none."""
    connector = make_connector(session_scope="none")
    captured = {}

    def capture(request, route):
        captured["session"] = request.headers.get("X-Session-Id")
        return httpx.Response(200, json=TOOL_CALL_RESPONSE)

    respx.post("http://mcp.local/mcp").mock(side_effect=capture)

    handler = _create_tool_handler(connector, "search")
    await handler(query="test", _session_id="conv-123")
    assert captured["session"] is None


# ── Tests tool_policy filtering dans list_tools ─────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_tools_read_only_filters():
    """list_tools avec policy read_only filtre les tools destructifs."""
    connector = make_connector(tool_policy="read_only")
    respx.post("http://mcp.local/mcp").mock(return_value=httpx.Response(200, json={
        "jsonrpc": "2.0",
        "result": {"tools": [
            {"name": "screenshot", "description": "Take screenshot", "annotations": {"readOnlyHint": True}},
            {"name": "click", "description": "Click element", "annotations": {"destructiveHint": True}},
        ]},
        "id": 1,
    }))

    client = MCPConnectorClient(connector)
    tools = await client.list_tools()
    names = [t[0].name for t in tools]
    assert "test_connector__screenshot" in names
    assert "test_connector__click" not in names


# ── Tests URL validation (SSRF) ────────────────────────────────────────────

def test_validate_blocks_private_ip():
    from colaig.security.url_validator import validate_navigation_url, URLValidationError
    with pytest.raises(URLValidationError):
        validate_navigation_url("http://169.254.169.254/meta-data/", resolve_dns=False)


def test_validate_blocks_localhost():
    from colaig.security.url_validator import validate_navigation_url, URLValidationError
    with pytest.raises(URLValidationError):
        validate_navigation_url("http://127.0.0.1:8080/admin", resolve_dns=False)


def test_validate_allows_public_url():
    from colaig.security.url_validator import validate_navigation_url
    result = validate_navigation_url("https://www.service-public.fr", resolve_dns=False)
    assert result == "https://www.service-public.fr"


def test_validate_domain_allowlist():
    from colaig.security.url_validator import validate_navigation_url, URLValidationError
    # Allowed
    validate_navigation_url(
        "https://demarches-simplifiees.fr/login",
        allowed_domains=["*.gouv.fr", "demarches-simplifiees.fr"],
        resolve_dns=False,
    )
    # Blocked
    with pytest.raises(URLValidationError, match="non autorisé"):
        validate_navigation_url(
            "https://evil.com/steal",
            allowed_domains=["*.gouv.fr"],
            resolve_dns=False,
        )


def test_validate_rejects_non_http():
    from colaig.security.url_validator import validate_navigation_url, URLValidationError
    with pytest.raises(URLValidationError, match="Schéma interdit"):
        validate_navigation_url("ftp://server/file", resolve_dns=False)


# ── Tests rate limiting ─────────────────────────────────────────────────────

def test_rate_limit_blocks_excess():
    from colaig.integrations.mcp_connector import _check_rate_limit
    url = "http://test-rate-limit/mcp"
    # 3 appels autorisés
    for _ in range(3):
        _check_rate_limit(url, 3)
    # 4e appel → bloqué
    with pytest.raises(RuntimeError, match="Rate limit"):
        _check_rate_limit(url, 3)


def test_rate_limit_zero_means_unlimited():
    from colaig.integrations.mcp_connector import _check_rate_limit
    for _ in range(100):
        _check_rate_limit("http://unlimited/mcp", 0)  # jamais d'erreur
