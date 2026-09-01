"""
Le garde-fou ne doit pas casser une reponse qui cite correctement son corpus.

Mesure du 01/09/2026 — `_chantier/scripts/effet_garde_fou.py` rejoue
`garde_fou_reponse.appliquer()` sur les 179 reponses archivees du coeur : il attrape
23 reponses fautives sur 23, et n'en abime qu'une seule sur 156 saines. Cette
unique perte est **mp-013**, une reponse juste, remplacee par un refus. Elle citait
« Article 4.1 » du CCAG Travaux, c'est-a-dire exactement ce qu'on lui demandait.

`verification_citations` PREDIT ce defaut en commentaire depuis le 23/08/2026 : « Une
reponse citant le CCAG etait donc vue comme ne citant rien, et
`garde_fou_reponse.appliquer()` l'aurait remplacee par un refus ». Le module porte
deja le remede — `FORMAT_CLAUSE` et les identifiants litteraux — mais `appliquer()`
n'en transmet aucun a `verifier()`. Le garde-fou du produit est donc moins capable
que celui du harnais de mesure, qui lui passe le vocabulaire du corpus.

Ces tests figent les deux moities du contrat :

- ce qui est **reconnu** quand le corpus le declare — sans quoi le garde-fou detruit
  les bonnes reponses qu'il est cense proteger ;
- ce qui reste **attrape**, et ce qui reste **inactif par defaut** — sans quoi on
  echange un faux positif contre un faux negatif, et le garde-fou ne garde plus rien.
"""
from colaig.rag.garde_fou_reponse import appliquer
from colaig.rag.verification_citations import FORMAT_CLAUSE, FORMAT_CODE

# Reprise de mp-013, le seul faux positif mesure sur 156 reponses saines.
REPONSE_CCAG = (
    "Le CCAG Travaux precise l'ordre de priorite des pieces contractuelles a son "
    "**Article 4.1**. En cas de contradiction entre les stipulations, elles "
    "prevalent dans l'ordre suivant : l'acte d'engagement, puis le CCAP."
)
PASSAGE_CCAG = (
    "CCAG Travaux - Chapitre 1er : Generalites\n\n"
    "Article 4.1\n\n"
    "En cas de contradiction entre les stipulations des pieces contractuelles, "
    "celles-ci prevalent dans l'ordre suivant : l'acte d'engagement et ses annexes "
    "financieres, le cahier des clauses administratives particulieres."
)


def test_une_citation_de_ccag_ne_fait_pas_remplacer_la_reponse():
    """Le cas mp-013 : une reponse juste ne doit pas devenir un refus.

    C'est la perte la plus grave que puisse causer un garde-fou : l'utilisateur
    recoit un refus la ou le corpus contenait la reponse, et rien ne le lui dit.
    """
    decision = appliquer(REPONSE_CCAG, [PASSAGE_CCAG],
                         formats=(FORMAT_CODE, FORMAT_CLAUSE))
    assert decision.action != "remplacée", decision.motif


def test_le_format_clause_reste_inactif_par_defaut():
    """Un nombre a point n'est pas une citation partout.

    Sur un fonds de procedures ou de notes, « 2.5 » est un taux, une version, une
    date. Actif partout, ce format declarerait ancrees des reponses qui ne le sont
    pas — le garde-fou serait alors pire qu'absent. Le format est une propriete du
    corpus, pas du code.
    """
    decision = appliquer(REPONSE_CCAG, [PASSAGE_CCAG])
    assert decision.action == "remplacée"


def test_un_identifiant_du_corpus_est_reconnu():
    """Les CCAG et les annexes ne numerotent pas selon un motif.

    « CCAG Travaux 4 » n'est descriptible par aucune expression reguliere honnete.
    Mais le corpus le porte en en-tete : on le cherche donc tel quel, ce qui est
    exact et sans faux positif possible.
    """
    decision = appliquer(
        "L'ordre de priorite figure a l'article CCAG Travaux 4.",
        ["Article CCAG Travaux 4\n\nEn cas de contradiction entre les stipulations."],
        identifiants={"CCAG Travaux 4"},
    )
    assert decision.action == "rendue", decision.motif


def test_une_citation_hors_des_passages_reste_attrapee():
    """Elargir ce qui est reconnu ne doit pas relacher ce qui est garde.

    Sans ce test, la correction du faux positif pourrait rendre le garde-fou
    permissif — et c'est precisement le signal pour lequel il existe.
    """
    decision = appliquer(
        "Le delai est fixe par l'article L2113-10.",
        ["Article R2161-1\n\nL'acheteur peut recourir a une procedure adaptee."],
        formats=(FORMAT_CODE, FORMAT_CLAUSE),
    )
    assert decision.action == "remplacée", decision.motif
