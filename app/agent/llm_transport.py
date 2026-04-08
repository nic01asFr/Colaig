# SPDX-License-Identifier: MIT
"""
Adapter de transport LLM pour la boucle agent.

Isole les spécificités format/protocole du provider LLM (Albert API, qui utilise
Mistral/GPT-OSS via une interface OpenAI-compatible) du cœur de la boucle agent.

Responsabilités :
- Appel `generate_with_tools` natif (format OpenAI tool_use).
- Fallback parsing texte si l'API refuse les tools (`generate` simple).
- Génération d'IDs `tool_call_id` conformes à la contrainte Mistral (9 chars
  alphanumériques exactement).
- Construction des messages `assistant` (avec tool_calls) et `tool` (résultats)
  au bon format pour l'API distante.

Le reste de la boucle agent (loop.py) ne connaît plus ces détails.
"""
from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.matrix_bot.config import logger
from app.agent.parser import parse_tool_calls, ToolCall


# ─── Génération d'IDs conformes ──────────────────────────────────────────────

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 9  # Mistral exige 9 chars alphanumériques


def make_tool_call_id() -> str:
    """Génère un ID de tool_call conforme à toutes les contraintes connues.

    Mistral exige : a-zA-Z0-9, longueur = 9.
    OpenAI accepte : longueur arbitraire, alphanumérique + underscore.
    Le format Mistral est compatible avec OpenAI.
    """
    return "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))


# ─── Structure de retour ─────────────────────────────────────────────────────

@dataclass
class LLMTurnResult:
    """Résultat d'un appel LLM, normalisé pour la boucle agent."""
    content: str
    tool_calls: List[ToolCall]
    # IDs alignés avec tool_calls (1:1) pour construire les messages tool
    call_ids: List[str] = field(default_factory=list)
    # Message assistant complet à ajouter à l'historique (format API)
    assistant_message: Dict[str, Any] = field(default_factory=dict)
    # True si on a utilisé le mode native tool_use (vs fallback texte)
    native_mode: bool = True


# ─── Transport principal ─────────────────────────────────────────────────────

class LLMTransport:
    """Adapter de transport LLM pour la boucle agent."""

    def __init__(self, config):
        self.config = config

    async def call(
        self,
        messages: List[Dict[str, Any]],
        openai_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMTurnResult:
        """Effectue un appel LLM avec support tool_use natif et fallback.

        Args:
            messages: Historique conversation au format OpenAI.
            openai_tools: Liste de tools au format OpenAI tool_use, ou None.

        Returns:
            LLMTurnResult normalisé prêt à être consommé par la boucle agent.
        """
        from app.core_llm import generate_with_tools, generate

        # Voie 1 — tool_use natif OpenAI
        try:
            result = await generate_with_tools(
                self.config, messages, tools=openai_tools or None,
            )
        except Exception as e:
            logger.warning(
                f"[LLM-TRANSPORT] generate_with_tools échoué ({e}), "
                f"fallback parsing texte"
            )
            return await self._call_text_fallback(messages)

        content = result.get("content", "") or ""
        native_calls = result.get("tool_calls", []) or []

        # Convertir les tool_calls natifs en ToolCall normalisés
        tool_calls: List[ToolCall] = [
            ToolCall(name=c["name"], arguments=c.get("arguments", {}))
            for c in native_calls
        ]

        # Voie 1bis — Si pas de tool_calls natifs mais le modèle a écrit des
        # balises dans le content (fallback Mistral text mode)
        if not tool_calls and content:
            cleaned_text, parsed = parse_tool_calls(content)
            if parsed:
                tool_calls = parsed
                content = cleaned_text

        # Construire le message assistant à ajouter à l'historique
        if native_calls:
            call_ids = [make_tool_call_id() for _ in native_calls]
            assistant_message = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": call_ids[i],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments", {})),
                        },
                    }
                    for i, c in enumerate(native_calls)
                ],
            }
        else:
            call_ids = []
            assistant_message = {"role": "assistant", "content": content or ""}

        return LLMTurnResult(
            content=content,
            tool_calls=tool_calls,
            call_ids=call_ids,
            assistant_message=assistant_message,
            native_mode=True,
        )

    async def _call_text_fallback(
        self,
        messages: List[Dict[str, Any]],
    ) -> LLMTurnResult:
        """Fallback : appel sans tools natifs, parsing texte des tool_calls."""
        from app.core_llm import generate

        raw = await generate(self.config, messages, force_norag=True)
        cleaned_text, parsed = parse_tool_calls(raw)

        return LLMTurnResult(
            content=cleaned_text or raw,
            tool_calls=parsed,
            call_ids=[],
            assistant_message={"role": "assistant", "content": raw},
            native_mode=False,
        )

    @staticmethod
    def build_tool_result_message(
        tool_call_id: str,
        tool_name: str,
        result: str,
        native_mode: bool,
    ) -> Dict[str, Any]:
        """Construit le message à injecter après exécution d'un outil.

        Format dépend du mode :
        - native_mode : {role: tool, tool_call_id: ..., content: ...}
        - fallback : {role: user, content: <tool_result name="...">...}
        """
        if native_mode and tool_call_id:
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            }
        return {
            "role": "user",
            "content": f'<tool_result name="{tool_name}">\n{result}\n</tool_result>',
        }
