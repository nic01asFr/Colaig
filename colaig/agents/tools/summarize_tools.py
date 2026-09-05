"""
agents/tools/summarize_tools.py — Outil de synthèse de texte pour l'Orchestrateur.

Outil : summarize_text
Utilise : LLMClientProtocol (1 appel LLM)
"""

from __future__ import annotations

import json
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter
from colaig.security.wrap import CONSIGNE, baliser

SUMMARIZE_TEXT_DEFINITION = ToolDefinition(
    name="summarize_text",
    description=(
        "Résume un texte long en un paragraphe concis. Utile après fetch_document "
        "pour condenser le contenu d'un document avant de l'intégrer dans la réponse."
    ),
    parameters=[
        ToolParameter(
            name="text",
            type="string",
            description="Le texte à résumer.",
            required=True,
        ),
        ToolParameter(
            name="max_sentences",
            type="integer",
            description="Nombre maximum de phrases dans le résumé (défaut : 5).",
            required=False,
        ),
        ToolParameter(
            name="language",
            type="string",
            description="Langue du résumé (défaut : 'fr').",
            required=False,
            enum=["fr", "en"],
        ),
    ],
    category="llm",
)


def create_summarize_handler(albert, model: str | None = None) -> Callable:
    """Crée un handler async pour summarize_text.

    Args:
        albert: Implémentation de LLMClientProtocol.
        model: Modèle à utiliser (défaut : modèle chat de l'instance Albert).
    """

    async def summarize_handler(
        text: str,
        max_sentences: int | None = 5,
        language: str | None = "fr",
    ) -> str:
        """Résume le texte via un appel Albert.

        Returns:
            JSON string : {"summary": ..., "original_length": ...}
        """
        max_s = max_sentences if max_sentences is not None else 5
        lang = language or "fr"

        # Le texte à résumer vient typiquement de `fetch_document`, donc de l'espace de
        # stockage. Un résumé n'est pas anodin : il est réinjecté dans la suite du
        # pipeline, et une consigne obéie ici se propage sous une forme qui a l'air
        # d'être notre propre production (L2.1).
        prompt = (
            f"Résume le texte suivant en {max_s} phrases maximum, en {lang}. "
            f"Sois concis et factuel. Réponds uniquement avec le résumé.\n"
            f"{CONSIGNE}\n\n"
            + baliser(text[:4000], source="texte fourni", nature="document")
        )

        summary = await albert.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.1,
            max_tokens=512,
        )

        return json.dumps({
            "summary": summary.strip(),
            "original_length": len(text),
        }, ensure_ascii=False)

    return summarize_handler
