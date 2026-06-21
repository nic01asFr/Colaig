"""
Colaig — Middleware d'authentification MCP.

Intercepte les requêtes vers /mcp, extrait le token Bearer et le valide.
Deux modes de validation supportés :

Mode "token" (défaut) :
    Token statique colaig_{slug}_{64hex} validé via TokenManager (fichier storage).

Mode "oidc" :
    JWT RS256/ES256 émis par un IdP enterprise (Azure AD, Keycloak...).
    Validé via OIDCValidator (signature JWKS + claims iss/aud/exp).
    Requis : pip install 'colaig[oidc]' + COLAIG_MCP_AUTH_MODE=oidc.

Comportement commun (COLAIG_MCP_AUTH_ENABLED=true) :
    - Token valide   → TokenContext dans ContextVar, requête transmise
    - Token invalide → 401 JSON immédiat
    - Pas de token   → pass-through (chatbot public, workspaces publics)

La distinction "token invalide" vs "pas de token" est intentionnelle :
    - Pas de token : l'utilisateur ne cherche pas à s'authentifier → pass-through
    - Token présent mais invalide : tentative d'auth échouée → 401 explicite

Ce middleware ne touche pas aux requêtes hors /mcp (routes web admin, /health...).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from colaig.auth.tokens import TokenManager, set_current_token

if TYPE_CHECKING:
    from colaig.auth.oidc_validator import OIDCValidator

logger = logging.getLogger(__name__)


class MCPTokenMiddleware(BaseHTTPMiddleware):
    """Middleware FastAPI/Starlette — validation token Bearer sur /mcp.

    Args:
        app           : ASGI app (injecté automatiquement par add_middleware).
        token_manager : Instance TokenManager partagée (mode "token").
        mcp_path      : Préfixe de chemin du serveur MCP (défaut "/mcp").
        auth_required : Conservé pour compatibilité — non utilisé.
        oidc_validator: OIDCValidator si mode="oidc", None sinon.
                        Quand fourni, la validation OIDC prend le dessus sur
                        la validation par token statique.
    """

    def __init__(
        self,
        app,
        token_manager: TokenManager,
        mcp_path: str = "/mcp",
        auth_required: bool = False,  # conservé pour compatibilité — non utilisé
        oidc_validator: OIDCValidator | None = None,
    ) -> None:
        super().__init__(app)
        self._token_manager = token_manager
        self._mcp_path = mcp_path
        self._oidc_validator = oidc_validator

    async def dispatch(self, request: Request, call_next) -> Response:
        # Appliquer uniquement aux requêtes MCP
        if not request.url.path.startswith(self._mcp_path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            raw_token = auth_header[len("Bearer "):]
            token_ctx = await self._resolve_token(raw_token, request.url.path)

            if token_ctx is None:
                return Response(
                    content=json.dumps(
                        {"error": "Token invalide ou expiré"}, ensure_ascii=False
                    ),
                    status_code=401,
                    media_type="application/json",
                )

            set_current_token(token_ctx)
            logger.debug(
                "MCP auth: user=%s scope=%s mode=%s",
                token_ctx.user_id,
                token_ctx.scope,
                "oidc" if self._oidc_validator else "token",
            )

        # Pas de header Authorization → pass-through sans TokenContext
        # Les tools MCP gèrent le cas non-authentifié (chatbot mode, workspaces publics)
        return await call_next(request)

    async def _resolve_token(self, raw_token: str, path: str):
        """Valide le token selon le mode configuré.

        Returns:
            TokenContext si valide, None si invalide.
        """
        if self._oidc_validator is not None:
            # Mode OIDC — JWT RS256/ES256 signé par l'IdP enterprise
            from colaig.auth.oidc_validator import OIDCValidationError
            try:
                return await self._oidc_validator.validate(raw_token)
            except OIDCValidationError as exc:
                logger.warning("MCP auth OIDC: token rejeté (path=%s) — %s", path, exc)
                return None
        else:
            # Mode token statique (défaut)
            token_ctx = await self._token_manager.resolve(raw_token)
            if token_ctx is None:
                logger.warning("MCP auth: token invalide ou expiré (path=%s)", path)
            return token_ctx
