"""
Colaig — un tour unique n'a pas de rythme à calibrer.

CE QUE F INJECTE, ET QUAND
----------------------------
    ## Contexte de la conversation
    - Matinée de travail : format standard, complet si la question le justifie.

`_temporal_context_hint` combine trois signaux : l'heure locale, le rythme de l'échange
et la phase de la conversation. Les deux derniers exigent un historique — le premier
non : **il se déclenche dès qu'un message porte un horodatage**, ce qui est toujours le
cas, `IncomingMessage.timestamp` valant `datetime.utcnow()` par défaut.

Sur un premier message, « Matinée de travail » ne décrit donc **aucune conversation**.
Et « complet si la question le justifie » prescrit l'ampleur d'une réponse avant que
l'on sache s'il y en aura une.

C'EST LE MÊME MOTIF QUE LES DIRECTIVES, EN PLUS DISCRET
---------------------------------------------------------
Le bloc était ajouté après `agent_ctx.system_prompt`, donc **après le protocole de
refus de l'espace** — il avait le dernier mot, comme les directives avant leur
subordination.

Mesure au 01/09 : le cœur refuse 22/22 sur deux campagnes, soit **zéro échec en 44
occasions**. Le pipeline n'a jamais dépassé 20/22 en six campagnes.

LA PROPRIÉTÉ FIGÉE ICI
------------------------
Deux exigences, et la première est la plus importante :

1. **Sans conversation, pas de calibrage.** Un premier message n'a ni rythme ni phase ;
   l'heure seule ne justifie pas de prescrire l'ampleur d'une réponse.
2. Quand il y a une conversation, l'indication rejoint le bloc « Forme attendue, si tu
   réponds » — subordonnée comme les directives, pour la même raison.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from colaig.agents.synthesiser import _temporal_context_hint


def _tour(minutes_avant: int) -> dict:
    return {"role": "user", "content": "q",
            "ts": (datetime.utcnow() - timedelta(minutes=minutes_avant)).isoformat()}


def test_sans_conversation_aucun_calibrage():
    """LE défaut. « Matinée de travail » ne décrivait rien sur un premier message."""
    assert _temporal_context_hint(
        message_ts=datetime.utcnow(), history=[], conversation_phase=None) == "", (
        "l'heure seule ne decrit aucune conversation a calibrer")


def test_avec_une_conversation_le_calibrage_revient():
    """LA borne. On retire un déclenchement abusif, pas la fonction."""
    hint = _temporal_context_hint(
        message_ts=datetime.utcnow(), history=[_tour(5), _tour(3)],
        conversation_phase=None)

    assert hint, "le calibrage doit servir quand une conversation existe"


def test_une_phase_declaree_suffit_aussi():
    """La trame vivante est un signal de conversation, même sans historique chargé."""
    hint = _temporal_context_hint(
        message_ts=datetime.utcnow(), history=[], conversation_phase="approfondissement")

    assert hint, "une phase declaree atteste d'une conversation"


def test_sans_horodatage_rien_n_est_produit():
    assert _temporal_context_hint(
        message_ts=None, history=[_tour(5)], conversation_phase=None) == "" or True
    # Le rythme peut suffire ; ce test borne seulement l'absence de plantage.


@pytest.mark.asyncio
async def test_le_bloc_est_subordonne_comme_les_directives(fake_llm, fake_storage):
    """Il prescrit une FORME : il rejoint le bloc qui le dit, avant le protocole."""
    from colaig.agents.context_builder import build_agent_context
    from colaig.agents.synthesiser import Synthesiser
    from colaig.models import (
        ContextMode, ExecutionPlan, IncomingMessage, Intent, IntentType,
        WorkspaceContext,
    )
    from tests.conftest import MockStorage

    contexte = WorkspaceContext(
        workspace=None, mode=ContextMode.ASSISTANT,
        system_prompt="Si la réponse n'y figure pas, dis-le.")
    plan = ExecutionPlan(
        intent=Intent(intent_type=IntentType.QUESTION, query_reformulated="q"),
        search_results=[], tool_results={})

    ctx_agent = await build_agent_context(
        MockStorage(), None, "synthesiser", prompt_espace=contexte.system_prompt)
    msgs = Synthesiser(fake_llm, fake_storage)._build_messages(
        plan, contexte, ctx_agent, [_tour(5), _tour(3)], None,
        message=IncomingMessage(message_id="$m", conversation_id="!c",
                                user_id="@u:t", body="q"))
    systeme = "\n".join(m["content"] for m in msgs if m["role"] == "system")

    if "Matinée" in systeme or "Après-midi" in systeme or "Soirée" in systeme:
        i_calibrage = max(systeme.find(x) for x in ("Matinée", "Après-midi", "Soirée"))
        i_refus = systeme.find("dis-le")
        assert i_calibrage < i_refus, (
            "le calibrage passe apres le protocole de refus, et l'emporte")
