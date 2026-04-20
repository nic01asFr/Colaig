"""Tests pour colaig/agents/workspace_delegate.py — délégation inter-workspace."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from colaig.agents.workspace_delegate import (
    WorkspaceAccessDenied,
    WorkspaceDelegateResult,
    WorkspaceNotFound,
    WorkspaceTaskResult,
    check_workspace_access,
    find_accessible_workspaces,
    run_rag_delegate,
    run_workspace_task,
)
from colaig.models import WorkspaceConfig


# =============================================================================
# Fixtures
# =============================================================================

def _make_ws(workspace_id: str, user_ids: list[str] | None = None) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id=workspace_id,
        name=f"Workspace {workspace_id}",
        storage_path=f"/{workspace_id}/",
        user_ids=user_ids or [],
        max_results=5,
        similarity_threshold=0.3,
    )


def _make_retriever(chunks: list | None = None) -> MagicMock:
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=chunks or [])
    return retriever


def _make_chunk(text: str = "contenu", source_name: str = "doc.pdf", score: float = 0.8):
    chunk = MagicMock()
    chunk.text = text
    chunk.source_name = source_name
    chunk.source_path = f"/{source_name}"
    chunk.page = 1
    chunk.section = ""
    result = MagicMock()
    result.chunk = chunk
    result.score = score
    return result


# =============================================================================
# ACL helpers
# =============================================================================

class TestFindAccessibleWorkspaces:
    def test_returns_matching_workspaces(self):
        wss = [
            _make_ws("rh", ["@alice:tchap.fr", "@bob:tchap.fr"]),
            _make_ws("infra", ["@charlie:tchap.fr"]),
            _make_ws("personal-alice", ["@alice:tchap.fr"]),
        ]
        result = find_accessible_workspaces(wss, "@alice:tchap.fr")
        assert len(result) == 2
        ids = {ws.workspace_id for ws in result}
        assert "rh" in ids
        assert "personal-alice" in ids

    def test_returns_empty_when_no_match(self):
        wss = [_make_ws("rh", ["@bob:tchap.fr"])]
        result = find_accessible_workspaces(wss, "@alice:tchap.fr")
        assert result == []

    def test_empty_workspaces(self):
        assert find_accessible_workspaces([], "@alice:tchap.fr") == []


class TestCheckWorkspaceAccess:
    def test_grants_access_when_user_in_list(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        check_workspace_access(ws, "@alice:tchap.fr")  # no exception

    def test_raises_when_user_not_in_list(self):
        ws = _make_ws("rh", ["@bob:tchap.fr"])
        with pytest.raises(WorkspaceAccessDenied) as exc_info:
            check_workspace_access(ws, "@alice:tchap.fr")
        assert "@alice:tchap.fr" in str(exc_info.value)
        assert "rh" in str(exc_info.value)
        assert exc_info.value.user_id == "@alice:tchap.fr"
        assert exc_info.value.workspace_id == "rh"

    def test_raises_when_user_ids_empty(self):
        ws = _make_ws("rh", [])
        with pytest.raises(WorkspaceAccessDenied):
            check_workspace_access(ws, "@alice:tchap.fr")


# =============================================================================
# run_rag_delegate
# =============================================================================

class TestRunRagDelegate:
    @pytest.mark.asyncio
    async def test_returns_chunks_on_success(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever([_make_chunk("Contenu RH", "policy.pdf", 0.85)])

        result = await run_rag_delegate(
            workspace_id="rh",
            query="congés payés",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
        )

        assert result.success is True
        assert result.workspace_id == "rh"
        assert result.workspace_name == "Workspace rh"
        assert len(result.chunks) == 1
        assert "Contenu RH" in result.chunks[0]["text"]
        assert "Workspace rh" in result.chunks[0]["source"]
        assert result.chunks[0]["score"] == 0.85

    @pytest.mark.asyncio
    async def test_raises_workspace_not_found(self):
        with pytest.raises(WorkspaceNotFound) as exc_info:
            await run_rag_delegate(
                workspace_id="inexistant",
                query="test",
                user_id="@alice:tchap.fr",
                all_workspaces=[],
                retriever=_make_retriever(),
            )
        assert exc_info.value.workspace_id == "inexistant"

    @pytest.mark.asyncio
    async def test_raises_access_denied(self):
        ws = _make_ws("rh", ["@bob:tchap.fr"])
        with pytest.raises(WorkspaceAccessDenied) as exc_info:
            await run_rag_delegate(
                workspace_id="rh",
                query="test",
                user_id="@alice:tchap.fr",
                all_workspaces=[ws],
                retriever=_make_retriever(),
            )
        assert exc_info.value.user_id == "@alice:tchap.fr"

    @pytest.mark.asyncio
    async def test_empty_results_when_no_chunks(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever([])

        result = await run_rag_delegate(
            workspace_id="rh",
            query="inexistant",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
        )

        assert result.success is True
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_uses_workspace_specific_store(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever()
        fake_store = MagicMock()
        workspace_stores = {"rh": fake_store}

        await run_rag_delegate(
            workspace_id="rh",
            query="test",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            workspace_stores=workspace_stores,
            retriever=retriever,
        )

        call_kwargs = retriever.retrieve.call_args.kwargs
        assert call_kwargs.get("store") is fake_store

    @pytest.mark.asyncio
    async def test_retriever_error_returns_failure(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(side_effect=RuntimeError("store down"))

        result = await run_rag_delegate(
            workspace_id="rh",
            query="test",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
        )

        assert result.success is False
        assert "store down" in result.error

    @pytest.mark.asyncio
    async def test_passes_k_parameter(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever()

        await run_rag_delegate(
            workspace_id="rh",
            query="test",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
            k=10,
        )

        call_kwargs = retriever.retrieve.call_args.kwargs
        assert call_kwargs["k"] == 10


# =============================================================================
# run_workspace_task
# =============================================================================

class TestRunWorkspaceTask:
    @pytest.mark.asyncio
    async def test_raises_workspace_not_found(self):
        with pytest.raises(WorkspaceNotFound):
            await run_workspace_task(
                workspace_id="inexistant",
                query="test",
                user_id="@alice:tchap.fr",
                all_workspaces=[],
            )

    @pytest.mark.asyncio
    async def test_raises_access_denied(self):
        ws = _make_ws("rh", ["@bob:tchap.fr"])
        with pytest.raises(WorkspaceAccessDenied):
            await run_workspace_task(
                workspace_id="rh",
                query="test",
                user_id="@alice:tchap.fr",
                all_workspaces=[ws],
            )

    @pytest.mark.asyncio
    async def test_no_pipeline_returns_error(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        result = await run_workspace_task(
            workspace_id="rh",
            query="test",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            # aucun pipeline fourni
        )
        assert result.success is False
        assert result.pipeline_used == "error"

    @pytest.mark.asyncio
    async def test_generator_pipeline_used(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever([_make_chunk("texte", "doc.pdf", 0.7)])

        generator = MagicMock()
        fake_response = MagicMock()
        fake_response.text = "Voici la réponse."
        generator.generate = AsyncMock(return_value=fake_response)

        result = await run_workspace_task(
            workspace_id="rh",
            query="politique télétravail",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
            generator=generator,
        )

        assert result.success is True
        assert result.response == "Voici la réponse."
        assert result.pipeline_used == "generator"
        assert "doc.pdf" in result.sources

    @pytest.mark.asyncio
    async def test_agents_pipeline_used(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])

        analyser = MagicMock()
        fake_intent = MagicMock()
        analyser.analyse = AsyncMock(return_value=fake_intent)

        orchestrator = MagicMock()
        fake_plan = MagicMock()
        orchestrator.execute = AsyncMock(return_value=fake_plan)

        synthesiser = MagicMock()
        fake_response = MagicMock()
        fake_response.text = "Réponse synthétisée."
        fake_response.sources = ["doc.pdf"]
        fake_response.confidence = 0.9
        synthesiser.synthesise = AsyncMock(return_value=fake_response)

        result = await run_workspace_task(
            workspace_id="rh",
            query="horaires de travail",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            analyser=analyser,
            orchestrator=orchestrator,
            synthesiser=synthesiser,
        )

        assert result.success is True
        assert result.response == "Réponse synthétisée."
        assert result.pipeline_used == "agents"
        assert result.confidence == 0.9
        analyser.analyse.assert_called_once()
        orchestrator.execute.assert_called_once()
        synthesiser.synthesise.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_exception_captured(self):
        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(side_effect=RuntimeError("panne critique"))

        generator = MagicMock()
        generator.generate = AsyncMock()

        result = await run_workspace_task(
            workspace_id="rh",
            query="test",
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
            generator=generator,
        )

        assert result.success is False
        assert result.pipeline_used == "error"


# =============================================================================
# ask_workspace handler (delegate_tools)
# =============================================================================

class TestAskWorkspaceHandler:
    @pytest.mark.asyncio
    async def test_returns_chunks_as_json(self):
        from colaig.agents.tools.delegate_tools import create_ask_workspace_handler

        ws = _make_ws("rh", ["@alice:tchap.fr"])
        retriever = _make_retriever([_make_chunk("politique congés", "policy.pdf", 0.75)])

        handler = create_ask_workspace_handler(
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=retriever,
        )
        raw = await handler(workspace_id="rh", query="congés payés")
        data = json.loads(raw)

        assert data["success"] is True
        assert data["workspace_id"] == "rh"
        assert data["count"] == 1
        assert len(data["chunks"]) == 1
        assert "politique congés" in data["chunks"][0]["text"]

    @pytest.mark.asyncio
    async def test_returns_error_json_on_not_found(self):
        from colaig.agents.tools.delegate_tools import create_ask_workspace_handler

        handler = create_ask_workspace_handler(
            user_id="@alice:tchap.fr",
            all_workspaces=[],
            retriever=_make_retriever(),
        )
        raw = await handler(workspace_id="inexistant", query="test")
        data = json.loads(raw)

        assert data["success"] is False
        assert "accessible_workspaces" in data

    @pytest.mark.asyncio
    async def test_returns_error_json_on_access_denied(self):
        from colaig.agents.tools.delegate_tools import create_ask_workspace_handler

        ws = _make_ws("rh", ["@bob:tchap.fr"])
        handler = create_ask_workspace_handler(
            user_id="@alice:tchap.fr",
            all_workspaces=[ws],
            retriever=_make_retriever(),
        )
        raw = await handler(workspace_id="rh", query="test")
        data = json.loads(raw)

        assert data["success"] is False
        assert "error" in data
