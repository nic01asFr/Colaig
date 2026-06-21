"""
agents/tool_registry.py — Registre d'outils pour l'Orchestrateur agentique.

Le ToolRegistry est construit en mémoire au démarrage (zéro database).
Chaque outil est une paire (ToolDefinition, callable async handler).
L'Orchestrateur l'utilise pour découvrir les outils disponibles et les exécuter.
"""

from __future__ import annotations

from collections.abc import Callable

from colaig.models import ToolCall, ToolDefinition, ToolResult


class ToolRegistry:
    """Registre central des outils disponibles pour l'Orchestrateur.

    Construit au démarrage via agents/context_builder.build_tool_registry().
    Les handlers sont des closures sur retriever/storage/albert — zéro database.
    """

    def __init__(self) -> None:
        # name → (definition, async handler)
        self._tools: dict[str, tuple[ToolDefinition, Callable]] = {}

    # ------------------------------------------------------------------
    # Enregistrement

    def register(self, definition: ToolDefinition, handler: Callable) -> None:
        """Enregistre un outil avec son handler async.

        Args:
            definition: Métadonnées et schéma JSON de l'outil.
            handler: Callable async exécutable par l'Orchestrateur.
        """
        self._tools[definition.name] = (definition, handler)

    # ------------------------------------------------------------------
    # Consultation

    def get(self, name: str) -> tuple[ToolDefinition, Callable] | None:
        """Retourne (definition, handler) pour un outil, ou None si inconnu."""
        return self._tools.get(name)

    def list_definitions(self) -> list[ToolDefinition]:
        """Retourne toutes les ToolDefinition enregistrées."""
        return [defn for defn, _ in self._tools.values()]

    def list_openai_schemas(self) -> list[dict]:
        """Retourne les schémas OpenAI function calling pour tous les outils."""
        return [defn.to_openai_schema() for defn, _ in self._tools.values()]

    def filter_by_names(self, names: list[str]) -> ToolRegistry:
        """Retourne un nouveau registry contenant uniquement les outils listés.

        Si names est vide, retourne tous les outils (convention : [] = pas de filtre).
        Les noms inconnus sont silencieusement ignorés.
        """
        if not names:
            return self
        filtered = ToolRegistry()
        for name in names:
            if name in self._tools:
                filtered._tools[name] = self._tools[name]
        return filtered

    def names(self) -> list[str]:
        """Retourne les noms de tous les outils enregistrés."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------------
    # Exécution

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """Exécute un ToolCall et retourne un ToolResult.

        Capture les exceptions et les retourne comme ToolResult(success=False).
        """
        entry = self._tools.get(tool_call.tool_name)
        if entry is None:
            return ToolResult(
                tool_name=tool_call.tool_name,
                call_id=tool_call.call_id,
                success=False,
                error=f"Outil inconnu : {tool_call.tool_name!r}",
            )

        definition, handler = entry
        try:
            result_str = await handler(**tool_call.arguments)
            return ToolResult(
                tool_name=tool_call.tool_name,
                call_id=tool_call.call_id,
                result=str(result_str),
                success=True,
            )
        except TypeError as exc:
            return ToolResult(
                tool_name=tool_call.tool_name,
                call_id=tool_call.call_id,
                success=False,
                error=f"Paramètres invalides : {exc}",
            )
        except Exception as exc:
            return ToolResult(
                tool_name=tool_call.tool_name,
                call_id=tool_call.call_id,
                success=False,
                error=str(exc),
            )
