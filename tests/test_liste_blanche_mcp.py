"""
Critère de fin du lot L2.2 — « un `mcp_servers.json` hors liste ne produit aucun outil ».

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.2

Le motif, écrit dans le plan
-----------------------------
> **L2.2 est le lot le plus urgent du chantier.** Aujourd'hui, quiconque écrit dans le
> WebDAV d'un espace injecte un outil arbitraire dans le registre de l'agent.

Le recensement de D37 l'a confirmé sur pièces : `mcp_connectors` est un champ de
`WorkspaceConfig`, donc lu depuis `.colaig/config.yaml`. Y écrire branche un serveur MCP
distant dont Colaig **appellera les outils** — avec ses propres identifiants.

Le balisage du lot L2.1 a traité le champ `instructions` de ces serveurs : il entre
désormais comme donnée et non comme instruction système. **Mais l'outil, lui, s'exécute.**
Déclarer n'est pas empêcher.

Pourquoi le défaut est REFUS, contrairement aux autres champs de `platform_policy`
------------------------------------------------------------------------------------
`clients.yml.example` pose la convention : « listes vides ou section absente = aucune
contrainte ». Elle est juste pour les autres champs — ils bornent ce que **l'opérateur**
déclare lui-même, et un opérateur qui ne borne rien n'a rien ouvert à personne.

`allowed_mcp_servers` borne ce que **l'utilisateur final écrit dans son espace**. Ce
n'est pas le même modèle de menace : « absent = tout autorisé » y reproduirait exactement
le trou que le lot doit fermer, et D44 a montré quatre fois ce que coûte une garde dont
le défaut est ouvert.

La différence est rendue **visible dans la valeur**, non cachée dans le code :

    absent          → aucun serveur MCP n'est monté
    ["*"]           → tous, explicitement
    ["https://…"]   → ceux-là, par préfixe

Ouvrir redevient un acte. Aucun déploiement déclaré n'utilise `mcp_connectors`
aujourd'hui — le refus par défaut ne casse donc rien, il empêche.
"""
from __future__ import annotations

from colaig.models import MCPConnectorConfig, PlatformPolicy
from colaig.security.mcp_policy import connecteurs_autorises


def _connecteur(nom: str, url: str) -> MCPConnectorConfig:
    return MCPConnectorConfig(name=nom, url=url)


DECLARES = [
    _connecteur("interne", "https://mcp.interieur.gouv.fr/mcp"),
    _connecteur("tiers", "https://mcp.exemple-douteux.fr/mcp"),
]


def test_sans_politique_aucun_serveur_n_est_monte():
    """LE critère du lot. Un connecteur déclaré dans un espace ne suffit pas."""
    retenus = connecteurs_autorises(DECLARES, PlatformPolicy())
    assert retenus == [], (
        "un serveur MCP declare dans le config.yaml d'un espace a ete monte sans "
        "figurer dans la politique d'instance"
    )


def test_l_etoile_autorise_tout_mais_explicitement():
    """Le mode permissif reste disponible — il faut l'ecrire."""
    politique = PlatformPolicy(allowed_mcp_servers=["*"])
    assert connecteurs_autorises(DECLARES, politique) == DECLARES


def test_une_liste_retient_ce_qu_elle_nomme():
    politique = PlatformPolicy(allowed_mcp_servers=["https://mcp.interieur.gouv.fr"])
    retenus = connecteurs_autorises(DECLARES, politique)
    assert [c.name for c in retenus] == ["interne"]


def test_la_comparaison_est_par_prefixe_et_ancree():
    """Un prefixe non ancre laisserait passer un domaine qui l'imite.

    `https://mcp.interieur.gouv.fr.attaquant.fr/mcp` COMMENCE par le domaine autorise
    si l'on compare betement des chaines. La comparaison doit donc porter sur une
    frontiere de chemin ou d'autorite, pas sur `startswith` nu.
    """
    politique = PlatformPolicy(allowed_mcp_servers=["https://mcp.interieur.gouv.fr"])
    imitateur = [_connecteur("imitateur",
                             "https://mcp.interieur.gouv.fr.attaquant.fr/mcp")]
    assert connecteurs_autorises(imitateur, politique) == []


def test_un_refus_est_journalise(caplog):
    """Un serveur ecarte en silence produit un incident incomprehensible.

    L'exploitant doit lire quel serveur a ete ecarte et ou l'autoriser.
    """
    import logging

    with caplog.at_level(logging.WARNING):
        connecteurs_autorises(DECLARES, PlatformPolicy())

    journal = " ".join(r.getMessage() for r in caplog.records)
    assert "allowed_mcp_servers" in journal, "l'issue doit etre nommee"
    assert "exemple-douteux" in journal or "interne" in journal, (
        "le serveur ecarte doit etre nomme"
    )


def test_une_liste_vide_n_est_pas_une_etoile():
    """`allowed_mcp_servers: []` ecrit explicitement veut dire AUCUN, pas TOUS.

    C'est la ou la convention des autres champs de `platform_policy` diverge, et le
    test est la pour que la divergence soit voulue et non subie.
    """
    assert connecteurs_autorises(DECLARES, PlatformPolicy(allowed_mcp_servers=[])) == []


# ── Le point de passage doit etre unique ────────────────────────────────────


def test_aucun_module_ne_lit_les_connecteurs_hors_du_point_unique():
    """Meme forme de garde que pour le balisage (L2.1), et pour la meme raison.

    Un filtre applique a trois sites sur quatre ne filtre rien : il suffit du quatrieme.
    Ce test echoue donc si un module lit `mcp_connectors` sans passer par
    `security/mcp_policy.py`.
    """
    import pathlib

    from tests.conftest import code_seul

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig"
    # Declarent le champ, le chargent, ou le REECRIVENT dans le yaml — aucun ne monte
    # de serveur a partir de lui.
    HORS_CONSOMMATION = {
        "models.py", "config.py", "security/mcp_policy.py", "context/workspace.py",
    }
    fautifs = []
    for chemin in racine.rglob("*.py"):
        relatif = chemin.relative_to(racine).as_posix()
        if relatif in HORS_CONSOMMATION:
            continue
        # Filtrer docstrings et commentaires : un module a le droit de MENTIONNER le
        # champ en expliquant d'ou il vient. C'est le code qui compte.
        source = code_seul(chemin.read_text(encoding="utf-8"))
        if ".mcp_connectors" not in source:
            continue
        if "mcp_policy" in source:
            continue
        fautifs.append(relatif)

    assert not fautifs, (
        "ces modules lisent `mcp_connectors` sans passer par "
        f"`security/mcp_policy.py` : {', '.join(sorted(fautifs))}"
    )
