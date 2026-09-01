"""
Contrat — le garde-fou de provenance obeit a l'espace, et le pipeline agent l'applique.

Deux ecarts mesures le 01/09/2026, qui rendaient le garde-fou inoperant la ou il
aurait servi.

1. LA DECISION ETAIT GLOBALE, LE BESOIN NE L'EST PAS
   `COLAIG_GARDE_FOU_ENABLED` est une variable d'environnement : elle vaut pour toute
   l'instance. Or Colaig est multi-tenant — un dossier, une instance. Un fonds
   juridique veut le garde-fou ; la FAQ RH voisine serait rendue muette par lui. Une
   instance qui heberge les deux ne pouvait donc l'activer nulle part. Le `TODO-HAUTE`
   de `generator.py` le disait depuis le 23/08 : le reglage appartient a l'espace.

2. LE PIPELINE AGENT N'AVAIT AUCUN GARDE-FOU
   `synthesiser.py` : zero occurrence. Le coeur en avait un, inactif ; le pipeline n'en
   avait meme pas le code. Rejoue sur ses reponses archivees, le garde-fou y attrape
   14 reponses fautives sur 14, sans en abimer aucune sur 52 saines. Activer le
   pipeline sans lui, c'etait donc perdre ce controle en chemin.

La grammaire suit le meme chemin que le drapeau, et pour la meme raison : mesure du
01/09, sans le format « clause » le garde-fou remplace par un refus la reponse juste de
mp-013, qui citait « Article 4.1 » du CCAG Travaux. Un garde-fou aveugle a la grammaire
de son corpus ne protege pas la reponse, il la detruit.
"""
from __future__ import annotations

import pytest

from colaig.models import (
    ContextMode,
    DocumentChunk,
    ExecutionPlan,
    Intent,
    IntentType,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.rag.generator import Generator
from tests.fakes import FakeLLM

PASSAGE_CODE = (
    "Titre Ier\n\nArticle L2113-10\n\n"
    "Les marches sont passes en lots separes, sauf si leur objet ne permet pas "
    "l'identification de prestations distinctes."
)
# Le cas mp-013 : un CCAG numerote « 4.1 », pas « L2113-10 ».
PASSAGE_CCAG = (
    "CCAG Travaux - Chapitre 1er\n\nArticle 4.1\n\n"
    "En cas de contradiction entre les stipulations des pieces contractuelles, "
    "celles-ci prevalent dans l'ordre suivant : l'acte d'engagement, puis le CCAP."
)


def _resultats(texte: str = PASSAGE_CODE, section: str = "Article L2113-10"):
    chunk = DocumentChunk(text=texte, source_path="ccp.md", source_name="ccp.md",
                          section=section, position=0, doc_type="md")
    return [SearchResult(chunk=chunk, score=0.8, rank=0)]


def _contexte(**reglages) -> WorkspaceContext:
    return WorkspaceContext(
        workspace=WorkspaceConfig(workspace_id="ccp", name="Commande publique",
                                  storage_path="/ccp/", **reglages),
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu reponds sur la commande publique.",
    )


async def _coeur(reponse: str, contexte, resultats=None) -> str:
    llm = FakeLLM()
    llm.chat_responses = [reponse]
    sortie = await Generator(llm).generate("une question", contexte,
                                           resultats if resultats is not None else _resultats())
    return sortie.text


# ── L'espace decide ──────────────────────────────────────────────────────────


async def test_un_espace_qui_declare_le_garde_fou_l_obtient_sans_variable(monkeypatch):
    """Sans cela, une instance hebergeant deux corpus ne peut l'activer nulle part."""
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    sortie = await _coeur("Le seuil est fixe par L9999-1.",
                          _contexte(garde_fou_provenance=True))
    assert "ne figure pas dans les documents consultés" in sortie


async def test_un_espace_qui_ne_le_declare_pas_n_est_pas_touche(monkeypatch):
    """Le defaut protege les espaces sans articles, qui sont la majorite."""
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    reponse = "La demande de conge se depose quinze jours avant. [guide.txt]"
    assert await _coeur(reponse, _contexte()) == reponse


async def test_la_variable_globale_reste_un_repli(monkeypatch):
    """Le deploiement en service s'en sert : la retirer d'un coup couperait le controle."""
    monkeypatch.setenv("COLAIG_GARDE_FOU_ENABLED", "1")
    sortie = await _coeur("Le seuil est fixe par L9999-1.", _contexte())
    assert "ne figure pas dans les documents consultés" in sortie


# ── La grammaire suit l'espace ───────────────────────────────────────────────


async def test_sans_grammaire_declaree_une_citation_de_ccag_est_detruite(monkeypatch):
    """Le defaut mesure — fige tel quel, pour que sa correction soit visible.

    Ce test dit ce qu'il en coute de ne rien declarer : une reponse juste devient un
    refus. Il ne valide pas ce comportement, il en garde la trace.
    """
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    sortie = await _coeur(
        "L'ordre de priorite des pieces figure a l'Article 4.1 du CCAG Travaux.",
        _contexte(garde_fou_provenance=True),
        _resultats(PASSAGE_CCAG, "Article CCAG Travaux 4"))
    assert "ne figure pas dans les documents consultés" in sortie


async def test_avec_la_grammaire_declaree_la_citation_de_ccag_est_reconnue(monkeypatch):
    """Le cas mp-013, de bout en bout : la reponse juste doit survivre."""
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    reponse = "L'ordre de priorite des pieces figure a l'Article 4.1 du CCAG Travaux."
    sortie = await _coeur(
        reponse,
        _contexte(garde_fou_provenance=True, format_citation=["code", "clause"]),
        _resultats(PASSAGE_CCAG, "Article CCAG Travaux 4"))
    assert sortie == reponse


# ── Le pipeline agent applique le meme controle ──────────────────────────────


@pytest.mark.asyncio
async def test_le_pipeline_agent_applique_le_garde_fou(fake_llm, fake_storage, monkeypatch):
    """Le coeur controlait, le pipeline non : activer le pipeline perdait le controle."""
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    from colaig.agents.synthesiser import Synthesiser

    fake_llm.chat_responses = ["Le seuil est fixe par L9999-1."]
    plan = ExecutionPlan(
        intent=Intent(intent_type=IntentType.QUESTION, query_reformulated="seuil"),
        search_results=_resultats(), tool_results={})
    sortie = await Synthesiser(fake_llm, fake_storage).synthesise(
        plan, _contexte(garde_fou_provenance=True))
    assert "ne figure pas dans les documents consultés" in sortie.text


@pytest.mark.asyncio
async def test_le_pipeline_agent_respecte_aussi_le_defaut(fake_llm, fake_storage, monkeypatch):
    """Inactif veut dire inactif des deux cotes, sans quoi les deux ne sont pas comparables."""
    monkeypatch.delenv("COLAIG_GARDE_FOU_ENABLED", raising=False)
    from colaig.agents.synthesiser import Synthesiser

    reponse = "La demande de conge se depose quinze jours avant."
    fake_llm.chat_responses = [reponse]
    plan = ExecutionPlan(
        intent=Intent(intent_type=IntentType.QUESTION, query_reformulated="conge"),
        search_results=_resultats(), tool_results={})
    sortie = await Synthesiser(fake_llm, fake_storage).synthesise(plan, _contexte())
    assert sortie.text == reponse
