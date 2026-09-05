"""
Contrat — les sondes du chart interrogent des points qui peuvent ÉCHOUER.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Le défaut
---------
Le chart posait `/health` pour **les deux** sondes. Or `/health` rend
`{"status": "ok"}` **inconditionnellement** — il n'interroge rien et ne peut pas
échouer.

Une sonde de disponibilité qui ne peut pas échouer n'est pas une sonde : c'est une
décoration. Kubernetes conclut « ce pod est prêt » et lui envoie du trafic **alors que
son stockage ou son LLM est injoignable** — l'utilisateur reçoit alors des erreurs d'un
pod que l'orchestrateur croit sain.

`/ready` existe depuis l'origine et fait le travail : il teste `storage.exists("/")` et
`llm_client.ping()`, et rend **503** quand une dépendance manque. Sa docstring dit
elle-même « utile pour les probes Kubernetes/Onyxia : pas de trafic tant que pas prêt ».
Personne ne l'avait branché.

Pourquoi ce test lit le gabarit plutôt que de rendre le chart
--------------------------------------------------------------
`helm template` exigerait `helm` dans le chemin d'exécution de la suite, ce qui la
rendrait dépendante de l'environnement — le contraire de son contrat. Le gabarit est lu
comme du texte : c'est suffisant pour épingler quel point chaque sonde interroge.
"""
from __future__ import annotations

import pathlib

import pytest

GABARIT = (pathlib.Path(__file__).resolve().parent.parent
           / "deploy" / "helm" / "colaig" / "templates" / "deployment.yaml")


def _sonde(nom: str) -> str:
    """Le chemin HTTP interrogé par une sonde du gabarit."""
    texte = GABARIT.read_text(encoding="utf-8")
    debut = texte.index(nom)
    bloc = texte[debut:debut + 400]
    for ligne in bloc.splitlines():
        if "path:" in ligne:
            return ligne.split("path:")[1].strip()
    raise AssertionError(f"aucun `path:` sous {nom}")


def test_la_sonde_de_DISPONIBILITE_interroge_ready():
    """`/ready` teste storage ET LLM, et rend 503 quand l'un manque.

    C'est le critère du lot : « pod qui répond `/ready` ».
    """
    assert _sonde("readinessProbe") == "/ready"


def test_la_sonde_de_VIE_interroge_live():
    """`/live` dit que le processus tourne — ce qu'une sonde de vie doit demander.

    La distinction compte : redémarrer un pod parce que le LLM est tombé ne ferait que
    le redémarrer en boucle, sans rien réparer. La vie et la disponibilité ne posent
    pas la même question.
    """
    assert _sonde("livenessProbe") == "/live"


def test_AUCUNE_sonde_n_interroge_health():
    """`/health` rend 200 sans rien vérifier — il ne peut pas échouer.

    Une sonde qui ne peut pas échouer laisse Kubernetes envoyer du trafic à un pod dont
    les dépendances sont tombées. Ce test empêche le retour en arrière.
    """
    for sonde in ("readinessProbe", "livenessProbe"):
        assert _sonde(sonde) != "/health", (
            f"{sonde} interroge /health, qui rend toujours 200"
        )


@pytest.mark.parametrize("chemin", ["/ready", "/live"])
def test_les_points_interroges_EXISTENT_dans_le_code(chemin):
    """Une sonde vers une route absente rendrait 404 — donc un pod jamais prêt.

    Le chart et le code vivent dans deux mondes que rien ne relie ; ce test les
    rapproche.
    """
    routes = (pathlib.Path(__file__).resolve().parent.parent
              / "colaig" / "web" / "routes.py").read_text(encoding="utf-8")
    assert f'@app.get("{chemin}"' in routes


def test_ready_peut_REELLEMENT_echouer():
    """La contrepartie du test précédent : la route doit savoir dire non.

    Une sonde correctement câblée vers une route qui rend toujours 200 ne vaudrait pas
    mieux que ce qu'on corrige ici.
    """
    routes = (pathlib.Path(__file__).resolve().parent.parent
              / "colaig" / "web" / "routes.py").read_text(encoding="utf-8")
    debut = routes.index('@app.get("/ready"')
    corps = routes[debut:debut + 1600]
    assert "503" in corps, "/ready ne peut pas signaler une indisponibilite"
