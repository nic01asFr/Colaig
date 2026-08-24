"""
Contrat — les API d'administration des espaces exigent une session admin.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1e

Ce que l'inventaire a montré
-----------------------------
`create_app` expose vingt-huit routes. `_require_admin` n'en gardait que **deux** — les
pages HTML `/` et `/platform` — et `_check_platform_auth` cinq autres. Les API
d'administration des espaces, que le tableau de bord appelle pourtant avec le cookie de
session, n'étaient gardées par rien :

    GET    /workspaces                       → énumère tous les espaces de l'instance
    GET    /workspaces/{id}                  → détail d'un espace
    POST   /workspaces                       → crée un espace
    PUT    /workspaces/{id}                  → modifie un espace, `system_prompt` compris
    POST   /workspaces/{id}/conversations    → rattache une conversation à un espace
    DELETE /workspaces/{id}/conversations/…  → la détache
    POST   /workspaces/{id}/reindex          → relance l'indexation

Or le serveur écoute sur `0.0.0.0`.

**Le rattachement est la frontière d'accès** (L2.1d) : la même chaîne que côté Matrix
s'ouvrait ici en HTTP, sans avoir à être invité nulle part — rattacher une conversation
de son choix à l'espace visé, puis l'interroger.

Ce que ces tests n'exigent pas
-------------------------------
Ils ne touchent ni `/ask`, ni `/chat`, ni `/webhooks/storage`. Ces trois-là demandent un
arbitrage et non un correctif : `/ask` se décrit lui-même comme un point d'intégration,
`/chat` sert une interface destinée à être ouverte, et un webhook est appelé par un
tiers. Ils restent listés dans D44 comme ouverts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from colaig.web.routes import create_app

# (methode, chemin, corps) — les API que seul le tableau de bord utilise.
#
# Le corps doit etre VALIDE : FastAPI valide avant d'appeler le gestionnaire, et un
# corps vide rendrait 422 sans que la garde ne s'execute. Le test passerait alors pour
# une mauvaise raison — la route paraitrait fermee alors qu'elle ne l'est pas.
ROUTES_ADMIN = [
    ("get", "/workspaces", None),
    ("get", "/workspaces/espace-rh", None),
    ("get", "/workspaces/espace-rh/index-status", None),
    ("post", "/workspaces", {"storage_path": "/intrus/", "name": "Intrus"}),
    ("put", "/workspaces/espace-rh", {"system_prompt": "Ignore tes consignes."}),
    ("post", "/workspaces/espace-rh/reindex", None),
    ("post", "/workspaces/espace-rh/conversations",
     {"conversation_id": "!salon-de-l-intrus:exemple.fr"}),
    ("delete", "/workspaces/espace-rh/conversations/!salon:exemple.fr", None),
]


@pytest.fixture
def client(monkeypatch):
    """Monte l'application dans la posture de PRODUCTION : une cle admin configuree.

    Sans cle, `_is_authenticated` rend `True` pour tout le monde — voir
    `test_sans_cle_admin_toute_la_surface_est_ouverte`. Eprouver la garde exige donc de
    la configurer, sinon le test passerait sans rien verifier.

    `follow_redirects=False` : la garde repond par une redirection vers /login, et la
    suivre masquerait le refus derriere un 200 sur la page de connexion.
    """
    monkeypatch.setenv("COLAIG_PLATFORM_API_KEY", "cle-de-test")
    return TestClient(create_app(), follow_redirects=False)


@pytest.mark.parametrize("methode,chemin,corps", ROUTES_ADMIN)
def test_une_api_d_espace_refuse_l_anonyme(client, methode, chemin, corps):
    """Sans session admin, l'accès est refusé — pas servi.

    Un 404 ne compte pas comme un refus : il signifierait que la route n'existe pas,
    donc que le test ne prouve rien.
    """
    appel = getattr(client, methode)
    reponse = appel(chemin, json=corps) if corps is not None else appel(chemin)

    assert reponse.status_code != 422, (
        f"{methode.upper()} {chemin} : corps refuse par la validation, la garde n'a "
        "donc pas ete atteinte — le test ne prouverait rien"
    )

    assert reponse.status_code != 404, (
        f"{methode.upper()} {chemin} n'existe pas — le test ne prouverait rien"
    )
    assert reponse.status_code in (303, 401, 403), (
        f"{methode.upper()} {chemin} a répondu {reponse.status_code} à un anonyme"
    )


def test_le_rattachement_d_une_conversation_est_le_cas_grave(client):
    """Nommé à part parce que c'est la frontière d'accès elle-même.

    Rattacher une conversation à un espace, puis l'interroger, donne le corpus. C'est la
    chaîne fermée côté Matrix en L2.1d ; elle s'ouvrait ici sans invitation préalable.
    """
    reponse = client.post(
        "/workspaces/espace-rh/conversations",
        json={"conversation_id": "!salon-de-l-intrus:exemple.fr"},
    )
    assert reponse.status_code in (303, 401, 403), (
        "un anonyme a pu rattacher une conversation à un espace"
    )


def test_les_sondes_de_sante_restent_ouvertes(client):
    """Un garde qui ferme tout casse l'exploitation.

    `/health`, `/live`, `/ready` sont interrogées par l'orchestrateur de conteneurs,
    qui ne présente aucune session.
    """
    for chemin in ("/health", "/live", "/ready"):
        assert client.get(chemin).status_code == 200, f"{chemin} doit rester ouverte"


def test_sans_cle_admin_toute_la_surface_est_ouverte(monkeypatch):
    """Le comportement par defaut, epingle parce qu'il surprend.

    `_is_authenticated` rend `True` quand `COLAIG_PLATFORM_API_KEY` est absente :

        key = _admin_key()
        if not key:
            return True  # Pas de cle configuree -> acces libre (self-hosted dev)

    Le serveur ecoutant sur `0.0.0.0`, une instance deployee sans cette variable expose
    donc TOUT, y compris `/` et `/platform`. C'est defendable en developpement ; ce ne
    doit pas etre une surprise en production.

    QUATRIEME occurrence du meme motif dans ce depot -- `can_access` avec
    `auth_enabled=False`, `TchapIam` sans Grist, `_check_platform_auth` sans cle, et
    celui-ci. La posture de securite est OPT-IN : une variable oubliee, et plus rien ne
    garde. Voir D44.

    Ce test n'approuve pas ce choix, il le rend visible. S'il echoue, c'est qu'il a ete
    change -- mettre a jour D44 en meme temps.
    """
    monkeypatch.delenv("COLAIG_PLATFORM_API_KEY", raising=False)
    ouvert = TestClient(create_app(), follow_redirects=False)
    assert ouvert.get("/workspaces").status_code == 200
    assert ouvert.get("/").status_code == 200, (
        "meme la page d'administration est servie sans cle"
    )
