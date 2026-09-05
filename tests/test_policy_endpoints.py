"""
Contrat de sécurité — `allowed_llm_endpoints` n'est pas contournable.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L0.3b

`allowed_llm_endpoints` est le **seul levier** par lequel un opérateur de plateforme
fait respecter la souveraineté du LLM : Colaig ne restreint aucun endpoint par défaut
(décision D9), la contrainte est donc entièrement portée par cette liste.

Jusqu'au lot L0.3b, le contrôle était `url.startswith(autorise)`. Il laissait passer :

    "https://llm.lab.sspcloud.fr.attaquant.example/v1"
        .startswith("https://llm.lab.sspcloud.fr")   →  True

Un opérateur croyait donc restreindre son parc au datalab, alors qu'un simple suffixe
de domaine suffisait à envoyer les conversations ailleurs. Ces tests interdisent le
retour de ce contournement.
"""
from __future__ import annotations

import pytest

from colaig.config import endpoint_autorise

AUTORISES = ["https://llm.lab.sspcloud.fr", "https://albert.api.etalab.gouv.fr/v1"]


@pytest.mark.parametrize(
    "url",
    [
        "https://llm.lab.sspcloud.fr",
        "https://llm.lab.sspcloud.fr/",
        "https://llm.lab.sspcloud.fr/api",
        "https://llm.lab.sspcloud.fr/api/v1",
        "https://LLM.LAB.SSPCLOUD.FR/api",  # l'autorité est insensible à la casse
        "https://albert.api.etalab.gouv.fr/v1",
        "https://albert.api.etalab.gouv.fr/v1/chat/completions",
    ],
)
def test_endpoints_legitimes_acceptes(url):
    assert endpoint_autorise(url, AUTORISES), url


@pytest.mark.parametrize(
    "url",
    [
        # Le contournement historique : suffixe ajouté au nom de domaine.
        "https://llm.lab.sspcloud.fr.attaquant.example/v1",
        "https://llm.lab.sspcloud.frx/api",
        # Sous-domaine non déclaré.
        "https://evil.llm.lab.sspcloud.fr/api",
        # Schéma dégradé — un endpoint autorisé en HTTPS ne l'est pas en clair.
        "http://llm.lab.sspcloud.fr/api",
        # Port différent = autorité différente.
        "https://llm.lab.sspcloud.fr:8443/api",
        # Hôte totalement autre.
        "https://api.openai.com/v1",
        # Chemin qui déborde du préfixe autorisé sans frontière de segment.
        "https://albert.api.etalab.gouv.fr/v1bis",
        "https://albert.api.etalab.gouv.fr/autre",
    ],
)
def test_endpoints_illegitimes_refuses(url):
    assert not endpoint_autorise(url, AUTORISES), url


def test_liste_vide_ne_dit_rien():
    """Une liste vide n'autorise rien *par cette fonction*.

    L'absence de contrainte est décidée en amont — les appelants ne consultent la
    policy que si `allowed_llm_endpoints` est non vide. La fonction, elle, ne doit
    jamais répondre « oui » sans raison.
    """
    assert not endpoint_autorise("https://llm.lab.sspcloud.fr/api", [])


def test_le_controle_est_bien_branche():
    """Régression : les deux points de validation doivent utiliser la fonction durcie."""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / "colaig" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "startswith(ep)" not in source, (
        "un contrôle de policy est revenu à startswith — contournable par suffixe de domaine"
    )
    assert source.count("endpoint_autorise(") >= 3  # définition + deux appels
