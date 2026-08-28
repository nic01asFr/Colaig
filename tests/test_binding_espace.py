"""
Contrat — quel espace documentaire un salon désigne-t-il ?

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.1

Pourquoi ce lot compte plus qu'il n'y paraît
----------------------------------------------
Le rattachement décide **quels documents un salon peut lire**. D42/D43 ont établi que
le dossier partagé est l'unité de confidentialité, et que le salon décide qui
interroge : un mauvais rattachement expose donc les documents d'un autre service.

Et depuis L3.7, il décide aussi **où l'on écrit** : un document déposé dans le salon
est rangé dans l'espace résolu. Avant, un rattachement erroné donnait de mauvaises
réponses ; maintenant il dépose des fichiers dans le mauvais dossier.

Les six niveaux, rangés par force du consentement
---------------------------------------------------
| niveau | source | qui a décidé |
|---|---|---|
| `conversation` | le salon figure dans `conversations` | l'utilisateur, explicitement |
| `user_id` | espace personnel, en DM | l'utilisateur |
| `room_name` / `room_topic` | regex du descripteur | le propriétaire de l'espace |
| `name_convention` | le nom du salon ressemble à celui de l'espace | **personne** |
| `default_workspace` | repli configuré | l'exploitant |

LA CONVENTION DE NOM DEVIENT OPT-IN. La version déployée rattachait un salon nommé
« Urbanisme » à l'espace « Urbanisme » **sans que personne ne l'ait décidé**. D41 le
formule ainsi : « le liage automatique à l'invitation est séduisant et dangereux […]
en retirant la règle de convention de nom **ou en la rendant opt-in comme les deux
regex** ». C'est la seconde branche qui est prise : elle garde la commodité là où le
propriétaire la veut, et rend le consentement explicite.

Un défaut de la version déployée, corrigé au passage
------------------------------------------------------
`_reason_for_score` **déduisait le motif du score**, or le score inclut `priority`, qui
vient du `config.yaml`. Un espace par défaut (score 10) avec `priority: 90` était donc
annoncé comme rattaché *par convention de nom*.

Le motif ment dès que la priorité comble l'écart entre deux niveaux — et c'est ce motif
qu'on montre à l'utilisateur pour justifier le rattachement. Il est désormais **porté**,
pas reconstitué.
"""
from __future__ import annotations

import pytest

from colaig.context.binding import score_candidat, selectionner_espace


def _cand(nom, chemin, descripteur=None):
    return {"name": nom, "path": chemin, "descriptor": descripteur or {}}


# ── Les niveaux, dans l'ordre ───────────────────────────────────────────────


def test_le_salon_explicitement_rattache_gagne():
    """Le signal le plus fort : quelqu'un l'a écrit dans `conversations`."""
    cands = [
        _cand("Urbanisme", "Urbanisme", {"conversations": ["!abc:s"]}),
        _cand("RH", "RH", {"match": {"room_name": "(?i).*"}}),   # matcherait tout
    ]
    best = selectionner_espace(cands, room_id="!abc:s", room_name="Coucou")
    assert best and best["path"] == "Urbanisme" and best["motif"] == "conversation"


def test_l_espace_personnel_en_DM():
    cands = [_cand("Perso", "perso", {"user_ids": ["@nicolas:s"]})]
    best = selectionner_espace(cands, room_id="!dm:s", user_id="@nicolas:s")
    assert best and best["motif"] == "user_id"


def test_regex_sur_le_nom_du_salon():
    cands = [_cand("Urbanisme", "Urbanisme", {"match": {"room_name": "(?i)urbanism"}})]
    best = selectionner_espace(cands, room_id="!x:s", room_name="Salon Urbanisme — Mairie")
    assert best and best["motif"] == "room_name"


def test_regex_sur_le_sujet_du_salon():
    cands = [_cand("PLU", "PLU", {"match": {"room_topic": r"(?i)\bPLU\b"}})]
    best = selectionner_espace(cands, room_id="!x:s", room_name="Divers",
                               room_topic="Questions PLU 2026")
    assert best and best["motif"] == "room_topic"


def test_repli_sur_l_espace_par_defaut():
    cands = [_cand("Accueil", "Accueil"), _cand("Autre", "Autre")]
    best = selectionner_espace(cands, room_id="!x:s", room_name="Sans rapport",
                               default_workspace="Accueil")
    assert best and best["path"] == "Accueil" and best["motif"] == "default_workspace"


def test_aucune_correspondance_rend_None():
    """Ne rien rattacher est une réponse. Rattacher au hasard n'en est pas une."""
    assert selectionner_espace([_cand("A", "A")], room_id="!x:s",
                               room_name="Sans rapport") is None


# ── LA convention de nom, devenue opt-in ────────────────────────────────────


def test_la_convention_de_nom_NE_S_APPLIQUE_PAS_par_defaut():
    """LE changement par rapport à la version déployée (D41).

    Un salon nommé « Urbanisme » ne se rattache PAS tout seul à l'espace « Urbanisme ».
    Personne ne l'a décidé, et le rattachement décide de ce qui est lisible — et
    désormais de l'endroit où l'on écrit.
    """
    cands = [_cand("Urbanisme", "dossiers/Urbanisme")]
    assert selectionner_espace(cands, room_id="!x:s", room_name="urbanisme") is None


def test_la_convention_de_nom_s_applique_si_le_proprietaire_la_DECLARE():
    """Opt-in « comme les deux regex » — la formulation même de l'arbitrage.

    Le propriétaire de l'espace écrit `match.name_convention: true` dans son
    `config.yaml`. La commodité est gardée là où elle est voulue.
    """
    cands = [_cand("Urbanisme", "dossiers/Urbanisme",
                   {"match": {"name_convention": True}})]
    best = selectionner_espace(cands, room_id="!x:s", room_name="urbanisme")
    assert best and best["motif"] == "name_convention"


def test_la_convention_declaree_ignore_accents_et_casse():
    """« PREFECTURE » et « Préfecture » désignent le même service."""
    cands = [_cand("Préfecture", "Prefecture", {"match": {"name_convention": True}})]
    best = selectionner_espace(cands, room_id="!x:s", room_name="PREFECTURE")
    assert best and best["motif"] == "name_convention"


# ── Le motif ne doit pas mentir ─────────────────────────────────────────────


def test_le_motif_survit_a_une_PRIORITE_elevee():
    """LE défaut de la version déployée.

    Elle déduisait le motif du score, or le score inclut `priority`, qui vient du
    `config.yaml`. Un espace par défaut à `priority: 90` était annoncé comme rattaché
    par convention de nom — un motif faux sur une décision de confidentialité.
    """
    cands = [_cand("Accueil", "Accueil", {"priority": 90})]
    best = selectionner_espace(cands, room_id="!x:s", room_name="Sans rapport",
                               default_workspace="Accueil")
    assert best and best["motif"] == "default_workspace", (
        f"motif annoncé : {best['motif'] if best else None} — la priorité l'a déplacé"
    )


def test_la_priorite_departage_a_niveau_EGAL():
    """Elle sert à cela, et à cela seulement : trancher entre deux espaces qui
    correspondent de la même façon.
    """
    cands = [
        _cand("A", "A", {"match": {"room_name": "(?i)commande"}, "priority": 1}),
        _cand("B", "B", {"match": {"room_name": "(?i)commande"}, "priority": 50}),
    ]
    best = selectionner_espace(cands, room_id="!x:s", room_name="Commande publique")
    assert best and best["path"] == "B" and best["motif"] == "room_name"


def test_une_priorite_ne_fait_pas_remonter_un_niveau_faible():
    """Une priorité de 10 000 sur un repli ne doit pas battre un rattachement explicite.

    Sinon `priority` deviendrait un moyen, pour le propriétaire d'un espace, de capter
    les salons rattachés à d'autres.
    """
    cands = [
        _cand("Explicite", "Explicite", {"conversations": ["!x:s"]}),
        _cand("Vorace", "Vorace", {"priority": 10000}),
    ]
    best = selectionner_espace(cands, room_id="!x:s", room_name="peu importe",
                               default_workspace="Vorace")
    assert best and best["path"] == "Explicite"


# ── Robustesse ──────────────────────────────────────────────────────────────


def test_une_regex_invalide_est_toleree():
    """Un `config.yaml` est écrit à la main. Une regex fautive ne doit pas faire tomber
    la résolution de TOUS les salons — elle ne doit simplement pas correspondre.
    """
    assert score_candidat(descripteur={"match": {"room_name": "([unclosed"}},
                          nom_dossier="X", chemin="X", room_id="!x:s",
                          room_name="X")[0] == 0


def test_un_descripteur_absent_ne_casse_rien():
    assert score_candidat(descripteur=None, nom_dossier="X", chemin="X",
                          room_id="!x:s")[0] == 0


def test_une_priorite_non_numerique_est_ignoree():
    """`priority: "haute"` ne doit pas lever, ni valoir zéro par accident silencieux."""
    cands = [_cand("A", "A", {"conversations": ["!x:s"], "priority": "haute"})]
    best = selectionner_espace(cands, room_id="!x:s")
    assert best and best["motif"] == "conversation"
