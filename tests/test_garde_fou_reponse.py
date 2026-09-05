"""
Contrat — le refus se construit, il ne se demande pas.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Mesuré sur le jeu doré, cas négatifs répétés trois fois :

| consigne | refuse aux 3 exécutions |
|---|---|
| prompt de l'espace | **0/8** |
| prompt **durci** | **3/8** |

Le durcissement améliore, et ne suffit pas. **Un comportement intermittent n'est pas un
comportement** : cinq cas sur huit refusent parfois ou jamais, et l'utilisateur ne peut
pas savoir dans quel cas il se trouve.

Ce que ces tests fixent, c'est le comportement du garde-fou lui-même — entièrement
déterministe, contrairement à ce qu'il encadre.
"""
from __future__ import annotations

import pytest

from colaig.rag.garde_fou_reponse import REFUS_TYPE, appliquer

PASSAGES = [
    "Titre Ier\n\nArticle L2113-10\n\nLes marchés sont passés en lots séparés.",
    "Titre II\n\nArticle R2122-8\n\nDispense en dessous de 60 000 euros hors taxes.",
]


# ── Ce qui passe sans être touché ───────────────────────────────────────────


def test_une_reponse_entierement_ancree_est_rendue_telle_quelle():
    reponse = "D'après L2113-10, l'allotissement est le principe."
    d = appliquer(reponse, PASSAGES)
    assert d.action == "rendue"
    assert d.reponse == reponse
    assert d.fiable


def test_un_refus_du_modele_est_respecte():
    """Ne pas remplacer un refus authentique par un refus fabriqué.

    Le modèle explique souvent **où chercher** ; écraser sa réponse ferait perdre cette
    indication, qui est précisément ce dont a besoin quelqu'un qui rédige.
    """
    reponse = ("Cette information ne figure pas dans les passages fournis. "
               "Les seuils européens sont fixés par un avis annexé au code.")
    d = appliquer(reponse, PASSAGES)
    assert d.action == "rendue"
    assert "avis annexé" in d.reponse, "l'indication du modèle doit survivre"


def test_une_reference_ecrite_avec_un_point_est_reconnue():
    """« L. 2113-10 » et « L2113-10 » désignent le même article.

    **53,7 % des références du corpus sont écrites avec un point.** Une reconnaissance
    qui l'ignorerait signalerait hors contexte la moitié des citations légitimes — c'est
    exactement le défaut qui avait faussé la première mesure du palier génération.
    """
    d = appliquer("Voir l'article L. 2113-10.", PASSAGES)
    assert d.action == "rendue", d.motif


def test_une_reponse_fondee_sur_un_article_preliminaire_survit():
    """Le garde-fou détruisait la bonne réponse qu'il était censé protéger.

    Tant que le motif exigeait quatre chiffres, une réponse citant `L2` — l'article qui
    **définit le marché public** — était vue comme ne citant rien. `appliquer()` la
    remplaçait alors par un refus, au motif d'une « réponse sans aucune référence aux
    passages », alors qu'elle était entièrement fondée.

    C'est le pire mode de défaillance possible pour un garde-fou : silencieux, et dirigé
    contre les réponses justes. Découvert en indexant le corpus pour étendre le jeu doré
    — 1754 articles indexés sur 1762 annoncés, et les 8 manquants étaient précisément
    ceux-là.
    """
    passages = [
        "Titre préliminaire" + chr(10) * 2 + "Article L2" + chr(10) * 2 +
        "Un marché est un contrat conclu à titre onéreux par un acheteur "
        "avec un ou plusieurs opérateurs économiques.",
    ]
    d = appliquer("D'après L2, le marché suppose un contrat à titre onéreux.", passages)
    assert d.action == "rendue", f"{d.action} — {d.motif}"
    assert d.fiable


# ── Ce qui est annoté ───────────────────────────────────────────────────────


def test_une_reponse_partiellement_ancree_est_annotee():
    """Elle garde une base : on signale, on ne détruit pas."""
    reponse = "D'après L2113-10 et L1414-3, l'allotissement s'impose."
    d = appliquer(reponse, PASSAGES)
    assert d.action == "annotée"
    assert reponse in d.reponse, "le texte d'origine doit rester lisible"
    assert "L1414-3" in d.reponse
    assert not d.fiable


# ── Ce qui est remplacé ─────────────────────────────────────────────────────


def test_une_reponse_sans_aucune_attache_est_remplacee():
    """Le seul cas où l'on retire quelque chose, et il est délibéré.

    Une affirmation de droit dont **aucune** référence ne provient des documents
    consultés n'est pas une réponse incomplète : c'est une réponse sans fondement.
    """
    d = appliquer("Le seuil est fixé par L9999-1 et R8888-2.", PASSAGES)
    assert d.action == "remplacée"
    assert d.reponse == REFUS_TYPE
    assert "L9999-1" not in d.reponse


def test_une_reponse_sans_aucune_reference_est_remplacee():
    """Sur un corpus juridique, une affirmation sans référence n'est pas utilisable.

    Celui qui rédige devra la justifier devant un contrôle ; une phrase sans article
    ne l'y aidera pas, et lui donnera à tort l'impression d'avoir une réponse.
    """
    d = appliquer("Le seuil applicable est de 100 000 euros hors taxes.", PASSAGES)
    assert d.action == "remplacée"
    assert d.reponse == REFUS_TYPE


def test_le_refus_fabrique_dit_pourquoi():
    """Un refus qui n'explique pas laisse l'utilisateur sans recours."""
    d = appliquer("Le seuil est fixé par L9999-1.", PASSAGES)
    assert "ne figure pas dans les documents consultés" in d.reponse
    assert "non vérifiables" in d.reponse or "vérifiables" in d.reponse


# ── Le garde-fou sait échouer et sait s'abstenir ────────────────────────────


def test_le_garde_fou_ne_declenche_pas_a_tort():
    """Cinq réponses légitimes, aucune ne doit être remplacée ni annotée."""
    legitimes = [
        "L2113-10 pose le principe de l'allotissement.",
        "Voir R2122-8 : la dispense s'arrête à 60 000 € HT.",
        "Selon l'article L. 2113-10, les lots sont séparés.",
        "Les articles L2113-10 et R2122-8 se combinent.",
        "Cette information ne figure pas dans les passages fournis.",
    ]
    for reponse in legitimes:
        d = appliquer(reponse, PASSAGES)
        assert d.action == "rendue", f"{reponse!r} → {d.action} ({d.motif})"


def test_le_garde_fou_sait_declencher():
    """Un garde-fou dont on n'a pas vu le rouge ne prouve rien.

    Cinquième fois que ce réflexe sert dans ce chantier.
    """
    fautives = [
        ("Le seuil est fixé par L9999-1.", "remplacée"),
        ("Aucune référence, juste une affirmation.", "remplacée"),
        ("L2113-10 et L9999-1 s'appliquent.", "annotée"),
    ]
    for reponse, attendu in fautives:
        d = appliquer(reponse, PASSAGES)
        assert d.action == attendu, f"{reponse!r} → {d.action}, attendu {attendu}"


def test_passages_vides():
    """Aucun passage remonté : rien ne peut être affirmé."""
    d = appliquer("Le seuil est de 100 000 euros selon L2113-10.", [])
    assert d.action == "remplacée"


@pytest.mark.parametrize("action", ["rendue", "annotée", "remplacée"])
def test_chaque_decision_porte_un_motif(action):
    """Une décision sans motif est indébuggable en exploitation."""
    exemples = {
        "rendue": ("L2113-10 pose le principe.", PASSAGES),
        "annotée": ("L2113-10 et L9999-1.", PASSAGES),
        "remplacée": ("Selon L9999-1.", PASSAGES),
    }
    d = appliquer(*exemples[action])
    assert d.action == action
    assert len(d.motif) > 10, "le motif doit être exploitable, pas un code"
