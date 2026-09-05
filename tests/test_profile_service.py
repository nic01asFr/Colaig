"""Tests — agents/profile_service.py (Phase 6)"""

from unittest.mock import AsyncMock

import pytest
import yaml

from colaig.agents.profile_service import ProfileService
from colaig.models import (
    BehaviorConfig,
    StorageFile,
    WorkspaceProfile,
)


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.download = AsyncMock(side_effect=Exception("not found"))
    storage.list_files = AsyncMock(return_value=[])
    return storage


@pytest.fixture
def mock_embeddings():
    emb = AsyncMock()
    emb.embed_text = AsyncMock(return_value=[0.1] * 8)
    return emb


@pytest.fixture
def service(mock_storage, mock_embeddings):
    return ProfileService(storage=mock_storage, embeddings=mock_embeddings)


# =============================================================================
# load_profile
# =============================================================================

class TestLoadProfile:
    @pytest.mark.asyncio
    async def test_returns_none_if_absent(self, service):
        result = await service.load_profile("/ws/")
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_identity_yaml(self, mock_storage, service):
        identity = {
            "name": "Colaig RH",
            "domain": "Ressources Humaines",
            "sub_domain": "Administration Publique",
            "language": "fr",
            "tone": "formel",
            "vocabulary": {
                "terms": ["RIFSEEP", "PPCR"],
                "abbreviations": {"CAP": "Commission Administrative Paritaire"},
            },
            "document_map": {
                "categorization_taxonomy": ["circulaire", "rapport"],
                "entity_types_to_extract": ["dates_importantes"],
                "metadata_enrichment_instructions": "Contexte RH",
            },
        }
        mock_storage.download = AsyncMock(return_value=yaml.dump(identity).encode("utf-8"))

        profile = await service.load_profile("/ws/")
        assert isinstance(profile, WorkspaceProfile)
        assert profile.name == "Colaig RH"
        assert profile.domain == "Ressources Humaines"
        assert "RIFSEEP" in profile.vocabulary.terms
        assert profile.vocabulary.abbreviations["CAP"] == "Commission Administrative Paritaire"
        assert "circulaire" in profile.document_map.categorization_taxonomy

    @pytest.mark.asyncio
    async def test_parses_minimal_yaml(self, mock_storage, service):
        mock_storage.download = AsyncMock(return_value=b"name: Test\n")
        profile = await service.load_profile("/ws/")
        assert profile is not None
        assert profile.name == "Test"
        assert profile.domain == ""


# =============================================================================
# load_behaviors
# =============================================================================

class TestLoadBehaviors:
    @pytest.mark.asyncio
    async def test_returns_empty_if_no_dir(self, service):
        result = await service.load_behaviors("/ws/")
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_non_yaml_files(self, mock_storage, service):
        mock_storage.list_files = AsyncMock(
            return_value=[
                StorageFile(path="/ws/.colaig/profile/behaviors/readme.md", name="readme.md"),
            ]
        )
        result = await service.load_behaviors("/ws/")
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_behavior_yaml(self, mock_storage, service):
        behavior_yaml = {
            "name": "draft_mode",
            "description": "Mode rédaction",
            "activation": {
                "semantic_triggers": ["rédige un document"],
                "intent_types": ["action"],
                "min_similarity": 0.75,
            },
            "agents": {
                "synthesiser": {"temperature": 0.7, "format": "markdown"}
            },
            "skills_boost": ["redaction"],
            "tools": {"disable": ["calculator"]},
            "output": {"tone": "formel", "include_sources": False},
        }
        mock_storage.list_files = AsyncMock(
            return_value=[
                StorageFile(path="/ws/.colaig/profile/behaviors/draft_mode.yaml", name="draft_mode.yaml"),
            ]
        )
        mock_storage.download = AsyncMock(return_value=yaml.dump(behavior_yaml).encode("utf-8"))

        behaviors = await service.load_behaviors("/ws/")
        assert len(behaviors) == 1
        b = behaviors[0]
        assert isinstance(b, BehaviorConfig)
        assert b.name == "draft_mode"
        assert "rédige un document" in b.activation.semantic_triggers
        assert b.activation.min_similarity == 0.75
        assert "synthesiser" in b.agents
        assert b.agents["synthesiser"].temperature == 0.7
        assert "redaction" in b.skills_boost
        assert "calculator" in b.tools.disable

    @pytest.mark.asyncio
    async def test_skips_invalid_yaml(self, mock_storage, service):
        mock_storage.list_files = AsyncMock(
            return_value=[
                StorageFile(path="/ws/b1.yaml", name="b1.yaml"),
                StorageFile(path="/ws/b2.yaml", name="b2.yaml"),
            ]
        )
        # b1 invalide, b2 valide
        valid_yaml = yaml.dump({"name": "b2"}).encode("utf-8")
        mock_storage.download = AsyncMock(
            side_effect=[Exception("IO error"), valid_yaml]
        )
        behaviors = await service.load_behaviors("/ws/")
        assert len(behaviors) == 1
        assert behaviors[0].name == "b2"


# =============================================================================
# resolve_behavior
# =============================================================================

class TestResolveBehavior:
    @pytest.mark.asyncio
    async def test_returns_none_if_no_behaviors(self, service):
        behavior, score = await service.resolve_behavior([0.1] * 8, [], "/ws/")
        assert behavior is None
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_reuses_active_behavior_from_trame(self, mock_storage, service):
        behaviors = [
            BehaviorConfig(name="draft_mode"),
            BehaviorConfig(name="expert_mode"),
        ]
        behavior, score = await service.resolve_behavior(
            [0.1] * 8, behaviors, "/ws/", active_behavior_name="expert_mode"
        )
        assert behavior is not None
        assert behavior.name == "expert_mode"
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_returns_none_if_faiss_absent(self, service):
        behaviors = [BehaviorConfig(name="draft_mode")]
        behavior, score = await service.resolve_behavior([0.1] * 8, behaviors, "/ws/")
        assert behavior is None
        assert score == 0.0
