"""
Colaig — le prompt de l'espace doit atteindre les agents, pas seulement le générateur.

Ce que la mesure du pipeline a trouvé
---------------------------------------
Le pipeline agent, mesuré le 30/08/2026 sur les mêmes cas et les mêmes passages que le
cœur RAG :

| | cœur RAG | pipeline agent |
|---|---|---|
| citation fantôme | 9/135 | **2/135** |
| citation hors contexte | 17/135 | **6/135** |
| article attendu cité | 96/113 | **98/113** |
| **refus sur cas négatif** | **22/22** | **0/22** |

Il cite trois fois mieux, et **il ne refuse jamais**. Sur 22 questions dont la réponse
n'est dans aucun passage, il répond 22 fois. Pour un assistant juridique, c'est le pire
des deux mondes : plus sûr de lui, et incapable de se taire.

La cause
----------
`build_agent_context` construit le prompt système depuis `DEFAULT_PROMPTS[role]`, ou
depuis un fichier `.colaig/prompts/{role}.md`. **Il ne recevait jamais le prompt de
l'espace.** Le seul consommateur de `WorkspaceContext.system_prompt` dans tout `colaig/`
était `generator.py:178` — c'est-à-dire la phase 1.

Deux mécanismes de configuration qui ne se rencontraient pas :

    phase 1 (déployée)   config.yaml → system_prompt
    phase 2 (agents)     .colaig/prompts/{role}.md

Rien ne le disait, rien ne les reliait. Activer les agents aurait fait tomber
**silencieusement** le protocole de refus, la personnalisation de l'espace, et la notice
de capacités posée le 29/08.

Ce que la correction fait, et ce qu'elle ne fait pas
------------------------------------------------------
Les deux prompts sont **complémentaires, pas concurrents** : le prompt de rôle dit
*comment* répondre, celui de l'espace dit *ce qu'est cet espace et quelles sont ses
règles*. On les compose, on ne les substitue pas.

L'espace vient **en dernier** : ses règles doivent l'emporter sur la description
générique du métier d'agent.
"""

from __future__ import annotations

import pytest

from colaig.models import WorkspaceConfig

_REGLE = "Si la réponse ne figure pas dans les passages, dis-le et ne cite aucun article."


@pytest.fixture
def espace():
    return WorkspaceConfig(workspace_id="essai", name="Essai", storage_path="/essai/")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["synthesiser", "orchestrator", "analyser"])
async def test_le_prompt_de_l_espace_atteint_chaque_agent(espace, role):
    """LE défaut : les règles de l'espace n'atteignaient aucun des trois agents."""
    from colaig.agents.context_builder import build_agent_context

    ctx = await build_agent_context(None, espace, role, prompt_espace=_REGLE)

    assert _REGLE in ctx.system_prompt, (
        f"le prompt de l'espace n'atteint pas l'agent « {role} » : ses règles — dont le "
        f"protocole de refus — sont perdues"
    )


@pytest.mark.asyncio
async def test_le_prompt_de_role_est_conserve(espace):
    """On compose, on ne substitue pas : l'agent doit savoir faire son métier."""
    from colaig.agents.context_builder import (
        DEFAULT_SYNTHESISER_PROMPT,
        build_agent_context,
    )

    ctx = await build_agent_context(None, espace, "synthesiser", prompt_espace=_REGLE)

    assert DEFAULT_SYNTHESISER_PROMPT.strip()[:40] in ctx.system_prompt, (
        "le prompt de rôle a été remplacé au lieu d'être complété"
    )


@pytest.mark.asyncio
async def test_l_espace_vient_apres_le_role(espace):
    """Les règles de l'espace doivent l'emporter, donc être lues en dernier."""
    from colaig.agents.context_builder import (
        DEFAULT_SYNTHESISER_PROMPT,
        build_agent_context,
    )

    ctx = await build_agent_context(None, espace, "synthesiser", prompt_espace=_REGLE)

    assert ctx.system_prompt.index(_REGLE) > ctx.system_prompt.index(
        DEFAULT_SYNTHESISER_PROMPT.strip()[:40]
    ), "le prompt de l'espace précède celui du rôle"


@pytest.mark.asyncio
async def test_sans_prompt_d_espace_rien_ne_change(espace):
    """Le comportement d'avant reste le comportement par défaut."""
    from colaig.agents.context_builder import (
        DEFAULT_SYNTHESISER_PROMPT,
        build_agent_context,
    )

    ctx = await build_agent_context(None, espace, "synthesiser")

    assert ctx.system_prompt.strip() == DEFAULT_SYNTHESISER_PROMPT.strip()


def test_les_trois_agents_transmettent_le_prompt():
    """Un paramètre que personne ne passe ne sert à rien.

    Douzième fois que ce dépôt écrit une capacité sans la brancher. Le test lit la
    source des trois agents plutôt que d'espérer.
    """
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig" / "agents"
    for fichier in ("synthesiser.py", "orchestrator.py", "analyser.py"):
        source = (racine / fichier).read_text(encoding="utf-8")
        assert "prompt_espace=" in source, (
            f"{fichier} appelle build_agent_context sans transmettre le prompt de "
            f"l'espace : le paramètre existe et ne sert à rien"
        )
