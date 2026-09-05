"""
Contrat — ce que le modèle reçoit vraiment, et non ce que le filtre rend.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L2.5c

Le défaut, et pourquoi les tests de L2.5b ne pouvaient pas le voir
-------------------------------------------------------------------
`test_outils_hors_plan.py` exerce `_filter_registry_for_intent` **isolément**, sur un
registre factice. Le filtre y fait exactement ce qu'on lui demande : il retire les
outils destructifs quand `needs_tools=False`.

Seulement, dans `_execute_agentic`, il est appelé **au milieu** de la construction du
catalogue. Six enregistrements le suivent :

    filtre par intention          <- la garde s'applique ICI
    handler search isolé
    ask_workspace
    find_workspace
    create_background_task        <- DESTRUCTIF, en mode PERSONAL
    outils d'administration       <- destructifs, sous garde ACL
    outils MCP                    <- destructifs PAR DEFAUT (annotation absente)
    tool_schemas = list_openai_schemas()   <- ce que le modele recoit

**Tout ce qui est enregistré après le filtre lui échappe.** Le filtre porte donc sur un
état intermédiaire qui n'est plus celui qu'on transmet.

Mesuré avant correction : en mode PERSONAL, avec `needs_tools=False`, le modèle recevait
`create_background_task` — un outil qui fait exécuter une requête plus tard, sans témoin.

Pourquoi ce fichier teste le catalogue TRANSMIS
-------------------------------------------------
Un test qui interroge la garde demande « la garde fonctionne-t-elle ? ». Celui-ci
demande « qu'est-ce qui arrive au modèle ? ». La première question a une réponse verte
depuis L2.5b ; c'est la seconde qui décrit le produit.

Ce que la mesure a montré par ailleurs
----------------------------------------
Le catalogue interactif ordinaire ne contient **aucun** outil destructif : basculer
`needs_tools` n'y change donc rien (`mesure_ancre_empoisonnee.py`, D52). C'est ce qui
explique le « 0/21 structurel » de L2.5.

La garde ne devient utile qu'au moment où un outil destructif devient joignable — le
mode PERSONAL aujourd'hui, les connecteurs MCP demain (L3.4). C'est précisément pour ce
moment-là qu'elle doit être posée au bon endroit.
"""
from __future__ import annotations

import pytest

from colaig.agents.context_builder import build_tool_registry
from colaig.agents.orchestrator import Orchestrator
from colaig.models import (
    ContextMode,
    Intent,
    IntentType,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.security.actions import est_destructif
from tests.fakes import FakeStorage


class _Retriever:
    async def retrieve(self, *a, **k):
        return []

    def set_store(self, store):
        pass


class _LLMEspion:
    """Retient le catalogue transmis, puis clôt la boucle sans appeler d'outil."""

    embedding_dim = 384

    def __init__(self) -> None:
        self.catalogues: list[list[str]] = []

    async def chat(self, messages, **kw):
        return "fini"

    async def chat_with_tools(self, messages, tools, **kw):
        self.catalogues.append([t["function"]["name"] for t in tools])
        return type("R", (), {"has_tool_calls": False, "tool_calls": [],
                              "content": "fini"})()

    async def embed(self, texte):
        return [0.0] * 384


async def _catalogue(mode: ContextMode, needs_tools: bool) -> list[str]:
    espace = WorkspaceConfig(workspace_id="perso", name="Perso",
                             storage_path="/perso/")
    llm = _LLMEspion()
    registre = build_tool_registry(retriever=_Retriever(), storage=FakeStorage(),
                                   albert=llm, workspace=espace)
    orchestrateur = Orchestrator(storage=FakeStorage(), retriever=_Retriever(),
                                 albert=llm, tool_registry=registre)

    await orchestrateur.execute(
        Intent(intent_type=IntentType.QUESTION, query_reformulated="q",
               needs_rag=True, needs_tools=needs_tools),
        WorkspaceContext(workspace=espace, mode=mode,
                         user_id="@nic:tchap.gouv.fr"))

    return sorted(llm.catalogues[0]) if llm.catalogues else []


# ── LE défaut ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aucun_destructif_n_est_transmis_quand_l_analyseur_dit_NON():
    """La garde de L2.5b, mesurée là où elle compte.

    Avant correction, ce test échouait en mode PERSONAL : `create_background_task`
    arrivait au modèle alors que l'Analyseur venait de juger qu'aucun outil n'était
    nécessaire. On ne résiste pas à la tentation d'un outil absent — encore faut-il
    qu'il soit vraiment absent.
    """
    transmis = await _catalogue(ContextMode.PERSONAL, needs_tools=False)
    destructifs = [n for n in transmis if est_destructif(n)]
    assert destructifs == [], (
        f"outils destructifs transmis malgre needs_tools=False : {destructifs}"
    )


@pytest.mark.asyncio
async def test_les_lecteurs_restent_transmis():
    """Une garde qui casse l'usage se fait retirer.

    Sans outil de lecture, une question documentaire n'obtient plus de réponse.
    """
    transmis = await _catalogue(ContextMode.PERSONAL, needs_tools=False)
    assert "search_documents" in transmis
    assert "fetch_document" in transmis


@pytest.mark.asyncio
async def test_quand_l_analyseur_dit_OUI_l_outil_revient():
    """La garde réduit la surface ; elle n'interdit pas l'usage légitime.

    C'est L2.4 qui traite l'appel qui subsiste, pas cette couche.
    """
    transmis = await _catalogue(ContextMode.PERSONAL, needs_tools=True)
    assert "create_background_task" in transmis, (
        "une garde qui ne rend jamais l'outil rendrait le mode PERSONAL inutilisable"
    )


@pytest.mark.asyncio
async def test_le_mode_ASSISTANT_ordinaire_n_expose_rien_de_destructif():
    """Constat mesuré, épinglé : le catalogue interactif ordinaire est sans danger.

    Ce n'est pas la garde qui l'obtient — c'est la composition du registre. L'épingler
    ici fait échouer le jour où quelqu'un ajoute un outil destructif au registre
    interactif, plutôt que de le découvrir par une mesure adversariale des mois après.
    """
    for verdict in (True, False):
        transmis = await _catalogue(ContextMode.ASSISTANT, needs_tools=verdict)
        assert [n for n in transmis if est_destructif(n)] == [], (
            f"le registre interactif expose un destructif (needs_tools={verdict})"
        )


# ── L'ordre, épinglé à la source ────────────────────────────────────────────


def test_le_filtre_est_applique_APRES_tous_les_enregistrements():
    """La cause du défaut, et non seulement son symptôme.

    Un test de comportement ne couvre que les outils qu'il connaît. Celui-ci refuse
    qu'un `register` soit ajouté après le filtre — ce qui arrivera au lot L3.4, où les
    outils MCP sont enregistrés dynamiquement et comptent pour destructifs faute
    d'annotation.
    """
    import inspect

    from colaig.agents.orchestrator import Orchestrator as O

    source = inspect.getsource(O._execute_agentic)
    filtre = source.index("_filter_registry_for_intent(")
    dernier = source.rindex("available_tools.register(")

    assert filtre > dernier, (
        "un outil est enregistre APRES le filtre par intention : il echappe a la garde"
    )
