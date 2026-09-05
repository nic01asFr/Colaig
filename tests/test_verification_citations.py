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


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("Selon L2, un marché est un contrat conclu à titre onéreux.", {"L2"}),
        ("L'article L. 3 énonce les principes.", {"L3"}),
        ("Voir L. 3-1 pour les concessions.", {"L3-1"}),
        ("Les articles L1 à L6 forment le titre préliminaire.", {"L1", "L6"}),
    ],
)
def test_les_articles_preliminaires_sont_reconnus(texte, attendu):
    """`L1` à `L6` définissent *marché*, *marché public*, *acheteur*.

    Ce sont les plus cités par un assistant à la rédaction, et le motif d'origine —
    quatre chiffres obligatoires — ne les voyait pas. L'angle mort n'était pas une
    lacune passive : voir `test_une_reponse_fondee_sur_un_article_preliminaire_survit`.
    """
    assert articles_cites(texte) == attendu


def test_ce_qui_n_est_pas_une_reference_ne_l_est_pas_devenu():
    """Élargir un motif, c'est risquer de tout attraper. Contrôle du revers.

    Sans ce test, `L1` reconnu partout ferait passer des fragments de prose pour des
    citations — et le garde-fou déclarerait « ancrées » des réponses qui ne le sont pas.
    """
    assert articles_cites("5 lots pour 3 candidats et 2 tranches") == set()
    assert articles_cites("le lot n° 4 et la tranche 2") == set()
    assert articles_cites("aucune référence ici") == set()


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


# ── Le format de citation est une propriété du corpus ───────────────────────


def test_une_citation_de_cahier_de_clauses_est_invisible_par_defaut():
    """Et c'est voulu — mais il fallait le savoir avant d'ajouter un tel corpus.

    Les CCAG numérotent « article 20.1 ». Le motif par défaut est celui des codes
    juridiques, donc une réponse citant le CCAG serait vue comme ne citant **rien**,
    et `garde_fou_reponse` la remplacerait par un refus. C'est le mode de défaillance
    déjà corrigé pour les articles préliminaires, transposé à un autre corpus.
    """
    assert articles_cites("Voir l'article 20.1 du CCAG Travaux.") == set()


def test_le_format_clause_reconnait_les_cahiers():
    from colaig.rag.verification_citations import FORMAT_CLAUSE, FORMAT_CODE

    deux = (FORMAT_CODE, FORMAT_CLAUSE)
    assert articles_cites("Voir l'article 20.1 du CCAG.", deux) == {"20.1"}
    assert articles_cites("L'article 46.2.3 précise.", deux) == {"46.2.3"}
    assert articles_cites("L2113-10 et l'article 20.1", deux) == {"L2113-10", "20.1"}


def test_pourquoi_le_format_clause_n_est_pas_actif_partout():
    """La mesure qui justifie le défaut, et qui doit rester lisible.

    Sur le corpus du code, ce motif relève 188 occurrences, **toutes** « 2.0 » — la
    mention « Licence Ouverte 2.0 » du pied de page. Inoffensif là ; mais sur un fonds
    de procédures, « 2.5 » est un taux ou un numéro de version. Un contrôle qui prend
    des fragments pour des citations déclare ancrées des réponses qui ne le sont pas :
    il est alors **pire qu'absent**.
    """
    from colaig.rag.verification_citations import FORMAT_CLAUSE, FORMAT_CODE

    prose = "Le taux passe de 2.5 à 3.5 selon la version 1.2 du document."
    assert articles_cites(prose) == set(), "le défaut ne doit rien voir dans cette prose"
    assert articles_cites(prose, (FORMAT_CODE, FORMAT_CLAUSE)) == {"2.5", "3.5", "1.2"}


def test_les_deux_cotes_du_controle_emploient_le_meme_format():
    """Reconnaître une graphie dans la réponse et pas dans les passages est le défaut
    qui a déjà fait conclure à tort que le modèle puisait dans sa mémoire.
    """
    from colaig.rag.verification_citations import FORMAT_CLAUSE, FORMAT_CODE

    deux = (FORMAT_CODE, FORMAT_CLAUSE)
    passage = "Article 20.1 — Le titulaire dispose d'un délai de trente jours."
    assert verifier("Selon 20.1, le délai est de trente jours.", [passage], deux).conforme
    # Avec le format par défaut, ni la réponse ni le passage ne portent de citation :
    # le contrôle reste cohérent, il ne voit simplement rien.
    v = verifier("Selon 20.1, le délai est de trente jours.", [passage])
    assert v.citations == set() and v.fournies == set()


def test_une_reference_de_section_n_est_pas_une_citation_d_article():
    """« les articles R2161 et suivants » désigne une section, pas un article.

    Le corpus ne compte que six articles en forme courte — L1 à L6, plus L3-1. Tout
    autre porte quatre chiffres **et** un tiret. Un nombre à quatre chiffres nu est
    donc une section, et cette façon d'écrire est correcte.

    Mesuré : quatre des dix « fantômes » annoncés sur 124 cas étaient de cette nature.
    Une métrique qui signale comme invention une manière correcte d'écrire gonfle son
    propre compte et perd la confiance qu'on lui accorde.
    """
    assert articles_cites("Voir les articles R2161 et suivants.") == set()
    assert articles_cites("La sous-section R2113 traite de l'allotissement.") == set()


def test_les_formes_courtes_restent_reconnues_dans_les_deux_sens():
    """Nécessaire pour le vrai comme pour le faux.

    `L2` définit le marché public — c'est un vrai article. `L30` a été cité par une
    réponse mesurée et n'existe pas — c'est une vraie invention. Le contrôle doit voir
    les deux, et il ne peut pas les distinguer par la forme : c'est la comparaison au
    corpus qui tranche, pas le motif.
    """
    assert articles_cites("Selon L2, un marché est un contrat.") == {"L2"}
    assert articles_cites("Voir l'article L30 du code.") == {"L30"}
    assert articles_cites("L. 511-16 du code monétaire") == {"L511-16"}


# ── Les identifiants propres au corpus ──────────────────────────────────────

IDENTIFIANTS = {"CCAG Travaux 4", "CCAG Travaux 41", "CCAG Travaux — texte 1",
                "Annexe 2 — Seuils de procédure — texte 1"}


def test_un_identifiant_de_corpus_est_reconnu_litteralement():
    """Certains corpus ne numérotent pas selon un motif.

    « CCAG Travaux 4 », « Annexe 2 — Seuils de procédure — texte 1 » : aucune
    expression régulière ne les décrit, et en écrire une qui les couvre attraperait la
    moitié de la prose. Mais ils sont **connus** — le corpus les porte en en-tête — et
    les chercher tels quels est exact par construction.
    """
    assert articles_cites("Selon CCAG Travaux 4, l'acte d'engagement prime.",
                          identifiants=IDENTIFIANTS) == {"CCAG Travaux 4"}
    assert articles_cites("Voir Annexe 2 — Seuils de procédure — texte 1.",
                          identifiants=IDENTIFIANTS) == {"Annexe 2 — Seuils de procédure — texte 1"}


def test_un_identifiant_n_est_pas_reconnu_dans_un_plus_long():
    """Le piège du préfixe, et il produirait une citation fausse à partir d'une juste.

    Sans frontière de fin, « CCAG Travaux 4 » se retrouve à l'intérieur de « CCAG
    Travaux 41 » : une réponse citant correctement l'article 41 se verrait attribuer
    aussi l'article 4, qu'elle ne cite pas — et le contrôle de provenance jugerait
    ensuite une citation qui n'existe pas.
    """
    trouves = articles_cites("La réception relève de CCAG Travaux 41.",
                             identifiants=IDENTIFIANTS)
    assert trouves == {"CCAG Travaux 41"}


def test_les_deux_voies_se_cumulent():
    """Un corpus mixte cite dans les deux vocabulaires, souvent dans la même phrase."""
    trouves = articles_cites("R2112-3 impose de le signaler, et CCAG Travaux 4 en donne l'effet.",
                             identifiants=IDENTIFIANTS)
    assert trouves == {"R2112-3", "CCAG Travaux 4"}


def test_sans_vocabulaire_rien_n_est_invente():
    """La voie littérale ne cherche que ce qu'on lui donne : pas de vocabulaire, pas de
    citation. C'est ce qui la rend sans faux positif possible.
    """
    assert articles_cites("Selon CCAG Travaux 4, l'acte d'engagement prime.") == set()
