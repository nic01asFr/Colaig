# SPDX-License-Identifier: MIT
"""
Sélection de l'espace documentaire (`.colaig`) à associer à un salon Tchap.

Modèle (aligné sur le format réel du storage) : un espace est un dossier
contenant un répertoire `.colaig` avec un descripteur `.colaig/config.yaml` :

    workspace_id: conception-routiere
    name: Conception Routière
    conversations:                 # salons explicitement rattachés (room_ids)
      - "!HSwgmpTDgVFXUwecab:agent.tchap.gouv.fr"
    user_ids:                      # utilisateurs rattachés (workspace DM/perso)
      - "@nicolas.laval:agent.tchap.gouv.fr"
    system_prompt: "..."
    # --- Extension optionnelle pour l'auto-détection à l'invitation ---
    match:
      room_name: "(?i)urbanism"    # regex sur le nom du salon
      room_topic: "(?i)\\bPLU\\b"  # regex sur le sujet du salon
    priority: 10

À l'invitation du bot, on score chaque espace selon les conditions du salon et
on retient le meilleur. Ordre décroissant : salon déjà rattaché (`conversations`)
> utilisateur rattaché (`user_ids`, mode DM) > regex nom > regex sujet >
convention de nom > `default_workspace`. Module **pur** (aucune I/O), testable.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

SCORE_ROOM_ID = 1000        # salon déjà dans `conversations`
SCORE_USER = 500            # utilisateur dans `user_ids` (DM/perso)
SCORE_NAME_REGEX = 300      # match.room_name
SCORE_TOPIC_REGEX = 200     # match.room_topic
SCORE_NAME_CONVENTION = 100 # nom du dossier/espace == nom du salon
SCORE_DEFAULT = 10          # repli default_workspace


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


def _as_list(v: Any) -> List[str]:
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def score_candidate(
    *,
    descriptor: Optional[Dict[str, Any]],
    folder_name: str,
    candidate_path: str,
    room_id: str,
    room_name: str = "",
    room_topic: str = "",
    user_id: str = "",
    default_workspace: str = "",
) -> int:
    """Score un espace candidat pour un salon donné. 0 = pas de correspondance."""
    descriptor = descriptor or {}
    match = descriptor.get("match") or {}
    try:
        priority = int(descriptor.get("priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0

    # 1. Salon déjà rattaché (champ réel `conversations` + extension `match.rooms`)
    explicit_rooms = _as_list(descriptor.get("conversations")) + _as_list(match.get("rooms"))
    if room_id and room_id in explicit_rooms:
        return SCORE_ROOM_ID + priority

    # 2. Utilisateur rattaché (workspace DM/personnel)
    if user_id and user_id in _as_list(descriptor.get("user_ids")):
        return SCORE_USER + priority

    # 3. Regex sur le nom du salon
    if _safe_search(match.get("room_name", ""), room_name):
        return SCORE_NAME_REGEX + priority

    # 4. Regex sur le sujet du salon
    if _safe_search(match.get("room_topic", ""), room_topic):
        return SCORE_TOPIC_REGEX + priority

    # 5. Convention de nom : nom du dossier / espace == nom du salon
    names = [folder_name, descriptor.get("name", ""), descriptor.get("workspace_id", "")]
    if room_name and any(n and _norm(n) == _norm(room_name) for n in names):
        return SCORE_NAME_CONVENTION + priority

    # 6. Repli sur le workspace par défaut
    if default_workspace and candidate_path.strip("/") == default_workspace.strip("/"):
        return SCORE_DEFAULT

    return 0


def select_workspace(
    candidates: List[Dict[str, Any]],
    *,
    room_id: str,
    room_name: str = "",
    room_topic: str = "",
    user_id: str = "",
    default_workspace: str = "",
) -> Optional[Dict[str, Any]]:
    """Retourne le meilleur espace pour un salon, ou None si aucune correspondance.

    candidates: liste de {"path", "name", "descriptor"} (espaces `.colaig`).
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
            user_id=user_id,
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
        return "conversation"
    if score >= SCORE_USER:
        return "user_id"
    if score >= SCORE_NAME_REGEX:
        return "room_name"
    if score >= SCORE_TOPIC_REGEX:
        return "room_topic"
    if score >= SCORE_NAME_CONVENTION:
        return "name_convention"
    return "default_workspace"
