"""
Colaig — les directives décrivent la forme d'une réponse, pas le fait d'en donner une.

L'ÉCART QUI RESTE, MESURÉ
---------------------------
Après avoir fermé la grammaire de citation et rendu au modèle la question posée :

    | | cœur (cible) | pipeline |
    | fantômes       | 4       | 5     |
    | hors contexte  | 16      | 8     |
    | cite l'attendu | 100/113 | 97/112 |
    | refus          | 22/22   | 18/21 + 3 intermittents |

Les deux déficits de citation sont fermés — le hors contexte est même deux fois
meilleur que le cœur. **Le refus est le dernier verrou**, et il n'a pas bougé.

CE QUE L'ANALYSEUR ÉCRIT, ET QUAND
------------------------------------
    parts.append(f"Instructions : {directives.instructions}")

`instructions` est du **texte libre produit par l'Analyseur**, injecté verbatim comme
consigne au Synthétiseur. Or **l'Analyseur s'exécute AVANT la recherche documentaire** :
il ne peut pas savoir si une réponse existe. Ses consignes présupposent donc toujours
qu'il y en a une — « explique la procédure », « liste les étapes ».

Et le bloc est ajouté **après** `agent_ctx.system_prompt`, donc après le protocole de
refus de l'espace. Il a le dernier mot.

> C'est la symétrie exacte du défaut de citation corrigé le 31/08 : là, l'espace venait
> en dernier sans l'emporter ; ici, les directives l'emportent en venant en dernier.
> **La position ne fait pas l'autorité — seule la subordination explicite la fait.**

LA PROPRIÉTÉ FIGÉE ICI
------------------------
Le bloc dit ce qu'il est : la forme d'une réponse **si l'on en donne une**, écrite avant
la recherche, et sans autorité sur la décision de répondre.
"""

from __future__ import annotations

import pytest

from colaig.models import (
    AgentDirectives,
    ContextMode,
    ExecutionPlan,
    IncomingMessage,
    Intent,
    IntentType,
    WorkspaceContext,
)


def _plan(**champs) -> ExecutionPlan:
    return ExecutionPlan(
        intent=Intent(intent_type=IntentType.QUESTION, query_reformulated="q",
                      synthesiser_directives=AgentDirectives(
                          target_agent="synthesiser", **champs)),
        search_results=[], tool_results={})


async def _systeme(synth, plan, contexte) -> str:
    from colaig.agents.context_builder import build_agent_context
    from tests.conftest import MockStorage

    # Comme `synthesise()` : le prompt de l'espace ET les directives sont passes au
    # contexte d'agent. Les omettre testerait un chemin que la production n'emprunte pas.
    ctx_agent = await build_agent_context(
        MockStorage(), None, "synthesiser",
        prompt_espace=contexte.system_prompt,
        directives=plan.intent.synthesiser_directives)
    msgs = synth._build_messages(
        plan, contexte, ctx_agent, None, None,
        message=IncomingMessage(message_id="$m", conversation_id="!c",
                                user_id="@u:t", body="q"))
    return "\n".join(m["content"] for m in msgs if m["role"] == "system")


@pytest.fixture
def synth(fake_llm, fake_storage):
    from colaig.agents.synthesiser import Synthesiser

    return Synthesiser(fake_llm, fake_storage)


@pytest.fixture
def contexte():
    return WorkspaceContext(
        workspace=None, mode=ContextMode.ASSISTANT,
        system_prompt="Si la réponse n'y figure pas, dis-le et ne cite aucun article.")


@pytest.mark.asyncio
async def test_le_bloc_dit_qu_il_ne_decide_pas_de_repondre(synth, contexte):
    """LE défaut. « Instructions : … » lisait comme un ordre de produire."""
    systeme = await _systeme(synth, _plan(instructions="Explique la procédure."), contexte)

    assert "Explique la procédure." in systeme, "la directive doit rester servie"
    assert "si tu réponds" in systeme.lower(), (
        f"rien ne subordonne les directives a la decision de repondre : {systeme!r}")


@pytest.mark.asyncio
async def test_le_bloc_dit_qu_il_precede_la_recherche(synth, contexte):
    """Le modèle doit savoir que ces consignes ont été écrites en aveugle."""
    systeme = await _systeme(synth, _plan(response_format="step-by-step"), contexte)

    assert "avant" in systeme.lower() and "recherche" in systeme.lower(), (
        "le modele ignore que les directives precedent la recherche")


@pytest.mark.asyncio
async def test_le_protocole_de_refus_reste_apres(synth, contexte):
    """LA borne. Le protocole de l'espace doit garder le dernier mot."""
    systeme = await _systeme(synth, _plan(instructions="Explique la procédure."), contexte)

    i_directives = systeme.find("Explique la procédure.")
    i_refus = systeme.find("dis-le et ne cite aucun article")
    assert i_directives < i_refus, (
        "les directives passent apres le protocole de refus, et l'emportent")


@pytest.mark.asyncio
async def test_sans_directive_aucun_bloc_n_est_ajoute(synth, contexte):
    """Un bloc vide dilue pour rien."""
    systeme = await _systeme(synth, _plan(), contexte)

    assert "si tu réponds" not in systeme.lower()
