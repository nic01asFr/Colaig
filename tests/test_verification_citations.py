"""
Contrat — la provenance des citations est vérifiée mécaniquement.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Mesuré sur 45 cas du jeu doré, avec un prompt système qui interdisait déjà d'inventer :
**10 réponses citaient un article réel absent des passages fournis**.

Une consigne se respecte « la plupart du temps ». Sur du droit, la plupart du temps ne
suffit pas — d'où un contrôle qui ne dépend pas du bon vouloir du modèle.
"""
from __future__ import annotations

import pytest

from colaig.rag.verification_citations import annoter, articles_cites, verifier

PASSAGE_1 = "Titre Ier\n\nArticle L2113-10\n\nLes marchés sont passés en lots séparés."
PASSAGE_2 = "Titre II\n\nArticle R2122-8\n\nL'acheteur peut passer un marché sans publicité."


# ── Reconnaissance des références ───────────────────────────────────────────


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("Selon L2113-10, les marchés sont allotis.", {"L2113-10"}),
        ("Voir l'article L. 2113-10 du code.", {"L2113-10"}),
        ("Les articles R. 2122-8 et D2392-2 s'appliquent.", {"R2122-8", "D2392-2"}),
        ("L'article R2122-9-1 vise les marchés innovants.", {"R2122-9-1"}),
        ("Aucune référence ici.", set()),
    ],
)
def test_les_graphies_usuelles_sont_reconnues(texte, attendu):
    """« L2113-10 », « L. 2113-10 », « article R. 2122-8 » désignent le même article.

    Un contrôle qui ne reconnaîtrait qu'une graphie laisserait passer les autres — et
    le modèle en emploie plusieurs dans une même réponse.
    """
    assert articles_cites(texte) == attendu


# ── Le contrôle ─────────────────────────────────────────────────────────────


def test_une_reponse_fidele_est_conforme():
    v = verifier("D'après L2113-10, l'allotissement est le principe.", [PASSAGE_1, PASSAGE_2])
    assert v.conforme
    assert v.hors_contexte == set()
    assert v.avertissement() == ""


def test_une_citation_hors_passages_est_detectee():
    """Le cas mesuré dix fois : un article réel, mais qui ne vient pas du corpus."""
    v = verifier("Voir L2113-10 et aussi L1414-3 du code.", [PASSAGE_1])
    assert not v.conforme
    assert v.hors_contexte == {"L1414-3"}


def test_le_cas_le_plus_insidieux_est_detecte():
    """Bonne réponse, mauvaise provenance.

    Une réponse a cité l'article **exact attendu** alors qu'il n'était pas dans les
    passages. Elle était juste — et elle le restera jusqu'au jour où le texte changera
    sans que le corpus ait été relu. La justesse ne prouve rien sur la provenance ;
    seul ce contrôle la révèle.
    """
    v = verifier("Le délai est de onze jours (R2182-1).", [PASSAGE_1, PASSAGE_2])
    assert not v.conforme, "une citation juste mais non fournie doit être signalée"
    assert v.hors_contexte == {"R2182-1"}


def test_aucune_citation_est_conforme():
    """Un refus bien formé ne cite rien — il ne doit pas être pénalisé."""
    v = verifier("Cette information ne figure pas dans les passages fournis.", [PASSAGE_1])
    assert v.conforme


# ── L'annotation ────────────────────────────────────────────────────────────


def test_annoter_laisse_la_reponse_intacte_si_conforme():
    reponse = "D'après L2113-10, l'allotissement est le principe."
    annotee, v = annoter(reponse, [PASSAGE_1])
    assert annotee == reponse
    assert v.conforme


def test_annoter_signale_sans_supprimer():
    """**Annoter plutôt que supprimer.**

    Retirer la référence rendrait la réponse plus propre et moins vérifiable :
    l'utilisateur perdrait justement ce qui lui permet de contrôler. La mention lui
    laisse la décision — c'est lui qui engage sa procédure.
    """
    reponse = "Voir L2113-10 et L1414-3."
    annotee, v = annoter(reponse, [PASSAGE_1])
    assert reponse in annotee, "la réponse d'origine doit rester lisible en entier"
    assert "L1414-3" in annotee
    assert "non vérifiable" in annotee
    assert "mémoire du modèle" in annotee


def test_l_avertissement_s_accorde_en_nombre():
    une, _ = annoter("Voir L1414-3.", [PASSAGE_1])
    plusieurs, _ = annoter("Voir L1414-3 et L1414-4.", [PASSAGE_1])
    assert "Référence non vérifiable" in une.split("---")[-1]
    assert "Références non vérifiables" in plusieurs.split("---")[-1]


def test_le_controle_sait_echouer():
    """Un garde-fou dont on n'a pas vu le rouge ne prouve rien.

    Quatrième fois que ce réflexe sert dans ce chantier — les trois précédentes, un
    contrôle était vert pour une mauvaise raison.
    """
    conforme = verifier("Selon L2113-10.", [PASSAGE_1])
    fautif = verifier("Selon L9999-1.", [PASSAGE_1])
    assert conforme.conforme and not fautif.conforme
    assert fautif.avertissement() != "" and conforme.avertissement() == ""
