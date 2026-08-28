"""
Contrat — un seuil de référence dit sur combien de tirages il repose.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

Ce qui s'est passé, et que ce fichier empêche de recommencer
--------------------------------------------------------------
Le 27/08 à 20 h 32, la consigne de production est durcie (D50). Dix minutes plus tard,
les valeurs de `reference.json` sont rebasées sur **un tirage unique** qui donnait
`cite_attendu` 0.823, `fantomes` 3, `hors_contexte` 17.

Le 28/08, quatre tirages de la même condition donnent 0.7615 · 0.7946 · 0.7928 · 0.8125.
**Aucun n'atteint 0.823.** Ce tirage était haut sur les trois indicateurs à la fois, et
le seuil qu'il a fixé était franchi par un tirage sur quatre **sur du code inchangé**.

Pourquoi personne ne l'a vu venir
-----------------------------------
`reference.json` portait une variance de **0.001** sur cet indicateur — mesurée par
comparaison d'une référence et d'**un seul** réplicat.

**Deux tirages ne peuvent pas estimer une dispersion.** Ils peuvent tomber proches par
chance, et c'est ce qui s'est produit. Le même bloc montrait pourtant `fantomes` 5 → 8,
donc une dispersion bien réelle : le signal était là et n'a pas été lu.

C'est ce 0.001 qui a rendu crédible un rebasage sur un tirage : si la variance vaut
0.001, un tirage suffit. Quatre tirages donnent σ = 0.021, cinquante fois plus.

Ce que ce test garde
---------------------
La règle « aucune valeur mise à jour sur moins de quatre tirages » est, sans lui, une
phrase dans un document. Un chiffre écrit dans un document ne bloque rien : il se lit
après coup, quand la dégradation est déjà livrée. C'est le constat qui a fait naître
`verifier_reference.py` ; il vaut pour la règle qui gouverne ce fichier.
"""
from __future__ import annotations

import json
import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
REFERENCE = RACINE / "_chantier" / "reference.json"

# Releve de 4 a 10 le 28/08/2026, APRES mesure.
#
# Quatre tirages ont tenu quelques heures : le rebasage des fantomes sur quatre
# observations (6, 6, 8, 11) a pose un plafond de 13, et le passage suivant de la porte
# a rendu 15. Quinze tirages donnent une etendue de 5 a 15 la ou quatre en montraient 5.
#
# Quatre tirages restent le minimum pour ne pas conclure sur un accident ; ils ne
# suffisent pas a caracteriser une DISPERSION, surtout sur un petit compte entier dont
# la queue est droite.
TIRAGES_MINIMUM = 10


def _reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def _blocs_de_seuil(reference: dict):
    """Les blocs qui portent une contrainte — ceux qui ont un `minimum` ou un `maximum`."""
    for section in ("recherche", "generation", "verificateur_fidelite"):
        for nom, bloc in reference.get(section, {}).items():
            if isinstance(bloc, dict) and ("minimum" in bloc or "maximum" in bloc):
                yield f"{section}.{nom}", bloc


# ── La règle ────────────────────────────────────────────────────────────────


def test_un_bloc_qui_declare_ses_tirages_en_a_au_moins_quatre():
    """LA règle, rendue mécanique.

    Un seuil rebasé sur moins de quatre tirages n'est pas fondé : on ne distingue pas
    un tirage haut d'une amélioration réelle.
    """
    fautifs = [
        (nom, bloc["_tirages"]) for nom, bloc in _blocs_de_seuil(_reference())
        if "_tirages" in bloc and bloc["_tirages"] < TIRAGES_MINIMUM
    ]
    assert not fautifs, (
        f"blocs rebasés sur moins de {TIRAGES_MINIMUM} tirages : {fautifs}"
    )


def test_les_blocs_rebases_portent_leurs_observations():
    """Une moyenne sans ses tirages ne se vérifie pas.

    Sans les valeurs brutes, personne ne peut recalculer la dispersion ni constater
    qu'un seuil a été posé au jugement.
    """
    for nom, bloc in _blocs_de_seuil(_reference()):
        if "_tirages" not in bloc:
            continue
        assert "_observe" in bloc, f"{nom} déclare ses tirages sans les donner"
        assert len(bloc["_observe"]) == bloc["_tirages"], (
            f"{nom} annonce {bloc['_tirages']} tirages et en liste "
            f"{len(bloc['_observe'])}"
        )


def test_la_valeur_declaree_est_la_moyenne_des_tirages():
    """Une valeur de référence est une moyenne, jamais un tirage choisi.

    C'est exactement la faute du 27/08 : la valeur retenue était le tirage le plus
    favorable, et non le centre de la distribution.
    """
    for nom, bloc in _blocs_de_seuil(_reference()):
        if "_observe" not in bloc:
            continue
        moyenne = sum(bloc["_observe"]) / len(bloc["_observe"])
        # Tolérance : les valeurs sont arrondies pour rester lisibles.
        tolerance = max(0.005, abs(moyenne) * 0.05)
        assert abs(bloc["valeur"] - moyenne) <= tolerance, (
            f"{nom} : valeur {bloc['valeur']} pour une moyenne de {moyenne:.4f} — "
            "une valeur de référence est une moyenne, pas un tirage"
        )


def test_aucun_tirage_observe_ne_franchit_son_propre_seuil():
    """Un seuil franchi par les tirages qui le fondent est déjà cassé à sa naissance.

    L'ancien seuil de 0.78 était franchi par un tirage sur quatre du bras qui l'avait
    produit. Ce test attrape ce cas au moment du rebasage, pas trois jours plus tard
    quand la porte s'ouvre.
    """
    for nom, bloc in _blocs_de_seuil(_reference()):
        if "_observe" not in bloc:
            continue
        if "minimum" in bloc:
            assert min(bloc["_observe"]) >= bloc["minimum"], (
                f"{nom} : le tirage {min(bloc['_observe'])} passe déjà sous le seuil "
                f"{bloc['minimum']}"
            )
        if "maximum" in bloc:
            assert max(bloc["_observe"]) <= bloc["maximum"], (
                f"{nom} : le tirage {max(bloc['_observe'])} dépasse déjà le plafond "
                f"{bloc['maximum']}"
            )


# ── La dette, rendue visible plutôt qu'oubliée ──────────────────────────────


# Les quatre blocs de `generation` qui figuraient ici ont ete fondes le 28/08 sur
# dix-sept observations : ils se lisent tous dans le rapport de `reanalyse_generation`,
# qui ne fait AUCUN appel au modele. Les mesurer ne coutait donc que la relecture
# d'archives deja produites.
#
# Les trois qui restent demandent d'autres harnais : `recherche` passe par
# `reference_l15.py`, `verificateur_fidelite` par le sien. Leur attribuer un nombre de
# tirages aujourd'hui reviendrait a inventer une donnee (`CLAUDE.md` §4.8).
SANS_TIRAGES_CONNUS = {
    "recherche.complets_sur_attendus",
    "verificateur_fidelite.detection_derives",
    "verificateur_fidelite.faux_negatifs_max",
}


def test_la_liste_des_blocs_sans_tirages_ne_grandit_pas():
    """Les blocs antérieurs au 28/08 ne portent pas leur nombre de tirages.

    Le leur attribuer aujourd'hui reviendrait à **inventer une donnée** — interdit
    (`CLAUDE.md` §4.8). Ils sont donc listés tels quels, et ce test empêche la liste de
    s'allonger : un seuil ajouté demain devra dire sur quoi il repose.

    Même forme que `actions.inconnus()` : un oubli doit se voir, pas se deviner.
    """
    actuels = {
        nom for nom, bloc in _blocs_de_seuil(_reference()) if "_tirages" not in bloc
    }
    nouveaux = actuels - SANS_TIRAGES_CONNUS
    assert not nouveaux, (
        f"ces seuils ne disent pas sur combien de tirages ils reposent : "
        f"{sorted(nouveaux)}. Mesurer quatre tirages, ou justifier ici."
    )


def test_un_bloc_rebase_declare_sa_regle_de_seuil():
    """Une regle ecrite se discute ; une regle implicite se choisit au jugement.

    Les quinze tirages du 28/08 ont montre que la regle depend de la NATURE de
    l'indicateur : `moyenne - 2 sigma` convient a une fraction, dont la distribution est
    proche de la symetrie ; un COMPTE suit un Poisson et sa queue droite demande trois
    sigma. A deux sigma, le plafond des fantomes etait franchi par une observation sur
    quinze — sur du code sain.
    """
    for nom, bloc in _blocs_de_seuil(_reference()):
        if "_tirages" not in bloc:
            continue
        assert bloc.get("_regle"), (
            f"{nom} est rebase sans dire selon quelle regle son seuil est pose"
        )


def test_le_test_sait_echouer():
    """Un garde-fou qu'on n'a jamais vu se déclencher ne vaut rien.

    Deux des garde-fous de ce dépôt étaient verts pour de mauvaises raisons avant
    qu'on le vérifie.
    """
    faux = {"generation": {"bidon": {"valeur": 0.9, "minimum": 0.8, "_tirages": 2,
                                     "_observe": [0.9, 0.9]}}}
    fautifs = [
        nom for nom, bloc in _blocs_de_seuil(faux)
        if bloc.get("_tirages", 99) < TIRAGES_MINIMUM
    ]
    assert fautifs == ["generation.bidon"]


# ── La trace du défaut, épinglée ────────────────────────────────────────────


def test_la_variance_de_deux_tirages_porte_sa_limite():
    """Le bloc `_variance_observee` compare une référence et UN réplicat.

    Il est conservé comme trace, mais il ne doit plus fonder aucun seuil — et cela doit
    être écrit dessus, sinon quelqu'un s'en resservira pour justifier un tirage unique.
    """
    variance = _reference().get("_variance_observee", {})
    assert "_LIMITE_DE_CE_BLOC" in variance, (
        "le bloc de variance à deux tirages doit porter sa limite par écrit"
    )
