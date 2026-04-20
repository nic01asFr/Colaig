"""
Colaig — Protection de la fédération décentralisée

Validation des URLs de peers (anti-SSRF) et des réponses de peers (anti-injection).
Utilisé par FederationService et workspace_delegate._call_peer_search().
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# IPs et hostnames bloqués pour prévenir le SSRF
_BLOCKED_HOSTS_RE = re.compile(
    r'^('
    r'localhost'
    r'|127\.\d{1,3}\.\d{1,3}\.\d{1,3}'
    r'|0\.0\.0\.0'
    r'|::1'
    r'|10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
    r'|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
    r'|192\.168\.\d{1,3}\.\d{1,3}'
    r'|169\.254\.\d{1,3}\.\d{1,3}'
    r'|metadata\.google\.internal'
    r'|100\.64\.\d{1,3}\.\d{1,3}'
    r')$',
    re.I,
)

_MAX_URL_LENGTH = 512
_MAX_CHUNKS = 20
_MAX_TEXT_LENGTH = 2000
_MAX_SOURCE_LENGTH = 200


def validate_peer_url(url: str) -> str:
    """Valide une URL peer pour la fédération.

    Règles de sécurité :
    - Doit commencer par https:// (HTTP non autorisé)
    - Host ne doit pas être dans la liste noire SSRF
    - Pas de credentials dans l'URL
    - Longueur max 512 chars

    Args:
        url: URL du peer MCP Colaig.

    Returns:
        URL validée (inchangée si valide).

    Raises:
        ValueError: Si l'URL est invalide ou dangereuse.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL peer vide ou invalide")

    url = url.strip()

    if len(url) > _MAX_URL_LENGTH:
        raise ValueError(f"URL peer trop longue (max {_MAX_URL_LENGTH} chars)")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError(
            f"URL peer doit utiliser HTTPS (reçu: '{parsed.scheme}')"
        )

    if not parsed.netloc:
        raise ValueError("URL peer sans hôte valide")

    # Extraire le host sans port
    host = parsed.hostname or ""
    if _BLOCKED_HOSTS_RE.match(host):
        raise ValueError(f"URL peer pointe vers un hôte bloqué (SSRF): '{host}'")

    if parsed.username or parsed.password:
        raise ValueError("Credentials dans l'URL peer non autorisés")

    return url


def validate_peer_chunks(
    raw_chunks: object,
    peer_name: str,
    max_chunks: int = _MAX_CHUNKS,
    max_text_length: int = _MAX_TEXT_LENGTH,
) -> list[dict]:
    """Valide et normalise les chunks reçus d'un peer distant.

    Protège contre l'injection de contenu forgé depuis un peer compromis.

    Args:
        raw_chunks: Données brutes parsées depuis la réponse JSON du peer.
        peer_name: Nom du peer (pour les logs).
        max_chunks: Nombre maximum de chunks acceptés.
        max_text_length: Longueur maximum du texte d'un chunk.

    Returns:
        Liste de dicts {"text": str, "source": str} normalisés et sûrs.
    """
    if not isinstance(raw_chunks, list):
        logger.warning(
            "federation_guard: réponse peer '%s' invalide (pas une liste)",
            peer_name,
        )
        return []

    validated = []
    for i, chunk in enumerate(raw_chunks[:max_chunks]):
        if not isinstance(chunk, dict):
            logger.debug("federation_guard: chunk[%d] non-dict depuis '%s'", i, peer_name)
            continue

        text = chunk.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue

        # Tronquer et nettoyer
        text = text.replace("\x00", "").strip()[:max_text_length]

        source = str(chunk.get("source", ""))[:_MAX_SOURCE_LENGTH]
        source = source.replace("\x00", "").strip()

        score = chunk.get("score", 0.0)
        try:
            score = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            score = 0.0

        validated.append({"text": text, "source": source, "score": score})

    return validated


def validate_peers_config(peers_raw: list) -> list[dict]:
    """Valide la liste de peers chargée depuis peers.yaml.

    Filtre silencieusement les peers avec des URLs invalides (log warning).

    Args:
        peers_raw: Liste de dicts peers bruts depuis le fichier YAML.

    Returns:
        Liste de dicts peers valides uniquement.
    """
    if not isinstance(peers_raw, list):
        return []

    validated = []
    for peer in peers_raw:
        if not isinstance(peer, dict):
            continue
        url = (peer.get("url") or "").strip()
        if not url:
            continue
        try:
            validate_peer_url(url)
            validated.append(peer)
        except ValueError as exc:
            logger.warning(
                "federation_guard: peer '%s' ignoré — URL invalide: %s",
                peer.get("name", url),
                exc,
            )
    return validated
