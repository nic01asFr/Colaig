"""
Contrat — la classification des outils destructifs (L2.4a).

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.4a

Ce module ne garde rien, il classe. La garde — « aucun destructif execute sans
confirmation » — attend un arbitrage : la confirmation par reaction exige d'etendre
`MessagingProtocol`, donc de toucher `protocols.py` (CLAUDE.md §5), et la boucle
agentique interactive n'a aucun mecanisme de suspension.

Ce qui est teste ici est donc la moitie qui ne depend d'aucun arbitrage, et qui sera
necessaire quel que soit le canal de confirmation retenu.
"""
from __future__ import annotations

import pytest

from colaig.security.actions import (
    DESTRUCTIFS_INTEGRES,
    LECTEURS_INTEGRES,
    est_destructif,
    inconnus,
)


@pytest.mark.parametrize("nom", sorted(DESTRUCTIFS_INTEGRES))
def test_les_outils_qui_modifient_sont_classes_destructifs(nom):
    assert est_destructif(nom)


@pytest.mark.parametrize("nom", sorted(LECTEURS_INTEGRES))
def test_les_lecteurs_ne_le_sont_pas(nom):
    assert not est_destructif(nom)


def test_aucun_outil_n_est_dans_les_deux_ensembles():
    """Un outil des deux cotes rendrait le classement dependant de l'ordre du code."""
    assert not (DESTRUCTIFS_INTEGRES & LECTEURS_INTEGRES)


def test_les_droits_et_le_prompt_comptent_parmi_les_plus_graves():
    """Nomme a part parce que ce sont les deux qui donnent l'agent lui-meme.

    `manage_workspace_owners` donne l'administration de l'espace ;
    `set_workspace_prompt` remplace le prompt systeme. Un appel non voulu de l'un ou
    l'autre ne se rattrape pas.
    """
    assert est_destructif("manage_workspace_owners")
    assert est_destructif("set_workspace_prompt")
    assert est_destructif("link_conversation"), (
        "le rattachement d'une conversation EST la frontiere d'acces (L2.1d)"
    )


# ── Outils MCP externes ─────────────────────────────────────────────────────


def test_un_outil_mcp_sans_annotation_est_destructif():
    """La specification MCP fait de `destructiveHint` un defaut VRAI hors lecture seule.

    Un serveur qui n'annote rien ne promet rien : c'est a lui de se declarer inoffensif,
    pas a nous de le supposer. Le sens sur est le seul defendable ici.
    """
    assert est_destructif("juridique__recherche", {})
    assert est_destructif("juridique__recherche", None)


def test_un_outil_mcp_declare_en_lecture_seule_ne_l_est_pas():
    assert not est_destructif("juridique__recherche", {"readOnlyHint": True})


def test_un_outil_mcp_declare_non_destructif_ne_l_est_pas():
    assert not est_destructif("juridique__recherche", {"destructiveHint": False})


def test_une_annotation_mensongere_reste_du_declaratif():
    """Ce test documente une LIMITE, il ne verifie pas une protection.

    `readOnlyHint` vient du serveur, donc d'un tiers. Un serveur malveillant se declare
    en lecture seule et fait ce qu'il veut. L'epinglage de L2.3 empeche de CHANGER cette
    annotation apres admission ; il n'empeche pas de mentir des le depart.

    C'est ce que la suite adversariale de L2.5 devra mesurer.
    """
    assert not est_destructif("piege__exfiltre", {"readOnlyHint": True})


# ── L'oubli doit se voir ────────────────────────────────────────────────────


def test_un_outil_integre_non_classe_fait_echouer_le_contrat():
    """Un outil oublie se comporterait comme un externe — destructif, mais en silence.

    Ce chantier a trouve quatre fois le motif « ecrit et jamais branche ». Celui-ci en
    est le cousin : classe nulle part, donc classe par accident. Ajouter un outil doit
    forcer une decision.
    """
    import pathlib
    import re

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig" / "agents" / "tools"
    declares = set()
    for chemin in racine.glob("*.py"):
        source = chemin.read_text(encoding="utf-8")
        for bloc in re.findall(r"ToolDefinition\((.*?)\n\)", source, re.DOTALL):
            trouve = re.search(r'name="([a-z_]+)"', bloc)
            if trouve:
                declares.add(trouve.group(1))

    assert declares, "aucun outil trouve — le test ne prouverait rien"
    oublies = inconnus(declares)
    assert not oublies, (
        "ces outils integres ne sont classes ni destructifs ni lecteurs dans "
        f"`colaig/security/actions.py` : {', '.join(oublies)}"
    )
