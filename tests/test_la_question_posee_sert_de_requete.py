"""On ne cherchait jamais avec ce que l'utilisateur a ecrit.

CE QUI A CONDUIT ICI
----------------------
Six campagnes du 04-05/09/2026 sur le service, jugees au grain du passage, sur les
113 cas dores porteurs d'un article attendu :

    article TOUJOURS servi     51
    servi PARFOIS              53
    JAMAIS servi                9

Le probleme n'est pas que la recherche ne trouve pas : c'est qu'elle trouve une fois
sur deux. Un cas sur deux bascule d'une campagne a l'autre sans qu'on ait rien touche,
et deux campagnes IDENTIQUES different sur 18 cas — plus que tous les ecarts de
reglage qu'on cherchait a trancher.

LA CAUSE
----------
`_execute_rag_search` cherche avec `intent.query_reformulated`, et avec rien d'autre.
C'est une chaine ECRITE PAR LE LLM a chaque tour. La question de l'utilisateur, elle,
ne sert jamais de requete — pas une seule fois dans le pipeline agent.

Une reformulation tiree autrement ramene d'autres passages : la recherche herite donc
entierement de l'instabilite du modele qui l'a formulee. Meme a temperature nulle, le
service ne rend pas deux fois la meme chaine.

CE QUE FIXE CE TEST
---------------------
La question POSEE est toujours l'une des requetes. Elle ne remplace pas la
reformulation — qui apporte le vocabulaire du domaine la ou l'usager emploie le sien —
elle lui ajoute un socle qui, lui, ne bouge pas d'un tour a l'autre.
"""

from __future__ import annotations

import pytest

from colaig.agents.orchestrator import Orchestrator
from colaig.models import (
    ContextMode,
    ExecutionPlan,
    ExecutionStep,
    Intent,
    IntentType,
    WorkspaceConfig,
    WorkspaceContext,
)


class RetrieverTemoin:
    def __init__(self):
        self.requetes: list[str] = []

    async def retrieve(self, query="", k=5, score_threshold=0.3, store=None, bm25_store=None):
        self.requetes.append(query)
        return []

    async def retrieve_many(self, queries, k=5, score_threshold=0.3, store=None, bm25_store=None):
        self.requetes.extend(queries)
        return [[] for _ in queries]


@pytest.fixture
def contexte():
    return WorkspaceContext(
        workspace=WorkspaceConfig(workspace_id="mesure", name="Mesure",
                                  storage_path="/espace-mesure/", max_results=5,
                                  similarity_threshold=0.3),
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig.",
    )


def _plan(question: str, reformulee: str) -> ExecutionPlan:
    return ExecutionPlan(intent=Intent(
        intent_type=IntentType.QUESTION,
        query_reformulated=reformulee,
        query_posee=question,
    ))


@pytest.mark.asyncio
async def test_la_question_posee_est_toujours_cherchee(contexte):
    retriever = RetrieverTemoin()
    orch = Orchestrator(storage=None, retriever=retriever)

    plan = _plan("Dois-je verser une avance à mon titulaire ?",
                 "conditions de versement de l'avance au titulaire du marché")
    step = ExecutionStep(step_type="rag_search", params={"query": plan.intent.query_reformulated})
    await orch._execute_rag_search(step, plan, contexte)

    assert "Dois-je verser une avance à mon titulaire ?" in retriever.requetes


@pytest.mark.asyncio
async def test_la_reformulation_reste_cherchee(contexte):
    """Le socle s'ajoute, il ne remplace pas : la reformulation porte le vocabulaire."""
    retriever = RetrieverTemoin()
    orch = Orchestrator(storage=None, retriever=retriever)

    plan = _plan("Dois-je verser une avance ?", "avance obligatoire marché public titulaire")
    step = ExecutionStep(step_type="rag_search", params={"query": plan.intent.query_reformulated})
    await orch._execute_rag_search(step, plan, contexte)

    assert "avance obligatoire marché public titulaire" in retriever.requetes
    assert len(retriever.requetes) == 2


@pytest.mark.asyncio
async def test_une_requete_identique_n_est_pas_cherchee_deux_fois(contexte):
    """Quand le modele rend la question telle quelle, on ne paie pas deux fois."""
    retriever = RetrieverTemoin()
    orch = Orchestrator(storage=None, retriever=retriever)

    plan = _plan("Dois-je verser une avance ?", "Dois-je verser une avance ?")
    step = ExecutionStep(step_type="rag_search", params={"query": plan.intent.query_reformulated})
    await orch._execute_rag_search(step, plan, contexte)

    assert retriever.requetes == ["Dois-je verser une avance ?"]


@pytest.mark.asyncio
async def test_sans_question_posee_le_comportement_ne_change_pas(contexte):
    """Un Intent construit ailleurs, sans ce champ, cherche comme avant."""
    retriever = RetrieverTemoin()
    orch = Orchestrator(storage=None, retriever=retriever)

    plan = ExecutionPlan(intent=Intent(intent_type=IntentType.QUESTION,
                                       query_reformulated="avance au titulaire"))
    step = ExecutionStep(step_type="rag_search", params={"query": "avance au titulaire"})
    await orch._execute_rag_search(step, plan, contexte)

    assert retriever.requetes == ["avance au titulaire"]
