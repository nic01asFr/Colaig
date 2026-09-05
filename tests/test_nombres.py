"""
Contrat — lire un nombre entièrement, ou rendre None.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (correction de la notation)

Le défaut mesuré
-----------------
`montants()` cherchait `\\d{1,3}( \\d{3})+` — « 25 000 » et rien d'autre. Sur le corpus
doré : 1042 grandeurs écrites en lettres contre 130 en chiffres, et **42 vues par la
métrique, soit 4 %**. L'indicateur `montants_inventes`, présenté comme le plus grave
des sept, était aveugle à un montant fabriqué écrit « quarante-cinq mille euros ».

Pourquoi ces tests viennent d'ailleurs
----------------------------------------
Portage depuis `Editeur/redacteur/src/coherence.js` — projet voisin dont le corpus est
assemblé depuis le nôtre, et qui a mesuré le même phénomène indépendamment (71 % sur
399 sources, contre 89 % sur nos 1021).

**Les cas qui comptent sont ceux qui échouent**, et ils viennent de leur mesure :
« quarante-cinq jours » lu « 5 jours » par le motif naïf, **2 fois sur 146**, dans
`CCAG 11.7` et dans `R2192-36`. Un vérificateur qui annonce 5 là où le texte dit 45 se
disqualifie — et il le fait en silence.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "_chantier" / "scripts"))

from nombres import grandeurs, lire_nombre, montants  # noqa: E402


# ── Les chiffres, sous leurs formes typographiques ──────────────────────────


@pytest.mark.parametrize("brut,attendu", [
    ("30", 30), ("25000", 25000),
    ("180 000", 180000),        # espace fine insécable
    ("180 000", 180000),         # espace insécable
    ("180 000", 180000),          # espace ordinaire
    ("4,5", 4.5), ("4.5", 4.5),
])
def test_les_formes_chiffrees(brut, attendu):
    assert lire_nombre(brut) == attendu


# ── Les lettres, et le défaut qui rendait tout faux ─────────────────────────


@pytest.mark.parametrize("brut,attendu", [
    ("trente", 30), ("quinze", 15), ("douze", 12), ("un", 1), ("une", 1),
])
def test_les_nombres_simples_en_lettres(brut, attendu):
    assert lire_nombre(brut) == attendu


@pytest.mark.parametrize("brut,attendu", [
    ("quarante-cinq", 45),          # LE cas : lu « 5 » par le motif naïf
    ("vingt-et-un", 21),
    ("trente-six", 36),
    ("soixante-dix", 70),
    ("soixante-et-onze", 71),
    ("quatre-vingts", 80),
    ("quatre-vingt-dix", 90),
    ("quatre-vingt-dix-sept", 97),
    ("cent", 100), ("cent vingt", 120), ("deux cents", 200),
    ("mille", 1000), ("deux mille cinq cents", 2500),
])
def test_LES_COMPOSES(brut, attendu):
    """Mesuré sur corpus : « quarante-cinq jours » lu « 5 jours », 2 fois sur 146.

    Les irrégularités du français sont ici : `quatre-vingt` où « quatre » ne vaut pas 4,
    et `soixante-dix` où « soixante » se combine au lieu de s'ajouter.
    """
    assert lire_nombre(brut) == attendu


# ── Le contrat : None plutôt qu'une valeur à moitié lue ─────────────────────


@pytest.mark.parametrize("brut", [
    "gazillion", "trente gazillions", "quarante-douzaine", "", None, "   ",
    "un texte quelconque",
])
def test_rend_None_plutot_qu_une_valeur_a_moitie_lue(brut):
    """C'est TOUT l'intérêt du portage.

    Une valeur partielle est pire qu'une absence : elle se compare, elle s'affiche, et
    personne ne sait qu'elle est fausse.
    """
    assert lire_nombre(brut) is None


def test_un_seul_jeton_inconnu_annule_la_lecture_entiere():
    """« deux mille gazillions » ne vaut pas 2000 : il ne vaut rien."""
    assert lire_nombre("deux mille gazillions") is None


# ── Les grandeurs : un nombre PORTANT une unité ─────────────────────────────


def test_un_nombre_nu_n_est_pas_une_grandeur():
    """« article 11 », « en trois exemplaires » ne se comparent pas d'un texte à
    l'autre. Les compter ferait du bruit sans rien apprendre.
    """
    assert grandeurs("l'article 11 en trois exemplaires") == []


@pytest.mark.parametrize("texte,nature,nombre", [
    ("un délai de trente jours", "duree", 30),
    ("quarante-cinq jours ouvrés", "duree", 45),
    ("25 000 euros hors taxes", "montant", 25000),
    ("cinq mille euros", "montant", 5000),
    ("une avance de 30 %", "taux", 30),
    ("douze mois", "duree", 12),
])
def test_les_grandeurs_sont_lues_avec_leur_nature(texte, nature, nombre):
    g = grandeurs(texte)
    assert g, f"aucune grandeur trouvée dans « {texte} »"
    assert g[0]["nature"] == nature
    assert g[0]["nombre"] == nombre


def test_le_chiffre_entre_parentheses_fait_foi():
    """« trente (30) jours » est courant en rédaction contractuelle.

    Sans cette règle on compterait DEUX grandeurs là où le texte n'en pose qu'une.
    """
    g = grandeurs("un délai de trente (30) jours")
    assert len(g) == 1
    assert g[0]["nombre"] == 30


def test_un_mot_quelconque_devant_une_unite_n_est_PAS_une_grandeur():
    """Décision de la source, reprise telle quelle — et mon test l'avait d'abord niée.

    « plusieurs mois », « les jours suivants », « gazillions jours » ne sont pas des
    grandeurs manquantes : ce sont des tournures. Les signaler produirait un flot
    d'alertes sur du texte parfaitement normal.

    La source traite bien un cas « illisible », mais c'est le TROU DE MODÈLE — « ___
    jours », « [à compléter] euros » : une valeur annoncée et absente, qui est un
    défaut de rédaction. Ce motif n'est PAS porté ici : un modèle de langue n'émet pas
    de gabarit à trous, et l'importer ajouterait une surface sans emploi.
    """
    assert grandeurs("un délai de gazillions jours") == []
    assert grandeurs("plusieurs mois") == []


def test_les_unites_ne_se_mangent_pas_entre_elles():
    """« ans » précède « mois » dans la liste : la première unité qui accroche gagne.

    Sinon « années » se ferait manger par un motif plus court, et la nature serait
    fausse sans que rien ne le signale.
    """
    g = grandeurs("deux ans et six mois")
    assert {x["nombre"] for x in g} == {2, 6}


def test_l_unite_ne_mord_pas_sur_un_mot_plus_long():
    """L'anti-regard empêche « mois » d'accrocher « moisissure »."""
    assert grandeurs("trois moisissures") == []


def test_le_pourcent_est_reconnu():
    """`\\b` ne s'accroche pas après « % », qui n'est pas un caractère de mot :
    « 5 % » ne matchait pas avec une frontière de mot classique.
    """
    g = grandeurs("une retenue de 5 %")
    assert g and g[0]["nature"] == "taux" and g[0]["nombre"] == 5


# ── Le remplacement de `montants()` ─────────────────────────────────────────


def test_montants_voit_les_lettres_que_l_ancien_motif_manquait():
    """LE défaut corrigé : 89 % des grandeurs du corpus sont écrites en lettres."""
    assert montants("le seuil est de quarante-cinq mille euros") == {"45000"}


def test_montants_voit_toujours_les_chiffres():
    assert montants("le seuil est de 25 000 euros") == {"25000"}


def test_montants_ne_rend_pas_ce_qu_il_n_a_pas_su_lire():
    """On ne compare pas ce qu'on n'a pas lu, et on ne l'invente pas.

    C'est exactement la faute que `montants_inventes` traque : rendre une valeur
    approchée reviendrait à la commettre dans l'outil qui la mesure.
    """
    assert montants("un montant de gazillions euros") == set()


def test_montants_ignore_les_durees_et_les_taux():
    assert montants("trente jours et 5 %") == set()


# ── L'ampleur du défaut, épinglée ───────────────────────────────────────────


def test_l_ancien_motif_ne_voyait_que_les_chiffres_groupes():
    """Épinglé pour que le gain reste mesurable.

    L'ancien motif était `\\d{1,3}( \\d{3})+` : il exigeait un groupement par milliers.
    « 25000 » sans espace lui échappait déjà, avant même la question des lettres.
    """
    import re

    ancien = re.compile(r"\b\d{1,3}(?:[  \xa0]\d{3})+\b")
    for texte in ("quarante-cinq mille euros", "25000 euros", "cinq mille euros"):
        assert not ancien.findall(texte), f"l'ancien motif voyait « {texte} »"
        assert montants(texte), f"le nouveau ne voit pas « {texte} »"
