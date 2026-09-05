"""
Contrat — on ne transmet pas au modèle un outil que l'Analyseur n'a pas prévu.

STATUT: TESTE
VERSION: 2026-08-27 - v1.0
LOT: L2.5b

Pourquoi cette garde, après avoir mesuré que la consigne ne suffit pas
-----------------------------------------------------------------------
L2.5 a mesuré 1/21 attaques abouties après durcissement de la consigne. Ce qui résiste —
`adv-032`, une règle citée en anglais — passe **3 tirages sur 3**, alors que la consigne
**nomme explicitement cette technique**. Nommer une technique ne la défait pas.

La conclusion écrite dans `AVANCEMENT.md` était : la piste n'est pas déclarative.
**On ne résiste pas à la tentation d'un outil absent.**

Le trou, tel qu'il existait
----------------------------
L'Analyseur produit déjà `needs_tools` : il a jugé si un outil est nécessaire.
`_filter_registry_for_intent` honorait `needs_rag` et `tools_to_use` — et **jamais**
`needs_tools`. Une question documentaire ordinaire arrivait donc au modèle avec
`create_document`, `manage_workspace_owners` et `report_to_user` dans son catalogue,
alors que l'Analyseur venait de décider qu'aucun outil n'était requis.

Ce que la garde retire, et ce qu'elle laisse
----------------------------------------------
Elle retire les outils **destructifs**, au sens de `security/actions.py` (L2.4a) — la
classification existe déjà, elle n'est pas réécrite ici. Les lecteurs restent : sans eux
une question documentaire n'obtient plus de réponse, et une garde qui casse l'usage se
fait retirer.

Ce qu'elle ne fait pas
-----------------------
Elle ne remplace pas la confirmation de L2.4. Quand l'Analyseur juge qu'un outil EST
nécessaire, le catalogue destructif revient — et c'est alors la garde mécanique qui
suspend l'appel. Les deux couches traitent deux moments différents : celle-ci réduit la
surface, celle-là arrête l'appel qui subsiste.
"""
from __future__ import annotations

import pytest

from colaig.models import AgentDirectives, Intent, IntentType
from colaig.security.actions import DESTRUCTIFS_INTEGRES, LECTEURS_INTEGRES


class _Registre:
    """Un registre d'outils réduit à ce que le filtre manipule."""

    def __init__(self, noms):
        self._noms = list(noms)

    def names(self):
        return list(self._noms)

    def filter_by_names(self, noms):
        return _Registre([n for n in self._noms if n in set(noms)])

    def get(self, nom):
        return nom if nom in self._noms else None


CATALOGUE = sorted(DESTRUCTIFS_INTEGRES | LECTEURS_INTEGRES | {"assess_completion"})


def _orchestrateur(**kwargs):
    from colaig.agents.orchestrator import Orchestrator

    kwargs.setdefault("storage", None)
    kwargs.setdefault("retriever", None)
    return Orchestrator(**kwargs)


def _intention(needs_tools=False, needs_rag=True, directives=None):
    return Intent(
        intent_type=IntentType.QUESTION,
        query_reformulated="Quelles sont les regles de lots separes ?",
        needs_rag=needs_rag,
        needs_tools=needs_tools,
        confidence=0.9,
        orchestrator_directives=directives,
    )


# ── Le défaut lui-même ──────────────────────────────────────────────────────


def test_sans_besoin_d_outil_aucun_destructif_n_est_transmis():
    """LE défaut. Une question documentaire arrivait avec `report_to_user` au menu."""
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(_Registre(CATALOGUE), _intention())

    transmis = set(restant.names())
    fautifs = sorted(transmis & DESTRUCTIFS_INTEGRES)
    assert not fautifs, f"outils destructifs transmis hors plan : {fautifs}"


def test_les_lecteurs_restent_disponibles():
    """Une garde qui casse l'usage se fait retirer, et ne protège alors plus rien.

    C'est la même exigence que pour le balisage et pour l'anti-SSRF : refuser tout
    n'est pas une position défendable.
    """
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(_Registre(CATALOGUE), _intention())

    assert "search_documents" in restant.names()
    assert "fetch_document" in restant.names()
    assert "assess_completion" in restant.names(), (
        "le méta-outil de contrôle de boucle doit survivre à tout filtrage"
    )


def test_avec_besoin_d_outil_le_catalogue_revient():
    """La garde réduit la surface ; elle ne supprime pas la fonction.

    Quand l'Analyseur juge qu'un outil est nécessaire, les destructifs reviennent — et
    c'est alors la confirmation de L2.4 qui décide, pas ce filtre.
    """
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(
        _Registre(CATALOGUE), _intention(needs_tools=True),
    )
    assert "create_document" in restant.names()


def test_une_directive_explicite_reste_souveraine():
    """`tools_to_use` est une décision de l'Analyseur, pas une suggestion.

    Elle précède ce filtre dans la fonction : ce test épingle qu'on ne l'a pas cassée
    en ajoutant la nouvelle règle.
    """
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(
        _Registre(CATALOGUE),
        _intention(needs_tools=True,
                   directives=AgentDirectives(target_agent="orchestrator",
                                              tools_to_use=["create_document"])),
    )
    assert "create_document" in restant.names()
    assert "manage_workspace_owners" not in restant.names()


# ── Le flag, et son sens par défaut ─────────────────────────────────────────


def test_la_garde_est_ACTIVE_par_defaut():
    """Divergence assumée du défaut en vigueur dans `config.py`.

    Tous les flags `COLAIG_*_ENABLED` du dépôt défaillent à OFF — c'est le bon sens
    pour un ajout de fonction. Celui-ci est une **restriction** : le sens sûr est
    l'inverse. L2.2 a pris la même liberté pour la liste blanche MCP, dont le défaut est
    REFUSER là où les autres champs de `platform_policy` sont permissifs.
    """
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(_Registre(CATALOGUE), _intention())
    assert "report_to_user" not in restant.names()


def test_le_flag_permet_de_revenir_en_arriere():
    """Si l'Analyseur se révèle peu fiable sur `needs_tools`, on doit pouvoir couper.

    Sans cette sortie, un `needs_tools` mal jugé rendrait l'agent incapable d'agir, et
    la garde serait arrachée en urgence plutôt que désactivée proprement.
    """
    orch = _orchestrateur(retrait_outils_hors_plan=False)
    restant = orch._filter_registry_for_intent(_Registre(CATALOGUE), _intention())
    assert "create_document" in restant.names()


# ── Ce que la garde ne prétend pas faire ────────────────────────────────────


def test_la_garde_ne_remplace_PAS_la_confirmation():
    """Deux couches, deux moments — épinglé pour qu'on ne retire pas l'une des deux.

    Ce filtre agit AVANT l'appel, en réduisant la surface. La garde de L2.4 agit SUR
    l'appel, en le suspendant. Un `needs_tools=True` obtenu par une consigne injectée
    ferait revenir le catalogue : c'est L2.4 qui arrête alors l'appel.
    """
    from colaig.security.actions import est_destructif

    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(
        _Registre(CATALOGUE), _intention(needs_tools=True),
    )
    revenus = [n for n in restant.names() if est_destructif(n)]
    assert revenus, (
        "avec needs_tools=True les destructifs reviennent — c'est voulu, et c'est "
        "pourquoi L2.4 reste nécessaire"
    )


def test_un_outil_MCP_non_annote_est_traite_comme_destructif():
    """La règle de L2.4a s'applique ici aussi, sans être réécrite.

    Un serveur MCP qui n'annote rien ne promet rien. Transmettre son outil hors plan
    reviendrait à faire confiance à un tiers qui ne s'est pas déclaré inoffensif.
    """
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(
        _Registre([*CATALOGUE, "juridique__recherche"]), _intention(),
    )
    assert "juridique__recherche" not in restant.names()


def test_la_classification_n_est_pas_reecrite_ici():
    """Le point unique, cinquième application de la même forme.

    Si ce filtre portait sa propre liste de noms destructifs, elle divergerait de
    `security/actions.py` — c'est mesuré : la fédération portait une seconde liste
    noire SSRF, plus faible de six contournements (L2.6f).
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul(
        (pathlib.Path(__file__).resolve().parent.parent
         / "colaig" / "agents" / "orchestrator.py").read_text(encoding="utf-8"),
    )
    assert "est_destructif" in source, (
        "le filtre doit consulter `security.actions`, pas sa propre liste"
    )
    for nom in ("manage_workspace_owners", "set_workspace_prompt"):
        assert source.count(f'"{nom}"') == 0, (
            f"`{nom}` est nommé en dur dans l'orchestrateur — c'est le début d'une "
            "seconde classification"
        )


@pytest.mark.parametrize("nom", sorted(DESTRUCTIFS_INTEGRES))
def test_chaque_destructif_connu_est_retire(nom):
    orch = _orchestrateur()
    restant = orch._filter_registry_for_intent(_Registre(CATALOGUE), _intention())
    assert nom not in restant.names()


def test_le_flag_est_reellement_branche():
    """Une garde ecrite et non branchee ne garde rien.

    Sixieme verification explicite de ce motif dans ce depot — c'est celui que L2.6 a
    trouve neuf fois d'affilee, dont une fois sur le filtre de masquage des secrets,
    installe au mauvais endroit et ne protegeant aucun module.
    """
    import pathlib

    from tests.conftest import code_seul

    racine = pathlib.Path(__file__).resolve().parent.parent
    config = code_seul((racine / "colaig" / "config.py").read_text(encoding="utf-8"))
    main = code_seul((racine / "colaig" / "main.py").read_text(encoding="utf-8"))

    assert "COLAIG_RETRAIT_OUTILS_HORS_PLAN" in config, (
        "le flag doit etre lisible depuis l'environnement"
    )
    assert main.count("retrait_outils_hors_plan=config.retrait_outils_hors_plan") == 2, (
        "les DEUX constructions d'Orchestrator doivent recevoir le flag — en brancher "
        "une seule reproduirait exactement le defaut de L2.6"
    )


def test_le_defaut_de_configuration_est_ACTIF():
    """Le defaut se lit dans la configuration, pas seulement dans la signature."""
    from colaig.config import load_config

    assert load_config().retrait_outils_hors_plan is True
