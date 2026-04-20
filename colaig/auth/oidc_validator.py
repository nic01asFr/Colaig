"""
Colaig — Validation JWT OIDC pour auth MCP enterprise.

Valide les tokens Bearer JWT RS256 émis par un IdP enterprise (Azure AD,
Keycloak, France Connect...) en vérifiant la signature via JWKS public.

Usage :
    validator = OIDCValidator(
        issuer="https://sso.org.fr/realms/org",
        audience="colaig-instance-rh",
        jwks_uri="https://sso.org.fr/realms/org/protocol/openid-connect/certs",
    )

    # Dans MCPTokenMiddleware
    try:
        token_ctx = await validator.validate(raw_token)
    except OIDCValidationError:
        return 401

Dépendance optionnelle :
    pip install 'colaig[oidc]'   # PyJWT[crypto]>=2.8.0
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx

from colaig.auth.tokens import TokenContext

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_JWKS_CACHE_TTL = 3600  # 1 heure — rotation de clés IdP rare


def _require_pyjwt():
    """Lazy import PyJWT — lève ImportError avec message explicite si absent."""
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm
        return jwt, RSAAlgorithm
    except ImportError:
        raise ImportError(
            "PyJWT[crypto] est requis pour le mode OIDC. "
            "Installez-le avec : pip install 'colaig[oidc]'"
        )


class OIDCValidationError(Exception):
    """Token OIDC invalide, expiré ou non vérifiable."""


class OIDCValidator:
    """Valide les JWT RS256/ES256 émis par un IdP enterprise via JWKS.

    Les clés publiques JWKS sont chargées depuis l'endpoint de l'IdP et
    mises en cache (TTL=1h). En cas de kid inconnu, le JWKS est re-fetché
    une fois (rotation de clé détectée).

    Thread-safe et async-safe via asyncio.Lock.

    Args:
        issuer  : URL de l'émetteur (claim iss).
                  Ex: "https://sso.org.fr/realms/org"
        audience: Audience attendue (claim aud).
                  Ex: "colaig-instance-rh"
        jwks_uri: URL de l'endpoint JWKS de l'IdP.
                  Ex: "https://sso.org.fr/realms/org/protocol/openid-connect/certs"
    """

    def __init__(self, issuer: str, audience: str, jwks_uri: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._jwks_cache: dict = {}       # kid → clé publique (RSA ou EC)
        self._cache_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def validate(self, raw_token: str) -> TokenContext:
        """Valide un JWT et retourne le TokenContext correspondant.

        Args:
            raw_token: JWT brut (header.payload.signature en base64url).

        Returns:
            TokenContext avec user_id extrait de email / preferred_username / sub.

        Raises:
            OIDCValidationError: Token invalide, expiré, audience incorrecte,
                                 issuer inconnu, ou kid introuvable dans JWKS.
        """
        jwt, RSAAlgorithm = _require_pyjwt()

        try:
            header = jwt.get_unverified_header(raw_token)
        except Exception as exc:
            raise OIDCValidationError(f"Header JWT malformé : {exc}")

        alg = header.get("alg", "")
        _SUPPORTED = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
        if alg not in _SUPPORTED:
            raise OIDCValidationError(
                f"Algorithme non supporté : {alg!r} (attendu : {', '.join(_SUPPORTED)})"
            )

        kid = header.get("kid", "")
        public_key = await self._get_public_key(kid, RSAAlgorithm)

        try:
            payload = jwt.decode(
                raw_token,
                public_key,
                algorithms=list(_SUPPORTED),
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.ExpiredSignatureError:
            raise OIDCValidationError("Token expiré")
        except jwt.InvalidAudienceError:
            raise OIDCValidationError(
                f"Audience invalide (attendu : {self._audience!r})"
            )
        except jwt.InvalidIssuerError:
            raise OIDCValidationError(
                f"Issuer invalide (attendu : {self._issuer!r})"
            )
        except jwt.InvalidTokenError as exc:
            raise OIDCValidationError(f"Token invalide : {exc}")

        # Extraire l'identifiant utilisateur — ordre de priorité OIDC standard
        user_id = (
            payload.get("email")
            or payload.get("preferred_username")
            or payload.get("sub")
            or ""
        )
        if not user_id:
            raise OIDCValidationError(
                "Aucun identifiant utilisateur dans le JWT (email / preferred_username / sub)"
            )

        return TokenContext(
            user_id=user_id,
            scope="*",
            name=payload.get("name", ""),
            role="user",  # rôle toujours côté serveur — jamais depuis le token client
        )

    # ── Gestion du cache JWKS ────────────────────────────────────────────

    async def _get_public_key(self, kid: str, RSAAlgorithm):
        """Retourne la clé publique pour ce kid, en fetchant le JWKS si nécessaire."""
        now = time.monotonic()

        # Cache hit : clé connue et cache frais
        if kid in self._jwks_cache and (now - self._cache_fetched_at) < _JWKS_CACHE_TTL:
            return self._jwks_cache[kid]

        # Cache miss ou expiré → re-fetch (double-check lock)
        async with self._lock:
            now = time.monotonic()
            if kid in self._jwks_cache and (now - self._cache_fetched_at) < _JWKS_CACHE_TTL:
                return self._jwks_cache[kid]
            await self._fetch_jwks(RSAAlgorithm)

        if kid not in self._jwks_cache:
            raise OIDCValidationError(
                f"kid {kid!r} introuvable dans le JWKS ({self._jwks_uri}). "
                "Rotation de clé en cours ?"
            )
        return self._jwks_cache[kid]

    async def _fetch_jwks(self, RSAAlgorithm) -> None:
        """Charge les clés publiques depuis l'endpoint JWKS de l'IdP."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._jwks_uri)
                resp.raise_for_status()
                jwks = resp.json()

            new_cache: dict = {}
            for key_data in jwks.get("keys", []):
                # Ignorer les clés non-signature (chiffrement, etc.)
                if key_data.get("use", "sig") != "sig":
                    continue
                k = key_data.get("kid", "default")
                try:
                    new_cache[k] = RSAAlgorithm.from_jwk(key_data)
                except Exception as exc:
                    logger.debug("oidc_validator: clé kid=%r ignorée — %s", k, exc)

            self._jwks_cache = new_cache
            self._cache_fetched_at = time.monotonic()
            logger.debug(
                "oidc_validator: JWKS chargé depuis %s (%d clé(s))",
                self._jwks_uri,
                len(new_cache),
            )
        except OIDCValidationError:
            raise
        except Exception as exc:
            # Réseau indisponible → conserver le cache existant si possible
            logger.warning(
                "oidc_validator: échec fetch JWKS %s — %s (cache conservé : %d clé(s))",
                self._jwks_uri,
                exc,
                len(self._jwks_cache),
            )
