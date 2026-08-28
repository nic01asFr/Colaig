"""
Contrat — la porte de régression consulte le canari des modèles.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

Le trou que le canari bouche
------------------------------
Toutes les valeurs de `reference.json` sont mesurées contre deux modèles **distants** :
`qwen3-6-35b-moe` pour la génération, `BAAI/bge-m3` pour les embeddings.

Vérifié le 28/08/2026 : les catalogues rendent le nom du modèle servi, mais **ni
version, ni date, ni empreinte**. Un changement de poids sous le même nom rendrait toute
la référence caduque en silence — et la porte imputerait la dérive à notre code.

Ce n'est pas théorique : la soirée du 27/08 a été passée à faire cette distinction à la
main, sur une porte devenue rouge sans qu'une ligne du chemin de génération n'ait bougé.

Ce que ce fichier garde
------------------------
Que le canari soit **branché**. Septième vérification explicite de ce motif dans ce
dépôt — celui que la phase 2 a trouvé neuf fois, dont une sur le filtre de masquage des
secrets, installé au mauvais endroit et ne protégeant aucun module.

Le canari lui-même est mesuré, pas supposé
--------------------------------------------
Sa calibration du 28/08 a **inversé les deux hypothèses** de son auteur :

- les embeddings **ne sont pas** déterministes — écart absolu 2.6 × 10⁻⁴ entre deux
  appels du même texte, et aucun arrondi ne stabilise une empreinte par hachage ;
- la génération à température 0 **l'est**, sur les trois questions retenues.

D'où une règle de comparaison inverse de celle prévue : cosinus pour les embeddings,
égalité stricte pour la génération. Discrimination mesurée : bruit 0.999999, seuil
0.9999, changement réel de contenu 0.433.
"""
from __future__ import annotations

import json
import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = RACINE / "_chantier" / "scripts"
EMPREINTE = RACINE / "_chantier" / "canari.json"


def _source(nom: str) -> str:
    from tests.conftest import code_seul

    return code_seul((SCRIPTS / nom).read_text(encoding="utf-8"))


# ── Le branchement ──────────────────────────────────────────────────────────


def test_la_porte_de_regression_consulte_le_canari():
    """Un canari écrit et non branché ne prévient de rien."""
    source = _source("verifier_reference.py")
    assert "canari_modeles.py" in source, (
        "`verifier_reference.py` doit lancer le canari avant de comparer les seuils"
    )
    assert "canari()" in source, "le canari doit être APPELÉ, pas seulement défini"


def test_une_derive_arrete_la_porte():
    """Continuer après une dérive produirait un diagnostic faux.

    C'est le point : sans arrêt, la porte attribuerait au code une dégradation venue
    d'un changement de modèle — exactement le contresens que la soirée du 27/08 a
    évité à la main.
    """
    source = _source("verifier_reference.py")
    debut = source.index("def canari")
    corps = source[debut:source.index("def main")]
    assert "SystemExit" in corps, "une dérive détectée doit arrêter la porte"


def test_un_canari_absent_ne_bloque_PAS():
    """Comportement documenté : la porte reste utilisable sans canari calibré.

    Un poste neuf ou une chaîne d'intégration n'en a pas encore. Bloquer rendrait la
    porte inutilisable et ferait retirer le canari — une garde trop zélée se fait
    désactiver, et ne protège alors plus rien.
    """
    source = _source("verifier_reference.py")
    debut = source.index("def canari")
    corps = source[debut:source.index("def main")]
    assert "exists()" in corps and "return" in corps, (
        "un canari absent doit avertir et laisser passer"
    )


# ── L'empreinte elle-même ───────────────────────────────────────────────────


@pytest.mark.skipif(not EMPREINTE.exists(), reason="canari non calibré sur ce poste")
def test_l_empreinte_couvre_les_DEUX_modeles():
    """La génération seule ne suffirait pas.

    Un `bge-m3` remplacé déplacerait toute la recherche documentaire **sans qu'une
    seule réponse paraisse fausse** — c'est le modèle le plus insidieux à changer, et
    celui qu'on penserait le moins à surveiller.
    """
    e = json.loads(EMPREINTE.read_text(encoding="utf-8"))
    assert "embeddings" in e and "generation" in e
    assert e["embeddings"]["vecteurs"], "l'empreinte des embeddings doit être posée"


@pytest.mark.skipif(not EMPREINTE.exists(), reason="canari non calibré sur ce poste")
def test_le_seuil_est_au_dessus_du_bruit_mesure():
    """Un garde-fou dont le seuil touche son propre bruit crie au loup.

    Le bruit propre a été mesuré AVANT de poser le seuil : c'est la seule raison d'être
    du mode `--calibrer`. Ce test épingle que la marge existe.
    """
    e = json.loads(EMPREINTE.read_text(encoding="utf-8"))["embeddings"]
    bruit = e["bruit_mesure"]["cosinus_min"]
    seuil = e["cosinus_minimum"]
    assert seuil < bruit, (
        f"seuil {seuil} au-dessus du bruit propre {bruit} — fausses alertes garanties"
    )


@pytest.mark.skipif(not EMPREINTE.exists(), reason="canari non calibré sur ce poste")
def test_la_generation_ne_retient_que_ses_questions_stables():
    """Une question dont la réponse varie ne peut pas servir de canari.

    Elle produirait une alerte à chaque exécution, et l'on apprendrait à ne plus la
    lire — ce qui reviendrait à ne pas avoir de canari du tout.
    """
    g = json.loads(EMPREINTE.read_text(encoding="utf-8"))["generation"]
    assert len(g["questions_stables"]) == len(g["reponses_stables"])
    assert not (set(g["questions_stables"]) & set(g["questions_ecartees"])), (
        "une question ne peut pas être à la fois retenue et écartée"
    )


@pytest.mark.skipif(not EMPREINTE.exists(), reason="canari non calibré sur ce poste")
def test_l_empreinte_ne_contient_aucun_secret():
    """Elle est commitée : des vecteurs et des réponses, jamais une clé."""
    brut = EMPREINTE.read_text(encoding="utf-8").lower()
    for motif in ("api_key", "bearer ", "password", "token="):
        assert motif not in brut, f"« {motif} » dans une empreinte commitée"
