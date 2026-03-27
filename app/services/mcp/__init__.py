"""
Package MCP (Model Context Protocol) pour Colaig.

Implémente le transport Streamable HTTP (spec 2025-03-26, stable en 2026)
pour la connexion à des serveurs MCP distants déclarés dans les workspaces.

Structure :
    client.py         — MCPHTTPClient : transport JSON-RPC over Streamable HTTP
    workspace_loader.py — lecture de .albert/config/mcp_servers.json sur WebDAV
    registry.py       — MCPRegistry : gestion des clients par workspace
"""

from app.services.mcp.client import MCPHTTPClient, MCPTool, MCPToolResult
from app.services.mcp.workspace_loader import MCPServerConfig, load_mcp_servers_from_webdav
from app.services.mcp.registry import MCPRegistry, get_mcp_registry

__all__ = [
    "MCPHTTPClient",
    "MCPTool",
    "MCPToolResult",
    "MCPServerConfig",
    "load_mcp_servers_from_webdav",
    "MCPRegistry",
    "get_mcp_registry",
]
