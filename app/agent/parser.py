# SPDX-License-Identifier: MIT
"""
Extraction des appels d'outils depuis la sortie LLM.

Format attendu dans la réponse du LLM :
    <tool_call>{"name": "search_documents", "arguments": {"query": "..."}}</tool_call>

Le LLM peut produire du texte libre autour des balises.
Plusieurs tool_call dans une même réponse sont supportés.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.matrix_bot.config import logger

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

# Format Mistral natif : [TOOL_CALLS]name{json_args}
# ou [TOOL_CALLS][{"name": "...", "arguments": {...}}]
MISTRAL_TOOL_CALL_PATTERN = re.compile(
    r"\[TOOL_CALLS\]\s*(\w+)\s*(\{.*?\})",
    re.DOTALL,
)
MISTRAL_TOOL_CALL_LIST_PATTERN = re.compile(
    r"\[TOOL_CALLS\]\s*(\[.*?\])",
    re.DOTALL,
)


@dataclass
class ToolCall:
    """Appel d'outil extrait de la sortie LLM."""
    name: str
    arguments: Dict[str, Any]

    @classmethod
    def from_json(cls, raw: str) -> Optional["ToolCall"]:
        try:
            data = json.loads(raw)
            name = data.get("name", "")
            if not name:
                return None
            return cls(name=name, arguments=data.get("arguments", {}))
        except (json.JSONDecodeError, AttributeError):
            logger.debug(f"Tool call JSON invalide: {raw!r}")
            return None


def parse_tool_calls(response: str) -> Tuple[str, List[ToolCall]]:
    """Parse la sortie LLM et sépare le texte des appels d'outils.

    Supporte plusieurs formats :
    - <tool_call>{"name": "...", "arguments": {...}}</tool_call>  (XML custom)
    - [TOOL_CALLS]nom{args}                                       (Mistral natif)
    - [TOOL_CALLS][{"name": "...", "arguments": {...}}, ...]      (Mistral list)

    Returns:
        (text_without_tool_calls, list_of_tool_calls)
    """
    calls: List[ToolCall] = []
    clean_text = response

    # Format 1 : XML custom <tool_call>
    for match in TOOL_CALL_PATTERN.finditer(response):
        tc = ToolCall.from_json(match.group(1))
        if tc:
            calls.append(tc)
    clean_text = TOOL_CALL_PATTERN.sub("", clean_text)

    # Format 2 : Mistral [TOOL_CALLS]name{args}
    for match in MISTRAL_TOOL_CALL_PATTERN.finditer(response):
        name = match.group(1)
        try:
            args = json.loads(match.group(2))
            calls.append(ToolCall(name=name, arguments=args))
        except json.JSONDecodeError:
            logger.warning(f"Mistral tool call JSON invalide: {match.group(2)[:100]}")
    clean_text = MISTRAL_TOOL_CALL_PATTERN.sub("", clean_text)

    # Format 3 : Mistral [TOOL_CALLS][...]
    for match in MISTRAL_TOOL_CALL_LIST_PATTERN.finditer(response):
        try:
            items = json.loads(match.group(1))
            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    calls.append(ToolCall(
                        name=item["name"],
                        arguments=item.get("arguments", {}),
                    ))
        except json.JSONDecodeError:
            logger.warning(f"Mistral tool call list invalide: {match.group(1)[:100]}")
    clean_text = MISTRAL_TOOL_CALL_LIST_PATTERN.sub("", clean_text)

    return clean_text.strip(), calls
