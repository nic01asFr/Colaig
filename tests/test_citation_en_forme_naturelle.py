"""« L'article 4.1 du CCAG Travaux » est une citation de « CCAG Travaux 4 ».

CE QUI A CONDUIT ICI
----------------------
Fin du lot L4.1 : l'article attendu est SERVI dans 102 cas dores sur 113 et CITE dans
92. Les dix cas restants devaient designer le Synthetiseur. En les lisant, cinq au
moins sont des reponses JUSTES que le compteur ne reconnait pas :

    mp-013  attendu « CCAG Travaux 4 »   -> « l'article 4.1 du CCAG Travaux »
    mp-125  attendu « CCAG Travaux 4 »   -> « l'article 4.1 du CCAG Travaux »
    mp-127  attendu « CCAG Travaux 41 »  -> « l'article 41.1 du CCAG Travaux »
    mp-121  attendu « Annexe 2 — Seuils de procedure — texte 1 »
                                         -> « l'Article Annexe 2 — Seuils de procedure »

Le corpus NOMME ses articles « CCAG Travaux 4 » parce que c'est ainsi qu'il les
indexe. Un redacteur, lui, ecrit « l'article 4.1 du CCAG Travaux » — la forme du
metier. La recherche litterale ne rapproche pas les deux, et une reponse exacte compte
pour un echec.

CE N'EST PAS QU'UN DEFAUT DE MESURE. `articles_cites` sert aussi au garde-fou : une
citation qu'il ne voit pas est une reponse qu'il peut annoter « non verifiable », donc
affaiblir, alors qu'elle est sourcee.

LE FAUX POSITIF QU'IL FAUT EVITER
-----------------------------------
Le corpus porte quatre CCAG paralleles dont les articles portent les MEMES numeros.
Une reponse qui cite « l'article 3 du CCAG Prestations intellectuelles » ne cite pas
« CCAG Travaux 3 ». Le rapprochement doit donc exiger le nom, a proximite immediate,
et refuser quand un autre nom s'interpose.
"""

from __future__ import annotations

import pytest

from colaig.rag.verification_citations import articles_cites

CORPUS = (
    "CCAG Travaux 4",
    "CCAG Travaux 41",
    "CCAG Travaux 3",
    "CCAG Prestations intellectuelles 3",
    "CCAG Fournitures et services 4",
    "Annexe 2 — Seuils de procédure — texte 1",
    "L2113-10",
)


def cites(texte):
    return articles_cites(texte, identifiants=CORPUS)


def test_la_forme_du_corpus_reste_reconnue():
    assert "CCAG Travaux 4" in cites("Voir CCAG Travaux 4 pour l'ordre des pièces.")


def test_l_article_suivi_de_son_cahier():
    assert "CCAG Travaux 4" in cites(
        "L'ordre de priorité est fixé par l'**article 4.1** du CCAG Travaux.")


def test_le_sous_article_remonte_a_son_article():
    """« 41.1 » appartient a l'article 41, pas a l'article 4."""
    trouves = cites("Selon l'article 41.1 du CCAG Travaux, le délai est de vingt jours.")
    assert "CCAG Travaux 41" in trouves
    assert "CCAG Travaux 4" not in trouves


def test_le_cahier_annonce_avant_l_article():
    assert "CCAG Travaux 3" in cites(
        "Le CCAG Travaux prévoit, à son article 3, la forme de la notification.")


def test_un_autre_cahier_ne_compte_pas_pour_celui_la():
    """Quatre CCAG paralleles portent les memes numeros d'article."""
    trouves = cites("L'article 3 du CCAG Prestations intellectuelles règle la notification.")
    assert "CCAG Prestations intellectuelles 3" in trouves
    assert "CCAG Travaux 3" not in trouves


def test_le_nom_doit_etre_proche():
    """Sans proximite, tout texte mentionnant un cahier citerait tous ses articles."""
    texte = ("Le CCAG Travaux s'applique. " + "Un long developpement sans rapport. " * 6
             + "Par ailleurs, l'article 4 d'un autre texte dispose que…")
    assert "CCAG Travaux 4" not in cites(texte)


def test_l_annexe_sans_son_suffixe_de_corpus():
    """« — texte 1 » est un artefact du decoupage, pas une part du nom."""
    assert "Annexe 2 — Seuils de procédure — texte 1" in cites(
        "Ce montant est fixé par l'Article Annexe 2 — Seuils de procédure du code.")


def test_le_code_juridique_est_inchange():
    trouves = cites("L'article L2113-10 impose l'allotissement.")
    assert trouves == {"L2113-10"}


def test_un_numero_isole_ne_cite_rien():
    """« article 4 » sans cahier nomme ne designe aucun des quatre CCAG."""
    assert cites("L'article 4 prévoit une exception.") == set()
