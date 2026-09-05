"""L'Orchestrateur cherche-t-il avec l'index lexical ?

POURQUOI CE TEST EXISTE
-----------------------
`COLAIG_HYBRID_SEARCH_ENABLED` construit un `BM25Store` par espace, le remplit a
l'indexation, le persiste dans `bm25.pkl` et le recharge au demarrage. Le coeur
(`handlers._handle_phase1`) le passe bien a `retrieve()`. L'Orchestrateur, lui, le
recevait dans son constructeur et ne s'en servait nulle part : ni pour la recherche
de son plan, ni pour l'outil `search_documents` qu'il enregistre.

`retriever.retrieve()` n'active la fusion RRF que si `bm25_store` lui parvient. Le
drapeau etait donc decoratif pour le pipeline agent : la mesure du 04/09/2026 qui
concluait « BM25 n'ameliore rien » ne mesurait rien du tout.

Ces tests fixent le cablage, pas le resultat de la recherche.
"""

import pytest

from colaig.agents.orchestrator import Orchestrator
from colaig.agents.tools.rag_tools import create_search_handler
from colaig.models import (
    ContextMode,
    Intent,
    IntentType,
    ExecutionPlan,
    ExecutionStep,
    WorkspaceConfig,
    WorkspaceContext,
)


class RetrieverTemoin:
    """Retient les arguments de chaque appel, ne cherche rien."""

    def __init__(self):
        self.appels: list[dict] = []

    async def retrieve(self, query="", k=5, score_threshold=0.3, store=None, bm25_store=None):
        self.appels.append({
            "query": query, "k": k, "score_threshold": score_threshold,
            "store": store, "bm25_store": bm25_store,
        })
        return []


@pytest.fixture
def espace():
    return WorkspaceConfig(
        workspace_id="mesure",
        name="Espace de mesure",
        storage_path="/espace-mesure/",
        max_results=5,
        similarity_threshold=0.3,
    )


@pytest.fixture
def contexte(espace):
    return WorkspaceContext(
        workspace=espace,
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu es Colaig.",
        available_tools=["search"],
    )


def _plan():
    return ExecutionPlan(intent=Intent(intent_type=IntentType.QUESTION))


def _orchestrateur(retriever, *, vectoriel=None, lexical=None):
    return Orchestrator(
        storage=None,
        retriever=retriever,
        workspace_stores=({"mesure": vectoriel} if vectoriel is not None else None),
        bm25_stores=({"mesure": lexical} if lexical is not None else None),
    )


@pytest.mark.asyncio
async def test_la_recherche_du_plan_recoit_l_index_lexical(contexte):
    retriever = RetrieverTemoin()
    vectoriel, lexical = object(), object()
    orch = _orchestrateur(retriever, vectoriel=vectoriel, lexical=lexical)

    step = ExecutionStep(step_type="rag_search", params={"query": "delai de paiement"})
    await orch._execute_rag_search(step, _plan(), contexte)

    assert len(retriever.appels) == 1
    assert retriever.appels[0]["store"] is vectoriel
    assert retriever.appels[0]["bm25_store"] is lexical


@pytest.mark.asyncio
async def test_sans_index_lexical_la_recherche_reste_vectorielle(contexte):
    retriever = RetrieverTemoin()
    vectoriel = object()
    orch = _orchestrateur(retriever, vectoriel=vectoriel)

    step = ExecutionStep(step_type="rag_search", params={"query": "delai de paiement"})
    await orch._execute_rag_search(step, _plan(), contexte)

    assert retriever.appels[0]["store"] is vectoriel
    assert retriever.appels[0]["bm25_store"] is None


@pytest.mark.asyncio
async def test_l_outil_search_documents_recoit_l_index_lexical(contexte):
    """L'outil que le modele appelle lui-meme cherche dans les deux index."""
    retriever = RetrieverTemoin()
    vectoriel, lexical = object(), object()
    orch = _orchestrateur(retriever, vectoriel=vectoriel, lexical=lexical)

    handler = orch._handler_de_recherche(contexte)
    assert handler is not None
    await handler("delai de paiement")

    assert retriever.appels[0]["store"] is vectoriel
    assert retriever.appels[0]["bm25_store"] is lexical


@pytest.mark.asyncio
async def test_le_handler_transmet_l_index_lexical():
    """Contrat de `create_search_handler`, independamment de l'Orchestrateur."""
    retriever = RetrieverTemoin()
    vectoriel, lexical = object(), object()

    handler = create_search_handler(retriever, store=vectoriel, bm25_store=lexical)
    await handler("delai de paiement", k=3, threshold=0.2)

    assert retriever.appels[0]["k"] == 3
    assert retriever.appels[0]["store"] is vectoriel
    assert retriever.appels[0]["bm25_store"] is lexical


@pytest.mark.asyncio
async def test_la_fusion_se_voit_dans_le_journal(caplog):
    """Sans cette ligne, on ne peut pas savoir laquelle des deux recherches tourne.

    Une campagne du 04/09/2026 a conclu « BM25 n'apporte rien » alors que la fusion
    n'avait jamais eu lieu : le drapeau etait actif, l'index construit et persiste, et
    l'orchestrateur ne transmettait pas le store. Aucune trace ne permettait de le voir.
    """
    import logging

    from colaig.models import DocumentChunk, SearchResult
    from colaig.rag.retriever import Retriever

    class _Embeddings:
        async def embed_query(self, texte):
            return [1.0, 0.0]

        async def embed_text(self, texte):
            return [1.0, 0.0]

    def _chunk(nom):
        return DocumentChunk(text=f"un passage extrait de {nom}", source_path=f"/{nom}",
                             source_name=nom, position=0, section="")

    class _Store:
        count = 1

        def search(self, embedding, k=5):
            return [SearchResult(chunk=_chunk("vectoriel.md"), score=0.9, rank=1)]

    class _BM25:
        count = 1

        def search(self, query, k=10):
            return [(_chunk("lexical.md"), 3.2)]

    retriever = Retriever(embedding_service=_Embeddings(), store=_Store(), albert_client=None)

    with caplog.at_level(logging.INFO, logger="colaig.rag.retriever"):
        await retriever.retrieve("delai de paiement", k=5, bm25_store=_BM25())

    assert any("fusion RRF" in r.getMessage() for r in caplog.records), caplog.text
