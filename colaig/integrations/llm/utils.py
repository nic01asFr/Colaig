"""
Colaig — Utilitaires partagés pour les clients LLM
"""
from __future__ import annotations

import re


def normalize_tool_call_id(raw_id: str) -> str:
    """Normalise un tool call ID en 9 caractères alphanumériques.

    Mistral impose : [a-zA-Z0-9], longueur exacte 9.
    On prend les 9 derniers alphanum du raw_id (la partie unique est souvent à la fin),
    ou on complète avec des 'a' si trop court.
    """
    alnum = re.sub(r"[^a-zA-Z0-9]", "", raw_id)
    if not alnum:
        import hashlib
        alnum = hashlib.sha256(raw_id.encode()).hexdigest()
    return alnum[-9:] if len(alnum) >= 9 else alnum.ljust(9, "a")
