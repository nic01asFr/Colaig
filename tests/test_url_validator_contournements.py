"""
Contrat — l'anti-SSRF résiste aux écritures alternatives d'une adresse.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut, mesuré
------------------
`validate_navigation_url` bloque `http://169.254.169.254/` — la forme classique. Il
laissait passer **quatre écritures de la même adresse** :

    http://2130706433/                    127.0.0.1 en décimal
    http://0x7f000001/                    127.0.0.1 en hexadécimal
    http://127.1/                         127.0.0.1 abrégé
    http://[0:0:0:0:0:ffff:127.0.0.1]/    IPv4 mappée en IPv6

Et les mêmes formes atteignent l'adresse de métadonnées cloud : `2852039166` et
`0xa9fea9fe` valent tous deux `169.254.169.254`.

Ce sont les contournements SSRF les plus documentés. Une couverture de 67 % sur ce
module signalait déjà que ses chemins n'avaient pas été éprouvés.

Pourquoi `ipaddress` seul ne suffit pas
-----------------------------------------
`ipaddress.ip_address("2130706433")` lève `ValueError` : la bibliothèque est **stricte**
et n'accepte que la forme pointée. Le code retombait alors sur la résolution DNS, qui ne
traite pas ces formes.

`socket.inet_aton`, lui, décode les écritures héritées de BSD — décimale, hexadécimale,
abrégée — exactement comme le fera la pile réseau au moment de la connexion. **C'est ce
que le système comprendra qu'il faut valider, pas ce que la bibliothèque stricte accepte.**
"""
from __future__ import annotations

import pytest

from colaig.security.url_validator import (
    URLValidationError,
    validate_navigation_url,
)

# (url, ce qu'elle atteint réellement)
CONTOURNEMENTS = [
    ("http://2130706433/", "127.0.0.1 en décimal"),
    ("http://0x7f000001/", "127.0.0.1 en hexadécimal"),
    ("http://127.1/", "127.0.0.1 abrégé"),
    ("http://[0:0:0:0:0:ffff:127.0.0.1]/", "IPv4 mappée en IPv6"),
    ("http://2852039166/latest/meta-data/", "métadonnées cloud en décimal"),
    ("http://0xa9fea9fe/latest/meta-data/", "métadonnées cloud en hexadécimal"),
    ("http://[::ffff:169.254.169.254]/", "métadonnées cloud mappée en IPv6"),
]


@pytest.mark.parametrize("url,quoi", CONTOURNEMENTS, ids=[c[1] for c in CONTOURNEMENTS])
def test_une_ecriture_alternative_est_bloquee(url, quoi):
    """LE défaut : la pile réseau comprend ces formes, la garde ne les voyait pas."""
    with pytest.raises(URLValidationError):
        validate_navigation_url(url, resolve_dns=False)


# ── Les formes déjà couvertes, pour qu'elles le restent ─────────────────────


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/",
    "http://10.1.2.3/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]/",
])
def test_les_formes_classiques_restent_bloquees(url):
    with pytest.raises(URLValidationError):
        validate_navigation_url(url, resolve_dns=False)


def test_une_adresse_publique_passe():
    """Une garde qui bloque tout se fait retirer, et ne protège alors plus rien."""
    assert validate_navigation_url("https://8.8.8.8/", resolve_dns=False)


def test_un_domaine_public_passe():
    assert validate_navigation_url("https://exemple.gouv.fr/page", resolve_dns=False)


# ── Schéma et forme ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://exemple.fr/",
    "ftp://exemple.fr/",
    "javascript:alert(1)",
])
def test_un_schema_non_http_est_refuse(url):
    with pytest.raises(URLValidationError):
        validate_navigation_url(url, resolve_dns=False)


@pytest.mark.parametrize("url", ["", None, "pas-une-url", "https://"])
def test_une_url_vide_ou_sans_hote_est_refusee(url):
    with pytest.raises(URLValidationError):
        validate_navigation_url(url, resolve_dns=False)


# ── La liste blanche de domaines ────────────────────────────────────────────


def test_un_domaine_hors_liste_est_refuse():
    with pytest.raises(URLValidationError):
        validate_navigation_url(
            "https://attaquant.example.org/",
            allowed_domains=["*.gouv.fr"], resolve_dns=False,
        )


def test_un_sous_domaine_autorise_passe():
    assert validate_navigation_url(
        "https://demarches.gouv.fr/", allowed_domains=["*.gouv.fr"], resolve_dns=False,
    )


def test_le_domaine_nu_couvre_ses_sous_domaines():
    """« gouv.fr » doit couvrir « www.gouv.fr » — sinon la liste est inutilisable."""
    assert validate_navigation_url(
        "https://www.gouv.fr/", allowed_domains=["gouv.fr"], resolve_dns=False,
    )


def test_un_domaine_qui_IMITE_l_autorise_est_refuse():
    """« gouv.fr.attaquant.org » ne relève pas de « gouv.fr ».

    C'est la même exigence d'ancrage que pour la liste blanche MCP (L2.2) : comparer des
    chaînes sans frontière laisse passer l'imitation.
    """
    for imitateur in ("https://gouv.fr.attaquant.org/",
                      "https://attaquant-gouv.fr/"):
        with pytest.raises(URLValidationError):
            validate_navigation_url(
                imitateur, allowed_domains=["gouv.fr"], resolve_dns=False,
            )


def test_sans_liste_blanche_tout_domaine_passe():
    """Comportement documenté, épinglé pour qu'il soit un choix et non une surprise.

    Sans `allowed_domains`, seule la blocklist d'IP s'applique. C'est défendable — la
    garde vise le SSRF, pas la navigation — mais c'est à savoir avant de s'y fier.
    """
    assert validate_navigation_url("https://n-importe-quoi.example.org/",
                                   resolve_dns=False)


# ── Une limite écrite plutôt que découverte ─────────────────────────────────


def test_la_reliaison_dns_n_est_PAS_couverte():
    """Limite connue : entre la validation et la connexion, le DNS peut changer.

    `resolve_dns=True` résout au moment du contrôle ; la pile réseau résoudra de nouveau
    au moment de la requête. Un domaine qui rend d'abord une IP publique puis une IP
    privée passerait — c'est le *DNS rebinding*.

    S'en prémunir demanderait de se connecter à l'IP validée plutôt qu'au nom, ce qui
    relève du client HTTP et non de ce module. Ce test ne réclame rien : il rend la
    limite visible.
    """
    assert validate_navigation_url("https://exemple.gouv.fr/", resolve_dns=False)
