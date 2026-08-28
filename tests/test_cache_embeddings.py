"""
Contrat — le cache d'embeddings accélère la mesure sans la changer.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

Pourquoi ce cache
------------------
Chaque tirage de la référence recalculait **1 156 embeddings** — 1 021 articles du
corpus et 135 questions — alors que ni le corpus ni les questions ne changent d'un
tirage à l'autre. Seule la **génération** est stochastique. Sur la campagne de
dispersion du 28/08, huit tirages, une vingtaine de minutes sur 72 y sont passées pour
rien.

Le coût de la mesure décide de sa fréquence, et une mesure qu'on rechigne à relancer
est une mesure qu'on remplace par une intuition. C'est tout l'enjeu.

Ce que le cache change à la mesure, et qui doit être su
--------------------------------------------------------
Un embedding **n'est pas déterministe** : 2,6 × 10⁻⁴ d'écart absolu mesuré entre deux
appels du même texte. Le cache retire donc cette variance de la mesure.

C'est **souhaitable** ici — on veut isoler la variance de génération, et le bruit
d'embedding ne déplace pas le classement : `recherche_complets` donnait déjà 0.929
contre 0.929 entre deux réplicats. Mais c'est un **choix**, et `COLAIG_REF_CACHE=0`
permet de remesurer la jambe de recherche à neuf.

Deux défauts trouvés en construisant ce cache
-----------------------------------------------
1. `numpy.savez_compressed` **ajoute lui-même `.npz`** à un chemin : l'écriture
   provisoire atterrissait à côté de sa cible et le remplacement atomique échouait. Un
   fichier ouvert, plutôt qu'un chemin, corrige cela.

2. Un premier essai a affiché « 2/2 connus » sur un cache **fraîchement supprimé**.
   C'était mon harnais de test, qui passait `__file__ = "x"` : `RACINE` se résolvait
   deux niveaux au-dessus du dépôt et le cache partait ailleurs. Un test qui ment sur
   l'emplacement d'un fichier ment sur tout le reste.
"""
from __future__ import annotations

import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = RACINE / "_chantier" / "scripts" / "reference_l15.py"


def _module():
    """Charge `reference_l15` comme le font les harnais, avec le VRAI `__file__`.

    C'est la précaution que le premier essai avait manquée : `RACINE` dérive de
    `__file__`, et un `__file__` factice envoie le cache hors du dépôt.
    """
    import sys

    ancien = sys.argv
    sys.argv = ["gen", "article"]
    try:
        ns = {"__name__": "gen", "__file__": str(SOURCE)}
        exec(compile(SOURCE.read_text(encoding="utf-8")
                     .replace("raise SystemExit(main())", "pass"),
                     str(SOURCE), "exec"), ns)
        return ns
    finally:
        sys.argv = ancien


@pytest.fixture(scope="module")
def module():
    return _module()


# ── Le point unique ─────────────────────────────────────────────────────────


def test_le_cache_est_au_point_unique_d_embedding():
    """Sept harnais de mesure appellent `embed` — un seul le définit.

    Cacher dans chaque appelant aurait produit sept caches divergents. Ce dépôt a déjà
    mesuré ce que coûte une seconde implémentation d'un même contrôle.
    """
    from tests.conftest import code_seul

    source = code_seul(SOURCE.read_text(encoding="utf-8"))
    assert "_embed_distant" in source, (
        "l'appel réseau doit être isolé, et `embed` être le point de passage"
    )
    assert "CACHE_ACTIF" in source


def test_la_cle_porte_le_nom_du_modele():
    """Un changement de modèle ne doit jamais être servi depuis le cache.

    Le canari le détecterait en amont, mais une clé qui ignore le modèle rendrait le
    cache complice d'une dérive au lieu d'y être étranger.
    """
    from tests.conftest import code_seul

    source = code_seul(SOURCE.read_text(encoding="utf-8"))
    debut = source.index("def _cle_cache")
    corps = source[debut:debut + 400]
    assert "MODELE_EMBED" in corps, "la clé de cache doit inclure le nom du modèle"


def test_deux_textes_distincts_ont_des_cles_distinctes(module):
    c = module["_cle_cache"]
    assert c("Le chat dort.") != c("Le chien dort.")
    assert c("") != c(" ")


def test_le_meme_texte_donne_la_meme_cle(module):
    c = module["_cle_cache"]
    assert c("Article L2113-10.") == c("Article L2113-10.")


# ── La sortie de secours ────────────────────────────────────────────────────


def test_le_cache_se_desactive(monkeypatch):
    """`COLAIG_REF_CACHE=0` doit rendre la jambe de recherche mesurable à neuf.

    Sans cette sortie, on ne pourrait plus jamais observer la variance réelle de la
    recherche — le cache l'aurait définitivement masquée.
    """
    import os

    monkeypatch.setenv("COLAIG_REF_CACHE", "0")
    assert _module()["CACHE_ACTIF"] is False
    monkeypatch.setenv("COLAIG_REF_CACHE", "1")
    assert _module()["CACHE_ACTIF"] is True


def test_le_cache_est_actif_par_defaut(monkeypatch):
    monkeypatch.delenv("COLAIG_REF_CACHE", raising=False)
    assert _module()["CACHE_ACTIF"] is True


# ── Ce qui ne doit pas casser ───────────────────────────────────────────────


def test_un_cache_illisible_ne_bloque_pas(module, tmp_path, monkeypatch, capsys):
    """Un fichier tronqué par une campagne interrompue ne doit pas arrêter la mesure.

    L'écriture est atomique, donc le cas est improbable — mais un cache est une
    optimisation : il n'a pas le droit d'empêcher ce qu'il accélère.
    """
    corrompu = tmp_path / "cache.npz"
    corrompu.write_bytes(b"ceci n'est pas un npz")
    monkeypatch.setitem(module, "_CACHE_FICHIER", corrompu)
    monkeypatch.setitem(module, "_CACHE", None)

    assert module["_cache_charger"]() == {}
    assert "illisible" in capsys.readouterr().err


def test_le_fichier_de_cache_n_est_pas_commite():
    """Il pèse des mégaoctets et se reconstruit seul.

    Un dépôt public n'a pas à porter des vecteurs dérivés — et un cache commité
    divergerait silencieusement du modèle qui l'a produit.
    """
    ignore = (RACINE / ".gitignore").read_text(encoding="utf-8")
    assert "cache-embeddings" in ignore
