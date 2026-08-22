"""
agents/tools/summarize_tools.py — Outil de synthèse de texte pour l'Orchestrateur.

Outil : summarize_text
Utilise : LLMClientProtocol (1 appel LLM)
"""

from __future__ import annotations

import json
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter

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

        prompt = (
            f"Résume le texte suivant en {max_s} phrases maximum, en {lang}. "
            f"Sois concis et factuel. Réponds uniquement avec le résumé.\n\n"
            f"Texte :\n{text[:4000]}"  # Troncature pour éviter dépassement token
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
