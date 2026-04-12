# SPDX-License-Identifier: MIT
"""
Formateur de réponses Colaig pour Tchap.

Assemble la réponse LLM + bloc sources + références skills en un
message Markdown structuré et cohérent, prêt pour send_markdown_message.

Le LLM génère le contenu brut. Ce module ajoute les métadonnées
(sources documentaires, datasets MCP, skills appliquées) dans un
format standardisé, après le texte du LLM.

Architecture dans le pipeline :

    agent_loop() → AgentResult(text, sources, skills, tools)
                        ↓
    format_agent_response(result, skills) → str Markdown complet
                        ↓
    send_markdown_message() → HTML Tchap
"""
from __future__ import annotations

from typing import List, Optional

from app.agent.result import AgentResult, Source


def format_agent_response(
    result: AgentResult,
    active_skills: Optional[List] = None,
) -> str:
    """Assemble la réponse LLM + métadonnées en Markdown structuré.

    Args:
        result: AgentResult retourné par agent_loop.
        active_skills: Skills activées par regex (noms à référencer).

    Returns:
        Message Markdown complet prêt pour send_markdown_message.
    """
    parts = [result.text.strip()]

    # Bloc sources (si des outils ont produit des résultats sourcés)
    sources_block = _format_sources(result.sources)
    if sources_block:
        parts.append(sources_block)

    # Bloc skills (si des procédures ont été appliquées)
    if active_skills:
        skills_block = _format_skills_ref(active_skills)
        if skills_block:
            parts.append(skills_block)

    return "\n\n".join(parts)


def _format_sources(sources: List[Source]) -> str:
    """Construit le bloc sources en Markdown.

    Sépare les sources par type :
    - 📄 Documents indexés (avec score de pertinence)
    - 🔗 Données ouvertes / MCP (avec lien si disponible)
    """
    if not sources:
        return ""

    doc_sources = [s for s in sources if s.type == "document"]
    mcp_sources = [s for s in sources if s.type.startswith("mcp_")]

    if not doc_sources and not mcp_sources:
        return ""

    lines = ["---"]

    if doc_sources:
        lines.append("📄 **Sources documentaires :**")
        # Trier par score décroissant
        doc_sources.sort(key=lambda s: s.score, reverse=True)
        for src in doc_sources:
            score_pct = f" — pertinence {int(src.score * 100)}%" if src.score > 0 else ""
            if src.url:
                lines.append(f"- [{src.name}]({src.url}){score_pct}")
            else:
                lines.append(f"- {src.name}{score_pct}")

    if mcp_sources:
        if doc_sources:
            lines.append("")  # espacement entre les deux blocs
        lines.append("🔗 **Données ouvertes :**")
        for src in mcp_sources:
            server = src.path or "MCP"
            if src.url:
                lines.append(f"- [{src.name}]({src.url}) — {server}")
            else:
                lines.append(f"- {src.name} — {server}")

    return "\n".join(lines)


def _format_skills_ref(active_skills: list) -> str:
    """Construit le bloc références des skills appliquées.

    Indique quelles procédures métier ont été utilisées pour contextualiser
    la réponse. Utile pour la traçabilité et la confiance utilisateur.
    """
    if not active_skills:
        return ""

    names = []
    for skill in active_skills:
        name = getattr(skill, "name", str(skill))
        desc = getattr(skill, "description", "")
        if desc:
            names.append(f"- {name} — {desc}")
        else:
            names.append(f"- {name}")

    if not names:
        return ""

    return "📋 **Procédures appliquées :**\n" + "\n".join(names)
