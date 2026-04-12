# SPDX-License-Identifier: MIT
"""
Résultat structuré de la boucle agent Colaig.

AgentResult encapsule la réponse finale du LLM avec les métadonnées
collectées pendant l'exécution : outils appelés, sources documentaires,
datasets MCP, skills activées.

Ces métadonnées servent au post-processing (response_formatter) pour
construire un message Tchap complet avec bloc sources et références.

Rétrocompatible : `str(result)` et `result.text` retournent le texte
brut du LLM pour les callers qui n'ont pas besoin des métadonnées.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolTrace:
    """Trace d'un appel d'outil pendant la boucle agent."""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""       # premiers 200 chars du résultat
    success: bool = True


@dataclass
class Source:
    """Source documentaire ou externe utilisée dans la réponse.

    type : 'document' | 'mcp_dataset' | 'mcp_resource' | 'skill'
    name : nom affichable (nom de fichier, titre du dataset, nom de la skill)
    path : chemin WebDAV ou identifiant technique
    url  : lien cliquable (WebDAV, data.gouv.fr, etc.) — vide si pas disponible
    score : score de pertinence (0-1, uniquement pour les documents)
    extra : métadonnées additionnelles (organisation, tags, etc.)
    """
    type: str
    name: str
    path: str = ""
    url: str = ""
    score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Résultat complet d'une exécution de la boucle agent.

    Contient la réponse LLM finale + les métadonnées pour le formatage.
    Rétrocompatible avec les callers qui attendent un str.
    """
    text: str = ""                              # réponse finale du LLM
    tools_called: List[ToolTrace] = field(default_factory=list)
    sources: List[Source] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    turns: int = 0                              # nombre de tours effectués

    def __str__(self) -> str:
        """Rétrocompatibilité : str(result) retourne le texte brut."""
        return self.text

    def add_tool_trace(self, name: str, arguments: dict = None,
                       result_preview: str = "", success: bool = True) -> None:
        self.tools_called.append(ToolTrace(
            name=name,
            arguments=arguments or {},
            result_preview=result_preview[:200],
            success=success,
        ))

    def add_source(self, type: str, name: str, path: str = "",
                   url: str = "", score: float = 0.0, **extra) -> None:
        self.sources.append(Source(
            type=type, name=name, path=path, url=url,
            score=score, extra=extra,
        ))

    @property
    def has_sources(self) -> bool:
        return bool(self.sources)

    @property
    def has_tools(self) -> bool:
        return bool(self.tools_called)
