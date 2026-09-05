"""
Contrat — le cache MCP ne traverse pas la frontière d'un espace.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.4

Le défaut
---------
`_TOOLS_CACHE` et `_INSTRUCTIONS_CACHE` existent depuis l'origine et sont **keyés sur
l'URL seule** :

    _TOOLS_CACHE[self._url] = (result, time.monotonic())

Or la valeur mise en cache n'est pas une donnée inerte : c'est la liste des
`(ToolDefinition, handler)`, et **chaque handler est une fermeture sur le
`MCPConnectorConfig`** de l'espace qui l'a construit. Il en emporte :

    auth_token           -> l'en-tête `Authorization` des appels
    allowed_domains      -> la liste blanche SSRF
    blocked_ip_ranges    -> les plages interdites
    max_calls_per_minute -> la limite d'appels
    max_result_length    -> la troncature
    session_scope/header -> l'isolation multi-utilisateur

Deux espaces qui déclarent **la même URL** partagent donc l'entrée. Le second reçoit les
handlers du premier — et appelle le serveur distant **avec le jeton du premier**, sous
la politique de sécurité du premier.

Ce n'est pas une fuite de contenu : c'est une fuite d'**identifiant** et de **politique**.
Colaig est multi-tenant, et la frontière d'un espace est son `config.yaml`.

Pourquoi ce fichier arrive maintenant
---------------------------------------
L'étude de la spec MCP 2026-07-28 (D54) portait sur `cacheScope` : le champ par lequel un
serveur déclare qu'une réponse **ne doit pas** être servie à un autre utilisateur. En
cherchant où le brancher, on trouve un cache qui ne distingue déjà pas les appelants.

Honorer `cacheScope: private` sur un cache keyé par URL n'aurait rien protégé — on
aurait ajouté un champ au-dessus d'un défaut. La clé se corrige d'abord.

Ce que la clé doit porter
---------------------------
Tout ce qui change la RÉPONSE ou la CONDUITE : l'URL, le jeton, et la politique
d'exposition. Deux connecteurs identiques en tout point peuvent légitimement partager
une entrée — c'est le cas d'un serveur public déclaré par plusieurs espaces, et c'est
justement ce que `cacheScope: "public"` autorise.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from colaig.integrations.mcp_connector import (
    _INSTRUCTIONS_CACHE,
    _TOOLS_CACHE,
    MCPConnectorClient,
)
from colaig.models import MCPConnectorConfig

URL = "http://mcp.local/mcp"

REPONSE_OUTILS = {
    "jsonrpc": "2.0",
    "result": {"tools": [{
        "name": "search",
        "description": "Recherche",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]},
    }]},
    "id": 1,
}

REPONSE_APPEL = {
    "jsonrpc": "2.0",
    "result": {"content": [{"type": "text", "text": "ok"}]},
    "id": 1,
}


@pytest.fixture(autouse=True)
def _vider_les_caches():
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()
    yield
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()


def _connecteur(**kw) -> MCPConnectorConfig:
    base = dict(name="commun", url=URL, enabled=True, expose_tools=True)
    base.update(kw)
    return MCPConnectorConfig(**base)


# ── LE défaut ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_le_jeton_d_un_espace_ne_part_PAS_pour_un_autre():
    """La fuite, prise à l'endroit où elle se voit : sur le fil.

    Deux espaces déclarent le même serveur avec leur propre jeton. Le second doit
    appeler le serveur avec LE SIEN — pas avec celui du premier, resté dans le handler
    mis en cache.
    """
    jetons: list[str] = []

    def _capturer(request, route):
        jetons.append(request.headers.get("Authorization", ""))
        corps = request.content.decode()
        return httpx.Response(200, json=REPONSE_APPEL if "tools/call" in corps
                              else REPONSE_OUTILS)

    respx.post(URL).mock(side_effect=_capturer)

    outils_a = await MCPConnectorClient(_connecteur(auth_token="jeton-A")).list_tools()
    outils_b = await MCPConnectorClient(_connecteur(auth_token="jeton-B")).list_tools()

    jetons.clear()
    await outils_b[0][1](query="x")          # l'espace B appelle son outil

    assert jetons == ["Bearer jeton-B"], (
        f"l'espace B a appele le serveur avec {jetons} — le handler mis en cache par "
        "l'espace A porte le jeton de A"
    )
    assert outils_a is not outils_b


@pytest.mark.asyncio
@respx.mock
async def test_la_liste_blanche_SSRF_d_un_espace_ne_s_applique_pas_a_un_autre():
    """Le handler emporte aussi `allowed_domains`.

    Un espace prudent qui restreint les domaines navigables se verrait appliquer la
    politique — plus permissive — d'un espace voisin déclarant la même URL.
    """
    respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_OUTILS))

    large = _connecteur(auth_token="A", allowed_domains=[])
    etroit = _connecteur(auth_token="B", allowed_domains=["*.gouv.fr"])

    await MCPConnectorClient(large).list_tools()
    outils = await MCPConnectorClient(etroit).list_tools()

    respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_APPEL))
    with pytest.raises(Exception):
        await outils[0][1](query="x", url="http://ailleurs.example.org/")


@pytest.mark.asyncio
@respx.mock
async def test_deux_politiques_d_exposition_ne_donnent_pas_la_meme_liste():
    """`tool_policy` filtre la liste AVANT la mise en cache.

    Un espace en `explicit` sans outil autorisé ne doit rien recevoir, même si un autre
    espace a déjà rempli l'entrée pour cette URL.
    """
    respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_OUTILS))

    tout = await MCPConnectorClient(_connecteur(tool_policy="all")).list_tools()
    aucun = await MCPConnectorClient(
        _connecteur(tool_policy="explicit", allowed_tools=[])).list_tools()

    assert len(tout) == 1
    assert aucun == [], (
        "un espace en politique explicite a recu la liste d'un espace en politique large"
    )


# ── Ce que le cache doit continuer de faire ─────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_le_cache_sert_toujours_a_quelque_chose():
    """Une correction de clé ne doit pas devenir une désactivation du cache.

    Le même connecteur, appelé deux fois, ne doit produire qu'un seul aller-retour.
    """
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_OUTILS))

    connecteur = _connecteur(auth_token="A")
    await MCPConnectorClient(connecteur).list_tools()
    await MCPConnectorClient(connecteur).list_tools()

    assert route.call_count == 1, "le cache ne sert plus"


@pytest.mark.asyncio
@respx.mock
async def test_deux_DECLARATIONS_identiques_partagent_l_entree():
    """Le cloisonnement porte sur ce qui DIFFÈRE, pas sur l'appelant.

    Un serveur public déclaré à l'identique par deux espaces partage légitimement une
    entrée — c'est ce que `cacheScope: "public"` autorise (D54). Cloisonner par espace
    ferait perdre le cache sans rien protéger.
    """
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_OUTILS))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 1, (
        "deux declarations identiques devraient partager l'entree"
    )


@pytest.mark.asyncio
@respx.mock
async def test_un_NOM_different_ne_partage_pas_l_entree():
    """Le nom du connecteur change la réponse : il préfixe les outils.

        name = f"{connector_name}__{raw_name}"     (mcp_connector.py:108)

    Et `_contrat_admis` épingle les schémas PAR NOM de serveur (L2.3). Deux espaces qui
    nomment différemment le même serveur n'obtiennent donc ni les mêmes outils, ni le
    même épinglage.

    Ce test corrige une erreur de ce fichier : sa première version affirmait que deux
    connecteurs de noms différents devaient partager l'entrée. Elle passait — **grâce au
    défaut qu'on corrige ici**, qui servait la liste du premier au second.
    """
    respx.post(URL).mock(return_value=httpx.Response(200, json=REPONSE_OUTILS))

    a = await MCPConnectorClient(_connecteur(name="juridique")).list_tools()
    b = await MCPConnectorClient(_connecteur(name="rh")).list_tools()

    assert a[0][0].name == "juridique__search"
    assert b[0][0].name == "rh__search", (
        "l'espace qui nomme le serveur `rh` a recu les outils prefixes `juridique`"
    )


@pytest.mark.asyncio
@respx.mock
async def test_les_instructions_serveur_sont_cloisonnees_aussi():
    """`_INSTRUCTIONS_CACHE` porte le même défaut de clé.

    Les instructions entrent dans le prompt système de l'Orchestrateur (balisées, L2.1).
    Un serveur peut légitimement les faire dépendre du jeton.
    """
    reponses = iter([
        {"jsonrpc": "2.0", "id": 1,
         "result": {"instructions": "INSTRUCTIONS DE A", "protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "id": 1,
         "result": {"instructions": "instructions de B", "protocolVersion": "2025-11-25"}},
    ])
    respx.post(URL).mock(side_effect=lambda request, route: httpx.Response(
        200, json=next(reponses)))

    a = await MCPConnectorClient(_connecteur(auth_token="A")).get_server_instructions()
    b = await MCPConnectorClient(_connecteur(auth_token="B")).get_server_instructions()

    assert a == "INSTRUCTIONS DE A"
    assert b == "instructions de B", (
        "l'espace B a recu les instructions serveur obtenues avec le jeton de A"
    )
