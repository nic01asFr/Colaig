"""
Package MCP (Model Context Protocol) pour Colaig.

Implémente le transport Streamable HTTP (spec 2025-03-26) pour la connexion
à des serveurs MCP distants déclarés dans les workspaces.

Capacités client complètes :
  Client → Serveur : tools, resources, prompts, completion, logging, ping
  Serveur → Client : sampling, roots, notifications (progress, log, list_changed…)

Structure :
    types.py            — MCPTool, MCPResource, MCPPrompt, MCPRoot, etc.
    transport.py        — StreamableHTTPTransport : POST + GET SSE bidirectionnel
    client.py           — MCPClient : API haut-niveau, tous les capabilities
    workspace_loader.py — lecture de .albert/config/mcp_servers.json (WebDAV)
    registry.py         — MCPRegistry singleton, gestion par workspace
"""

from app.services.mcp.types import (
    MCPCompletionResult,
    MCPContent,
    MCPPrompt,
    MCPPromptResult,
    MCPResource,
    MCPResourceContent,
    MCPRoot,
    MCPTool,
    MCPToolResult,
    ServerCapabilities,
)
from app.services.mcp.transport import StreamableHTTPTransport
from app.services.mcp.client import MCPClient
from app.services.mcp.workspace_loader import MCPServerConfig, load_mcp_servers_from_webdav
from app.services.mcp.registry import MCPRegistry, get_mcp_registry, close_mcp_registry

__all__ = [
    # Types
    "MCPContent",
    "MCPTool",
    "MCPToolResult",
    "MCPResource",
    "MCPResourceContent",
    "MCPPrompt",
    "MCPPromptResult",
    "MCPRoot",
    "MCPCompletionResult",
    "ServerCapabilities",
    # Transport
    "StreamableHTTPTransport",
    # Client
    "MCPClient",
    # Workspace
    "MCPServerConfig",
    "load_mcp_servers_from_webdav",
    # Registry
    "MCPRegistry",
    "get_mcp_registry",
    "close_mcp_registry",
]
