"""
Colaig — Filtre des secrets dans les logs

logging.Filter qui masque automatiquement les API keys, tokens et passwords
dans tous les log records de l'application.

Installation (une seule fois dans main.py ou utils/logging.py) :
    from colaig.security.secrets_filter import SecretsMaskingFilter
    logging.getLogger().addFilter(SecretsMaskingFilter())
"""

from __future__ import annotations

import logging
import re

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # API keys et tokens génériques dans les chaînes key=value
    (
        re.compile(
            r'(api[_-]?key|token|password|secret|bearer)\s*[=:]\s*["\']?([a-zA-Z0-9_\-\.]{8,})',
            re.I,
        ),
        r'\1=***',
    ),
    # Tokens Colaig auto-localisants
    (re.compile(r'colaig_[a-z0-9_]+_[0-9a-f]{16,}'), 'colaig_***'),
    # Clés Albert API (format ark_)
    (re.compile(r'ark_[a-zA-Z0-9_\-]{8,}'), 'ark_***'),
    # Bearer tokens dans headers HTTP
    (re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]{8,}'), 'Bearer ***'),
    # Credentials dans URLs (user:pass@host)
    (re.compile(r'(https?://)([^:@/\s]+):([^@/\s]+)@'), r'\1\2:***@'),
    # Mots de passe WebDAV/S3 explicites
    (re.compile(r'(WEBDAV_PASSWORD|S3_SECRET|MATRIX_PASSWORD)\s*=\s*\S+', re.I), r'\1=***'),
]


def mask_secrets(text: str) -> str:
    """Masque les patterns de secrets dans une chaîne de texte.

    Args:
        text: Texte brut potentiellement contenant des secrets.

    Returns:
        Texte avec les secrets remplacés par '***'.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretsMaskingFilter(logging.Filter):
    """logging.Filter qui masque les secrets dans tous les log records.

    S'installe sur le logger racine pour couvrir toute l'application.

    Exemple :
        logging.getLogger().addFilter(SecretsMaskingFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Masquer dans le message
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)

        # Masquer dans les arguments de formatage
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    mask_secrets(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: mask_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }

        return True
