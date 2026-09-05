"""
Colaig — les espaces réservés ne sont pas des citations
(campagne d'usage réel du 29/08/2026, défaut C).

Ce que la campagne a montré
-----------------------------
    citation_checker: 1 citation(s) sans source correspondante: ['lien']
    citation_checker: 4 citation(s) sans source: ['espace', "nom de l'espace", ...]

Ce sont des **crochets que Colaig a écrits lui-même** — « remplacez `[nom de l'espace]`
par... » — relus comme des citations documentaires sans source.

Deux causes se cumulaient :

1. `_looks_like_ref` ne retenait qu'un critère — « contient une lettre ». Toute phrase
   entre crochets passait donc pour une référence.
2. Quand **aucune source** n'est fournie — le cas nominal en conversation directe, où
   `rag_enabled` est faux — la comparaison classait *tout* en « sans source ».

Conséquence mesurable, et c'est ce qui la rend non cosmétique : `audit_and_adjust`
**pénalise la confiance de 30 %** sur ces fausses détections. Une réponse correcte
était dégradée parce qu'elle contenait un exemple entre crochets.

Ce que la correction ne doit pas perdre
-----------------------------------------
Un nom de fichier cité sans source reste une hallucination à signaler. C'est la raison
d'être du vérificateur, et le mode « aucune source » est justement celui où inventer
un document serait le plus trompeur.
"""

from __future__ import annotations

from colaig.security.citation_checker import audit_and_adjust, check_citations


# ─────────────────────────────────────────────────────────────────────────────
# Ce qui n'est pas une citation
# ─────────────────────────────────────────────────────────────────────────────


def test_un_espace_reserve_n_est_pas_une_citation():
    """Le cas exact relevé sur le fil."""
    texte = ("Utilisez `colaig lier [nom de l'espace]` puis ouvrez [lien] "
             "pour rejoindre l'[espace].")

    resultat = check_citations(texte, sources=[])

    assert resultat["ungrounded"] == [], (
        f"espaces réservés pris pour des citations : {resultat['ungrounded']}"
    )
    assert resultat["all_grounded"] is True


def test_une_phrase_entre_crochets_n_est_pas_une_citation():
    """Une consigne, une glose, une incise : rien de tout cela ne désigne un document."""
    texte = "La réponse [voir ci-dessus] dépend de votre configuration [par défaut]."

    assert check_citations(texte, sources=["note.md"])["ungrounded"] == []


def test_la_confiance_n_est_plus_penalisee_par_ses_propres_exemples():
    """L'effet mesurable : 30 % de confiance perdus sur une réponse correcte."""
    texte = "Tapez `colaig créer [nom de l'espace]`."

    assert audit_and_adjust(texte, sources=[], confidence=0.9) == 0.9


# ─────────────────────────────────────────────────────────────────────────────
# Ce qui reste une citation — le signal à ne pas perdre
# ─────────────────────────────────────────────────────────────────────────────


def test_un_nom_de_fichier_invente_est_toujours_signale():
    """Sans cela, la correction supprimerait le vérificateur au lieu de l'affiner."""
    texte = "D'après [rapport_annuel_2024.pdf], le seuil est de 40 000 euros."

    resultat = check_citations(texte, sources=["autre_document.pdf"])

    assert resultat["ungrounded"] == ["rapport_annuel_2024.pdf"]
    assert resultat["all_grounded"] is False


def test_un_nom_de_fichier_invente_est_signale_meme_sans_aucune_source():
    """Le cas le plus trompeur : citer un document quand il n'y en avait aucun."""
    resultat = check_citations("Voir [note_interne.docx].", sources=[])

    assert resultat["ungrounded"] == ["note_interne.docx"]


def test_un_chemin_est_une_citation():
    """Une source peut être citée par son chemin autant que par son nom."""
    resultat = check_citations("Voir [dossiers/marches/cctp].", sources=[])

    assert resultat["ungrounded"] == ["dossiers/marches/cctp"]


def test_une_source_reellement_fournie_reste_reconnue():
    """Un titre sans extension compte s'il correspond à une source annoncée.

    C'est ce qui permet de garder le critère strict sans perdre les citations justes.
    """
    resultat = check_citations("D'après [Rapport annuel], le seuil a changé.",
                               sources=["Rapport annuel"])

    assert resultat["grounded"] == ["Rapport annuel"]
    assert resultat["all_grounded"] is True


def test_une_citation_sourcee_ne_penalise_pas_la_confiance():
    assert audit_and_adjust("Voir [note.md].", sources=["note.md"],
                            confidence=0.8) == 0.8
