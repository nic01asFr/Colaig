"""
Contrat — l'auto-découverte de la clé LLM dans un pod Onyxia.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Ce que la découverte fait
---------------------------
Sur Onyxia / SSP Cloud, l'utilisateur renseigne sa clé LLM dans son espace. Le pod, s'il
porte le rôle `edit`, peut explorer le namespace et l'y retrouver — plutôt que d'exiger
qu'on la lui repasse par `--set llm.apiKey` au lancement.

Elle n'intervient **que si `LLM_API_KEY` est vide**. Une clé passée explicitement gagne
toujours : c'est l'opérateur qui a décidé.

LE RISQUE, ET POURQUOI LA SÉLECTION EST ÉTROITE
--------------------------------------------------
Le rôle `edit` donne au pod la lecture de **tous les secrets du namespace** — mots de
passe de bases, jetons S3, identifiants de services voisins.

Une découverte qui prendrait « le premier secret qui ressemble à une clé » enverrait
donc un jour un mot de passe PostgreSQL à un endpoint LLM tiers. C'est une exfiltration,
même si personne ne l'a voulue.

D'où deux règles :

**1. Seuls les secrets dont le NOM désigne le LLM sont regardés.** `postgres-password`
n'est jamais lu, quoi qu'il contienne.

**2. Aucun essai spéculatif.** On ne teste pas des candidats contre l'endpoint pour voir
lequel marche : ce serait envoyer les identifiants des autres services au LLM, un par
un, jusqu'à en trouver un bon. C'est pire que le problème.

Ce que je n'ai PAS pu vérifier
--------------------------------
Le nom exact sous lequel Onyxia range la clé d'un espace. La liste par défaut est un
POINT DE DÉPART, surchargeable par `COLAIG_SSPCLOUD_SECRETS`, et la découverte
**journalise ce qu'elle a trouvé et où** — pour qu'un premier déploiement dise la
vérité plutôt que d'échouer en silence.
"""
from __future__ import annotations

import base64

import pytest


@pytest.fixture
def pod(tmp_path, monkeypatch):
    """Simule les fichiers qu'un pod Kubernetes monte pour son compte de service."""
    racine = tmp_path / "serviceaccount"
    racine.mkdir()
    (racine / "token").write_text("jeton-du-compte", encoding="utf-8")
    (racine / "namespace").write_text("user-nicolaslaval", encoding="utf-8")
    (racine / "ca.crt").write_text("-----BEGIN CERTIFICATE-----", encoding="utf-8")

    import colaig.integrations.sspcloud as ssp

    monkeypatch.setattr(ssp, "_RACINE_COMPTE", racine)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    return ssp


def _secret(nom: str, donnees: dict[str, str]) -> dict:
    return {"metadata": {"name": nom},
            "data": {k: base64.b64encode(v.encode()).decode()
                     for k, v in donnees.items()}}


def _liste(*secrets) -> dict:
    return {"items": list(secrets)}


# ── La clé explicite gagne ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_cle_EXPLICITE_dispense_de_toute_exploration(pod, monkeypatch):
    """L'opérateur a décidé — on n'explore pas, et on ne touche pas au cluster.

    Explorer quand même ferait un appel réseau inutile au démarrage de chaque pod, et
    ouvrirait la porte à ce qu'une découverte remplace un choix délibéré.
    """
    monkeypatch.setenv("LLM_API_KEY", "cle-de-l-operateur")

    appels: list = []

    async def _jamais(*a, **k):
        appels.append(a)
        raise AssertionError("le cluster a ete interroge malgre une cle explicite")

    monkeypatch.setattr(pod, "_lire_secrets", _jamais)

    cle, source = await pod.decouvrir_cle()
    assert cle == "cle-de-l-operateur"
    assert "explicite" in source or "LLM_API_KEY" in source
    assert appels == []


# ── Hors d'un pod ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hors_d_un_pod_la_decouverte_ne_casse_rien(monkeypatch, tmp_path):
    """Colaig tourne aussi sur un poste de développement.

    Rendre une raison lisible plutôt que lever : le démarrage ne doit pas dépendre de
    la présence d'un cluster.
    """
    import colaig.integrations.sspcloud as ssp

    monkeypatch.setattr(ssp, "_RACINE_COMPTE", tmp_path / "absent")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

    cle, source = await ssp.decouvrir_cle()
    assert cle == ""
    assert source, "une decouverte infructueuse doit dire POURQUOI"


# ── LA garde : la sélection est étroite ─────────────────────────────────────


@pytest.mark.asyncio
async def test_un_secret_SANS_RAPPORT_n_est_jamais_lu(pod, monkeypatch):
    """LA garde de ce module.

    Le rôle `edit` donne la lecture de tous les secrets du namespace. Prendre « le
    premier qui ressemble à une clé » enverrait un jour un mot de passe PostgreSQL à un
    endpoint LLM tiers — une exfiltration que personne n'a voulue.
    """
    async def _secrets(*a, **k):
        return _liste(
            _secret("postgres-password", {"password": "SECRET-BASE-DE-DONNEES"}),
            _secret("s3-credentials", {"AWS_SECRET_ACCESS_KEY": "SECRET-S3"}),
        )

    monkeypatch.setattr(pod, "_lire_secrets", _secrets)

    cle, source = await pod.decouvrir_cle()
    assert cle == "", f"un secret sans rapport a ete retenu (source={source})"
    assert "SECRET-BASE" not in source and "SECRET-S3" not in source


@pytest.mark.asyncio
async def test_le_secret_qui_DESIGNE_le_llm_est_retenu(pod, monkeypatch):
    async def _secrets(*a, **k):
        return _liste(
            _secret("postgres-password", {"password": "SECRET-BASE"}),
            _secret("sspcloud-llm", {"LLM_API_KEY": "la-vraie-cle"}),
        )

    monkeypatch.setattr(pod, "_lire_secrets", _secrets)

    cle, source = await pod.decouvrir_cle()
    assert cle == "la-vraie-cle"
    assert "sspcloud-llm" in source, "la source doit etre nommee, pour l'audit"


@pytest.mark.asyncio
async def test_la_liste_des_noms_est_SURCHARGEABLE(pod, monkeypatch):
    """Je n'ai pas pu vérifier le nom sous lequel Onyxia range la clé.

    La liste par défaut est un point de départ ; un opérateur qui connaît le sien doit
    pouvoir le dire sans modifier le code.
    """
    monkeypatch.setenv("COLAIG_SSPCLOUD_SECRETS", "mon-secret-maison")

    async def _secrets(*a, **k):
        return _liste(_secret("mon-secret-maison", {"api_key": "cle-maison"}))

    monkeypatch.setattr(pod, "_lire_secrets", _secrets)

    cle, _ = await pod.decouvrir_cle()
    assert cle == "cle-maison"


@pytest.mark.asyncio
async def test_aucun_ESSAI_SPECULATIF_contre_l_endpoint(pod):
    """On ne teste pas des candidats contre le LLM pour voir lequel marche.

    Ce serait envoyer les identifiants des services voisins à un tiers, un par un.
    Le module ne doit contenir aucun appel sortant vers l'endpoint LLM.
    """
    import inspect

    from tests.conftest import code_seul

    source = code_seul(inspect.getsource(pod))

    # `Authorization` est LEGITIME ici : c'est l'en-tete de l'API Kubernetes, seul
    # appel sortant du module. La premiere version de ce test l'interdisait — elle
    # confondait « parler au cluster » et « parler au LLM ».
    # `openai-api-key` est un NOM DE SECRET candidat, pas un endpoint : l'interdire
    # confondait le nom d'un fournisseur et l'adresse de son service.
    for interdit in ("chat/completions", "llm.lab.sspcloud.fr", "/v1/models",
                     "llm_base_url"):
        assert interdit not in source.lower(), (
            f"le module reference `{interdit}` — il essaie des cles contre l'endpoint"
        )

    # La seule URL construite doit viser l'API Kubernetes.
    assert "KUBERNETES_SERVICE_HOST" in source
    assert source.count("https://") == 1, (
        "un seul hote doit etre joint : l'API Kubernetes"
    )


# ── Ce que la découverte dit ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_cle_n_est_JAMAIS_journalisee(pod, monkeypatch, caplog):
    """Une clé dans un journal est une clé publiée.

    Le nom de la source, oui — c'est ce qui rend un premier déploiement diagnosticable.
    """
    import logging

    async def _secrets(*a, **k):
        return _liste(_secret("sspcloud-llm", {"LLM_API_KEY": "cle-tres-secrete"}))

    monkeypatch.setattr(pod, "_lire_secrets", _secrets)

    with caplog.at_level(logging.DEBUG):
        await pod.decouvrir_cle()

    journal = "\n".join(r.getMessage() for r in caplog.records)
    assert "cle-tres-secrete" not in journal
    assert "sspcloud-llm" in journal, "la source doit apparaitre, pour l'audit"


@pytest.mark.asyncio
async def test_un_refus_du_cluster_est_dit_et_non_avale(pod, monkeypatch):
    """Sans le rôle `edit`, l'API rend 403.

    C'est la cause la plus probable d'un premier déploiement qui ne trouve rien : elle
    doit se lire, pas se deviner.
    """
    async def _refuse(*a, **k):
        raise PermissionError("secrets is forbidden: role edit manquant")

    monkeypatch.setattr(pod, "_lire_secrets", _refuse)

    cle, source = await pod.decouvrir_cle()
    assert cle == ""
    assert "forbidden" in source or "edit" in source or "droit" in source.lower()


# ── Le branchement ──────────────────────────────────────────────────────────


def test_la_decouverte_est_BRANCHEE_dans_main():
    """Seizième vérification du motif « écrit et non branché » dans ce dépôt.

    Un module de découverte que `main()` n'appelle pas ne découvrirait jamais rien, et
    le premier déploiement Onyxia conclurait que le mécanisme ne marche pas.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "main.py").read_text(encoding="utf-8"))

    assert "decouvrir_cle()" in source, "main() n'appelle pas la decouverte"
    assert "config.llm_api_key = cle" in source, (
        "la cle decouverte n'est pas posee dans la configuration"
    )


def test_la_decouverte_ne_s_execute_QUE_si_la_cle_manque():
    """Une clé explicite est un choix de l'opérateur.

    Sans cette condition, chaque démarrage de pod ferait un appel au cluster pour rien,
    et une découverte pourrait remplacer une décision délibérée.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "main.py").read_text(encoding="utf-8"))
    garde = source.index("if not config.llm_api_key and not config.albert_api_key:")
    appel = source.index("decouvrir_cle()")
    assert garde < appel, "la decouverte s'execute sans verifier que la cle manque"
