"""
Tests Phase 4 — Nouveaux modèles (AgentDirectives, AgentContext, ContextCard, etc.)
"""

from dataclasses import asdict

import pytest

from colaig.exceptions import AnalysisError, MCPError, OrchestrationError, SynthesisError
from colaig.models import (
    AgentContext,
    AgentDirectives,
    Attachment,
    ContextCard,
    ExecutionPlan,
    GeneratedResponse,
    IncomingMessage,
    Intent,
    IntentType,
    PipelinePhase,
    WorkspaceConfig,
)


class TestPipelinePhase:
    def test_enum_values(self):
        assert PipelinePhase.THINKING == "thinking"
        assert PipelinePhase.RETRIEVING == "retrieving"
        assert PipelinePhase.EXECUTING == "executing"
        assert PipelinePhase.SYNTHESIZING == "synthesizing"
        assert PipelinePhase.COMPLETE == "complete"

    def test_phases_iterable(self):
        phases = list(PipelinePhase)
        assert len(phases) == 5


class TestAttachment:
    def test_create_minimal(self):
        att = Attachment(filename="doc.pdf")
        assert att.filename == "doc.pdf"
        assert att.content_type == ""
        assert att.size == 0
        assert att.content is None

    def test_create_full(self):
        att = Attachment(
            filename="rapport.pdf",
            content_type="application/pdf",
            size=1024,
            url="https://storage.example.com/rapport.pdf",
            content=b"PDF content",
        )
        assert att.size == 1024
        assert att.content == b"PDF content"


class TestIncomingMessageExtensions:
    def test_platform_field(self):
        msg = IncomingMessage(
            user_id="@user:server",
            conversation_id="!room:server",
            body="test",
            platform="matrix",
        )
        assert msg.platform == "matrix"

    def test_platform_default_empty(self):
        msg = IncomingMessage(user_id="u", conversation_id="c", body="b")
        assert msg.platform == ""

    def test_attachments_field(self):
        att = Attachment(filename="fichier.txt", content_type="text/plain")
        msg = IncomingMessage(
            user_id="@user:server",
            conversation_id="!room:server",
            body="voici le fichier",
            attachments=[att],
        )
        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "fichier.txt"

    def test_attachments_default_empty(self):
        msg = IncomingMessage(user_id="u", conversation_id="c", body="b")
        assert msg.attachments == []


class TestAgentDirectives:
    def test_create_orchestrator_directives(self):
        d = AgentDirectives(
            target_agent="orchestrator",
            instructions="Chercher en priorité dans les documents récents",
            resources_to_target=["guide.pdf", "procedures.md"],
            tools_to_use=["search"],
            search_strategy="precise",
        )
        assert d.target_agent == "orchestrator"
        assert d.search_strategy == "precise"
        assert len(d.resources_to_target) == 2

    def test_create_synthesiser_directives(self):
        d = AgentDirectives(
            target_agent="synthesiser",
            response_format="step-by-step",
            response_tone="formal",
            focus_points=["dates", "responsables"],
        )
        assert d.response_format == "step-by-step"
        assert d.response_tone == "formal"
        assert len(d.focus_points) == 2

    def test_defaults(self):
        d = AgentDirectives(target_agent="orchestrator")
        assert d.instructions == ""
        assert d.resources_to_target == []
        assert d.tools_to_use == []
        assert d.search_strategy == ""
        assert d.focus_points == []

    def test_serializable(self):
        d = AgentDirectives(target_agent="orchestrator", search_strategy="broad")
        data = asdict(d)
        assert data["target_agent"] == "orchestrator"
        assert data["search_strategy"] == "broad"


class TestAgentContext:
    def test_create_analyser_context(self):
        ctx = AgentContext(
            agent_role="analyser",
            system_prompt="Tu analyses les messages...",
        )
        assert ctx.agent_role == "analyser"
        assert ctx.workspace_config is None
        assert ctx.directives is None
        assert ctx.skills == []

    def test_create_with_workspace(self):
        ws = WorkspaceConfig(
            workspace_id="test", name="Test", storage_path="/test/",
        )
        ctx = AgentContext(
            agent_role="orchestrator",
            system_prompt="Tu coordonnes...",
            workspace_config=ws,
            available_tools=["search", "summarize"],
            available_resources=["guide.pdf"],
        )
        assert ctx.workspace_config.workspace_id == "test"
        assert len(ctx.available_tools) == 2

    def test_with_directives(self):
        d = AgentDirectives(
            target_agent="orchestrator",
            search_strategy="multi-step",
        )
        ctx = AgentContext(
            agent_role="orchestrator",
            directives=d,
        )
        assert ctx.directives.search_strategy == "multi-step"

    def test_skills_loading(self):
        ctx = AgentContext(
            agent_role="synthesiser",
            skills=[
                {"name": "glossaire", "content": "DGFiP = Direction Générale..."},
                {"name": "procedures", "content": "Étape 1 : soumettre le formulaire"},
            ],
        )
        assert len(ctx.skills) == 2
        assert ctx.skills[0]["name"] == "glossaire"


class TestContextCard:
    def test_create_empty(self):
        card = ContextCard()
        assert card.mode == ""
        assert card.confidence == 0.0
        assert card.sources_used == []

    def test_create_full(self):
        card = ContextCard(
            mode="assistant",
            workspace_id="projet-x",
            workspace_name="Projet X",
            available_tools=["search", "summarize"],
            suggested_resources=["guide.pdf"],
            workflow_hint="Recherche documentaire complète",
            sources_used=["guide.pdf", "procedure.md"],
            pipeline_phases=["thinking", "retrieving", "synthesizing"],
            confidence=0.85,
        )
        assert card.workspace_id == "projet-x"
        assert len(card.pipeline_phases) == 3
        assert card.confidence == 0.85

    def test_serializable(self):
        card = ContextCard(mode="assistant", workspace_id="test")
        data = asdict(card)
        assert data["mode"] == "assistant"
        assert isinstance(data["available_tools"], list)


class TestIntentDirectives:
    def test_intent_with_directives(self):
        orch_d = AgentDirectives(
            target_agent="orchestrator",
            search_strategy="precise",
            resources_to_target=["guide.pdf"],
        )
        synth_d = AgentDirectives(
            target_agent="synthesiser",
            response_format="list",
            response_tone="casual",
        )
        intent = Intent(
            intent_type=IntentType.QUESTION,
            query_reformulated="Quelle est la procédure de validation ?",
            needs_rag=True,
            confidence=0.9,
            orchestrator_directives=orch_d,
            synthesiser_directives=synth_d,
        )
        assert intent.orchestrator_directives.search_strategy == "precise"
        assert intent.synthesiser_directives.response_format == "list"

    def test_intent_directives_default_none(self):
        intent = Intent(intent_type=IntentType.GREETING)
        assert intent.orchestrator_directives is None
        assert intent.synthesiser_directives is None


class TestExecutionPlanContextCard:
    def test_plan_with_context_card(self):
        card = ContextCard(mode="assistant", workspace_id="test")
        plan = ExecutionPlan(
            intent=Intent(intent_type=IntentType.QUESTION),
            context_card=card,
        )
        assert plan.context_card.workspace_id == "test"

    def test_plan_context_card_default_none(self):
        plan = ExecutionPlan(intent=Intent(intent_type=IntentType.QUESTION))
        assert plan.context_card is None


class TestGeneratedResponseContextCard:
    def test_response_with_context_card(self):
        card = ContextCard(
            mode="assistant",
            sources_used=["guide.pdf"],
            confidence=0.85,
        )
        resp = GeneratedResponse(
            text="La procédure comporte 3 étapes.",
            sources=["guide.pdf"],
            context_card=card,
        )
        assert resp.context_card.confidence == 0.85
        assert resp.context_card.sources_used == ["guide.pdf"]

    def test_response_context_card_default_none(self):
        resp = GeneratedResponse(text="Bonjour !")
        assert resp.context_card is None


class TestPhase4Exceptions:
    def test_analysis_error(self):
        with pytest.raises(AnalysisError):
            raise AnalysisError("Analyse échouée")

    def test_orchestration_error(self):
        with pytest.raises(OrchestrationError):
            raise OrchestrationError("Orchestration échouée")

    def test_synthesis_error(self):
        with pytest.raises(SynthesisError):
            raise SynthesisError("Synthèse échouée")

    def test_mcp_error(self):
        with pytest.raises(MCPError):
            raise MCPError("MCP échoué")


class TestColaigConfigPhase2:
    def test_agents_enabled_default_false(self):
        from colaig.models import ColaigConfig
        config = ColaigConfig()
        assert config.agents_enabled is False
        assert config.mcp_enabled is False

    def test_agent_temperatures(self):
        from colaig.models import ColaigConfig
        config = ColaigConfig(
            agents_enabled=True,
            analyser_temperature=0.05,
            synthesiser_temperature=0.5,
        )
        assert config.analyser_temperature == 0.05
        assert config.synthesiser_temperature == 0.5
