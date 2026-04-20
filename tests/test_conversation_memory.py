"""
Tests pour Step 3 — Mémoire Conversationnelle Contextualisée.

Couvre :
- ConversationMemory : load_relevant_history, save_turn
- Récupération sémantique vs. fallback last-N
- Toujours inclure les ALWAYS_INCLUDE_RECENT derniers messages
- Déduplication et tri chronologique
- Graceful degradation si embedding échoue
- Intégration avec load_relevant_conversation_history() dans layers.py
"""

import json
import pytest

from colaig.context.conversation_memory import ConversationMemory, ALWAYS_INCLUDE_RECENT
from colaig.context.layers import load_relevant_conversation_history


# =============================================================================
# Helpers
# =============================================================================

def _make_messages(n: int) -> list[dict]:
    """Génère n messages alternés user/assistant."""
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"Message {i}"})
    return messages


def _store_history(mock_storage, workspace_path, conversation_id, messages):
    """Pré-charge un historique dans le mock storage."""
    from colaig.context.layers import _sanitize_id
    safe_id = _sanitize_id(conversation_id)
    path = f"{workspace_path.rstrip('/')}/.colaig/conversations/{safe_id}.json"
    mock_storage.add_file(path, json.dumps(messages, ensure_ascii=False).encode("utf-8"), "application/json")


# =============================================================================
# Tests ConversationMemory — chargement
# =============================================================================

@pytest.mark.asyncio
async def test_load_empty_history(mock_storage):
    """Historique inexistant → liste vide."""
    memory = ConversationMemory(mock_storage)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=10)
    assert result == []


@pytest.mark.asyncio
async def test_load_history_within_limit(mock_storage):
    """Historique ≤ max_messages → retourne tout l'historique."""
    messages = _make_messages(5)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=10)
    assert len(result) == 5


@pytest.mark.asyncio
async def test_load_history_no_embedding_fallback(mock_storage):
    """Sans embedding service → derniers max_messages messages."""
    messages = _make_messages(20)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage, embedding_service=None)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=8)
    assert len(result) == 8
    # Derniers 8 messages
    assert result == messages[-8:]


@pytest.mark.asyncio
async def test_load_always_includes_recent_messages(mock_storage, mock_albert):
    """Les ALWAYS_INCLUDE_RECENT derniers messages sont toujours inclus."""
    messages = _make_messages(20)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage, embedding_service=mock_albert, max_retrieved=5)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=5)

    # Les derniers ALWAYS_INCLUDE_RECENT messages doivent être dans le résultat
    recent = messages[-ALWAYS_INCLUDE_RECENT:]
    for msg in recent:
        assert msg in result


@pytest.mark.asyncio
async def test_load_semantic_returns_at_most_max_messages(mock_storage, mock_albert):
    """Résultat ne dépasse jamais max_messages."""
    messages = _make_messages(30)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage, embedding_service=mock_albert)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=7)
    assert len(result) <= 7


@pytest.mark.asyncio
async def test_load_result_is_chronologically_ordered(mock_storage, mock_albert):
    """Les messages retournés sont dans l'ordre chronologique."""
    messages = _make_messages(20)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage, embedding_service=mock_albert)
    result = await memory.load_relevant_history("/ws/", "conv-1", "question", max_messages=8)

    # Vérifier que l'ordre est préservé par rapport à l'historique original
    indices = [messages.index(m) for m in result if m in messages]
    assert indices == sorted(indices)


@pytest.mark.asyncio
async def test_load_empty_query_falls_back_to_recency(mock_storage, mock_albert):
    """Requête vide → pas de recherche sémantique, derniers N messages."""
    messages = _make_messages(20)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage, embedding_service=mock_albert)
    result = await memory.load_relevant_history("/ws/", "conv-1", current_query="", max_messages=5)
    # Fallback vers derniers 5 (pas d'embedding pour requête vide)
    assert len(result) == 5
    assert result == messages[-5:]


# =============================================================================
# Tests ConversationMemory — sauvegarde
# =============================================================================

@pytest.mark.asyncio
async def test_save_turn_creates_history(mock_storage):
    """save_turn crée un fichier d'historique si inexistant."""
    memory = ConversationMemory(mock_storage)
    result = await memory.save_turn("/ws/", "conv-1", "question", "réponse", [])
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_save_turn_appends_to_existing(mock_storage):
    """save_turn ajoute aux messages existants."""
    existing = _make_messages(4)
    _store_history(mock_storage, "/ws/", "conv-1", existing)

    memory = ConversationMemory(mock_storage)
    result = await memory.save_turn("/ws/", "conv-1", "new q", "new a", existing)
    assert len(result) == 6  # 4 + 2
    assert result[-2]["content"] == "new q"
    assert result[-1]["content"] == "new a"


@pytest.mark.asyncio
async def test_save_turn_trims_to_max_stored(mock_storage):
    """save_turn tronque l'historique à max_stored."""
    existing = _make_messages(98)  # 98 messages existants
    _store_history(mock_storage, "/ws/", "conv-1", existing)

    memory = ConversationMemory(mock_storage, max_stored=100)
    result = await memory.save_turn("/ws/", "conv-1", "q", "a", existing)
    assert len(result) == 100  # 98 + 2 = 100 exactement


@pytest.mark.asyncio
async def test_save_turn_over_max_stored_trims(mock_storage):
    """Dépassement max_stored → les plus anciens sont supprimés."""
    existing = _make_messages(100)
    _store_history(mock_storage, "/ws/", "conv-1", existing)

    memory = ConversationMemory(mock_storage, max_stored=100)
    result = await memory.save_turn("/ws/", "conv-1", "q", "a", existing)
    # 100 + 2 = 102, trimmed à 100 → les 2 premiers sont perdus
    assert len(result) == 100
    assert result[-1]["content"] == "a"


@pytest.mark.asyncio
async def test_save_and_reload_consistency(mock_storage):
    """Sauvegarde puis rechargement produit un résultat cohérent."""
    memory = ConversationMemory(mock_storage)
    await memory.save_turn("/ws/", "conv-1", "question 1", "réponse 1", [])
    await memory.save_turn("/ws/", "conv-1", "question 2", "réponse 2", [])

    result = await memory.load_relevant_history("/ws/", "conv-1", "", max_messages=10)
    assert len(result) == 4
    assert result[0]["content"] == "question 1"
    assert result[-1]["content"] == "réponse 2"


# =============================================================================
# Tests layers.load_relevant_conversation_history
# =============================================================================

@pytest.mark.asyncio
async def test_layers_delegates_to_memory(mock_storage):
    """load_relevant_conversation_history délègue à ConversationMemory si fournie."""
    messages = _make_messages(5)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage)
    result = await load_relevant_conversation_history(
        mock_storage, "/ws/", "conv-1", "question", max_messages=10, conversation_memory=memory
    )
    assert len(result) == 5


@pytest.mark.asyncio
async def test_layers_fallback_without_memory(mock_storage):
    """Sans ConversationMemory → comportement classique last-N."""
    messages = _make_messages(15)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    result = await load_relevant_conversation_history(
        mock_storage, "/ws/", "conv-1", "question", max_messages=5, conversation_memory=None
    )
    assert len(result) == 5
    assert result == messages[-5:]


@pytest.mark.asyncio
async def test_layers_fallback_empty_query(mock_storage):
    """Requête vide → comportement classique même si memory fournie."""
    messages = _make_messages(10)
    _store_history(mock_storage, "/ws/", "conv-1", messages)

    memory = ConversationMemory(mock_storage)
    result = await load_relevant_conversation_history(
        mock_storage, "/ws/", "conv-1", current_query="", max_messages=5, conversation_memory=memory
    )
    assert len(result) == 5
