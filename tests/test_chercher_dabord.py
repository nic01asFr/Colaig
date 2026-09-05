"""
Colaig — chercher d'abord, au lieu de prédire s'il faut chercher (D68).

Ce que le tronc faisait
-------------------------
L'Analyseur produisait `needs_rag` **avant toute recherche**. S'il valait faux :

- `_filter_registry_for_intent` retirait tous les outils de recherche — le modèle ne
  pouvait plus en appeler un ;
- `_plan_steps` n'ajoutait aucune étape `rag_search`.

**Un modèle jugeait donc à l'avance si le corpus valait d'être consulté, sans l'avoir
consulté.** C'est une prédiction là où un fait était disponible.

Pourquoi la porte ne protégeait rien
--------------------------------------
Mesuré le 30/08/2026 sur la pile de production, corpus de 1021 articles :

    latence de recherche       médiane 1,6 ms  (min 1,5 · max 2,4)
    embedding d'une question   0 ms en moyenne (cache)

Contre ~1 000 ms pour un appel de génération. **La porte ne faisait économiser que du
bruit** — et coûtait potentiellement des refus : 4,9 % des cas refusent alors que le
passage était disponible.

Ce qui est conservé, et pourquoi
----------------------------------
**Une salutation pure ne cherche pas.** « bonjour » n'a rien à voir dans le corpus, et
le raccourci évite un aller-retour de modèle entier — c'est le critère mesuré de L4.4
(« 1 appel LLM sur bonjour »). La borne est donc `GREETING` **et** `needs_rag=False`,
pas `needs_rag` seul.

Ce que `needs_rag` devient
----------------------------
Une **observation de l'Analyseur**, plus une décision. Elle reste produite et
journalisée — elle dira, en mesure, à quelle fréquence l'Analyseur se serait trompé.
"""

from __future__ import annotations

import pytest

from colaig.models import (
    AgentDirectives,
    ContextMode,
    Intent,
    IntentType,
    WorkspaceConfig,
    WorkspaceContext,
)


@pytest.fixture
def contexte():
    espace = WorkspaceConfig(workspace_id="essai", name="Essai",
                             storage_path="/espace-test/")
    return WorkspaceContext(workspace=espace, mode=ContextMode.ASSISTANT)


def _orchestrateur():
    from colaig.agents.orchestrator import Orchestrator
    from tests.conftest import MockStorage

    class _Retriever:
        async def retrieve(self, *a, **k):
            return []

    return Orchestrator(MockStorage(), _Retriever())


# ─────────────────────────────────────────────────────────────────────────────
# La recherche a lieu, quoi qu'en dise l'Analyseur
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_question_cherche_meme_si_l_analyseur_dit_non(contexte):
    """LE lot. Une question documentaire doit consulter le corpus.

    L'Analyseur pouvait décider qu'elle n'en valait pas la peine — sans regarder.
    """
    intent = Intent(intent_type=IntentType.QUESTION,
                    query_reformulated="quel est le seuil ?",
                    needs_rag=False)

    plan = await _orchestrateur().execute(intent, contexte)

    assert any(e.step_type == "rag_search" for e in plan.steps), (
        "aucune recherche : la porte `needs_rag` empêche encore de consulter le corpus"
    )


@pytest.mark.asyncio
async def test_une_salutation_pure_ne_cherche_toujours_pas(contexte):
    """La borne conservée : « bonjour » n'a rien à voir dans le corpus.

    Sans elle, on paierait un aller-retour de modèle pour chaque salutation — le
    critère mesuré de L4.4.
    """
    intent = Intent(intent_type=IntentType.GREETING, needs_rag=False)

    plan = await _orchestrateur().execute(intent, contexte)

    assert plan.steps == [], "une salutation pure ne doit engager aucune étape"


@pytest.mark.asyncio
async def test_une_salutation_qui_demande_le_corpus_cherche(contexte):
    """`GREETING` avec `needs_rag=True` — « bonjour, quel est le seuil ? »."""
    intent = Intent(intent_type=IntentType.GREETING,
                    query_reformulated="quel est le seuil ?",
                    needs_rag=True)

    plan = await _orchestrateur().execute(intent, contexte)

    assert any(e.step_type == "rag_search" for e in plan.steps)


@pytest.mark.asyncio
async def test_la_recherche_precede_les_autres_etapes(contexte):
    """Chercher d'abord : le résultat doit pouvoir guider ce qui suit."""
    intent = Intent(intent_type=IntentType.QUESTION,
                    query_reformulated="guide",
                    needs_rag=False,
                    orchestrator_directives=AgentDirectives(
                        target_agent="orchestrator",
                        resources_to_target=["documents/guide.txt"]))

    plan = await _orchestrateur().execute(intent, contexte)
    types = [e.step_type for e in plan.steps]

    assert types and types[0] == "rag_search", f"ordre inattendu : {types}"


# ─────────────────────────────────────────────────────────────────────────────
# Les outils de recherche restent disponibles
# ─────────────────────────────────────────────────────────────────────────────


class _Registre:
    def __init__(self, noms):
        self._noms = list(noms)

    def names(self):
        return list(self._noms)

    def filter_by_names(self, noms):
        return _Registre(noms)


_OUTILS = ["search_documents", "search_document_index", "list_documents",
           "fetch_document", "assess_completion", "create_document"]


def test_les_outils_de_recherche_ne_sont_plus_retires():
    """Le modèle doit pouvoir consulter le corpus s'il le juge utile.

    Les lui retirer d'avance, c'est décider à sa place sur la foi d'une prédiction.
    """
    orch = _orchestrateur()
    intent = Intent(intent_type=IntentType.QUESTION, needs_rag=False)

    restant = orch._filter_registry_for_intent(_Registre(_OUTILS), intent).names()

    for outil in ("search_documents", "search_document_index", "list_documents"):
        assert outil in restant, f"{outil} retiré alors que `needs_rag` ne décide plus"


def test_le_filtrage_des_outils_destructifs_est_intact():
    """L2.5b n'est pas touché : `needs_tools=False` écarte toujours le destructif."""
    orch = _orchestrateur()
    intent = Intent(intent_type=IntentType.QUESTION, needs_rag=False,
                    needs_tools=False)

    restant = orch._filter_registry_for_intent(_Registre(_OUTILS), intent).names()

    assert "create_document" not in restant, (
        "un outil destructif survit à `needs_tools=False` — L2.5b est cassé"
    )
    assert "assess_completion" in restant
