"""
Contrat — désigner un espace n'est pas désigner un chemin.

STATUT: TESTE
VERSION: 2026-08-27 - v1.0
LOT: L2.6

Pourquoi ce fichier
--------------------
`path_validator.py` était couvert à 75 %, et le trou portait entièrement sur
`validate_workspace_path` — jamais exercée.

Ce n'est pas un doublon de `validate_storage_path`. Un **espace** est un objet plus
contraint qu'un chemin : c'est un dossier de **premier niveau**, à la racine du stockage
de Colaig. Cette forme n'est pas décorative — elle vient de la topologie réelle du
produit :

> Un collègue partage un dossier de SON stockage avec Colaig ; le dossier apparaît **à
> la racine** du stockage de Colaig et devient un espace de travail.

Un espace est donc, par construction, `/<un-seul-segment>/`. Accepter `/rh/sous/dossier`
comme espace reviendrait à déclarer instance Colaig un sous-dossier d'un partage — avec
son propre `.colaig/`, sa propre configuration, ses propres droits, à l'intérieur d'un
espace qui en a déjà.
"""
from __future__ import annotations

import pytest

from colaig.exceptions import StorageError
from colaig.security.path_validator import validate_workspace_path


# ── La forme d'un espace ────────────────────────────────────────────────────


@pytest.mark.parametrize("chemin", ["/espace-rh/", "/espace-rh", "/rh_2026/", "/a/"])
def test_un_dossier_de_premier_niveau_est_un_espace(chemin):
    assert validate_workspace_path(chemin)


@pytest.mark.parametrize("chemin", [
    "/rh/sous-dossier/",
    "/rh/sous/dossier/",
    "/",
    "",
])
def test_ce_qui_n_est_pas_un_dossier_de_premier_niveau_est_refuse(chemin):
    """Un sous-dossier ne peut pas être un espace.

    Sinon un espace contiendrait un espace : deux `.colaig/`, deux configurations, deux
    jeux de droits pour un même contenu — et rien ne dirait lequel fait foi.
    """
    with pytest.raises((StorageError, ValueError)):
        validate_workspace_path(chemin)


@pytest.mark.parametrize("chemin", ["/-rh/", "/_rh/", "/.colaig/", "/.cache/"])
def test_un_nom_qui_ne_commence_pas_par_un_alphanumerique_est_refuse(chemin):
    """`.colaig` en tête de liste : le dossier d'instance n'est pas un espace.

    Le déclarer comme tel ferait de la configuration de Colaig un corpus indexable, et
    de son dossier de prompts une destination d'écriture légitime.
    """
    with pytest.raises((StorageError, ValueError)):
        validate_workspace_path(chemin)


def test_un_chemin_sans_slash_initial_est_normalise_puis_accepte():
    """Comportement epingle parce qu'il n'est pas evident.

    `rh/` devient `/rh/` : la validation amont ancre tout chemin a la racine. C'est
    coherent avec `is_subpath`, qui normalise de la meme facon — deux predicats qui
    ancreraient differemment finiraient par diverger sur un cas limite.
    """
    assert validate_workspace_path("rh/") == "/rh"


def test_une_traversee_est_refusee():
    with pytest.raises((StorageError, ValueError)):
        validate_workspace_path("/rh/../etc/")


# ── La liste des espaces connus ─────────────────────────────────────────────


def test_un_espace_hors_de_la_liste_connue_est_refuse():
    """La forme ne suffit pas : `/nimporte-quoi/` est bien formé et n'existe pas.

    Sans ce contrôle, désigner un espace inexistant ferait créer un `.colaig/` à un
    endroit arbitraire de la racine — une instance Colaig que personne n'a partagée.
    """
    with pytest.raises(ValueError):
        validate_workspace_path("/inconnu/", known_paths=["/espace-rh/"])


@pytest.mark.parametrize("connu", ["/espace-rh/", "/espace-rh"])
def test_la_comparaison_tolere_le_slash_final(connu):
    """Le slash final ne doit pas décider de l'existence d'un espace.

    `paths.py` normalise, mais `known_paths` vient d'ailleurs — d'une configuration ou
    d'un résolveur. Faire dépendre le résultat d'un slash produirait un refus que
    personne ne saurait expliquer.
    """
    assert validate_workspace_path("/espace-rh/", known_paths=[connu])
    assert validate_workspace_path("/espace-rh", known_paths=[connu])


def test_sans_liste_connue_seule_la_forme_decide():
    """Comportement documenté, épinglé pour qu'il soit un choix et non une surprise."""
    assert validate_workspace_path("/nouvel-espace/")
