"""
Colaig — Authentification MCP.

Exports publics :
    TokenManager       — création, validation et révocation des tokens statiques
    TokenContext       — contexte résolu depuis un token (user_id, scope, role)
    get_current_token  — lit le ContextVar async (utilisé par les MCP tools)
    MCPTokenMiddleware — middleware FastAPI/Starlette pour /mcp
    OIDCValidator      — validation JWT RS256 via JWKS (mode enterprise SSO)
    OIDCValidationError — exception levée si JWT invalide/expiré
"""

from colaig.auth.tokens import TokenContext, TokenManager, get_current_token
from colaig.auth.middleware import MCPTokenMiddleware
from colaig.auth.oidc_validator import OIDCValidator, OIDCValidationError

__all__ = [
    "TokenContext",
    "TokenManager",
    "get_current_token",
    "MCPTokenMiddleware",
    "OIDCValidator",
    "OIDCValidationError",
]
