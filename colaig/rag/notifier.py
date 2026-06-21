"""
Colaig — Notificateur proactif de changements documentaires

Formate les notifications de nouveaux/modifiés documents pour envoi via MessagingProtocol.

Mode A : liste des fichiers (toujours disponible)
Mode B : enrichissement sémantique via contextual_prefix des chunks
         (zéro appel LLM supplémentaire — préfixes déjà générés à l'indexation)
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from colaig.models import UpdateSummary
    from colaig.rag.faiss_store import FaissStore

logger = logging.getLogger(__name__)

_MAX_DOCS_IN_NOTIFICATION = 10   # Au-delà, on résume en "N documents"
_MAX_PREFIX_LENGTH = 200          # Troncature du contextual_prefix si trop long


def format_notification(
    workspace_name: str,
    update: UpdateSummary,
    store: FaissStore | None = None,
    language: str = "fr",
) -> str:
    """Formate une notification de changements documentaires.

    Mode A (store=None ou pas de contextual_prefix) :
        Notification légère — liste des noms de fichiers uniquement.

    Mode B (store fourni + contextual_prefix disponibles) :
        Notification enrichie — description sémantique par document,
        extraite du premier chunk contextuel (déjà calculé lors de l'indexation).

    Args:
        workspace_name: Nom affiché du workspace.
        update: Résultat de check_updates() avec changed_paths et removed_paths.
        store: FaissStore du workspace (pour extraction des contextual_prefix).
        language: Langue de la notification ("fr" ou "en").

    Returns:
        Texte formaté Markdown prêt à envoyer.
    """
    changed = update.changed_paths
    removed = list(update.removed_paths)

    if not changed and not removed:
        return ""

    lines: list[str] = []

    # En-tête
    if language == "fr":
        lines.append(f"📄 **{workspace_name}** — mise à jour documentaire")
    else:
        lines.append(f"📄 **{workspace_name}** — document update")

    # Documents nouveaux / modifiés
    if changed:
        if language == "fr":
            label = "Nouveau" if len(changed) == 1 else f"{len(changed)} documents"
            if len(changed) == 1:
                label = "1 document mis à jour"
            else:
                label = f"{len(changed)} documents mis à jour"
        else:
            label = "1 document updated" if len(changed) == 1 else f"{len(changed)} documents updated"

        lines.append(f"\n**{label}**")

        # Récupère les descriptions sémantiques si disponibles
        doc_descriptions = _extract_descriptions(changed, store)

        truncated = changed[:_MAX_DOCS_IN_NOTIFICATION]
        for path in truncated:
            name = os.path.basename(path)
            prefix = doc_descriptions.get(path, "")
            if prefix:
                lines.append(f"• **{name}** — {prefix}")
            else:
                lines.append(f"• {name}")

        if len(changed) > _MAX_DOCS_IN_NOTIFICATION:
            remainder = len(changed) - _MAX_DOCS_IN_NOTIFICATION
            if language == "fr":
                lines.append(f"• … et {remainder} autre(s)")
            else:
                lines.append(f"• … and {remainder} more")

    # Documents supprimés
    if removed:
        if language == "fr":
            label = "1 document supprimé" if len(removed) == 1 else f"{len(removed)} documents supprimés"
        else:
            label = "1 document removed" if len(removed) == 1 else f"{len(removed)} documents removed"
        lines.append(f"\n**{label}**")
        for path in removed[:_MAX_DOCS_IN_NOTIFICATION]:
            lines.append(f"• ~~{os.path.basename(path)}~~")

    return "\n".join(lines)


def _extract_descriptions(
    paths: list[str],
    store: FaissStore | None,
) -> dict[str, str]:
    """Extrait le contextual_prefix du premier chunk actif pour chaque chemin.

    Ne lève jamais d'exception — retourne un dict vide en cas d'erreur.
    Le FaissStore contient déjà les chunks en mémoire (pas d'I/O).
    """
    if store is None:
        return {}

    try:
        all_chunks = store.get_all_active_chunks()
    except Exception:
        logger.debug("notifier: impossible de lire les chunks du store", exc_info=True)
        return {}

    # Index source_path → premier chunk avec contextual_prefix non vide
    first_by_source: dict[str, str] = {}
    for chunk in all_chunks:
        if chunk.source_path not in first_by_source and chunk.contextual_prefix:
            prefix = chunk.contextual_prefix.strip()
            if prefix:
                # Tronque si nécessaire
                if len(prefix) > _MAX_PREFIX_LENGTH:
                    prefix = prefix[:_MAX_PREFIX_LENGTH].rstrip() + "…"
                first_by_source[chunk.source_path] = prefix

    return {path: first_by_source[path] for path in paths if path in first_by_source}
