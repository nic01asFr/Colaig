"""Tests — agents/trame_manager.py (Phase 6)"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig.agents.trame_manager import TrameManager
from colaig.models import (
    ContextAnchor,
    ConversationTrame,
    DocumentChunk,
    ExecutionPlan,
    GeneratedResponse,
    Intent,
    IntentType,
    SearchResult,
)


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.download = AsyncMock(side_effect=Exception("not found"))
    storage.upload = AsyncMock()
    return storage


@pytest.fixture
def manager(mock_storage):
    return TrameManager(storage=mock_storage)


@pytest.fixture
def sample_trame():
    return ConversationTrame(
        conv_id="conv-123",
        workspace_id="/espace-test/",
        conversation_phase="discovery",
    )


# =============================================================================
# load
# =============================================================================

class TestTrameManagerLoad:
    @pytest.mark.asyncio
    async def test_load_returns_empty_trame_if_absent(self, manager):
        trame = await manager.load("conv-123", "/espace-test/")
        assert trame.conv_id == "conv-123"
        assert trame.conversation_phase == "discovery"
        assert trame.context_anchors == []
        assert trame.turn_count == 0

    @pytest.mark.asyncio
    async def test_load_deserializes_existing_trame(self, mock_storage, manager):
        data = {
            "conv_id": "conv-123",
            "workspace_id": "/espace-test/",
            "conversation_phase": "drafting",
            "active_behavior": "draft_mode",
            "workflow_steps": [],
            "context_anchors": [
                {
                    "anchor_type": "document",
                    "ref": "/espace-test/doc.pdf",
                    "description": "Guide RH",
                    "turn_index": 2,
                }
            ],
            "turn_count": 3,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        mock_storage.download = AsyncMock(return_value=json.dumps(data).encode("utf-8"))

        trame = await manager.load("conv-123", "/espace-test/")
        assert trame.conversation_phase == "drafting"
        assert trame.active_behavior == "draft_mode"
        assert len(trame.context_anchors) == 1
        assert trame.context_anchors[0].ref == "/espace-test/doc.pdf"
        assert trame.turn_count == 3


# =============================================================================
# update
# =============================================================================

class TestTrameManagerUpdate:
    @pytest.mark.asyncio
    async def test_update_increments_turn_count(self, manager, sample_trame):
        assert sample_trame.turn_count == 0
        await manager.update(sample_trame)
        assert sample_trame.turn_count == 1

    @pytest.mark.asyncio
    async def test_update_applies_suggested_next_phase(self, manager, sample_trame):
        intent = Intent(
            intent_type=IntentType.QUESTION,
            suggested_next_phase="analysis",
        )
        await manager.update(sample_trame, intent=intent)
        assert sample_trame.conversation_phase == "analysis"

    @pytest.mark.asyncio
    async def test_update_no_phase_change_if_none(self, manager, sample_trame):
        intent = Intent(intent_type=IntentType.QUESTION)
        await manager.update(sample_trame, intent=intent)
        assert sample_trame.conversation_phase == "discovery"

    @pytest.mark.asyncio
    async def test_update_adds_new_anchors_from_intent(self, manager, sample_trame):
        intent = Intent(
            intent_type=IntentType.QUESTION,
            new_anchors=[
                ContextAnchor(anchor_type="decision", ref="traitement-rh-2024")
            ],
        )
        await manager.update(sample_trame, intent=intent)
        assert len(sample_trame.context_anchors) == 1
        assert sample_trame.context_anchors[0].ref == "traitement-rh-2024"

    @pytest.mark.asyncio
    async def test_update_no_duplicate_anchors(self, manager, sample_trame):
        sample_trame.context_anchors = [
            ContextAnchor(anchor_type="document", ref="/doc.pdf")
        ]
        intent = Intent(
            intent_type=IntentType.QUESTION,
            new_anchors=[ContextAnchor(anchor_type="document", ref="/doc.pdf")],
        )
        await manager.update(sample_trame, intent=intent)
        assert len(sample_trame.context_anchors) == 1

    @pytest.mark.asyncio
    async def test_update_adds_document_anchors_from_plan(self, manager, sample_trame):
        chunk = DocumentChunk(
            text="contenu",
            source_path="/docs/guide.pdf",
            source_name="guide.pdf",
        )
        result = SearchResult(chunk=chunk, score=0.9, rank=0)
        plan = MagicMock(spec=ExecutionPlan)
        plan.search_results = [result]

        await manager.update(sample_trame, plan=plan)
        assert len(sample_trame.context_anchors) == 1
        assert sample_trame.context_anchors[0].anchor_type == "document"
        assert sample_trame.context_anchors[0].ref == "/docs/guide.pdf"

    @pytest.mark.asyncio
    async def test_update_with_none_plan_no_crash(self, manager, sample_trame):
        await manager.update(sample_trame, plan=None)
        assert sample_trame.turn_count == 1

    @pytest.mark.asyncio
    async def test_update_response_new_anchors(self, manager, sample_trame):
        response = MagicMock(spec=GeneratedResponse)
        response.metadata = {
            "new_anchors": [
                {"type": "decision", "ref": "décision-recrutement", "description": "Budget validé"}
            ]
        }
        await manager.update(sample_trame, response=response)
        assert any(a.ref == "décision-recrutement" for a in sample_trame.context_anchors)


# =============================================================================
# save
# =============================================================================

class TestTrameManagerSave:
    @pytest.mark.asyncio
    async def test_save_uploads_json(self, mock_storage, manager, sample_trame):
        await manager.save(sample_trame, "/espace-test/")
        mock_storage.upload.assert_awaited_once()
        path, data = mock_storage.upload.call_args[0]
        assert "_trame.json" in path
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["conv_id"] == "conv-123"

    @pytest.mark.asyncio
    async def test_save_noop_if_readonly(self, mock_storage, manager, sample_trame):
        await manager.save(sample_trame, "/espace-test/", storage_readonly=True)
        mock_storage.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_silently_ignores_upload_error(self, mock_storage, manager, sample_trame):
        mock_storage.upload = AsyncMock(side_effect=Exception("storage down"))
        # Ne doit pas lever
        await manager.save(sample_trame, "/espace-test/")

    @pytest.mark.asyncio
    async def test_roundtrip_serialize_deserialize(self, mock_storage, manager):
        trame = ConversationTrame(
            conv_id="conv-456",
            workspace_id="/ws/",
            conversation_phase="drafting",
            active_behavior="draft_mode",
            turn_count=5,
        )
        trame.context_anchors = [
            ContextAnchor(anchor_type="document", ref="/doc.pdf", turn_index=2)
        ]
        serialized = manager._serialize(trame)
        restored = manager._deserialize(serialized)

        assert restored.conv_id == trame.conv_id
        assert restored.conversation_phase == trame.conversation_phase
        assert restored.active_behavior == trame.active_behavior
        assert restored.turn_count == trame.turn_count
        assert len(restored.context_anchors) == 1
        assert restored.context_anchors[0].ref == "/doc.pdf"
