"""
Colaig — Validation des chemins storage

Protection contre les path traversal attacks côté appelant,
indépendante des implémentations concrètes de StorageProtocol.

Usage :
    from colaig.security.path_validator import validate_storage_path

    # Dans un backend storage
    path = validate_storage_path(user_path, allow_dotcolaig=True)

    # Pour un upload utilisateur
    path = validate_storage_path(user_path, allow_dotcolaig=False)
"""

from __future__ import annotations

import logging
import posixpath
import re

from colaig import paths

logger = logging.getLogger(__name__)

# Patterns de traversal détectés
_TRAVERSAL_RE = re.compile(r'\.\.|\x00|\\')
# Séquences interdites supplémentaires
_DOUBLE_SLASH_RE = re.compile(r'//')
# Chemin de premier niveau workspace : /slug/ ou /slug
_WORKSPACE_PATH_RE = re.compile(r'^/[a-zA-Z0-9][a-zA-Z0-9_\-]*/?$')


def validate_storage_path(
    path: str,
    *,
    allow_dotcolaig: bool = True,
    context: str = "",
) -> str:
    """Valide et normalise un chemin storage.

    Règles :
    - Rejette les séquences ``..``, null bytes, backslashes
    - Rejette les doubles slashes ``//``
    - Si allow_dotcolaig=False, rejette tout chemin contenant ``/.colaig``
    - Normalise : ajoute le slash initial si absent, strip les espaces

    Args:
        path: Chemin à valider.
        allow_dotcolaig: Si False, rejette les accès au répertoire .colaig/.
            Utiliser False pour les uploads utilisateur, True pour les accès internes.
        context: Contexte pour le log d'audit.

    Returns:
        Chemin normalisé (toujours avec '/' initial).

    Raises:
        StorageError: Si le chemin est invalide ou dangereux.
    """

    if not path or not isinstance(path, str):
        _reject(path, "chemin vide ou invalide", context)

    path = path.strip()

    if _TRAVERSAL_RE.search(path):
        _reject(path, "séquence de traversal détectée", context)

    if _DOUBLE_SLASH_RE.search(path):
        _reject(path, "double slash détecté", context)

    # Normaliser : assurer le slash initial
    if not path.startswith("/"):
        path = "/" + path

    # Normalisation POSIX (supprime les . inutiles, etc.)
    path = posixpath.normpath(path)
    # normpath peut supprimer le slash final — on ne le remet pas (géré par l'appelant)

    if not allow_dotcolaig:
        if paths.is_reserved_path(path):
            _reject(path, "accès interdit à .colaig/", context)

    return path


def validate_workspace_path(
    path: str,
    known_paths: set | None = None,
) -> str:
    """Valide un workspace_path (chemin de premier niveau).

    Args:
        path: Chemin du workspace (ex: '/espace-rh/' ou '/espace-rh').
        known_paths: Ensemble de chemins connus — si fourni, vérifie que path y figure.

    Returns:
        Chemin normalisé.

    Raises:
        StorageError: Si le chemin est invalide.
        ValueError: Si le chemin n'est pas dans known_paths.
    """
    normalized = validate_storage_path(path, allow_dotcolaig=False)

    # Doit être un chemin de premier niveau
    if not _WORKSPACE_PATH_RE.match(normalized) and not _WORKSPACE_PATH_RE.match(normalized + "/"):
        from colaig.exceptions import StorageError
        raise StorageError(f"Chemin workspace invalide : '{path}'")

    if known_paths is not None:
        # Normaliser les entrées connues pour la comparaison
        norm = normalized.rstrip("/") + "/"
        norm2 = normalized.rstrip("/")
        if norm not in known_paths and norm2 not in known_paths and normalized not in known_paths:
            raise ValueError(f"Workspace inconnu : '{path}'")

    return normalized


def is_subpath(child: str, parent: str) -> bool:
    """Retourne True si child est un sous-chemin de parent.

    Exemples :
        is_subpath('/alice/docs/file.pdf', '/alice/') → True
        is_subpath('/bob/file.pdf', '/alice/') → False
        is_subpath('/alice/../bob/', '/alice/') → False (normpath résout)

    Args:
        child: Chemin enfant à tester.
        parent: Chemin parent de référence.
    """
    # Normaliser les deux
    child_norm = posixpath.normpath("/" + child.strip().lstrip("/")) + "/"
    parent_norm = posixpath.normpath("/" + parent.strip().lstrip("/")) + "/"
    return child_norm.startswith(parent_norm)


def _reject(path: str, reason: str, context: str = "") -> None:
    """Log et lève StorageError."""
    from colaig.exceptions import StorageError
    ctx = f" [{context}]" if context else ""
    logger.warning("path_validator: chemin rejeté%s — %s: %r", ctx, reason, path)
    raise StorageError(f"Chemin interdit : {reason}")
