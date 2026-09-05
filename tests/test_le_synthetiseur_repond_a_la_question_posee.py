"""
Colaig — le Synthétiseur doit répondre à la question posée, pas à sa reformulation.

DEUX ÉCARTS MESURÉS ENTRE LE CŒUR ET LE PIPELINE
--------------------------------------------------
    | | cœur (31/08) | pipeline |
    | fantômes       | 4        | 10–12 |
    | hors contexte  | 16       | 23–28 |
    | cite l'attendu | 100/113  | 95–96 |
    | refus          | 22/22    | 19–20 + 2–3 intermittents |

Les deux chemins finissent par un seul appel au modèle. Tout l'écart est donc dans ce
qu'on lui envoie. Deux différences sont corrigées ici.

① LA CONSIGNE DE CITATION EN DUR
----------------------------------
`synthesiser.py` réinjectait « Cite tes sources entre crochets [nom_fichier] » dans les
DEUX branches d'assemblage des passages — alors que le prompt d'espace peut en
prescrire une autre (« Cite l'article, toujours »).

    Un correctif du 31/08 avait retiré cette consigne du prompt de RÔLE. Il n'a rien
    changé : elle était réinjectée trente lignes plus loin. L'expérience lancée ce
    jour-là ne testait donc pas ce qu'elle croyait tester.

② LA QUESTION ENVOYÉE N'ÉTAIT PAS CELLE DE L'UTILISATEUR
----------------------------------------------------------
    query = plan.intent.query_reformulated or ""

Le cœur envoie la question telle qu'elle a été posée. Le pipeline envoyait **la
reformulation qu'un premier modèle en avait faite**. Si elle dérive — précise ce qui
était vague, généralise ce qui était précis — le second modèle répond juste à une
question qui n'est plus la bonne.

Cela expliquerait les trois écarts d'un coup : couverture moindre, hors contexte plus
élevé, et refus intermittent — une question reformulée peut cesser d'être sans réponse.

LA FORME RETENUE : LES DEUX, MAIS PAS AU MÊME RANG
----------------------------------------------------
La **question posée** est la question. La **reformulation** est un outil de recherche,
et elle est présentée comme telle — ce sur quoi les passages ont été cherchés — jamais
comme la demande. Le modèle sait alors à quoi il répond, et sur quoi on a cherché.
"""

from __future__ import annotations

import pytest

from colaig.models import (
    ContextMode,
    ExecutionPlan,
    IncomingMessage,
    Intent,
    IntentType,
    WorkspaceContext,
)


def _message(corps: str) -> IncomingMessage:
    return IncomingMessage(message_id="$m", conversation_id="!c",
                           user_id="@u:test.local", body=corps)


def _plan(reformulee: str) -> ExecutionPlan:
    return ExecutionPlan(
        intent=Intent(intent_type=IntentType.QUESTION, query_reformulated=reformulee),
        search_results=[], tool_results={})


async def _messages(synth, plan, message, contexte):
    from colaig.agents.context_builder import build_agent_context
    from tests.conftest import MockStorage

    # Comme `synthesise()` le fait : le prompt de l'espace est passe au contexte
    # d'agent. Le batir sans lui testerait un chemin que la production n'emprunte pas.
    ctx_agent = await build_agent_context(
        MockStorage(), None, "synthesiser",
        prompt_espace=contexte.system_prompt)
    return synth._build_messages(plan, contexte, ctx_agent, None, None, message=message)


@pytest.fixture
def synth(fake_llm, fake_storage):
    from colaig.agents.synthesiser import Synthesiser

    return Synthesiser(fake_llm, fake_storage)


@pytest.fixture
def contexte():
    return WorkspaceContext(workspace=None, mode=ContextMode.ASSISTANT,
                            system_prompt="Cite l'article, toujours.")


# ─────────────────────────────────────────────────────────────────────────────
# ② La question posée
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_le_dernier_message_porte_la_question_posee(synth, contexte):
    """LE défaut. Le modèle répondait à la reformulation."""
    msgs = await _messages(
        synth, _plan("procédure applicable aux marchés de travaux publics"),
        _message("et pour les petits marchés ?"), contexte)

    dernier = [m for m in msgs if m["role"] == "user"][-1]
    assert "et pour les petits marchés ?" in dernier["content"], (
        f"le modele repond a la reformulation, pas a la question : {dernier['content']!r}")


@pytest.mark.asyncio
async def test_la_reformulation_reste_disponible_mais_subordonnee(synth, contexte):
    """Elle dit sur quoi on a cherché — c'est utile, ce n'est pas la demande."""
    msgs = await _messages(
        synth, _plan("procédure applicable aux marchés de travaux publics"),
        _message("et pour les petits marchés ?"), contexte)

    tout = "\n".join(m["content"] for m in msgs)
    assert "marchés de travaux publics" in tout, "la reformulation est perdue"

    dernier = [m for m in msgs if m["role"] == "user"][-1]
    assert dernier["content"].strip().startswith("et pour les petits marchés ?"), (
        "la question posee doit venir en tete du message final")


@pytest.mark.asyncio
async def test_une_reformulation_identique_n_est_pas_repetee(synth, contexte):
    """Quand elle n'apporte rien, la redire dilue pour rien."""
    msgs = await _messages(synth, _plan("quel délai ?"), _message("quel délai ?"),
                           contexte)

    tout = "\n".join(m["content"] for m in msgs)
    assert tout.count("quel délai ?") == 1, f"reformulation redondante : {tout!r}"


@pytest.mark.asyncio
async def test_sans_message_la_reformulation_sert_encore(synth, contexte):
    """LA borne. Certains appelants n'ont pas le message d'origine.

    Leur rendre une question vide serait pire que de leur rendre la reformulation.
    """
    msgs = await _messages(synth, _plan("quel délai pour le certificat ?"), None,
                           contexte)

    dernier = [m for m in msgs if m["role"] == "user"][-1]
    assert "quel délai pour le certificat ?" in dernier["content"]


# ─────────────────────────────────────────────────────────────────────────────
# ① Une seule grammaire de citation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_consigne_en_dur_ne_contredit_plus_l_espace(synth, contexte):
    """Elle était réinjectée dans les deux branches, après l'avoir retirée du rôle."""
    from colaig.models import DocumentChunk, SearchResult

    plan = _plan("q")
    plan.search_results = [SearchResult(
        chunk=DocumentChunk(text="Article L2123-1 — …", source_path="/e/a.md",
                            source_name="a.md"), score=0.9)]

    msgs = await _messages(synth, plan, _message("q"), contexte)
    systeme = "\n".join(m["content"] for m in msgs if m["role"] == "system")

    assert "[nom_du_fichier]" not in systeme, (
        "la consigne en dur contredit celle de l'espace")
    assert "Cite l'article" in systeme, "les regles de l'espace doivent rester"


def test_les_deux_branches_sont_traitees():
    """LA borne : `synthesiser.py` assemble les passages à DEUX endroits.

    N'en corriger qu'un laisserait le mode agentique porter le défaut — et c'est
    précisément le mode qu'on cherche à pouvoir activer.
    """
    from pathlib import Path

    source = Path("colaig/agents/synthesiser.py").read_text(encoding="utf-8")
    assert "[nom_du_fichier]" not in source, (
        "la consigne en dur subsiste dans une branche")
