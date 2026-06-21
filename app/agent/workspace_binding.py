# SPDX-License-Identifier: MIT
"""
Sélection de l'espace documentaire (`.colaig`) à associer à un salon Tchap.

Modèle : un espace est un dossier du storage contenant un répertoire `.colaig`.
Ce répertoire peut contenir un descripteur `colaig.yaml` portant des critères de
correspondance avec le salon :

    name: "Urbanisme"
    priority: 10
    match:
      rooms: ["!abc123:agent.tchap.gouv.fr"]   # IDs de salons explicites
      room_name: "(?i)urbanism"                 # regex sur le nom du salon
      room_topic: "(?i)\\bPLU\\b"               # regex sur le sujet du salon

À l'invitation du bot dans un salon, on score chaque espace candidat selon les
conditions du salon et on retient le meilleur. Ordre de priorité décroissant :
ID de salon explicite > regex nom > regex sujet > convention de nom > workspace
par défaut. Ce module est **pur** (aucune I/O) pour être testable isolément.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

# Scores de base par type de correspondance (le `priority` du descripteur s'y ajoute).
SCORE_ROOM_ID = 1000
SCORE_NAME_REGEX = 300
SCORE_TOPIC_REGEX = 200
SCORE_NAME_CONVENTION = 100
SCORE_DEFAULT = 10


def _norm(s: str) -> str:
    """Normalise pour comparaison : minuscules, sans accents, alphanumérique."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _safe_search(pattern: str, text: str) -> bool:
    """Recherche regex tolérante : False si pattern invalide ou texte vide."""
    if not pattern or not text:
        return False
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return False


def score_candidate(
    *,
    descriptor: Optional[Dict[str, Any]],
    folder_name: str,
    candidate_path: str,
    room_id: str,
    room_name: str,
    room_topic: str,
    default_workspace: str = "",
) -> int:
    """Score un espace candidat pour un salon donné. 0 = pas de correspondance."""
    descriptor = descriptor or {}
    match = descriptor.get("match") or {}
    try:
        priority = int(descriptor.get("priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0

    # 1. ID de salon explicite (le plus fort)
    rooms = match.get("rooms") or []
    if isinstance(rooms, str):
        rooms = [rooms]
    if room_id and room_id in rooms:
        return SCORE_ROOM_ID + priority

    # 2. Regex sur le nom du salon
    if _safe_search(match.get("room_name", ""), room_name):
        return SCORE_NAME_REGEX + priority

    # 3. Regex sur le sujet du salon
    if _safe_search(match.get("room_topic", ""), room_topic):
        return SCORE_TOPIC_REGEX + priority

    # 4. Convention de nom : nom du dossier == nom du salon (normalisés)
    if folder_name and room_name and _norm(folder_name) == _norm(room_name):
        return SCORE_NAME_CONVENTION + priority

    # 5. Repli sur le workspace par défaut
    if default_workspace and candidate_path.strip("/") == default_workspace.strip("/"):
        return SCORE_DEFAULT

    return 0


def select_workspace(
    candidates: List[Dict[str, Any]],
    *,
    room_id: str,
    room_name: str = "",
    room_topic: str = "",
    default_workspace: str = "",
) -> Optional[Dict[str, Any]]:
    """Retourne le meilleur espace pour un salon, ou None si aucune correspondance.

    Args:
        candidates: liste de {"path", "name", "descriptor"} (espaces `.colaig`).
        room_id, room_name, room_topic: conditions du salon.
        default_workspace: chemin du workspace par défaut (repli).

    Returns:
        Le candidat retenu enrichi de "score" et "reason", ou None.
    """
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for c in candidates:
        s = score_candidate(
            descriptor=c.get("descriptor"),
            folder_name=c.get("name", ""),
            candidate_path=c.get("path", ""),
            room_id=room_id,
            room_name=room_name,
            room_topic=room_topic,
            default_workspace=default_workspace,
        )
        if s > best_score:
            best_score = s
            best = c

    if best and best_score > 0:
        return {**best, "score": best_score, "reason": _reason_for_score(best_score)}
    return None


def _reason_for_score(score: int) -> str:
    if score >= SCORE_ROOM_ID:
        return "room_id"
    if score >= SCORE_NAME_REGEX:
        return "room_name"
    if score >= SCORE_TOPIC_REGEX:
        return "room_topic"
    if score >= SCORE_NAME_CONVENTION:
        return "name_convention"
    return "default_workspace"
