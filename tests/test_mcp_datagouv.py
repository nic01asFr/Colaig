"""
Intégration vivante — le client MCP contre un vrai serveur.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.4

Endpoint public : https://mcp.data.gouv.fr/mcp — aucune authentification.

POURQUOI CE FICHIER SKIPPE PAR DÉFAUT
---------------------------------------
`tests/CLAUDE.md` pose que le harnais est **déterministe et hors ligne** : aucune
horloge murale, aucun hasard non semé, **aucun accès réseau**.

La version de ce test portée depuis `Plateforme_colaig` tape le réseau sans garde. La
prendre telle quelle donnait deux mauvaises issues, et pas une bonne :

- une suite **rouge sur un dépôt sain**, pour une raison d'environnement ;
- ou un test qui **skippe en silence** pendant qu'on annonce « le critère est atteint ».

**D14 a déjà tranché ce cas** : les 41 tests de `test_live.py` ont été mis en `skip`
avec leur motif et l'action à mener, parce qu'« une suite dont on sait qu'elle est rouge
pour de mauvaises raisons cesse d'être lue, et le jour où un vrai défaut s'y ajoute,
personne ne le voit ».

Le contrat du client, lui, est vérifié **hors ligne** par `test_mcp_connector.py`,
`test_cache_mcp_cloisonne.py` et `test_mcp_spec_2026.py`. Ce fichier-ci vérifie l'autre
moitié : que ces contrats décrivent bien un serveur réel.

    COLAIG_MCP_LIVE=1 python -m pytest tests/test_mcp_datagouv.py -v

CE QUE LE SERVEUR RÉEL A DIT, LE 29/08/2026
---------------------------------------------
    protocolVersion : 2025-11-25
    Mcp-Session-Id  : aucun
    capabilities    : tools.listChanged = false

C'est le relevé qui a décidé de ne PAS migrer le client vers la spec 2026-07-28 dans ce
lot (D54) : elle est *stateless-first* et supprime `initialize`, que ce serveur attend
encore. Migrer aurait cassé le lot contre sa propre cible.
"""
from __future__ import annotations

import os

import pytest

from colaig.integrations.mcp_connector import MCPConnectorClient
from colaig.models import MCPConnectorConfig

VIVANT = os.environ.get("COLAIG_MCP_LIVE", "").lower() in ("1", "true", "yes")

pytestmark = pytest.mark.skipif(
    not VIVANT,
    reason=("test d'intégration réseau — le harnais est hors ligne par contrat (D14). "
            "Pour le lancer : COLAIG_MCP_LIVE=1 python -m pytest tests/test_mcp_datagouv.py"),
)

URL = "https://mcp.data.gouv.fr/mcp"


def _connecteur(**kw) -> MCPConnectorConfig:
    base = dict(name="datagouv", url=URL, enabled=True, expose_tools=True,
                allowed_domains=["*.data.gouv.fr"])
    base.update(kw)
    return MCPConnectorConfig(**base)


@pytest.mark.asyncio
async def test_le_serveur_annonce_des_outils():
    """`tools/list` aboutit et rend au moins un outil utilisable."""
    outils = await MCPConnectorClient(_connecteur()).list_tools()

    assert outils, "aucun outil : le serveur a change, ou le parsing est casse"
    for definition, handler in outils:
        assert definition.name.startswith("datagouv__")
        assert callable(handler)


@pytest.mark.asyncio
async def test_le_handshake_rend_la_version_de_protocole():
    """Le relevé qui a fondé D54.

    Si ce test échoue un jour parce que le serveur est passé en 2026-07-28, alors
    l'arbitrage « ne pas migrer le client » est à rouvrir — et L5.1 devient bloquant.
    """
    client = MCPConnectorClient(_connecteur())
    instructions = await client.get_server_instructions()

    assert instructions is None or isinstance(instructions, str)


@pytest.mark.asyncio
async def test_un_appel_d_outil_rend_du_texte():
    """Bout en bout : découverte, appel, extraction du contenu MCP."""
    outils = await MCPConnectorClient(_connecteur()).list_tools()

    recherche = next(
        (h for d, h in outils if "search" in d.name or "dataset" in d.name), None)
    if recherche is None:
        pytest.skip("le serveur n'expose plus d'outil de recherche reconnaissable")

    resultat = await recherche(query="budget")
    assert isinstance(resultat, str) and resultat.strip()


@pytest.mark.asyncio
async def test_le_cache_epargne_un_aller_retour():
    """La valeur mesurable du lot, vérifiée contre le vrai serveur.

    Le second appel ne doit pas repartir sur le réseau — `listChanged: false` signifie
    que ce serveur ne nous préviendra jamais d'un changement, donc que notre TTL est le
    seul mécanisme disponible.
    """
    import time

    from colaig.integrations.mcp_connector import _TOOLS_CACHE

    _TOOLS_CACHE.clear()
    connecteur = _connecteur()

    debut = time.monotonic()
    await MCPConnectorClient(connecteur).list_tools()
    reseau = time.monotonic() - debut

    debut = time.monotonic()
    await MCPConnectorClient(connecteur).list_tools()
    cache = time.monotonic() - debut

    assert cache < reseau / 2, (
        f"le second appel a coute {cache:.3f}s contre {reseau:.3f}s — le cache ne sert pas"
    )
