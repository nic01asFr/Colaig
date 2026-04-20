"""
Colaig — Sanitisation des contenus admin avant injection LLM

Atténuation des risques d'injection de prompt via les champs de configuration workspace
(system_prompt, description, topics).

Stratégie : log d'audit + troncature plutôt que rejet silencieux.
L'admin a la légitimité d'écrire des prompts complexes — le log alerte sans casser.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_MAX_SYSTEM_PROMPT_LENGTH = 4096
_MAX_DESCRIPTION_LENGTH = 512

# Patterns d'instruction-hijacking connus
_INJECTION_PATTERNS = [
    re.compile(
        r'(ignore|oublie|forget)\s+(toutes?\s+)?(les\s+)?(instructions?|consignes?|règles?)',
        re.I,
    ),
    re.compile(
        r'(envoie|transmets?|répète|affiche|retourne|révèle)\s+.{0,50}'
        r'(token|clé|password|secret|api.?key|credential)',
        re.I,
    ),
    re.compile(r'<\|?(system|user|assistant|im_start|im_end)\|?>', re.I),
]

# Caractères de contrôle à supprimer (garde \t, \n, \r)
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _strip_control_chars(text: str) -> str:
    """Supprime les caractères de contrôle non imprimables."""
    return _CONTROL_CHARS_RE.sub('', text)


def _check_injection(text: str, field_name: str) -> None:
    """Log un warning si un pattern d'injection potentiel est détecté."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning(
                "prompt_sanitizer: pattern d'injection potentiel dans '%s' — audit recommandé",
                field_name,
            )
            break


def sanitize_system_prompt(text: str) -> str:
    """Sanitise un system_prompt provenant de config.yaml.

    Args:
        text: Contenu brut du system_prompt workspace.

    Returns:
        Texte sanitisé (tronqué si trop long, caractères de contrôle supprimés).
    """
    if not text:
        return text

    text = _strip_control_chars(text)

    if len(text) > _MAX_SYSTEM_PROMPT_LENGTH:
        logger.warning(
            "prompt_sanitizer: system_prompt tronqué (%d → %d chars)",
            len(text),
            _MAX_SYSTEM_PROMPT_LENGTH,
        )
        text = text[:_MAX_SYSTEM_PROMPT_LENGTH]

    _check_injection(text, "system_prompt")
    return text


def sanitize_description(text: str, max_length: int = _MAX_DESCRIPTION_LENGTH) -> str:
    """Sanitise un champ description ou topic avant injection dans un prompt.

    Args:
        text: Contenu brut (workspace.description ou topic).
        max_length: Longueur maximum autorisée.

    Returns:
        Texte sanitisé.
    """
    if not text:
        return text

    text = _strip_control_chars(text)

    if len(text) > max_length:
        text = text[:max_length]

    _check_injection(text, "description/topic")
    return text
