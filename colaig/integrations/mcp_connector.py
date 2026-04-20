"""
Colaig — Client MCP Connector (Phase 6)

Permet à l'Orchestrateur de découvrir et appeler les outils exposés par des serveurs
MCP externes déclarés dans WorkspaceConfig.mcp_connectors.

Protocole : MCP Streamable HTTP (POST /mcp, Content-Type: application/json).
Authentification : Bearer token optionnel.

Sécurité :
- URL validation anti-SSRF pour les tools de navigation (allowed_domains, blocked_ip_ranges)
- Tool policy : "all" | "read_only" | "explicit" — filtrage des tools exposés
- Session isolation : X-Session-Id par conversation/utilisateur
- Troncature des résultats (protection prompt injection via contenu web)
- Rate limiting par connector

Utilisation :
    client = MCPConnectorClient(connector_config)
    tools = await client.list_tools()   # → list[tuple[ToolDefinition, Callable]]
    # Puis enregistrer dans le ToolRegistry :
    for definition, handler in tools:
        registry.register(definition, handler)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import httpx

from colaig.models import MCPConnectorConfig, ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)

# Timeout pour les appels MCP externes
_HTTP_TIMEOUT = 30.0

# TTL caches par URL de connector
# Clé : connector URL — Valeur : (résultat, timestamp)
_TOOLS_CACHE: dict[str, tuple[list, float]] = {}
_TOOLS_CACHE_TTL = 300.0  # 5 minutes

_INSTRUCTIONS_CACHE: dict[str, tuple[Optional[str], float]] = {}
_INSTRUCTIONS_CACHE_TTL = 600.0  # 10 minutes

# Rate limiting — clé : connector URL, valeur : list[timestamp]
_RATE_LIMITER: dict[str, list[float]] = {}

# Paramètres URL connus qui contiennent des URLs navigables (pour la validation SSRF)
_URL_PARAM_NAMES = {"url", "uri", "href", "link", "target", "src", "source", "page"}


def _json_type_to_colaig(json_type: str) -> str:
    """Normalise les types JSON Schema vers les types ToolParameter."""
    mapping = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "string",  # objects → sérialisés en JSON string pour le LLM
    }
    return mapping.get(json_type, "string")


def _parse_tool_definition(
    raw: dict,
    connector_name: str,
) -> Optional[tuple[ToolDefinition, dict]]:
    """Convertit un schéma d'outil MCP en ToolDefinition Colaig + annotations.

    Le nom de l'outil est préfixé par le nom du connector pour éviter les collisions :
    ex: "search" sur connector "juridique" → "juridique__search"

    Returns:
        Tuple (ToolDefinition, annotations_dict) ou None si l'outil est invalide.
    """
    raw_name = raw.get("name", "")
    if not raw_name:
        return None

    name = f"{connector_name}__{raw_name}"
    description = raw.get("description", f"Outil externe {name}")

    # Extraire les paramètres depuis inputSchema (format JSON Schema)
    parameters: list[ToolParameter] = []
    input_schema = raw.get("inputSchema") or raw.get("input_schema", {})
    if isinstance(input_schema, dict):
        props = input_schema.get("properties", {})
        required_names = set(input_schema.get("required", []))
        for param_name, param_schema in props.items():
            if not isinstance(param_schema, dict):
                continue
            parameters.append(ToolParameter(
                name=param_name,
                type=_json_type_to_colaig(param_schema.get("type", "string")),
                description=param_schema.get("description", ""),
                required=param_name in required_names,
                enum=param_schema.get("enum", []),
            ))

    # Annotations MCP (readOnlyHint, destructiveHint, idempotentHint)
    annotations = raw.get("annotations", {})

    definition = ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        category="mcp_external",
    )
    return definition, annotations


def _check_rate_limit(url: str, max_per_minute: int) -> None:
    """Vérifie le rate limit pour un connector. Lève RuntimeError si dépassé."""
    if max_per_minute <= 0:
        return
    now = time.monotonic()
    calls = _RATE_LIMITER.get(url, [])
    # Purger les appels de plus d'une minute
    calls = [t for t in calls if now - t < 60.0]
    if len(calls) >= max_per_minute:
        raise RuntimeError(
            f"Rate limit dépassé : {max_per_minute} appels/minute sur {url}"
        )
    calls.append(now)
    _RATE_LIMITER[url] = calls


def _validate_tool_arguments(kwargs: dict, connector: MCPConnectorConfig) -> dict:
    """Valide les arguments d'un tool call MCP — protection SSRF.

    Intercepte les paramètres nommés url/uri/href/... et valide
    l'URL via le module de sécurité Colaig.

    Args:
        kwargs: Arguments du tool call.
        connector: Config du connector (allowed_domains, blocked_ip_ranges).

    Returns:
        kwargs nettoyés (inchangés si valides).

    Raises:
        RuntimeError: Si une URL est bloquée par la politique.
    """
    if not connector.allowed_domains and not connector.blocked_ip_ranges:
        return kwargs

    from colaig.security.url_validator import URLValidationError, validate_navigation_url

    for key, value in kwargs.items():
        if key.lower() in _URL_PARAM_NAMES and isinstance(value, str) and value.startswith(("http://", "https://")):
            try:
                validate_navigation_url(
                    value,
                    allowed_domains=connector.allowed_domains or None,
                    blocked_ip_ranges=connector.blocked_ip_ranges or None,
                )
            except URLValidationError as e:
                logger.warning(
                    "mcp_connector: URL bloquée dans %s.%s : %s",
                    connector.name, key, e,
                )
                raise RuntimeError(f"URL bloquée : {e}") from e
    return kwargs


def _extract_mcp_content(content: Any, max_length: int = 10000) -> str:
    """Extrait le contenu textuel d'une réponse MCP.

    Gère les types de contenu MCP :
    - text → texte brut
    - image → description + métadonnées (pas le base64 — trop lourd pour le LLM)
    - resource → contenu de la resource

    Args:
        content: Champ content de la réponse MCP (list ou autre).
        max_length: Troncature max du résultat.

    Returns:
        Contenu textuel assemblé et tronqué.
    """
    if not isinstance(content, list):
        result = str(content)
        return result[:max_length] if len(result) > max_length else result

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        content_type = item.get("type", "")
        if content_type == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif content_type == "image":
            mime = item.get("mimeType", "image/png")
            data_len = len(item.get("data", ""))
            parts.append(f"[Image capturée : {mime}, {data_len} bytes base64]")
        elif content_type == "resource":
            resource = item.get("resource", {})
            res_text = resource.get("text", "")
            if res_text:
                parts.append(f"[Resource {resource.get('uri', '')}]\n{res_text}")

    result = "\n".join(parts)

    if len(result) > max_length:
        result = result[:max_length] + f"\n[… tronqué à {max_length} chars]"

    return result


def _should_expose_tool(
    annotations: dict,
    connector: MCPConnectorConfig,
    raw_name: str,
) -> bool:
    """Détermine si un tool doit être exposé selon la tool_policy du connector.

    Args:
        annotations: Annotations MCP du tool (readOnlyHint, destructiveHint).
        connector: Config du connector.
        raw_name: Nom brut du tool sur le serveur distant.

    Returns:
        True si le tool doit être enregistré dans le ToolRegistry.
    """
    policy = connector.tool_policy

    if policy == "all":
        return True

    if policy == "read_only":
        # Exposer uniquement les tools non-destructifs
        # Si readOnlyHint est explicitement True → OK
        # Si destructiveHint est True → bloquer
        # Si pas d'annotation → bloquer par prudence
        if annotations.get("readOnlyHint") is True:
            return True
        if annotations.get("destructiveHint") is True:
            return False
        # Pas d'annotation : considérer comme potentiellement destructif
        # sauf si le nom suggère une lecture seule
        read_only_prefixes = (
            "get", "list", "search", "find", "read", "fetch", "query",
            "screenshot", "snapshot", "inspect", "describe", "check",
        )
        return raw_name.lower().startswith(read_only_prefixes)

    if policy == "explicit":
        return raw_name in connector.allowed_tools

    return True


def _create_tool_handler(
    connector: MCPConnectorConfig,
    raw_tool_name: str,
) -> Callable:
    """Crée un handler async qui appelle l'outil sur le serveur MCP distant.

    Intègre :
    - Validation SSRF des URLs dans les arguments
    - Session ID (X-Session-Id) injecté depuis _session_id kwarg
    - Rate limiting
    - Troncature des résultats
    - Parsing des contenus image/resource MCP

    Args:
        connector: Configuration du serveur MCP.
        raw_tool_name: Nom de l'outil sur le serveur MCP (sans préfixe).

    Returns:
        Coroutine compatible ToolRegistry.execute() → ToolResult.
    """
    url = connector.url.rstrip("/")
    base_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if connector.auth_token:
        base_headers["Authorization"] = f"Bearer {connector.auth_token}"

    async def handler(**kwargs: Any) -> str:
        """Appelle l'outil sur le serveur MCP et retourne le résultat en string."""
        # Extraire le session_id injecté par l'Orchestrateur (pas un argument tool)
        session_id = kwargs.pop("_session_id", None)

        # Rate limiting
        _check_rate_limit(url, connector.max_calls_per_minute)

        # Validation SSRF des URLs dans les arguments
        kwargs = _validate_tool_arguments(kwargs, connector)

        # Headers avec session_id
        headers = dict(base_headers)
        if session_id and connector.session_scope != "none":
            headers[connector.session_header] = str(session_id)

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": raw_tool_name,
                "arguments": kwargs,
            },
            "id": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
                resp = await http.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("mcp_connector: HTTP %d sur %s/%s", e.response.status_code, url, raw_tool_name)
            raise RuntimeError(f"HTTP {e.response.status_code} : {e}") from e
        except Exception as e:
            logger.warning("mcp_connector: erreur réseau %s/%s: %s", url, raw_tool_name, e)
            raise RuntimeError(str(e)) from e

        if "error" in data:
            err = data["error"]
            raise RuntimeError(err.get("message", str(err)))

        # Extraire le contenu avec support image/resource + troncature
        result_obj = data.get("result", {})
        content = result_obj.get("content", [])
        return _extract_mcp_content(content, max_length=connector.max_result_length)

    return handler


class MCPConnectorClient:
    """Client MCP qui découvre les outils d'un serveur MCP externe.

    Implémente le protocole MCP Streamable HTTP (JSON-RPC 2.0).
    Utilisé par l'Orchestrateur pour enregistrer dynamiquement les outils
    exposés par les serveurs déclarés dans WorkspaceConfig.mcp_connectors.

    Sécurité intégrée :
    - tool_policy filtre les tools exposés (all/read_only/explicit)
    - URL validation SSRF dans chaque handler
    - Session isolation via X-Session-Id
    - Rate limiting par connector
    - Troncature des résultats
    """

    def __init__(self, connector: MCPConnectorConfig) -> None:
        self._connector = connector
        self._url = connector.url.rstrip("/")
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if connector.auth_token:
            self._headers["Authorization"] = f"Bearer {connector.auth_token}"

    async def list_tools(self) -> list[tuple[ToolDefinition, Callable]]:
        """Découvre les outils disponibles sur le serveur MCP.

        Appelle tools/list et convertit chaque outil en (ToolDefinition, handler).
        Applique le filtrage tool_policy avant de retourner.
        Cache TTL 5 minutes pour éviter une découverte HTTP à chaque message.
        Retourne une liste vide en cas d'erreur (graceful degradation).
        """
        # C3 — TTL cache
        cached = _TOOLS_CACHE.get(self._url)
        if cached is not None:
            result, ts = cached
            if time.monotonic() - ts < _TOOLS_CACHE_TTL:
                logger.debug("mcp_connector: tools/list depuis cache pour %s", self._connector.name)
                return result

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
                resp = await http.post(self._url, json=payload, headers=self._headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(
                "mcp_connector: impossible de contacter %s (%s): %s",
                self._connector.name, self._url, e,
            )
            return []

        if "error" in data:
            logger.warning(
                "mcp_connector: erreur tools/list sur %s: %s",
                self._connector.name, data["error"],
            )
            return []

        raw_tools = data.get("result", {}).get("tools", [])
        result: list[tuple[ToolDefinition, Callable]] = []
        filtered_count = 0

        for raw in raw_tools:
            parsed = _parse_tool_definition(raw, self._connector.name)
            if parsed is None:
                continue

            definition, annotations = parsed
            raw_name = raw.get("name", "")

            # Filtrage par tool_policy
            if not _should_expose_tool(annotations, self._connector, raw_name):
                filtered_count += 1
                logger.debug(
                    "mcp_connector: outil filtré par policy '%s': %s (connector=%s)",
                    self._connector.tool_policy, raw_name, self._connector.name,
                )
                continue

            handler = _create_tool_handler(self._connector, raw_name)
            result.append((definition, handler))
            logger.debug(
                "mcp_connector: outil exposé %s (connector=%s)",
                definition.name, self._connector.name,
            )

        logger.info(
            "mcp_connector: %d outil(s) exposé(s), %d filtré(s) sur %s (policy=%s)",
            len(result), filtered_count, self._connector.name, self._connector.tool_policy,
        )
        _TOOLS_CACHE[self._url] = (result, time.monotonic())
        return result

    async def get_server_instructions(self) -> Optional[str]:
        """Récupère les instructions du serveur MCP via le handshake initialize.

        Appelle initialize (JSON-RPC MCP) et extrait result.instructions.
        Cache TTL 10 minutes.
        Retourne None si le serveur ne fournit pas d'instructions ou en cas d'erreur.
        """
        # C4 — TTL cache
        cached = _INSTRUCTIONS_CACHE.get(self._url)
        if cached is not None:
            instructions, ts = cached
            if time.monotonic() - ts < _INSTRUCTIONS_CACHE_TTL:
                return instructions

        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "colaig", "version": "3"},
            },
            "id": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
                resp = await http.post(self._url, json=payload, headers=self._headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.debug(
                "mcp_connector: initialize échoué sur %s: %s",
                self._connector.name, e,
            )
            _INSTRUCTIONS_CACHE[self._url] = (None, time.monotonic())
            return None

        instructions: Optional[str] = None
        if "result" in data:
            instructions = data["result"].get("instructions") or None

        _INSTRUCTIONS_CACHE[self._url] = (instructions, time.monotonic())
        if instructions:
            logger.debug(
                "mcp_connector: instructions récupérées depuis %s (%d chars)",
                self._connector.name, len(instructions),
            )
        return instructions

    async def close_session(self, session_id: str) -> None:
        """Ferme une session sur le serveur MCP distant.

        Pour les serveurs multi-session (ex: chrome-devtools-mcp),
        libère les ressources (instance Chrome) associées au session_id.

        Appel best-effort — les erreurs sont loguées et ignorées.
        """
        # Construire l'URL de gestion des sessions (hors endpoint /mcp)
        base_url = self._url.rsplit("/mcp", 1)[0] if "/mcp" in self._url else self._url
        delete_url = f"{base_url}/sessions/{session_id}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.delete(delete_url, headers=self._headers)
                if resp.status_code < 300:
                    logger.debug(
                        "mcp_connector: session %s fermée sur %s",
                        session_id, self._connector.name,
                    )
                else:
                    logger.debug(
                        "mcp_connector: close_session %s → HTTP %d",
                        session_id, resp.status_code,
                    )
        except Exception as e:
            logger.debug(
                "mcp_connector: close_session échoué pour %s sur %s: %s",
                session_id, self._connector.name, e,
            )
