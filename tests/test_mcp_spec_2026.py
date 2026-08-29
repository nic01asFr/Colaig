"""
Contrat — ce que le client fait des champs de cache de la spec MCP 2026-07-28.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.4

La spec, relevée sur la source (SEP-2549)
-------------------------------------------
`ttlMs` et `cacheScope` accompagnent les réponses de `tools/list`, `prompts/list`,
`resources/list`, `resources/read` et `resources/templates/list` — **jamais les
résultats de `tools/call`**.

    ttlMs absent    -> traiter comme 0, ET « rely on their own caching heuristics »
    ttlMs negatif   -> ignorer, traiter comme 0
    ttlMs > 0       -> frais pendant ttlMs ms a compter de la RECEPTION

    cacheScope "public"   -> tout cache partage peut servir la reponse a tout le monde
    cacheScope "private"  -> « Shared caches MUST NOT serve a cached copy to a
                             different user »

Une correction de D54, et pourquoi
------------------------------------
D54 concluait « `cacheScope` absent vaut `private` », par prudence. **C'était trop
strict, et cela aurait désactivé le cache contre tous les serveurs existants** : aucun
serveur en protocole 2025-11-25 n'émet ce champ, et `mcp.data.gouv.fr` — celui que le
critère du lot nomme — est dans ce cas.

Ce qui rend l'absence sans danger ici est **vérifiable, pas supposé** : `cacheScope`
gouverne le partage entre UTILISATEURS, or notre requête `tools/list` ne porte aucun
identifiant d'utilisateur. Le jeton est celui de l'espace, et il entre déjà dans la clé
(L3.4a). La réponse ne peut donc pas être propre à un utilisateur.

`test_list_tools_ne_porte_AUCUN_identifiant_d_utilisateur` épingle exactement cette
condition. Le jour où quelqu'un ajoute une identité par utilisateur à `tools/list`, il
tombe — et l'absence redevient dangereuse.

Quand le champ EST là, il fait loi : `private` interdit le cache, parce qu'un cache de
processus ne sait pas à qui il sert.
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

OUTIL = {"name": "search", "description": "Recherche",
         "inputSchema": {"type": "object",
                         "properties": {"query": {"type": "string"}},
                         "required": ["query"]}}


def _reponse(**extra) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [OUTIL], **extra}}


@pytest.fixture(autouse=True)
def _vider():
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()
    yield
    _TOOLS_CACHE.clear()
    _INSTRUCTIONS_CACHE.clear()


def _flux(objet: dict) -> str:
    """Un bloc de flux d'evenements, tel qu'un serveur MCP le rend."""
    import json as _json

    return "event: message\ndata: " + _json.dumps(objet) + "\n\n"


def _connecteur(**kw) -> MCPConnectorConfig:
    base = dict(name="c", url=URL, enabled=True, expose_tools=True)
    base.update(kw)
    return MCPConnectorConfig(**base)


# ── ttlMs ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_un_ttlMs_court_fait_expirer_l_entree():
    """Le serveur décide de la fraîcheur, pas nous.

    Un serveur dont la liste bouge souvent peut annoncer un TTL bref ; l'ignorer
    servirait des outils disparus pendant cinq minutes.
    """
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(ttlMs=1)))

    client = MCPConnectorClient(_connecteur())
    await client.list_tools()
    import asyncio
    await asyncio.sleep(0.01)
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 2, "un ttlMs de 1 ms doit avoir expire"


@pytest.mark.asyncio
@respx.mock
async def test_un_ttlMs_long_prolonge_l_entree():
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(ttlMs=3_600_000)))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_un_ttlMs_NEGATIF_est_ignore():
    """La spec : « If a server returns a negative value, clients SHOULD ignore it ».

    On retombe sur notre propre heuristique, pas sur zéro : sans quoi un serveur
    malveillant désactiverait le cache d'un simple `-1`.
    """
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(ttlMs=-1)))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_sans_ttlMs_notre_heuristique_gouverne():
    """Le cas de TOUS les serveurs d'aujourd'hui.

    La spec le prévoit : « rely on their own caching heuristics ». Traiter l'absence
    comme zéro rendrait le cache inopérant contre chaque serveur existant.
    """
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_reponse()))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 1


# ── cacheScope ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cacheScope_private_INTERDIT_le_cache():
    """« Shared caches MUST NOT serve a cached copy to a different user ».

    Notre cache vit dans le processus et ne sait pas à qui il sert. Le seul moyen de
    respecter `private` est donc de ne rien garder.
    """
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(cacheScope="private",
                                                       ttlMs=3_600_000)))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 2, (
        "une reponse declaree privee a ete servie depuis un cache partage"
    )


@pytest.mark.asyncio
@respx.mock
async def test_cacheScope_public_autorise_le_cache():
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(cacheScope="public")))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_une_valeur_INCONNUE_de_cacheScope_est_traitee_comme_privee():
    """Un champ présent mais illisible est une déclaration qu'on ne comprend pas.

    Le sens sûr est de ne pas partager — même règle que pour une annotation MCP absente
    dans `security/actions.py`.
    """
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_reponse(cacheScope="chelou")))

    await MCPConnectorClient(_connecteur()).list_tools()
    await MCPConnectorClient(_connecteur()).list_tools()

    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_ne_porte_AUCUN_identifiant_d_utilisateur():
    """LA condition qui rend l'absence de `cacheScope` sans danger.

    Si `tools/list` transportait un jour une identité par utilisateur — en-tête de
    session, jeton délégué — la réponse pourrait devenir propre à un utilisateur, et
    notre cache partagé la servirait à un autre.

    Ce test tombe ce jour-là. C'est le signal qu'il faut alors traiter l'absence de
    `cacheScope` comme `private`.
    """
    vus: list[dict] = []

    def _capturer(request, route):
        vus.append(dict(request.headers))
        return httpx.Response(200, json=_reponse())

    respx.post(URL).mock(side_effect=_capturer)

    await MCPConnectorClient(
        _connecteur(session_scope="user", session_header="X-Session-Id")).list_tools()

    assert "x-session-id" not in {k.lower() for k in vus[0]}, (
        "tools/list transporte une identite d'utilisateur : le cache partage devient "
        "dangereux, voir la docstring de ce fichier"
    )


# ── Invalidation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_un_outil_INCONNU_invalide_la_liste_en_cache():
    """La spec l'autorise explicitement : « Clients MAY re-fetch if they have reason to
    believe the data has changed. Examples include receiving an unexpected error on a
    tool call indicating that the method was not found ».

    Sans cela, un serveur qui renomme un outil laisse Colaig appeler un nom mort
    pendant toute la durée du TTL.

    C'est la seule invalidation câblable aujourd'hui : les serveurs que nous atteignons
    déclarent `listChanged: false` et n'émettent donc aucune notification.
    """
    from colaig.integrations.mcp_connector import _TOOLS_CACHE

    def _repondre(request, route):
        corps = request.content.decode()
        if "tools/call" in corps:
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32601, "message": "Method not found"}})
        return httpx.Response(200, json=_reponse())

    respx.post(URL).mock(side_effect=_repondre)

    outils = await MCPConnectorClient(_connecteur()).list_tools()
    assert len(_TOOLS_CACHE) == 1

    try:
        await outils[0][1](query="x")
    except Exception:
        pass

    assert _TOOLS_CACHE == {}, (
        "un outil inconnu n'a pas invalide la liste : Colaig rappellera le nom mort"
    )


# ── Le délai ────────────────────────────────────────────────────────────────


def test_le_delai_est_de_vingt_secondes():
    """Critère du lot. Trente secondes tenaient un tour de conversation en otage."""
    from colaig.integrations.mcp_connector import _HTTP_TIMEOUT

    assert _HTTP_TIMEOUT == 20.0


# ── Compaction ──────────────────────────────────────────────────────────────


def test_un_resultat_court_passe_INTACT():
    """La compaction ne doit pas abîmer ce qui tient déjà."""
    from colaig.integrations.mcp_connector import _extract_mcp_content

    contenu = [{"type": "text", "text": "trois lignes de resultat"}]
    assert _extract_mcp_content(contenu, max_length=1000) == "trois lignes de resultat"


def test_un_resultat_LONG_garde_sa_tete_ET_sa_queue():
    """Une coupe franche perd la fin, où se trouvent souvent le total et la conclusion.

    La troncature structurée garde les deux bouts et dit ce qu'elle a retiré — le
    modèle sait alors qu'il lit un extrait, au lieu de croire lire tout.
    """
    from colaig.integrations.mcp_connector import _extract_mcp_content

    texte = "DEBUT " + ("x" * 5000) + " FIN"
    rendu = _extract_mcp_content([{"type": "text", "text": texte}], max_length=500)

    assert len(rendu) <= 700
    assert rendu.startswith("DEBUT")
    assert rendu.endswith("FIN")
    assert "omis" in rendu or "tronqu" in rendu.lower(), (
        "le modele doit savoir qu'il lit un extrait"
    )


# ── Le transport ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_le_client_annonce_accepter_le_FLUX_D_EVENEMENTS():
    """Le défaut que seul le test vivant pouvait trouver.

    Le transport « Streamable HTTP » de MCP laisse le serveur répondre en JSON ou en
    flux d'événements, et EXIGE du client qu'il annonce accepter les deux. Un serveur
    conforme répond `406 Not Acceptable` à qui n'annonce que `application/json`.

    Mesuré le 29/08/2026 : `mcp.data.gouv.fr` rendait 406 sur chaque appel. Le client
    ne savait parler à AUCUN serveur MCP conforme — et aucun test hors ligne ne pouvait
    le voir, une doublure HTTP ne vérifiant pas les en-têtes qu'on lui envoie.

    Ce test le vérifie désormais explicitement.
    """
    vus: list[str] = []

    def _capturer(request, route):
        vus.append(request.headers.get("accept", ""))
        return httpx.Response(200, json=_reponse())

    respx.post(URL).mock(side_effect=_capturer)
    await MCPConnectorClient(_connecteur()).list_tools()

    assert "text/event-stream" in vus[0], (
        f"le client annonce `Accept: {vus[0]}` — un serveur conforme repondra 406"
    )
    assert "application/json" in vus[0]


@pytest.mark.asyncio
@respx.mock
async def test_une_reponse_en_flux_d_evenements_est_LUE():
    """`resp.json()` seul échouait sur la forme que renvoient les implémentations
    de référence :

        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{…}}
    """
    respx.post(URL).mock(return_value=httpx.Response(
        200, text=_flux(_reponse()), headers={"content-type": "text/event-stream"}))

    outils = await MCPConnectorClient(_connecteur()).list_tools()
    assert len(outils) == 1
    assert outils[0][0].name == "c__search"


@pytest.mark.asyncio
@respx.mock
async def test_dans_un_flux_c_est_le_RESULTAT_qui_est_retenu():
    """Un flux peut porter des événements de progression avant le résultat.

    Prendre le premier bloc `data:` rendrait la progression à la place de la réponse.
    """
    corps = _flux({"jsonrpc": "2.0", "method": "notifications/progress",
                   "params": {}}) + _flux(_reponse())
    respx.post(URL).mock(return_value=httpx.Response(
        200, text=corps, headers={"content-type": "text/event-stream"}))

    outils = await MCPConnectorClient(_connecteur()).list_tools()
    assert len(outils) == 1
