"""
Contrat — tout client LLM sait dire s'il répond.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Le défaut, trouvé par un déploiement réel
------------------------------------------
`/ready` interroge le client LLM ainsi :

    ping = getattr(llm_client, "ping", None)
    checks["llm"] = "ok" if (ping and await ping()) else "unavailable"

Un client SANS `ping` tombe donc dans la branche `else` — indistinguable d'un endpoint
en panne. Or `ping()` n'existait que sur `AlbertClient`.

Avec `LLM_BACKEND=openai` — **la cible de production** (`CLAUDE.md` §3 : « Cible de
production : SSPCloud, endpoint OpenAI-compatible ») — le client injecté est
`OpenAIClient`. `/ready` répondait donc **toujours** 503, et le pod **ne devenait jamais
prêt** : Kubernetes n'y envoyait aucun trafic, indéfiniment.

Mesuré le 29/08/2026 sur le déploiement `colaig-test` :

    /ready -> 503 {'storage': 'ok', 'llm': 'unavailable'}

alors que le même pod, interrogeant l'endpoint directement, recevait **HTTP 200** et la
liste des modèles. L'endpoint allait bien ; c'est la sonde qui ne savait pas le demander.

Pourquoi il n'avait jamais été vu
-----------------------------------
Le chart posait `/health` sur les deux sondes — un point qui rend 200 sans rien
vérifier. La correction de ce matin, qui branche `/ready`, est ce qui a rendu ce défaut
visible. Une sonde qui ne peut pas échouer ne cache pas seulement les pannes : elle
cache aussi ses propres trous.

Ce que `ping` doit faire, et ne pas faire
------------------------------------------
`GET {base}/v1/models`, sans consommer de jetons. **Tout statut < 500 vaut disponible** :
un 401 prouve que le serveur est joignable et répond — c'est la disponibilité qu'on
mesure ici, pas l'autorisation. Un 500 ou une exception réseau valent indisponible.

Et il **ne lève jamais** : une sonde qui lève transforme une dépendance lente en pod
redémarré.
"""
from __future__ import annotations

import httpx
import pytest
import respx

BASE = "http://llm.local"


def _clients():
    """Les clients que `create_llm_client` peut injecter dans `/ready`."""
    from colaig.integrations.llm.openai_client import OpenAIClient

    return [("OpenAIClient", OpenAIClient(api_key="x", base_url=BASE))]


@pytest.mark.parametrize("nom,client", _clients())
def test_le_client_POSSEDE_ping(nom, client):
    """LE défaut : sans cet attribut, `/ready` conclut « indisponible » sans demander.

    `getattr(llm_client, "ping", None)` rend None, et la branche `else` est prise —
    exactement comme si l'endpoint était tombé.
    """
    assert hasattr(client, "ping"), (
        f"{nom} n'a pas de ping() : /ready le declarera toujours indisponible"
    )


@pytest.mark.asyncio
@respx.mock
async def test_un_endpoint_qui_repond_est_DISPONIBLE():
    from colaig.integrations.llm.openai_client import OpenAIClient

    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "un-modele"}]}))

    assert await OpenAIClient(api_key="x", base_url=BASE).ping() is True


@pytest.mark.asyncio
@respx.mock
async def test_un_401_vaut_DISPONIBLE():
    """La sonde mesure la JOIGNABILITÉ, pas l'autorisation.

    Un 401 prouve qu'un serveur est là et répond. Conclure « indisponible » ferait
    sortir le pod du service pour une clé expirée — un problème réel, mais que le
    redémarrage ne répare pas, et que la sonde de vie traiterait mal.
    """
    from colaig.integrations.llm.openai_client import OpenAIClient

    respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(401))
    assert await OpenAIClient(api_key="mauvaise", base_url=BASE).ping() is True


@pytest.mark.asyncio
@respx.mock
async def test_un_500_vaut_INDISPONIBLE():
    from colaig.integrations.llm.openai_client import OpenAIClient

    respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(503))
    assert await OpenAIClient(api_key="x", base_url=BASE).ping() is False


@pytest.mark.asyncio
@respx.mock
async def test_une_panne_reseau_ne_LEVE_pas():
    """Une sonde qui lève transforme une dépendance lente en pod redémarré."""
    from colaig.integrations.llm.openai_client import OpenAIClient

    respx.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("injoignable"))
    assert await OpenAIClient(api_key="x", base_url=BASE).ping() is False


@pytest.mark.asyncio
@respx.mock
async def test_ping_ne_CONSOMME_pas_de_jetons():
    """Une sonde appelée toutes les dix secondes ne doit rien coûter.

    Un `ping` qui passerait par `chat/completions` facturerait une inférence par sonde
    et par pod — 8 640 par jour et par instance.
    """
    from colaig.integrations.llm.openai_client import OpenAIClient

    modeles = respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    chat = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={}))

    await OpenAIClient(api_key="x", base_url=BASE).ping()

    assert modeles.called
    assert not chat.called, "la sonde appelle le modele : elle facture une inference"


# ── Les trois autres backends ───────────────────────────────────────────────


def test_TOUS_les_clients_injectables_ont_ping():
    """Le défaut ne touchait pas qu'OpenAI.

    `create_llm_client` peut injecter quatre clients, et `main.py` passe le résultat à
    `/ready`. Trois sur quatre n'avaient pas de sonde — chacun aurait garde son pod
    indéfiniment non prêt.
    """
    from colaig.integrations.albert import AlbertClient
    from colaig.integrations.llm.azure_client import AzureClient
    from colaig.integrations.llm.capability_chain import CapabilityChain
    from colaig.integrations.llm.ollama_client import OllamaClient
    from colaig.integrations.llm.openai_client import OpenAIClient

    manquants = [c.__name__ for c in
                 (AlbertClient, OpenAIClient, AzureClient, OllamaClient, CapabilityChain)
                 if not hasattr(c, "ping")]
    assert manquants == [], f"sans ping, /ready les declarera indisponibles : {manquants}"


@pytest.mark.asyncio
async def test_la_chaine_est_disponible_si_UN_maillon_repond():
    """C'est la définition d'une chaîne de repli : elle sert tant qu'un maillon tient.

    Exiger que tous répondent sortirait le pod du service pour la panne d'un secours —
    l'inverse de ce que le repli existe pour faire.
    """
    from colaig.integrations.llm.capability_chain import CapabilityChain

    class _Maillon:
        def __init__(self, repond): self._r = repond
        async def ping(self, timeout=5.0): return self._r

    assert await CapabilityChain([(_Maillon(False), "a"),
                                  (_Maillon(True), "b")]).ping() is True
    assert await CapabilityChain([(_Maillon(False), "a"),
                                  (_Maillon(False), "b")]).ping() is False


@pytest.mark.asyncio
async def test_un_maillon_qui_LEVE_n_arrete_pas_la_chaine():
    """Un fournisseur en panne franche ne doit pas masquer un secours qui marche."""
    from colaig.integrations.llm.capability_chain import CapabilityChain

    class _Casse:
        async def ping(self, timeout=5.0): raise OSError("injoignable")

    class _Bon:
        async def ping(self, timeout=5.0): return True

    assert await CapabilityChain([(_Casse(), "a"), (_Bon(), "b")]).ping() is True
