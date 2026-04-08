# SPDX-License-Identifier: MIT
"""
Construction du system prompt pour la boucle agent.

Intègre :
- Identité et règles de Colaig
- Outils disponibles (internes + MCP)
- Instructions des serveurs MCP
- Contexte workspace (.albert/behavior, index, docs)
- Behaviors personnalisés du workspace
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.matrix_bot.config import logger
from app.agent.tools import ToolRegistry


def build_system_prompt(
    registry: ToolRegistry,
    *,
    mcp_instructions: Dict[str, str] = None,
    mcp_tools_desc: str = "",
    workspace_info: Dict[str, Any] = None,
    behaviors_summary: str = "",
    max_turns: int = 5,
    persona_override: str = "",
    active_skills: List[Any] = None,
) -> str:
    """Construit le system prompt complet pour la boucle agent.

    Args:
        registry: Registre des outils internes disponibles.
        mcp_instructions: Dict[server_name, instructions] des serveurs MCP.
        mcp_tools_desc: Descriptions formatées des outils MCP.
        workspace_info: Infos sur le workspace (nom, docs indexés, etc.).
        behaviors_summary: Résumé des behaviors .albert du workspace.
        max_turns: Limite d'appels d'outils par requête.
        persona_override: Identité custom du workspace.
        active_skills: Liste de Skill activées par triggers regex.
            Leur body Markdown est injecté en section "Procédure recommandée".
    """
    sections = [_identity_section(persona_override)]

    # Skills actives : injectées en haut, juste après l'identité
    # (priorité maximale dans le contexte LLM)
    if active_skills:
        skills_section = _skills_section(active_skills)
        if skills_section:
            sections.append(skills_section)

    # Outils internes
    tools_prompt = registry.to_prompt()
    if tools_prompt:
        sections.append(f"## Outils internes\n\n{tools_prompt}")

    # Outils MCP
    if mcp_tools_desc:
        sections.append(f"## Outils MCP (données ouvertes et services)\n\n{mcp_tools_desc}")

    # Instructions des serveurs MCP
    if mcp_instructions:
        parts = []
        for srv, instr in mcp_instructions.items():
            parts.append(f"**{srv}** : {instr.strip()}")
        sections.append(
            "## Instructions des serveurs MCP\n\n" + "\n\n".join(parts)
        )

    # Contexte workspace
    ws_section = _workspace_section(workspace_info, behaviors_summary)
    if ws_section:
        sections.append(ws_section)

    # Règles d'utilisation des outils
    sections.append(_rules_section(max_turns))

    return "\n\n---\n\n".join(sections)


def _skills_section(active_skills: List[Any]) -> str:
    """Construit la section des skills actives à injecter dans le system prompt.

    Chaque skill apporte sa procédure (body Markdown). Les skills sont
    triées par priority décroissante en amont. On les sépare par un titre
    explicite pour que le LLM comprenne qu'il s'agit de procédures à suivre.
    """
    if not active_skills:
        return ""

    parts = ["## Procédures applicables à cette requête\n"]
    for skill in active_skills:
        name = getattr(skill, "name", "skill")
        body = getattr(skill, "body", "").strip()
        if not body:
            continue
        parts.append(f"### Skill : {name}\n\n{body}")

    return "\n\n".join(parts)


def _identity_section(persona_override: str = "") -> str:
    """Construit la section d'identité du system prompt.

    Si `persona_override` est fourni (depuis .albert/config/workspace.yaml),
    c'est lui qui définit le ton et la mission. Sinon, identité Colaig par défaut.
    """
    if persona_override:
        return persona_override.strip()
    return (
        "Tu es **Colaig**, l'assistant IA de l'État français, déployé sur Tchap.\n\n"
        "Tu aides les agents publics en exploitant les documents de leur espace de travail, "
        "les données ouvertes, et tes connaissances générales. "
        "Tu es précis, concis, et tu cites tes sources quand tu utilises des documents."
    )


def _workspace_section(
    workspace_info: Dict[str, Any] = None,
    behaviors_summary: str = "",
) -> str:
    if not workspace_info and not behaviors_summary:
        return ""

    parts = ["## Contexte du workspace"]
    ws = workspace_info or {}

    if ws.get("name"):
        parts.append(f"- Espace : **{ws['name']}**")
    if ws.get("total_documents"):
        parts.append(f"- Documents indexés : {ws['total_documents']}")
    if ws.get("is_fresh") is not None:
        parts.append(f"- Index à jour : {'oui' if ws['is_fresh'] else 'non'}")

    if behaviors_summary:
        parts.append(f"\n### Comportements configurés\n{behaviors_summary}")

    return "\n".join(parts)


def _rules_section(max_turns: int) -> str:
    return f"""## Règles d'usage des outils

**Comment appeler un outil** : utilise le mécanisme natif de tool_use de l'API
(le système gère le format automatiquement). Tu peux enchaîner jusqu'à {max_turns}
appels d'outils par requête utilisateur.

**Pour répondre à l'utilisateur** : écris ta réponse en texte directement,
sans appel d'outil. C'est le signal de fin de boucle.

## Stratégie pour les outils de recherche / listing

Quand un outil de recherche (search_*, list_*) retourne **plus de 10 résultats** :

1. **Annonce le nombre total** au début de ta réponse
   (ex: « J'ai trouvé 252 jeux de données… »)
2. **Identifie les catégories** majeures (organisation, thème, type) plutôt que
   de lister les 5 premiers seulement
3. **Donne quelques exemples diversifiés** (3-6) couvrant les principales catégories
4. **Propose d'affiner** si la liste est longue (par mot-clé, organisation, période)
5. Conserve les **identifiants techniques** pour permettre des requêtes ciblées
   (`get_dataset_info`, etc.) si l'utilisateur veut creuser

Si le résultat a déjà été pré-résumé (présence de catégories et compteurs dans
le contenu de l'outil), exploite-le directement plutôt que de re-analyser.

## Stratégie pour les récupérations ciblées (get_*, query_*)

Ces outils retournent des données précises non tronquées : restitue fidèlement
les champs importants, n'invente rien, cite les valeurs telles quelles.

## Comportement général

- **Salutations et conversations** : si l'utilisateur dit bonjour, te demande
  comment tu vas, ou pose une question simple sur tes capacités, **réponds
  directement sans appeler d'outils**. Les outils ne servent que pour les
  vraies questions métier (recherche de données, documents, etc.).
- Si un outil retourne une erreur, essaie une approche différente plutôt que
  de répéter le même appel à l'identique.
- Cite tes sources quand tu utilises des résultats d'outils.
- Sois concis : l'utilisateur est dans une messagerie, pas un rapport."""


def build_mcp_tools_description(mcp_tools: list) -> str:
    """Formate les outils MCP pour le system prompt.

    Args:
        mcp_tools: Liste de MCPTool depuis le registre.
    """
    if not mcp_tools:
        return ""

    lines = []
    for t in mcp_tools:
        if hasattr(t, "annotations") and "assistant" not in t.annotations.audience:
            continue
        line = f"- `{t.qualified_name}` : {t.description}"
        if hasattr(t, "annotations"):
            if t.annotations.destructive:
                line += "  [destructif]"
            if t.annotations.read_only:
                line += "  [lecture seule]"
        lines.append(line)

    return "\n".join(lines)


async def gather_workspace_info(config, behavior_manager=None) -> Dict[str, Any]:
    """Collecte les infos du workspace pour le prompt.

    Appelé une fois par session agent, pas à chaque tour.
    """
    info: Dict[str, Any] = {}

    # Index status
    try:
        from app.services.index_service import get_index_service
        index_svc = await get_index_service(config)
        status = await index_svc.get_status()
        info["total_documents"] = status.get("total_documents", 0)
        info["is_fresh"] = status.get("is_fresh", False)
    except Exception:
        pass

    return info


async def gather_behaviors_summary(behavior_manager) -> str:
    """Résume les behaviors .albert du workspace pour le prompt."""
    if not behavior_manager:
        return ""

    try:
        await behavior_manager.ensure_behaviors_loaded()
        behaviors = getattr(behavior_manager, "_behaviors", {})
        if not behaviors:
            return ""

        parts = []
        for btype, items in behaviors.items():
            if items:
                names = [b.get("id", b.get("name", "?")) for b in items[:5]]
                parts.append(f"- {btype} : {', '.join(names)}")

        return "\n".join(parts) if parts else ""
    except Exception as e:
        logger.debug(f"Behaviors summary error: {e}")
        return ""
