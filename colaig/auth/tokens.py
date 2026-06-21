"""
Colaig — Gestionnaire de tokens MCP utilisateur.

Principe : un token MCP est une session utilisateur, traitée exactement comme
un DM Tchap/Matrix du point de vue du .colaig. Le token résout vers un user_id
qui donne accès au workspace personnel de l'utilisateur (ContextMode.PERSONAL)
ou à un workspace spécifique (ContextMode.ASSISTANT selon scope).

Format du token — auto-localisant (O(1) sans index central) :
    colaig_{safe_slug}_{secret_64hex}
    ex: colaig_alice_tchap_fr_a3f8b2c9d1e4f5...64chars

    - safe_slug : dérivé de user_id (même convention que workspace.py)
    - secret_64hex : 64 chars hex = 256 bits d'entropie (secrets.token_hex(32))

Stockage décentralisé (StorageProtocol — provider-agnostic) :
    /{safe_slug}/.colaig/tokens/{sha256(token)}.json
    /{safe_slug}/.colaig/mcp-configs/{safe_name}.json

Révocation = suppression du fichier JSON (pas de liste centrale).
Backward compatible : si COLAIG_MCP_AUTH_ENABLED=false, le système existant
(user_id passé en paramètre) continue de fonctionner sans modification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ContextVar per-async-task — lu par les MCP tools après injection par le middleware
_current_token: ContextVar[TokenContext | None] = ContextVar(
    "colaig_mcp_token", default=None
)


# =============================================================================
# Modèle de contexte token
# =============================================================================

@dataclass
class TokenContext:
    """Contexte résolu depuis un token MCP valide.

    user_id : identifiant complet de l'utilisateur (ex: @alice:tchap.fr)
    scope   : "*" → workspace personnel (PERSONAL) + ask_workspace disponible
              "workspace-id" → workspace spécifique uniquement (ASSISTANT)
    name    : nom lisible du token (ex: "Claude Desktop")
    role    : "user" (défaut) | "admin" — déterminé à la création par
              COLAIG_ADMIN_USER_IDS, jamais modifiable par le client.
              Les tokens créés avant l'introduction du rôle ont role="user"
              (backward compatible via default dans resolve()).
    """
    user_id: str
    scope: str = "*"
    name: str = ""
    role: str = "user"


def require_admin(token_ctx: TokenContext | None) -> str | None:
    """Vérifie que le token courant a le rôle admin.

    Retourne None si l'accès est autorisé, ou un JSON d'erreur (str) à
    retourner directement depuis le tool MCP si l'accès est refusé.

    Suit le même pattern que les guards existants dans server.py :
        if err := require_admin(get_current_token()):
            return err

    Args:
        token_ctx: TokenContext courant (None = non authentifié).

    Returns:
        None si admin, json.dumps({"error": "..."}) sinon.
    """
    if token_ctx is None:
        return json.dumps({"error": "Authentification requise"})
    if token_ctx.role != "admin":
        return json.dumps({"error": "Droits administrateur requis"})
    return None


def get_current_token() -> TokenContext | None:
    """Retourne le TokenContext du token MCP courant (None si non authentifié).

    Lit le ContextVar positionné par MCPTokenMiddleware. Utilisé par les
    tools MCP pour récupérer l'identité de l'utilisateur.
    """
    return _current_token.get()


def set_current_token(ctx: TokenContext | None) -> None:
    """Positionne le TokenContext dans le ContextVar courant.

    Appelé par MCPTokenMiddleware — une seule fois par requête HTTP.
    """
    _current_token.set(ctx)


# =============================================================================
# Utilitaires de chemin (en phase avec workspace.py:get_or_create_personal_workspace)
# =============================================================================

def _personal_ws_slug(user_id: str) -> str:
    """Dérive le slug du workspace personnel depuis user_id.

    Délègue à personal_workspace_slug() dans workspace.py — source unique de
    vérité pour cette transformation, partagée avec user_memory.py.

    @alice:tchap.fr                      → alice_tchap_fr
    @nicolas.laval:agent.tchap.gouv.fr   → nicolas_laval_agent_tchap_gouv_fr
    """
    from colaig.context.workspace import personal_workspace_slug
    return personal_workspace_slug(user_id)


def _personal_ws_path(slug: str) -> str:
    return f"/{slug}/"


def _token_dir(slug: str) -> str:
    return f"/{slug}/.colaig/tokens/"


def _token_path(slug: str, token_hash: str) -> str:
    return f"{_token_dir(slug)}{token_hash}.json"


def _mcp_configs_dir(slug: str) -> str:
    return f"/{slug}/.colaig/mcp-configs/"


def _mcp_config_path(slug: str, safe_name: str) -> str:
    return f"{_mcp_configs_dir(slug)}colaig-{safe_name}.json"


def _safe_name(name: str) -> str:
    """Transforme un nom libre en nom de fichier sûr."""
    return re.sub(r"[^a-z0-9\-]", "-", name.lower().strip())[:48].strip("-") or "token"


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _parse_token(raw_token: str) -> tuple[str, str] | None:
    """Extrait (safe_slug, secret) depuis un token brut.

    Format : colaig_{safe_slug}_{64hex}
    Le secret fait toujours exactement 64 chars hex — extraction par la droite.
    """
    prefix = "colaig_"
    if not raw_token.startswith(prefix):
        return None
    rest = raw_token[len(prefix):]          # "{safe_slug}_{64hex}"
    if len(rest) < 66:                       # slug≥1 + "_" + 64hex
        return None
    secret = rest[-64:]
    if not re.fullmatch(r"[0-9a-f]{64}", secret):
        return None
    if rest[-65] != "_":
        return None
    safe_slug = rest[:-65]
    if not safe_slug:
        return None
    return safe_slug, secret


# =============================================================================
# TokenManager
# =============================================================================

class TokenManager:
    """Crée, valide et révoque les tokens MCP utilisateur.

    Stockage décentralisé dans le workspace personnel de chaque utilisateur,
    via StorageProtocol. Aucune base de données, aucun index central.

    Args:
        storage : Instance StorageProtocol — même instance que le reste de Colaig.
        base_url: URL publique de l'instance (ex: https://colaig.org.fr).
                  Utilisée pour générer les fichiers de config MCP.
    """

    def __init__(self, storage, base_url: str = "", default_expiry_days: int = 365) -> None:
        self._storage = storage
        self._base_url = base_url.rstrip("/")
        self._default_expiry_days = default_expiry_days

    def _is_admin(self, user_id: str) -> bool:
        """Vérifie si user_id est dans COLAIG_ADMIN_USER_IDS.

        La liste est lue depuis l'env à chaque appel — pas de cache — ce qui
        permet à l'opérateur de modifier la liste sans redémarrer le service
        (dans les cas où l'env est rechargé dynamiquement).

        COLAIG_ADMIN_USER_IDS=@alice:tchap.fr,@bob:agent.gouv.fr
        """
        raw = os.getenv("COLAIG_ADMIN_USER_IDS", "")
        if not raw.strip():
            return False
        return user_id in {u.strip() for u in raw.split(",") if u.strip()}

    async def create(
        self,
        user_id: str,
        name: str,
        scope: str = "*",
        expires_at: str | None = None,
    ) -> str:
        """Crée un token, le persiste et génère la config MCP prête à l'emploi.

        Args:
            user_id   : Identifiant de l'utilisateur (ex: @alice:tchap.fr).
            name      : Nom lisible du token (ex: "Claude Desktop").
            scope     : "*" (global) ou workspace_id (restreint à un workspace).
            expires_at: ISO 8601 ou None (pas d'expiration).

        Returns:
            Token brut à communiquer à l'utilisateur (affiché une seule fois).
        """
        slug = _personal_ws_slug(user_id)
        secret = secrets.token_hex(32)
        raw_token = f"colaig_{slug}_{secret}"
        t_hash = _token_hash(raw_token)
        sname = _safe_name(name)

        # Appliquer l'expiration par défaut si non précisée
        if expires_at is None and self._default_expiry_days > 0:
            from datetime import timedelta
            expires_at = (
                datetime.now(tz=UTC) + timedelta(days=self._default_expiry_days)
            ).isoformat()

        # Le rôle est toujours déterminé côté serveur par COLAIG_ADMIN_USER_IDS.
        # Le caller ne peut jamais s'auto-promouvoir.
        role = "admin" if self._is_admin(user_id) else "user"

        token_data = {
            "user_id": user_id,
            "role": role,
            "scope": scope,
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": expires_at,
        }

        # Scaffold du dossier tokens si nécessaire
        try:
            await self._storage.mkdir(_token_dir(slug))
            await self._storage.mkdir(_mcp_configs_dir(slug))
        except Exception:
            pass  # idempotent

        # Persister le token
        token_path = _token_path(slug, t_hash)
        await self._storage.upload(
            token_path,
            json.dumps(token_data, ensure_ascii=False).encode(),
        )

        # Générer et persister la config MCP prête à copier
        await self._write_mcp_config(slug, sname, name, scope, raw_token)

        logger.info(
            "token créé: user=%s scope=%s name=%r",
            user_id, scope, name,
        )
        return raw_token

    async def resolve(self, raw_token: str) -> TokenContext | None:
        """Valide un token et retourne son contexte.

        Résolution O(1) sans index central :
        1. Parse slug depuis le token
        2. Charge le fichier token par sha256 hash
        3. Vérifie l'expiration

        Returns:
            TokenContext si valide, None sinon.
        """
        parsed = _parse_token(raw_token)
        if parsed is None:
            return None
        slug, _ = parsed

        t_path = _token_path(slug, _token_hash(raw_token))
        try:
            content = await self._storage.download(t_path)
            data = json.loads(content)
        except Exception:
            return None

        # Vérification expiration
        if data.get("expires_at"):
            try:
                exp = datetime.fromisoformat(data["expires_at"])
                if datetime.now(UTC) > exp:
                    logger.debug("token expiré: %s", t_path)
                    return None
            except Exception:
                pass

        return TokenContext(
            user_id=data.get("user_id", ""),
            scope=data.get("scope", "*"),
            name=data.get("name", ""),
            role=data.get("role", "user"),  # backward compat : tokens sans role → "user"
        )

    async def revoke(self, user_id: str, token_name: str) -> bool:
        """Révoque un token par nom.

        Supprime le fichier token JSON et la config MCP associée.
        Itère sur les tokens de l'utilisateur pour trouver celui qui correspond.

        Returns:
            True si révoqué, False si non trouvé.
        """
        slug = _personal_ws_slug(user_id)
        tokens = await self._list_token_files(slug)
        revoked = False
        for data, t_path in tokens:
            if data.get("name") == token_name:
                try:
                    await self._storage.delete(t_path)
                except Exception:
                    pass
                # Supprimer la config MCP associée
                sname = _safe_name(token_name)
                cfg_path = _mcp_config_path(slug, sname)
                try:
                    await self._storage.delete(cfg_path)
                except Exception:
                    pass
                revoked = True
                logger.info("token révoqué: user=%s name=%r", user_id, token_name)
        return revoked

    async def list_tokens(self, user_id: str) -> list[dict]:
        """Liste les tokens d'un utilisateur (sans exposer les secrets).

        Returns:
            Liste de dicts : name, scope, created_at, expires_at.
        """
        slug = _personal_ws_slug(user_id)
        tokens = await self._list_token_files(slug)
        return [
            {
                "name": d.get("name", ""),
                "scope": d.get("scope", "*"),
                "created_at": d.get("created_at", ""),
                "expires_at": d.get("expires_at"),
            }
            for d, _ in tokens
        ]

    # =========================================================================
    # Privé
    # =========================================================================

    async def _list_token_files(self, slug: str) -> list[tuple[dict, str]]:
        """Charge tous les fichiers token du workspace personnel."""
        token_dir = _token_dir(slug)
        results: list[tuple[dict, str]] = []
        try:
            files = await self._storage.list_files(token_dir)
        except Exception:
            return results
        for f in files:
            path = f.path if hasattr(f, "path") else str(f)
            if not path.endswith(".json"):
                continue
            try:
                content = await self._storage.download(path)
                data = json.loads(content)
                results.append((data, path))
            except Exception:
                pass
        return results

    async def _write_mcp_config(
        self, slug: str, sname: str, name: str, scope: str, raw_token: str
    ) -> None:
        """Génère et persiste la config MCP JSON prête à copier."""
        display_name = f"colaig-{sname}" if scope == "*" else f"colaig-{sname}-{scope}"
        mcp_config = {
            "mcpServers": {
                display_name: {
                    "type": "http",
                    "url": f"{self._base_url}/mcp" if self._base_url else "/mcp",
                    "headers": {
                        "Authorization": f"Bearer {raw_token}",
                    },
                }
            },
            "_colaig": {
                "token_name": name,
                "scope": scope,
                "generated_at": datetime.now(UTC).isoformat(),
                "note": (
                    "Copiez ce fichier dans votre config MCP client. "
                    "Ne partagez pas ce fichier — il contient votre token d'accès."
                ),
            },
        }
        cfg_path = _mcp_config_path(slug, sname)
        await self._storage.upload(
            cfg_path,
            json.dumps(mcp_config, ensure_ascii=False, indent=2).encode(),
        )
