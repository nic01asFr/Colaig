"""
Colaig — Protection de la fédération décentralisée

Validation des URLs de peers (anti-SSRF) et des réponses de peers (anti-injection).
Utilisé par FederationService et workspace_delegate._call_peer_search().
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# NOMS d'hote bloques — ceux qu'aucune plage d'adresses ne couvre.
#
# La verification des ADRESSES est deleguee a `security/url_validator.py`. Ce module
# portait sa propre liste noire, en expression reguliere sur la chaine du nom d'hote :
# une seconde copie du meme controle, et PLUS FAIBLE. Mesure le 25/08/2026, elle
# laissait passer six formes sur neuf — loopback en decimal, hexadecimal, abrege, IPv4
# mappee en IPv6, et les plages IPv6 `fc00::/7` et `fe80::/10` qui manquaient purement
# et simplement.
#
# C'est le cout d'une garde dupliquee, mesure : on corrige la premiere et la seconde
# reste ouverte.
_BLOCKED_HOST_NAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.goog",
})

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
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOST_NAMES:
        raise ValueError(f"URL peer pointe vers un hôte bloqué (SSRF): '{host}'")

    # Les ADRESSES sont jugees par le point de passage unique — y compris les ecritures
    # heritees (decimale, hexadecimale, abregee) et les IPv4 mappees en IPv6.
    from colaig.security.url_validator import (
        URLValidationError,
        validate_navigation_url,
    )

    try:
        # `resolve_dns=False` : un pair est declare par l'operateur dans `peers.yaml`,
        # pas soumis par un tiers. Resoudre le DNS a la validation ajouterait une
        # dependance reseau a une fonction pure, sans fermer la reliaison DNS pour
        # autant — voir la limite epinglee dans les tests d'`url_validator`.
        validate_navigation_url(url, resolve_dns=False)
    except URLValidationError as exc:
        raise ValueError(f"URL peer bloquée : {exc}") from exc

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
