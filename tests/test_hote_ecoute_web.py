"""
Contrat — sans clé d'administration, le serveur web n'écoute que la boucle locale.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.2a

Le raisonnement
---------------
D44 et D45 ont recensé **quatre gardes** de ce dépôt qui rendent « autorisé » quand leur
configuration est absente. `_is_authenticated` en fait partie : sans
`COLAIG_PLATFORM_API_KEY`, toute la surface web est servie — et le serveur écoutait sur
`0.0.0.0`.

Le défaut n'était donc pas « ouvert en développement » mais **ouvert partout**, y compris
sur une installation Helm par défaut, où `platformApiKey` vaut `""`.

Ce que ce contrat change — et ce qu'il ne change pas
------------------------------------------------------
Il ne ferme pas la garde d'authentification : cela casserait les déploiements
auto-hébergés qui s'en passent délibérément. **Il change le sens de l'échec.**

- clé absente → on n'écoute que `127.0.0.1`. Un déploiement mal configuré devient
  **inaccessible** au lieu d'être **ouvert**.
- clé posée → `0.0.0.0`, l'exploitant a fait le nécessaire.
- `COLAIG_WEB_HOST` explicite → ce qu'il dit.

Pourquoi une porte de sortie explicite n'est pas le motif qu'on dénonce
------------------------------------------------------------------------
`COLAIG_WEB_HOST` permet d'écouter largement sans clé — cas légitime d'un mandataire
inverse qui porte lui-même l'authentification.

La différence avec les quatre gardes recensées tient en un mot : leur défaut est
**ouvert**, celui-ci est **fermé**. Une variable oubliée y donnait tout ; ici elle ne
donne rien. Ouvrir redevient un acte.

Pourquoi maintenant, et pas plus tard
--------------------------------------
La liste blanche MCP du lot L2.2 vivra dans `config/clients.yml`, réécrivable par
`POST /api/platform/provision`, gardée par une clé absente par défaut. Livrer L2.2 sans
cela produirait un lot dont le critère passe en test et **reste inerte en déploiement** —
exactement le défaut que ce chantier passe son temps à trouver.
"""
from __future__ import annotations

import logging

import pytest

from colaig.main import hote_ecoute_web


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    monkeypatch.delenv("COLAIG_PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("COLAIG_WEB_HOST", raising=False)


def test_sans_cle_on_n_ecoute_que_la_boucle_locale():
    """Le cœur du contrat : mal configuré doit vouloir dire inaccessible."""
    assert hote_ecoute_web() == "127.0.0.1"


def test_avec_une_cle_on_ecoute_largement(monkeypatch):
    """L'exploitant a fait le nécessaire — ne pas lui barrer la route."""
    monkeypatch.setenv("COLAIG_PLATFORM_API_KEY", "une-cle")
    assert hote_ecoute_web() == "0.0.0.0"


def test_un_hote_explicite_l_emporte(monkeypatch):
    """Le cas du mandataire inverse qui porte lui-même l'authentification."""
    monkeypatch.setenv("COLAIG_WEB_HOST", "0.0.0.0")
    assert hote_ecoute_web() == "0.0.0.0"


def test_le_repli_est_annonce(caplog):
    """Un serveur qui se restreint sans le dire produit un incident incompréhensible.

    L'exploitant doit lire la cause — pas de clé — et les deux sorties : en poser une,
    ou déclarer `COLAIG_WEB_HOST` s'il a un mandataire devant.
    """
    with caplog.at_level(logging.WARNING):
        hote_ecoute_web()

    journal = " ".join(r.getMessage() for r in caplog.records)
    assert "COLAIG_PLATFORM_API_KEY" in journal, "la cause doit être nommée"
    assert "COLAIG_WEB_HOST" in journal, "l'issue explicite doit être nommée"


def test_aucun_bruit_quand_la_configuration_est_faite(monkeypatch, caplog):
    """Un avertissement qui se déclenche toujours cesse d'être lu."""
    monkeypatch.setenv("COLAIG_PLATFORM_API_KEY", "une-cle")
    with caplog.at_level(logging.WARNING):
        hote_ecoute_web()
    assert not caplog.records
