"""
Colaig — capacités optionnelles d'un client LLM.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.3

`LLMClientProtocol` déclare **cinq** méthodes : `chat`, `chat_stream`,
`chat_with_tools`, `embed`, `embed_batch`. Tous les backends les fournissent.

Mais le code métier en appelle **trois de plus**, et aucune n'est déclarée :

| capacité | appelée par | albert | openai | azure | ollama |
|---|---|---|---|---|---|
| `ocr` | `rag/indexer.py` | ✅ | — | — | — |
| `rerank` | `rag/retriever.py` | ✅ | ✅ | — | — |
| `transcribe` | `messaging/handlers.py` | ✅ | ✅ | — | — |

Avec `LLM_BACKEND=ollama`, indexer un PDF scanné levait donc un `AttributeError`,
rattrapé par un `except Exception` générique et rapporté comme « OCR en échec ». Le
message envoyait chercher du côté du modèle, alors que le backend n'a simplement pas
cette capacité. Un diagnostic faux coûte plus cher qu'une absence de diagnostic.

Ce module permet de **demander avant d'appeler**. Il ne rend pas les capacités
obligatoires — les rendre obligatoires forcerait Ollama à implémenter un OCR qu'il n'a
pas — il rend leur absence **explicite et traitable**.
"""
from __future__ import annotations

CAPACITES_OPTIONNELLES: frozenset[str] = frozenset({"ocr", "rerank", "transcribe"})
"""Capacités appelées par le code métier mais absentes de `LLMClientProtocol`."""


def supporte(client: object, capacite: str) -> bool:
    """Le client expose-t-il cette capacité ?

    `supporte(client, "ocr")` avant `client.ocr(...)` remplace un `AttributeError`
    au milieu d'une indexation par une décision prise à froid.
    """
    if client is None:
        return False
    return callable(getattr(client, capacite, None))


def capacites(client: object) -> set[str]:
    """Ensemble des capacités optionnelles réellement disponibles sur ce client."""
    return {c for c in CAPACITES_OPTIONNELLES if supporte(client, c)}


def motif_absence(client: object, capacite: str) -> str:
    """Message expliquant **pourquoi** une capacité manque, pour un journal ou un rapport.

    Distingue les deux causes, qui n'envoient pas chercher au même endroit : aucun
    client configuré (affaire de configuration) ou backend dépourvu de la capacité
    (affaire de choix de provider).
    """
    if client is None:
        return f"aucun client LLM configuré, donc pas de {capacite}"
    nom = type(client).__name__
    return f"le backend LLM ({nom}) ne fournit pas la capacité « {capacite} »"
