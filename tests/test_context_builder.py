"""
Tests — Context Builder pour les agents du pipeline
"""

import pytest

from colaig.models import AgentDirectives, WorkspaceConfig
from colaig.agents.context_builder import (
    DEFAULT_ANALYSER_PROMPT,
    DEFAULT_ORCHESTRATOR_PROMPT,
    DEFAULT_SYNTHESISER_PROMPT,
    build_agent_context,
    _load_agent_prompt_override,
    _load_workspace_skills,
)
from tests.conftest import MockStorage


@pytest.fixture
def workspace():
    return WorkspaceConfig(
        workspace_id="test-ws",
        name="Test Workspace",
        storage_path="/espace-test/",
        tools_enabled=["search_documents", "fetch_document", "list_documents", "summarize_text"],
        priority_documents=["guide.pdf", "procedure.md"],
    )


@pytest.fixture
def storage_with_overrides():
    """Storage avec prompts override et skills."""
    storage = MockStorage()
    storage.add_file(
        "/espace-test/.colaig/prompts/analyser.md",
        "Tu es un analyseur spécialisé en droit administratif.".encode("utf-8"),
    )
    storage.add_file(
        "/espace-test/.colaig/prompts/synthesiser.md",
        "Réponds toujours en format liste numérotée.".encode("utf-8"),
    )
    # Skills
    from colaig.models import StorageFile
    storage.add_file(
        "/espace-test/.colaig/skills/glossaire.md",
        "DGFiP = Direction Générale des Finances Publiques".encode("utf-8"),
    )
    storage.add_file(
        "/espace-test/.colaig/skills/procedures.md",
        "Étape 1 : Soumettre le formulaire A1.".encode("utf-8"),
    )
    return storage


class TestBuildAgentContextDefaults:
    @pytest.mark.asyncio
    async def test_analyser_default_prompt(self, workspace):
        """L'analyseur utilise le prompt par défaut si pas d'override."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "analyser")
        assert ctx.agent_role == "analyser"
        assert ctx.system_prompt == DEFAULT_ANALYSER_PROMPT

    @pytest.mark.asyncio
    async def test_orchestrator_default_prompt(self, workspace):
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "orchestrator")
        assert ctx.system_prompt == DEFAULT_ORCHESTRATOR_PROMPT

    @pytest.mark.asyncio
    async def test_synthesiser_default_prompt(self, workspace):
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "synthesiser")
        assert ctx.system_prompt == DEFAULT_SYNTHESISER_PROMPT

    @pytest.mark.asyncio
    async def test_no_workspace_uses_defaults(self):
        """Sans workspace, le prompt par défaut s'applique."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, None, "analyser")
        assert ctx.system_prompt == DEFAULT_ANALYSER_PROMPT
        assert ctx.workspace_config is None
        assert ctx.available_tools == []


class TestBuildAgentContextOverrides:
    @pytest.mark.asyncio
    async def test_override_prompt_loaded(self, workspace, storage_with_overrides):
        """Le prompt override remplace le default."""
        ctx = await build_agent_context(storage_with_overrides, workspace, "analyser")
        assert ctx.system_prompt == "Tu es un analyseur spécialisé en droit administratif."

    @pytest.mark.asyncio
    async def test_no_override_falls_back(self, workspace, storage_with_overrides):
        """Sans fichier override, le default s'applique."""
        # orchestrator.md n'existe pas dans storage_with_overrides
        ctx = await build_agent_context(storage_with_overrides, workspace, "orchestrator")
        assert ctx.system_prompt == DEFAULT_ORCHESTRATOR_PROMPT

    @pytest.mark.asyncio
    async def test_synthesiser_override(self, workspace, storage_with_overrides):
        ctx = await build_agent_context(storage_with_overrides, workspace, "synthesiser")
        assert ctx.system_prompt == "Réponds toujours en format liste numérotée."


class TestBuildAgentContextSkills:
    @pytest.mark.asyncio
    async def test_skills_loaded_for_orchestrator(self, workspace, storage_with_overrides):
        ctx = await build_agent_context(storage_with_overrides, workspace, "orchestrator")
        assert len(ctx.skills) == 2
        names = [s["name"] for s in ctx.skills]
        assert "glossaire" in names
        assert "procedures" in names

    @pytest.mark.asyncio
    async def test_skills_loaded_for_synthesiser(self, workspace, storage_with_overrides):
        ctx = await build_agent_context(storage_with_overrides, workspace, "synthesiser")
        assert len(ctx.skills) == 2

    @pytest.mark.asyncio
    async def test_no_skills_for_analyser(self, workspace, storage_with_overrides):
        """L'analyseur ne charge pas les skills."""
        ctx = await build_agent_context(storage_with_overrides, workspace, "analyser")
        assert ctx.skills == []

    @pytest.mark.asyncio
    async def test_missing_skills_dir_graceful(self, workspace):
        """Pas de dossier skills = pas de skills, sans erreur."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "orchestrator")
        assert ctx.skills == []


class TestBuildAgentContextTools:
    @pytest.mark.asyncio
    async def test_analyser_no_tools(self, workspace):
        """L'analyseur n'a pas de tools."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "analyser")
        assert ctx.available_tools == []

    @pytest.mark.asyncio
    async def test_orchestrator_filtered_tools(self, workspace):
        """L'orchestrateur ne voit que les tools activés ET dans son rôle."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "orchestrator")
        assert "search_documents" in ctx.available_tools
        assert "summarize_text" in ctx.available_tools

    @pytest.mark.asyncio
    async def test_synthesiser_no_tools(self, workspace):
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "synthesiser")
        assert ctx.available_tools == []


class TestBuildAgentContextDirectives:
    @pytest.mark.asyncio
    async def test_directives_passed_through(self, workspace):
        """Les directives de l'analyseur sont transmises au contexte."""
        storage = MockStorage()
        d = AgentDirectives(
            target_agent="orchestrator",
            search_strategy="precise",
        )
        ctx = await build_agent_context(storage, workspace, "orchestrator", directives=d)
        assert ctx.directives is not None
        assert ctx.directives.search_strategy == "precise"

    @pytest.mark.asyncio
    async def test_no_directives_default(self, workspace):
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "analyser")
        assert ctx.directives is None


class TestBuildAgentContextResources:
    @pytest.mark.asyncio
    async def test_priority_documents_as_resources(self, workspace):
        """Les priority_documents du workspace deviennent les available_resources."""
        storage = MockStorage()
        ctx = await build_agent_context(storage, workspace, "orchestrator")
        assert ctx.available_resources == ["guide.pdf", "procedure.md"]

    @pytest.mark.asyncio
    async def test_no_workspace_no_resources(self):
        storage = MockStorage()
        ctx = await build_agent_context(storage, None, "orchestrator")
        assert ctx.available_resources == []


class TestLoadAgentPromptOverride:
    @pytest.mark.asyncio
    async def test_loads_existing_file(self, storage_with_overrides):
        result = await _load_agent_prompt_override(
            storage_with_overrides, "/espace-test/", "analyser"
        )
        assert result == "Tu es un analyseur spécialisé en droit administratif."

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        storage = MockStorage()
        result = await _load_agent_prompt_override(storage, "/espace-test/", "analyser")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_file(self):
        storage = MockStorage()
        storage.add_file("/espace-test/.colaig/prompts/analyser.md", b"   ")
        result = await _load_agent_prompt_override(storage, "/espace-test/", "analyser")
        assert result is None


class TestLoadWorkspaceSkills:
    @pytest.mark.asyncio
    async def test_loads_md_files(self, storage_with_overrides):
        skills = await _load_workspace_skills(storage_with_overrides, "/espace-test/")
        assert len(skills) == 2
        contents = {s["name"]: s["content"] for s in skills}
        assert "DGFiP" in contents["glossaire"]
        assert "formulaire" in contents["procedures"]

    @pytest.mark.asyncio
    async def test_empty_dir(self):
        storage = MockStorage()
        skills = await _load_workspace_skills(storage, "/espace-test/")
        assert skills == []
