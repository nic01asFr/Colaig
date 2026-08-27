"""
Colaig — Validation des URLs pour les connecteurs MCP

Protection contre les attaques SSRF (Server-Side Request Forgery) lorsque
des tools MCP externes (ex: Chrome DevTools) naviguent vers des URLs.

Deux niveaux de contrôle :
- Blocklist IP : empêche l'accès aux réseaux privés / metadata cloud
- Allowlist domaines : restreint la navigation aux domaines autorisés (glob)

Usage :
    from colaig.security.url_validator import validate_navigation_url

    validate_navigation_url(
        "https://demarches-simplifiees.fr/login",
        allowed_domains=["*.gouv.fr", "demarches-simplifiees.fr"],
        blocked_ip_ranges=["169.254.0.0/16", "10.0.0.0/8"],
    )
    # → OK

    validate_navigation_url("http://169.254.169.254/latest/meta-data/")
    # → URLValidationError (cloud metadata)
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from fnmatch import fnmatch
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class URLValidationError(Exception):
    """URL bloquée par la politique de sécurité."""


# Blocklist par défaut — réseaux privés et metadata cloud
DEFAULT_BLOCKED_IP_RANGES = [
    "169.254.0.0/16",   # link-local / cloud metadata (AWS, GCP, Azure)
    "10.0.0.0/8",       # réseau privé classe A
    "172.16.0.0/12",    # réseau privé classe B
    "192.168.0.0/16",   # réseau privé classe C
    "127.0.0.0/8",      # loopback
    "0.0.0.0/8",        # non routable
    "::1/128",          # loopback IPv6
    "fc00::/7",         # unique local IPv6
    "fe80::/10",        # link-local IPv6
]


def _parse_ip_networks(
    ranges: list[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse des CIDR strings en objets réseau, ignore les invalides."""
    networks = []
    for r in ranges:
        try:
            networks.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            logger.debug("url_validator: CIDR invalide ignoré: %s", r)
    return networks


def _is_ip_blocked(
    hostname: str,
    blocked_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Vérifie si un hostname résout vers une IP bloquée.

    Résout le DNS pour détecter les attaques par redirection DNS
    (hostname public qui pointe vers IP privée).
    """
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS ne résout pas — laisser passer, le serveur MCP échouera
        return False

    for family, _type, _proto, _canonname, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in blocked_networks:
            if ip in net:
                logger.warning(
                    "url_validator: IP %s (%s) dans réseau bloqué %s",
                    ip_str, hostname, net,
                )
                return True
    return False


def adresse_litterale(hostname: str):
    """L'adresse IP que la pile reseau comprendra, quelle qu'en soit l'ecriture.

    POURQUOI `ipaddress` SEUL NE SUFFIT PAS
    -----------------------------------------
    `ipaddress.ip_address("2130706433")` leve `ValueError` : la bibliotheque est
    STRICTE et n'accepte que la forme pointee. Le controle retombait alors sur la
    resolution DNS, qui ne traite pas ces ecritures — et quatre formes de 127.0.0.1
    passaient, ainsi que trois de l'adresse de metadonnees cloud.

    `socket.inet_aton` decode les ecritures heritees de BSD — decimale, hexadecimale,
    abregee — exactement comme le fera la pile au moment de la connexion.

    **Ce qu'il faut valider est ce que le systeme comprendra, pas ce qu'une
    bibliotheque stricte accepte.**

    Les IPv4 mappees en IPv6 (`::ffff:127.0.0.1`) sont ramenees a leur IPv4 : sinon
    elles echappent aux plages IPv4 bloquees tout en atteignant la meme machine.

    Returns:
        Une adresse, ou `None` si le hostname est un vrai nom de domaine.
    """
    # Forme stricte d'abord — la plus courante.
    try:
        adresse = ipaddress.ip_address(hostname)
    except ValueError:
        adresse = None

    if adresse is None:
        # Ecritures heritees : 2130706433, 0x7f000001, 127.1
        try:
            adresse = ipaddress.ip_address(socket.inet_aton(hostname))
        except (OSError, ValueError):
            return None

    # Une IPv4 mappee atteint la meme machine que l'IPv4 qu'elle porte.
    mappee = getattr(adresse, "ipv4_mapped", None)
    return mappee or adresse


def _is_domain_allowed(hostname: str, allowed_domains: list[str]) -> bool:
    """Vérifie si le hostname correspond à un pattern autorisé.

    Patterns supportés (fnmatch) :
    - "example.com"      → match exact
    - "*.gouv.fr"        → sous-domaines de gouv.fr
    - "*.demarches-*"    → patterns complexes
    """
    for pattern in allowed_domains:
        if fnmatch(hostname, pattern):
            return True
        # Support implicite : "gouv.fr" doit matcher "www.gouv.fr"
        if fnmatch(hostname, f"*.{pattern}"):
            return True
    return False


def validate_navigation_url(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    blocked_ip_ranges: list[str] | None = None,
    resolve_dns: bool = True,
) -> str:
    """Valide une URL avant navigation par un tool MCP.

    Args:
        url: URL à valider.
        allowed_domains: Whitelist de domaines (glob). Si vide/None, tous acceptés.
        blocked_ip_ranges: CIDR des réseaux interdits. Si None, DEFAULT_BLOCKED_IP_RANGES.
        resolve_dns: Si True, résout le DNS pour vérifier l'IP réelle.

    Returns:
        URL validée (inchangée).

    Raises:
        URLValidationError: Si l'URL est bloquée.
    """
    if not url or not isinstance(url, str):
        raise URLValidationError("URL vide ou invalide")

    parsed = urlparse(url)

    # Schéma autorisé
    if parsed.scheme not in ("http", "https"):
        raise URLValidationError(
            f"Schéma interdit : '{parsed.scheme}' (seuls http/https autorisés)"
        )

    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError(f"Pas de hostname dans l'URL : {url}")

    # 1. Vérifier la allowlist de domaines (si configurée)
    if allowed_domains:
        if not _is_domain_allowed(hostname, allowed_domains):
            raise URLValidationError(
                f"Domaine non autorisé : '{hostname}'. "
                f"Domaines autorisés : {', '.join(allowed_domains)}"
            )

    # 2. Vérifier la blocklist IP
    ip_ranges = blocked_ip_ranges if blocked_ip_ranges is not None else DEFAULT_BLOCKED_IP_RANGES
    blocked_networks = _parse_ip_networks(ip_ranges)

    # Le hostname est-il une adresse, SOUS QUELQUE ECRITURE QUE CE SOIT ?
    ip = adresse_litterale(hostname)
    if ip is not None:
        for net in blocked_networks:
            if ip in net:
                raise URLValidationError(
                    f"IP bloquée : {hostname} → {ip} (réseau {net})"
                )
    elif resolve_dns and blocked_networks:
        # Un vrai nom de domaine — verifier ou il pointe.
        if _is_ip_blocked(hostname, blocked_networks):
            raise URLValidationError(
                f"Hostname '{hostname}' résout vers une IP privée/bloquée"
            )

    return url
