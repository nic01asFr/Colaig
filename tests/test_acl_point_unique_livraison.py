"""
Contrat — une seule garde decide d'une cible de livraison.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le defaut, et il est de moi
-----------------------------
`WorkspaceACL.validate_delivery_target` existait. Il valide une cible de livraison,
refuse `.colaig/`, ET confine la cible au dossier personnel de l'utilisateur.

Il n'etait branche que sur UN des deux chemins de creation de tache : celui du serveur
MCP. Le chemin AGENTIQUE — `create_background_task` appele par l'orchestrateur — ne le
consultait pas. C'est precisement le trou trouve au lot L2.1b.

Et je l'ai bouche en ECRIVANT UNE SECONDE IMPLEMENTATION, sans chercher si une garde
existait. Plus faible que celle qui dormait a cote : la mienne ne confinait pas au
dossier personnel.

C'est le motif que ce chantier corrige chez les autres depuis le debut, produit par moi
dans le meme mouvement. La lecon tient en une phrase : **avant d'ecrire une garde,
chercher celle qui existe.**

Ce que ce fichier fixe
-----------------------
Les trois chemins qui designent une cible d'ecriture passent par le MEME predicat.
"""
from __future__ import annotations

import pytest

from colaig.security.acl import WorkspaceACL


CIBLES_INTERDITES = [
    "/espace/.colaig/prompts/synthesiser.md",
    "/espace/.colaig/config.yaml",
    "/espace/.colaig-ignore",
    "/espace/documents/../.colaig/prompts/analyser.md",
]


@pytest.mark.parametrize("cible", CIBLES_INTERDITES)
def test_le_dossier_d_instance_est_refuse(cible):
    with pytest.raises(ValueError):
        WorkspaceACL.validate_delivery_target("document", cible)


def test_une_cible_hors_du_dossier_personnel_est_refusee():
    """Ce que ma propre implementation ne faisait PAS.

    Une tache creee par un utilisateur ne doit pas ecrire dans l'espace d'un autre —
    meme hors de `.colaig/`.
    """
    with pytest.raises(ValueError):
        WorkspaceACL.validate_delivery_target(
            "document", "/espace-d-autrui/rapport.md",
            personal_workspace_path="/mon-espace/",
        )


def test_une_cible_dans_le_dossier_personnel_passe():
    assert WorkspaceACL.validate_delivery_target(
        "document", "/mon-espace/rapports/hebdo.md",
        personal_workspace_path="/mon-espace/",
    )


@pytest.mark.parametrize("cible", ["", "   ", None])
def test_une_cible_vide_est_refusee(cible):
    with pytest.raises(ValueError):
        WorkspaceACL.validate_delivery_target("document", cible)


# ── Les cibles de conversation ──────────────────────────────────────────────


def test_un_identifiant_de_salon_passe():
    assert WorkspaceACL.validate_delivery_target(
        "messaging", "!salon:exemple.fr",
    ) == "!salon:exemple.fr"


@pytest.mark.parametrize("cible", [
    "salon avec espaces",
    "salon;rm -rf",
    "salon\nautre",
    "s" * 300,
])
def test_un_identifiant_de_salon_malforme_est_refuse(cible):
    with pytest.raises(ValueError):
        WorkspaceACL.validate_delivery_target("messaging", cible)


# ── Le point de passage doit etre unique ────────────────────────────────────


def test_les_chemins_de_livraison_passent_par_la_meme_garde():
    """Une garde appliquee a un chemin de creation sur deux ne garde rien.

    C'est exactement ce qui s'est produit : le chemin MCP la consultait, le chemin
    agentique non — et c'est par la que passait le trou de L2.1b.
    """
    import pathlib

    from tests.conftest import code_seul

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig"
    chemins = [
        racine / "agents" / "tools" / "task_tools.py",
        racine / "agents" / "task_scheduler.py",
        racine / "mcp" / "server.py",
    ]
    fautifs = [
        c.name for c in chemins
        if "validate_delivery_target" not in code_seul(c.read_text(encoding="utf-8"))
    ]
    assert not fautifs, (
        "ces modules designent une cible de livraison sans passer par "
        f"`WorkspaceACL.validate_delivery_target` : {fautifs}"
    )
