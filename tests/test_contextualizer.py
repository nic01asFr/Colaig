"""Tests de ChunkContextualizer — contextualisation LLM des chunks à l'indexation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.models import DocumentChunk
from colaig.rag.contextualizer import ChunkContextualizer


def make_chunk(text: str, source_path: str = "/doc.pdf", position: int = 0) -> DocumentChunk:
    return DocumentChunk(
        text=text,
        source_path=source_path,
        source_name="doc.pdf",
        position=position,
    )


def make_llm(response: str = "Contexte généré par le LLM."):
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=response)
    return llm


# ── enrich_batch — cas nominal ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_batch_adds_prefix():
    llm = make_llm("Ce document traite des ressources humaines.")
    ctx = ChunkContextualizer(llm, model="test-model")
    chunks = [make_chunk("Congés annuels : 25 jours par an.")]
    enriched = await ctx.enrich_batch(chunks, workspace_name="RH", workspace_description="Gestion RH")
    assert len(enriched) == 1
    assert enriched[0].contextual_prefix == "Ce document traite des ressources humaines."


@pytest.mark.asyncio
async def test_enrich_batch_multiple_chunks():
    llm = make_llm("Contexte X.")
    ctx = ChunkContextualizer(llm)
    chunks = [make_chunk(f"Texte chunk {i}", position=i) for i in range(3)]
    enriched = await ctx.enrich_batch(chunks, workspace_name="Test")
    assert len(enriched) == 3
    assert all(c.contextual_prefix == "Contexte X." for c in enriched)


@pytest.mark.asyncio
async def test_enrich_batch_empty_returns_empty():
    llm = make_llm()
    ctx = ChunkContextualizer(llm)
    result = await ctx.enrich_batch([])
    assert result == []


@pytest.mark.asyncio
async def test_enrich_batch_uses_workspace_context():
    """Vérifie que le prompt inclut bien les infos workspace."""
    llm = make_llm("Contexte.")
    ctx = ChunkContextualizer(llm)
    chunks = [make_chunk("Procédure d'achat public.")]
    await ctx.enrich_batch(
        chunks,
        workspace_name="Marchés publics",
        workspace_description="Procédures administratives",
        workspace_system_prompt="Tu es un assistant spécialisé en marchés publics.",
    )
    # Vérifier que le chat a bien été appelé avec les infos workspace dans le prompt
    call_args = llm.chat.call_args
    user_content = call_args.kwargs.get("messages", [{}])[0].get("content", "")
    assert "Marchés publics" in user_content
    assert "Procédures administratives" in user_content


# ── graceful fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_batch_fallback_on_llm_error():
    """En cas d'erreur LLM sur un chunk, retourner le chunk original sans préfixe."""
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=Exception("LLM down"))
    ctx = ChunkContextualizer(llm)
    chunk = make_chunk("Texte important.")
    enriched = await ctx.enrich_batch([chunk])
    assert len(enriched) == 1
    assert enriched[0].contextual_prefix == ""
    assert enriched[0].text == "Texte important."


@pytest.mark.asyncio
async def test_enrich_batch_fallback_on_empty_response():
    """Réponse vide du LLM → pas de préfixe."""
    llm = make_llm("")
    ctx = ChunkContextualizer(llm)
    enriched = await ctx.enrich_batch([make_chunk("Contenu.")])
    assert enriched[0].contextual_prefix == ""


@pytest.mark.asyncio
async def test_enrich_batch_fallback_on_too_long_response():
    """Réponse LLM > 500 chars → rejetée (qualité douteuse)."""
    llm = make_llm("X" * 501)
    ctx = ChunkContextualizer(llm)
    enriched = await ctx.enrich_batch([make_chunk("Contenu.")])
    assert enriched[0].contextual_prefix == ""


@pytest.mark.asyncio
async def test_enrich_batch_partial_failure():
    """Un chunk échoue, les autres sont enrichis correctement."""
    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("erreur chunk 2")
        return "Bon contexte."

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=side_effect)
    ctx = ChunkContextualizer(llm)
    chunks = [make_chunk(f"Texte {i}", position=i) for i in range(3)]
    enriched = await ctx.enrich_batch(chunks)
    assert len(enriched) == 3
    # chunk 0 et 2 enrichis, chunk 1 sans préfixe
    assert enriched[0].contextual_prefix == "Bon contexte."
    assert enriched[1].contextual_prefix == ""
    assert enriched[2].contextual_prefix == "Bon contexte."


# ── model param ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_uses_specified_model():
    llm = make_llm("Contexte ok.")
    ctx = ChunkContextualizer(llm, model="mistralai/Ministral-3-8B-Instruct-2512")
    await ctx.enrich_batch([make_chunk("Texte.")])
    call_kwargs = llm.chat.call_args.kwargs
    assert call_kwargs.get("model") == "mistralai/Ministral-3-8B-Instruct-2512"


@pytest.mark.asyncio
async def test_enrich_no_model_no_model_kwarg():
    """Sans modèle configuré, ne pas passer model= au LLM."""
    llm = make_llm("Contexte.")
    ctx = ChunkContextualizer(llm, model="")
    await ctx.enrich_batch([make_chunk("Texte.")])
    call_kwargs = llm.chat.call_args.kwargs
    assert "model" not in call_kwargs
