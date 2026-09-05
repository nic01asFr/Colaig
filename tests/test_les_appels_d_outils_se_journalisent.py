"""Quels outils le modele appelle-t-il ? Rien ne le disait.

CE QU'ON NE POUVAIT PAS SAVOIR
--------------------------------
L'Orchestrateur ne journalisait QUE les outils destructifs — suspendus ou executes
sur accord. Un appel a `search_documents`, `fetch_document` ou `list_documents` ne
laissait aucune trace.

Le cas qui l'a rendu genant, mp-057 du jeu dore, 05/09/2026. Le modele repond :

    « Le document specifique relatif a la definition du besoin
      (096-…-definition-du-besoin.md) a ete identifie dans le sommaire »

puis declare que l'information ne figure pas dans les passages fournis. Il a donc lu
le sommaire, NOMME le bon fichier, et n'est pas alle le chercher — alors que
`fetch_document` est enregistre et n'est pas retire par le filtre d'intention.
A-t-il essaye ? Le journal ne permet pas de le dire.

C'est le meme trou que la fusion RRF, qui a coute une campagne entiere avant qu'une
ligne de journal ne montre qu'elle n'avait jamais lieu.

CE QUE CETTE TRACE PERMET
---------------------------
Compter les appels par outil sur une campagne, et voir lesquels echouent. Sans elle,
« le modele n'utilise pas `fetch_document` » reste une conjecture.
"""

from __future__ import annotations

import logging

import pytest

from colaig.agents.orchestrator import Orchestrator
from colaig.agents.tool_registry import ToolRegistry
from colaig.models import (
    ContextMode,
    ExecutionPlan,
    Intent,
    IntentType,
    ToolCall,
    ToolDefinition,
    WorkspaceConfig,
    WorkspaceContext,
)

LIRE = ToolDefinition(name="fetch_document", description="Lit un document",
                      parameters=[], category="storage")


@pytest.fixture
def contexte():
    return WorkspaceContext(
        workspace=WorkspaceConfig(workspace_id="mesure", name="Mesure",
                                  storage_path="/espace-mesure/"),
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig.",
    )


def _plan():
    return ExecutionPlan(intent=Intent(intent_type=IntentType.QUESTION))


@pytest.mark.asyncio
async def test_un_appel_reussi_laisse_une_trace(contexte, caplog):
    registre = ToolRegistry()

    async def handler(chemin: str = ""):
        return "le contenu du document"

    registre.register(LIRE, handler)
    orch = Orchestrator(storage=None, retriever=None)

    with caplog.at_level(logging.INFO, logger="colaig.agents.orchestrator"):
        await orch._execute_tool_call(
            ToolCall(tool_name="fetch_document", arguments={"chemin": "096-besoin.md"},
                     call_id="c1"),
            registre, _plan(), contexte)

    trace = " ".join(r.getMessage() for r in caplog.records)
    assert "fetch_document" in trace
    assert "096-besoin.md" in trace, "les arguments disent CE QUE le modele cherchait"


@pytest.mark.asyncio
async def test_un_appel_echoue_le_dit(contexte, caplog):
    """Un outil qui echoue silencieusement est pire qu'un outil absent."""
    registre = ToolRegistry()

    async def handler(chemin: str = ""):
        raise FileNotFoundError("pas de document a ce chemin")

    registre.register(LIRE, handler)
    orch = Orchestrator(storage=None, retriever=None)

    with caplog.at_level(logging.INFO, logger="colaig.agents.orchestrator"):
        await orch._execute_tool_call(
            ToolCall(tool_name="fetch_document", arguments={"chemin": "absent.md"},
                     call_id="c2"),
            registre, _plan(), contexte)

    trace = " ".join(r.getMessage() for r in caplog.records)
    assert "fetch_document" in trace
    assert "echec" in trace.lower() or "échec" in trace.lower()
