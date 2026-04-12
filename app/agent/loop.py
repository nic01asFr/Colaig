# SPDX-License-Identifier: MIT
"""
Boucle agent Colaig.

Implémente le cycle : LLM → parse tool_call → execute → inject result → LLM
Termine quand le LLM répond sans tool_call ou que max_turns est atteint.

Inspiré du pattern agent-loop de Claude Code :
- Single-threaded master loop
- Tool results feed back into reasoning
- Max turns pour éviter les boucles infinies
- Dédup intra-tour des appels redondants

Le format LLM-spécifique (Mistral, OpenAI tool_use, IDs 9 chars, etc.) est
isolé dans `app.agent.llm_transport.LLMTransport`. La boucle ne connaît
pas le format wire.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from app.matrix_bot.config import logger
from app.agent.parser import ToolCall
from app.agent.result import AgentResult
from app.agent.tools import ToolRegistry
from app.agent.llm_transport import LLMTransport


# Limite de sécurité par défaut, surchargée par YAML config
DEFAULT_MAX_TURNS = 5


def _get_max_turns() -> int:
    """Lit la limite de tours depuis le YAML config (dynamique)."""
    try:
        from app.agent.config_loader import get_loop_config
        return get_loop_config().get("max_turns", DEFAULT_MAX_TURNS)
    except Exception:
        return DEFAULT_MAX_TURNS


async def agent_loop(
    message: str,
    history: List[Dict[str, str]],
    system_prompt: str,
    registry: ToolRegistry,
    config,
    *,
    tool_context: Optional[Dict[str, Any]] = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    on_tool_start: Optional[Any] = None,
    on_tool_end: Optional[Any] = None,
) -> str:
    """Boucle agent principale.

    Args:
        message: Message utilisateur courant.
        history: Historique de conversation [{role, content}, ...].
        system_prompt: System prompt complet (identité + outils + contexte).
        registry: Registre des outils disponibles.
        config: Configuration Colaig (pour l'API LLM).
        tool_context: Contexte partagé passé aux handlers d'outils.
        max_turns: Nombre max d'appels d'outils.
        on_tool_start: async callback(tool_name) appelé avant exécution.
        on_tool_end: async callback(tool_name, result_preview) appelé après.

    Returns:
        Réponse textuelle finale du LLM.
    """
    # Charger max_turns depuis le YAML si valeur par défaut
    if max_turns == DEFAULT_MAX_TURNS:
        max_turns = _get_max_turns()

    ctx = tool_context or {}
    transport = LLMTransport(config)

    # Construire la conversation
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    # Préparer les tools au format OpenAI
    openai_tools = [t.to_openai_tool() for t in registry.all_tools]

    logger.info(
        f"[AGENT] Démarrage — outils={len(openai_tools)}, "
        f"max_turns={max_turns}, message={message[:80]!r}"
    )

    # Accumulateur de métadonnées pour le formatage final
    agent_result = AgentResult()

    for turn in range(max_turns):
        result = await transport.call(messages, openai_tools=openai_tools or None)

        logger.info(
            f"[AGENT] Tour {turn + 1}/{max_turns} — "
            f"content_len={len(result.content)} tool_calls={len(result.tool_calls)}"
        )

        # Pas d'appel d'outil → réponse finale
        if not result.tool_calls:
            agent_result.text = result.content
            agent_result.turns = turn + 1
            _collect_sources_from_ctx(agent_result, ctx)
            return agent_result

        # Ajouter le message assistant (avec tool_calls éventuels)
        messages.append(result.assistant_message)

        # Exécution des outils — dédup intra-tour
        executed_in_turn: Set[Tuple[str, str]] = set()

        for i, tc in enumerate(result.tool_calls):
            call_key = (tc.name, json.dumps(tc.arguments, sort_keys=True))

            if call_key in executed_in_turn:
                tool_result = (
                    f"⚠️ Cet outil a déjà été appelé avec ces mêmes arguments "
                    f"dans ce tour. Ne le rappelle pas — utilise le résultat précédent."
                )
                logger.info(f"[AGENT] Dédup intra-tour: {tc.name}")
            else:
                executed_in_turn.add(call_key)
                tool_result = await _execute_tool_call(
                    tc, registry, ctx,
                    on_start=on_tool_start, on_end=on_tool_end,
                )

            # Tracer l'appel pour le formatage final
            agent_result.add_tool_trace(
                name=tc.name,
                arguments=tc.arguments,
                result_preview=tool_result,
                success="Erreur" not in tool_result[:50],
            )

            # Construire le message de résultat au bon format (natif vs fallback)
            tool_call_id = result.call_ids[i] if i < len(result.call_ids) else ""
            messages.append(
                LLMTransport.build_tool_result_message(
                    tool_call_id=tool_call_id,
                    tool_name=tc.name,
                    result=tool_result,
                    native_mode=result.native_mode,
                )
            )

    # Max turns atteint — forcer une réponse finale sans tools
    logger.warning(f"[AGENT] Limite de {max_turns} tours atteinte, réponse forcée")
    final = await transport.call(messages, openai_tools=None)
    agent_result.text = final.content or "Désolé, je n'ai pas pu traiter votre demande."
    agent_result.turns = max_turns
    _collect_sources_from_ctx(agent_result, ctx)
    return agent_result


def _collect_sources_from_ctx(agent_result: AgentResult, ctx: Dict[str, Any]) -> None:
    """Transfère les sources collectées par les tools depuis le contexte partagé."""
    collected = ctx.get("_collected_sources", [])
    for src in collected:
        agent_result.add_source(
            type=src.get("type", "unknown"),
            name=src.get("name", ""),
            path=src.get("path", ""),
            url=src.get("url", ""),
            score=src.get("score", 0.0),
            **(src.get("extra", {})),
        )


async def _execute_tool_call(
    tc: ToolCall,
    registry: ToolRegistry,
    ctx: Dict[str, Any],
    on_start=None,
    on_end=None,
) -> str:
    """Exécute un appel d'outil et retourne le résultat en texte.

    Sépare proprement les `args` (fournis par le LLM) du `ctx` (injecté
    par la boucle agent) pour éviter les collisions de noms.
    """
    tool = registry.get(tc.name)

    if not tool:
        msg = f"Outil inconnu : {tc.name}"
        logger.warning(f"[AGENT] {msg}")
        return msg

    if not tool.handler:
        return f"Outil {tc.name} n'a pas de handler configuré."

    if on_start:
        try:
            await on_start(tc.name)
        except Exception:
            pass

    # Exécuter le handler avec args + ctx séparés
    # Les handlers acceptent la signature handler(args: dict, ctx: dict)
    # Fallback : signature legacy handler(**args, **ctx) si TypeError
    try:
        result = await tool.handler(tc.arguments, ctx)
    except TypeError:
        # Fallback compatibilité : ancienne signature kwargs unifiés
        try:
            result = await tool.handler(**tc.arguments, **ctx)
        except TypeError as e:
            logger.warning(f"[AGENT] Erreur d'arguments pour {tc.name}: {e}")
            result = f"Erreur d'arguments pour {tc.name} : {e}"
        except Exception as e:
            logger.error(f"[AGENT] Erreur exécution {tc.name}: {e}")
            result = f"Erreur lors de l'exécution de {tc.name} : {e}"
    except Exception as e:
        logger.error(f"[AGENT] Erreur exécution {tc.name}: {e}")
        result = f"Erreur lors de l'exécution de {tc.name} : {e}"

    # Compaction structurée (P1)
    from app.agent.compact import compact_tool_result
    result = compact_tool_result(result)

    if on_end:
        try:
            preview = result[:200] if result else ""
            await on_end(tc.name, preview)
        except Exception:
            pass

    logger.info(f"[AGENT] {tc.name} → {len(result)} chars")
    return result
