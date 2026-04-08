# SPDX-License-Identifier: MIT
"""
Context manager pour l'indicateur "en train d'écrire" de Matrix.

Usage :
    async with typing_indicator(matrix_client, room_id):
        await long_operation()

Garantit la désactivation de l'indicateur même en cas d'exception.
"""

from contextlib import asynccontextmanager
import logging

logger = logging.getLogger("colaig.typing")


@asynccontextmanager
async def typing_indicator(matrix_client, room_id: str):
    """Active l'indicateur de frappe et le désactive en sortie (finally)."""
    try:
        await matrix_client.room_typing(room_id, typing_state=True)
    except Exception:
        pass  # Ne pas bloquer si l'indicateur échoue
    try:
        yield
    finally:
        try:
            await matrix_client.room_typing(room_id, typing_state=False)
        except Exception:
            pass  # Cleanup best-effort
