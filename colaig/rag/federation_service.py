"""
Colaig — FederationService

Agrège tous les peers Colaig accessibles depuis cette instance.

Sources (par ordre de portée) :
  1. /.colaig/federation/peers.yaml  — admin-déclaré, portée d'instance
     (distinct des mcp_connectors qui sont portée workspace + MCP générique)
  2. mcp_connectors de tous les workspaces — portée workspace, peut inclure des peers Colaig

Le FederationService est la couche de découverte qui alimente WorkspaceDirectory.
Il ne remplace pas les mcp_connectors existants (utilisés par run_rag_delegate niveau 3) :
il les agrège pour la construction du répertoire vectoriel fédéré.

Peer discovery : appel parallèle colaig_list_workspaces sur chaque peer → résultats cachés
TTL (défaut : 5 min). Ce cache VOLATILE est distinct du WorkspaceDirectory FAISS qui
persiste sur storage.

Format peers.yaml (à la racine du storage) :
    peers:
      - name: colaig-rh
        url: https://colaig-rh.example.fr/mcp
        auth_token: ark_xxxx
      - name: colaig-public
        url: https://colaig-public.example.fr/mcp
        # auth_token optionnel pour instance publique

Vision plateforme : la fédération est pilotée par la topologie des connexions déclarées
(storage root + workspace connectors) — pas par un registre central. Chaque instance voit
ce qu'elle peut atteindre selon ses droits. Scalable de l'usage individuel à l'inter-orga.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_PEERS_PATH = "/.colaig/federation/peers.yaml"
_CACHE_TTL = 300  # secondes — durée de vie du cache de workspace distants


@dataclass
class FederationPeer:
    """Un peer Colaig accessible via son endpoint MCP."""
    name: str
    url: str
    auth_token: str = ""
    source: str = "peers_file"  # "peers_file" | "connector"


@dataclass
class RemoteWorkspace:
    """Workspace découvert sur un peer Colaig via colaig_list_workspaces."""
    peer: FederationPeer
    workspace_id: str
    name: str
    description: str = ""
    topics: list = field(default_factory=list)
    rag_enabled: bool = True


class FederationService:
    """Découverte et agrégation des workspaces fédérés.

    Charge les peers depuis /.colaig/federation/peers.yaml (portée instance)
    et optionnellement depuis les mcp_connectors des workspaces (portée workspace).
    Interroge colaig_list_workspaces sur chaque peer en parallèle, avec cache TTL.

    Args:
        storage: Backend de stockage (StorageProtocol) — pour lire peers.yaml.
    """

    def __init__(self, storage) -> None:
        self._storage = storage
        # Cache volatile : url → list[dict] (workspaces bruts de l'API MCP)
        self._ws_cache: dict[str, list[dict]] = {}
        self._cache_ts: float = 0.0

    async def load_peers(
        self, workspace_connectors: Optional[list] = None
    ) -> list[FederationPeer]:
        """Charge les peers depuis peers.yaml + mcp_connectors optionnels.

        Args:
            workspace_connectors: Instances MCPConnectorConfig — tous les mcp_connectors
                de tous les workspaces, fournis par l'appelant pour éviter l'import circulaire.

        Returns:
            Liste de FederationPeer dédupliquée par URL.
        """
        peers: list[FederationPeer] = []
        seen_urls: set[str] = set()

        # ── Source 1 : peers.yaml (instance-level) ──────────────────────────
        try:
            raw = await self._storage.download(_PEERS_PATH)
            import yaml as _yaml
            cfg = _yaml.safe_load(raw.decode("utf-8", errors="replace")) or {}
            from colaig.security.federation_guard import validate_peers_config
            raw_peers = cfg.get("peers", [])
            validated_raw = validate_peers_config(raw_peers)
            for p in validated_raw:
                url = (p.get("url") or "").strip()
                if url and url not in seen_urls:
                    peers.append(FederationPeer(
                        name=p.get("name") or url,
                        url=url,
                        auth_token=p.get("auth_token") or "",
                        source="peers_file",
                    ))
                    seen_urls.add(url)
        except Exception:
            pass  # Normal si le fichier n'existe pas encore

        # ── Source 2 : mcp_connectors des workspaces ─────────────────────────
        # On inclut tous les connectors activés — certains pointent vers d'autres
        # Colaig (ils exposent colaig_list_workspaces), d'autres vers des MCP externes.
        # On tente colaig_list_workspaces dans list_remote_workspaces() et on ignore
        # gracieusement si la réponse n'est pas un format Colaig reconnu.
        from colaig.security.federation_guard import validate_peer_url
        for conn in (workspace_connectors or []):
            url = (getattr(conn, "url", None) or "").strip()
            if not url or url in seen_urls or not getattr(conn, "enabled", True):
                continue
            try:
                validate_peer_url(url)
            except ValueError:
                logger.debug(
                    "federation: connector URL '%s' ignorée (non HTTPS ou invalide)", url
                )
                continue
            peers.append(FederationPeer(
                name=getattr(conn, "name", url),
                url=url,
                auth_token=getattr(conn, "auth_token", "") or "",
                source="connector",
            ))
            seen_urls.add(url)

        return peers

    async def list_remote_workspaces(
        self, peers: list[FederationPeer]
    ) -> list[RemoteWorkspace]:
        """Interroge colaig_list_workspaces sur chaque peer (parallèle, cache TTL).

        Appels silencieux : un peer inaccessible → [] sans exception.
        Format de réponse Colaig attendu : JSON array avec workspace_id, name, description.
        Autres serveurs MCP qui ne comprennent pas colaig_list_workspaces → ignorés.

        Args:
            peers: Liste de FederationPeer à interroger.

        Returns:
            Liste de RemoteWorkspace (tous peers confondus).
        """
        if not peers:
            return []

        # Utiliser le cache si frais
        now = time.monotonic()
        cache_valid = (now - self._cache_ts) < _CACHE_TTL and bool(self._ws_cache)
        if cache_valid:
            result = []
            for peer in peers:
                for ws in self._ws_cache.get(peer.url, []):
                    result.append(_make_remote(peer, ws))
            return result

        # Interroger tous les peers en parallèle
        raw_results = await asyncio.gather(
            *[self._fetch_workspaces(peer) for peer in peers],
            return_exceptions=True,
        )

        new_cache: dict[str, list[dict]] = {}
        all_remote: list[RemoteWorkspace] = []
        for peer, result in zip(peers, raw_results):
            if isinstance(result, Exception):
                logger.debug("federation: peer '%s' erreur gather: %s", peer.name, result)
                new_cache[peer.url] = []
                continue
            workspaces = result
            new_cache[peer.url] = workspaces
            for ws in workspaces:
                all_remote.append(_make_remote(peer, ws))

        self._ws_cache = new_cache
        self._cache_ts = time.monotonic()
        return all_remote

    async def _fetch_workspaces(self, peer: FederationPeer) -> list[dict]:
        """Appelle colaig_list_workspaces sur un peer via JSON-RPC MCP."""
        import httpx as _httpx

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "colaig_list_workspaces",
                "arguments": {},
            },
        }
        headers: dict[str, str] = {}
        if peer.auth_token:
            headers["Authorization"] = f"Bearer {peer.auth_token}"

        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(peer.url, json=payload, headers=headers)
                resp.raise_for_status()
            data = resp.json()
            content = data.get("result", {}).get("content", [])
            if not content:
                return []
            raw = json.loads(content[0].get("text", "[]"))
            if not isinstance(raw, list):
                return []
            return raw
        except Exception as exc:
            logger.debug("federation: colaig_list_workspaces '%s' échoué: %s", peer.name, exc)
            return []

    def invalidate_cache(self) -> None:
        """Force le rechargement au prochain appel à list_remote_workspaces."""
        self._cache_ts = 0.0
        self._ws_cache = {}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ──────────────────────────────────────────────────────────────────────────────

def _make_remote(peer: FederationPeer, ws: dict) -> RemoteWorkspace:
    """Construit un RemoteWorkspace depuis la réponse brute MCP d'un peer."""
    return RemoteWorkspace(
        peer=peer,
        workspace_id=ws.get("workspace_id", ""),
        name=ws.get("name", ""),
        description=ws.get("description", ""),
        topics=ws.get("topics", []),
        rag_enabled=ws.get("rag_enabled", True),
    )
