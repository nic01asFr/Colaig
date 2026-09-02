"""
Reconnaître un refus : deux familles, une seule liste.

CE QUI A ÉTÉ MESURÉ, le 02/09/2026
-----------------------------------
Le compteur de refus de la référence ne reconnaissait qu'une façon de refuser :
dire que **l'information manque**. Or 7 des 22 cas négatifs du jeu doré — un tiers
— reposent sur une **prémisse inexacte**, qui ne se réfute pas ainsi.

Sur mp-098, la réponse attendue est « le code ne fixe aucun maximum ». Le pipeline
répond « Le Code de la commande publique **ne fixe pas de limite légale générale** ».
C'est la réponse attendue, et elle était comptée comme échec.

L'effet est **asymétrique**, ce qui est le pire cas pour une comparaison : ajouter la
famille « prémisse » fait gagner **2 cas au pipeline et 0 au cœur**. Le cœur préfixe en
effet toutes ses réponses de « Cette information ne figure pas dans les passages
fournis », y compris sur les cas de prémisse — où c'est d'ailleurs inexact, l'information
ne manquant pas.

Manquaient aussi les **pluriels** : « ne figure pas » ET « ne figurent pas » étaient là,
mais seulement « ne contient pas », pas « ne contiennent pas » — que le pipeline écrit
naturellement, ses phrases ayant « les passages » pour sujet.

UNE SEULE LISTE, ET C'EST LE POINT
-----------------------------------
Le harnais et le garde-fou jugeaient « est-ce un refus » avec deux listes distinctes.
Ce dépôt a déjà payé cinq fois la copie d'un motif. La liste vit donc dans le produit,
et la mesure l'importe.
"""
from colaig.rag.garde_fou_reponse import (
    MARQUEURS_ABSENCE,
    MARQUEURS_PREMISSE,
    _est_un_refus,
)


def test_les_deux_familles_sont_distinctes_et_non_vides():
    """Elles ne se recouvrent pas : refuser faute d'information n'est pas réfuter."""
    assert MARQUEURS_ABSENCE and MARQUEURS_PREMISSE
    assert not set(MARQUEURS_ABSENCE) & set(MARQUEURS_PREMISSE)


def test_un_refus_par_absence_au_pluriel_est_reconnu():
    """« les passages ne contiennent pas » — la forme naturelle du synthétiseur."""
    assert _est_un_refus("Les passages fournis ne contiennent pas la liste des services.")
    assert _est_un_refus("Les documents ne mentionnent pas ce taux.")


def test_une_refutation_de_premisse_est_reconnue():
    """Le cas mp-098, mot pour mot ce que le jeu doré attend."""
    assert _est_un_refus(
        "Le Code de la commande publique ne fixe pas de limite légale générale au "
        "nombre maximal de lots qu'un opérateur peut se voir attribuer.")
    assert _est_un_refus("Le code n'impose aucun formalisme de négociation.")


def test_une_reponse_qui_repond_n_est_pas_prise_pour_un_refus():
    """L'autre moitié, et elle décide de tout.

    `garde_fou_reponse.appliquer()` rend telle quelle une réponse jugée « refus assumé ».
    Un marqueur trop large rendrait donc le garde-fou permissif sur les réponses mêmes
    qu'il doit contrôler — on échangerait un faux positif contre un faux négatif.
    """
    assert not _est_un_refus(
        "Le montant de la retenue de garantie ne peut être supérieur à 5 % du montant "
        "initial du marché, conformément à l'article R2191-33.")
    assert not _est_un_refus(
        "Le CCAG Travaux fixe une pénalité journalière de 1/3 000 à l'article 19.2.3.")
    assert not _est_un_refus(
        "L'allotissement est le principe, selon l'article L2113-10.")


def test_le_harnais_de_mesure_partage_la_meme_liste():
    """Deux listes divergentes feraient mesurer autre chose que ce qui est décidé."""
    import sys
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(racine / "_chantier" / "scripts"))
    import reference_generation as harnais

    assert set(harnais.MARQUEURS_REFUS) == set(MARQUEURS_ABSENCE) | set(MARQUEURS_PREMISSE)
