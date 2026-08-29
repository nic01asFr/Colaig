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

import dataclasses
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from colaig.models import MCPConnectorConfig, ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)

# Delai des appels MCP externes. Vingt secondes : un tour de conversation ne peut
# pas rester suspendu une demi-minute a un serveur tiers (critere du lot L3.4).
_HTTP_TIMEOUT = 20.0

# TTL caches par DÉCLARATION de connecteur.
#
# Clé : empreinte de la configuration entière — Valeur : (résultat, timestamp)
#
# POURQUOI PAS L'URL SEULE (L3.4)
# ---------------------------------
# Ces caches étaient keyés sur `connector.url`. Or la valeur mise en cache n'est pas
# une donnée inerte : c'est la liste des `(ToolDefinition, handler)`, et chaque handler
# est une FERMETURE sur le `MCPConnectorConfig` de l'espace qui l'a construit — donc sur
# son `auth_token`, sa liste blanche SSRF, sa limite d'appels, sa troncature.
#
# Deux espaces déclarant la même URL partageaient l'entrée : le second appelait le
# serveur distant AVEC LE JETON DU PREMIER, sous la politique de sécurité du premier.
# Une fuite d'identifiant et de politique, pas de contenu — et Colaig est multi-tenant.
#
# Le nom compte aussi : il préfixe les outils (`{name}__{raw_name}`) et sert de clé à
# l'épinglage des schémas (L2.3).
_TOOLS_CACHE: dict[str, tuple[list, float]] = {}   # cle -> (resultat, echeance)
_TOOLS_CACHE_TTL = 300.0  # 5 minutes

_INSTRUCTIONS_CACHE: dict[str, tuple[str | None, float]] = {}   # cle -> (valeur, echeance)
_INSTRUCTIONS_CACHE_TTL = 600.0  # 10 minutes


def _cle_cache(connector: MCPConnectorConfig) -> str:
    """Empreinte d'une déclaration de connecteur, pour clé de cache.

    ON HACHE LA CONFIGURATION ENTIÈRE, délibérément — pas une liste choisie de champs.
    Deux entrées ne se partagent que si les déclarations sont identiques en tout point.

    Le motif est celui d'un défaut par défaut : une liste de champs à inclure oblige à
    penser à chaque ajout futur, et un champ oublié rouvre le partage en silence. Hacher
    l'ensemble fait qu'un nouveau champ RESTREINT le partage par construction — le sens
    sûr, celui que `security/actions.py` applique déjà aux annotations MCP absentes.

    Le jeton entre dans le condensat, pas dans la clé en clair : une clé de dictionnaire
    finit tôt ou tard dans un journal ou un traceback.
    """
    empreinte = json.dumps(dataclasses.asdict(connector), sort_keys=True, default=str)
    return hashlib.sha256(empreinte.encode("utf-8")).hexdigest()


def _memoriser(cache: dict, cle: str, valeur, resultat_mcp: dict,
               ttl_defaut: float, nom: str) -> None:
    """Range une réponse en honorant `ttlMs` et `cacheScope` (spec 2026-07-28, SEP-2549).

    `cacheScope` — qui a le droit de lire
    --------------------------------------
    « private » signifie : *Shared caches MUST NOT serve a cached copy to a different
    user.* Notre cache vit dans le processus et ne sait pas à qui il sert : le seul
    moyen de respecter cette déclaration est de **ne rien garder**. Une valeur présente
    mais inconnue est traitée de même — une déclaration qu'on ne comprend pas ne
    s'interprète pas dans le sens permissif.

    **L'ABSENCE, ELLE, N'INTERDIT PAS DE CACHER**, et c'est une correction de D54 qui
    concluait l'inverse. Aucun serveur en protocole 2025-11-25 n'émet ce champ — pas
    même `mcp.data.gouv.fr`, que le critère du lot nomme. Traiter l'absence comme
    « private » aurait désactivé le cache contre l'intégralité des serveurs existants.

    Ce qui rend l'absence sans danger est **vérifiable** : `cacheScope` gouverne le
    partage entre UTILISATEURS, et notre requête `tools/list` ne porte aucune identité
    d'utilisateur — le jeton est celui de l'espace, et il entre déjà dans la clé.
    `test_list_tools_ne_porte_AUCUN_identifiant_d_utilisateur` épingle cette condition
    et tombera le jour où elle cessera d'être vraie.

    `ttlMs` — combien de temps
    ---------------------------
    Présent et positif : il fait loi, le serveur connaît la volatilité de sa liste.
    Négatif : la spec dit de l'ignorer — on retombe sur notre durée, jamais sur zéro,
    sans quoi un serveur désactiverait notre cache d'un simple `-1`.
    Absent : notre propre durée, ce que la spec prévoit explicitement pour les serveurs
    antérieurs (« rely on their own caching heuristics »).
    """
    portee = resultat_mcp.get("cacheScope")
    if portee is not None and portee != "public":
        logger.debug("mcp_connector: reponse non mise en cache (cacheScope=%r) sur %s",
                     portee, nom)
        return

    duree = ttl_defaut
    brut = resultat_mcp.get("ttlMs")
    if isinstance(brut, (int, float)) and not isinstance(brut, bool) and brut >= 0:
        duree = float(brut) / 1000.0

    cache[cle] = (valeur, time.monotonic() + duree)


def invalider(connector: MCPConnectorConfig) -> None:
    """Oublie ce qu'on savait des outils de ce serveur.

    Appelé quand un appel d'outil échoue en « méthode inconnue » : le serveur a changé
    sa liste sous nos pieds. La spec l'autorise nommément — *Clients MAY re-fetch if
    they have reason to believe the data has changed […] receiving an unexpected error
    on a tool call indicating that the method was not found.*

    C'est la seule invalidation câblable aujourd'hui : les serveurs que nous atteignons
    annoncent `listChanged: false` et n'émettent donc aucune notification. Sans elle,
    Colaig rappellerait un nom d'outil mort pendant toute la durée du TTL.
    """
    cle = _cle_cache(connector)
    _TOOLS_CACHE.pop(cle, None)
    _INSTRUCTIONS_CACHE.pop(cle, None)


# CE QUE LE CLIENT DÉCLARE ACCEPTER.
#
# Le transport « Streamable HTTP » de MCP laisse le serveur répondre soit en JSON, soit
# en flux d'événements — et il EXIGE du client qu'il annonce accepter les deux. Un
# serveur conforme répond `406 Not Acceptable` à qui n'annonce que `application/json`.
#
# Mesuré le 29/08/2026 : `mcp.data.gouv.fr` rendait 406 sur chaque appel. Le client ne
# savait donc parler à AUCUN serveur MCP conforme, et aucun test hors ligne ne pouvait
# le voir — une doublure HTTP ne vérifie pas les en-têtes qu'on lui envoie. C'est le
# test vivant `test_mcp_datagouv.py` qui l'a trouvé.
_ACCEPT = "application/json, text/event-stream"


def _lire_reponse(resp: httpx.Response) -> dict:
    """Lit une réponse MCP, qu'elle soit en JSON ou en flux d'événements.

    Le serveur choisit sa forme. `resp.json()` seul échouait sur la seconde, qui est
    pourtant celle que renvoient les implémentations de référence :

        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{…}}

    On prend le DERNIER bloc `data:` : un flux peut porter des événements de progression
    avant le résultat, et c'est le résultat qui nous intéresse.
    """
    type_contenu = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" not in type_contenu:
        return resp.json()

    dernier: dict = {}
    for ligne in resp.text.splitlines():
        if not ligne.startswith("data:"):
            continue
        charge = ligne[len("data:"):].strip()
        if not charge:
            continue
        try:
            objet = json.loads(charge)
        except ValueError:
            continue
        if isinstance(objet, dict) and ("result" in objet or "error" in objet):
            dernier = objet
    return dernier


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


_MAGASIN_PINS = None


def _contrat_admis(serveur: str, brut: dict) -> bool:
    """Le contrat de cet outil est-il celui qui a ete admis ? (L2.3)

    La liste blanche de L2.2 decide quels SERVEURS sont montes ; elle ne dit rien de ce
    qu'ils font ensuite. Un serveur admis peut changer la description ou le schema d'un
    outil que le modele a appris a utiliser — c'est un rug-pull, et le modele, lui, voit
    un outil qu'il connait.

    Le magasin est charge paresseusement : il lit un fichier de l'hote, et un import de
    module ne doit pas toucher au disque.
    """
    global _MAGASIN_PINS
    from colaig.security.mcp_pins import Magasin, verifier

    if _MAGASIN_PINS is None:
        _MAGASIN_PINS = Magasin()
    admis, _ = verifier(serveur, brut, _MAGASIN_PINS)
    return admis


def _parse_tool_definition(
    raw: dict,
    connector_name: str,
) -> tuple[ToolDefinition, dict] | None:
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
        return _compacter(str(content), max_length)

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

    return _compacter(result, max_length)


def _compacter(texte: str, max_length: int) -> str:
    """Troncature STRUCTURÉE : on garde les deux bouts, et on le dit.

    Une coupe franche perd la fin — or c'est souvent là que se trouvent le total d'une
    liste, la conclusion d'un document, la dernière ligne d'un tableau. Le modèle, lui,
    ne voit pas qu'il manque quelque chose : il lit un texte qui s'arrête, et répond
    comme s'il l'avait lu entier.

    Garder tête ET queue, en nommant ce qui a été retiré, transforme une amputation
    silencieuse en extrait déclaré.

    La troisième stratégie de la version déployée — un résumé par appel LLM — n'est PAS
    portée : c'est une option coûteuse, et le `CLAUDE.md` racine §2.6 veut qu'une option
    coûteuse soit un drapeau mesuré par espace. Elle n'est pas mesurée ; elle n'est donc
    pas activée.
    """
    if len(texte) <= max_length:
        return texte

    # Deux tiers en tête, un tiers en queue : ce qui vient d'abord porte le plus de sens
    # dans une liste triée par pertinence, ce qui vient à la fin porte le bilan.
    tete = (max_length * 2) // 3
    queue = max_length - tete
    omis = len(texte) - max_length
    marqueur = f"\n\n[… {omis} caractères omis — extrait, pas le contenu entier …]\n\n"
    return texte[:tete] + marqueur + texte[-queue:]


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
        "Accept": _ACCEPT,
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
                data = _lire_reponse(resp)
        except httpx.HTTPStatusError as e:
            logger.warning("mcp_connector: HTTP %d sur %s/%s", e.response.status_code, url, raw_tool_name)
            raise RuntimeError(f"HTTP {e.response.status_code} : {e}") from e
        except Exception as e:
            logger.warning("mcp_connector: erreur réseau %s/%s: %s", url, raw_tool_name, e)
            raise RuntimeError(str(e)) from e

        if "error" in data:
            err = data["error"]
            message = err.get("message", str(err))
            # Le serveur a changé sa liste sous nos pieds : oublier ce qu'on croyait
            # savoir, plutôt que de rappeler un nom mort pendant tout le TTL.
            # -32601 est « Method not found » en JSON-RPC.
            if err.get("code") == -32601 or "not found" in message.lower():
                invalider(connector)
            raise RuntimeError(message)

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
        # Empreinte de la DECLARATION, pas de l'URL : voir `_cle_cache`.
        self._cle = _cle_cache(connector)
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": _ACCEPT,
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
        # Cache : l'entree porte son ECHEANCE, pas sa date de pose — le serveur peut
        # fixer la duree par `ttlMs` (spec 2026-07-28, SEP-2549).
        cached = _TOOLS_CACHE.get(self._cle)
        if cached is not None:
            result, echeance = cached
            if time.monotonic() < echeance:
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
                data = _lire_reponse(resp)
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

            # Epinglage du contrat (L2.3) — apres le filtrage par policy, avant
            # d'exposer quoi que ce soit au registre de l'agent.
            if not _contrat_admis(self._connector.name, raw):
                filtered_count += 1
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
        _memoriser(_TOOLS_CACHE, self._cle, result,
                   data.get("result") or {}, _TOOLS_CACHE_TTL, self._connector.name)
        return result

    async def get_server_instructions(self) -> str | None:
        """Récupère les instructions du serveur MCP via le handshake initialize.

        Appelle initialize (JSON-RPC MCP) et extrait result.instructions.
        Cache TTL 10 minutes.
        Retourne None si le serveur ne fournit pas d'instructions ou en cas d'erreur.
        """
        # C4 — TTL cache
        cached = _INSTRUCTIONS_CACHE.get(self._cle)
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
                data = _lire_reponse(resp)
        except Exception as e:
            logger.debug(
                "mcp_connector: initialize échoué sur %s: %s",
                self._connector.name, e,
            )
            _INSTRUCTIONS_CACHE[self._cle] = (None, time.monotonic() + _INSTRUCTIONS_CACHE_TTL)
            return None

        instructions: str | None = None
        if "result" in data:
            instructions = data["result"].get("instructions") or None

        _INSTRUCTIONS_CACHE[self._cle] = (instructions,
                                          time.monotonic() + _INSTRUCTIONS_CACHE_TTL)
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
