"""
Colaig — ColaigIndex

Source de vérité unique pour les clés du FaissIndexRegistry et les chemins
de persistance de tous les index FAISS du projet.

Responsabilités :
1. Clés de registry    — format "{ws_path}::{type}" défini en un seul endroit
2. Chemins storage     — "{ws}/.colaig/indexes/..." défini en un seul endroit
3. Chargement partagé  — load_store() réutilisé par tous les consommateurs

Types d'index gérés :
    docs      — chunks RAG documents (index.faiss + metadata.pkl)
    behaviors — activation sémantique des behaviors (behaviors.faiss + .pkl)
    skills    — sélection lazy des skills (skills.faiss + .pkl)
    memory    — mémoire sémantique per-user (memory.faiss + .pkl)
    federation— répertoire vectoriel des workspaces (workspaces.faiss + .pkl)
    knowledge — cartographie sémantique workspace / Bloc D (à venir)

Usage :
    # Clés (méthodes statiques — aucune instance requise)
    key = ColaigIndex.docs_key("/espace-rh/")
    # → "/espace-rh::docs"

    key = ColaigIndex.user_memory_key("/espace-rh/", "alice_tchap_fr")
    # → "user::/espace-rh::alice_tchap_fr"

    # Chemins (méthodes statiques)
    faiss, meta = ColaigIndex.behaviors_paths("/espace-rh/")
    # → ("/espace-rh/.colaig/indexes/behaviors.faiss",
    #    "/espace-rh/.colaig/indexes/behaviors.pkl")

    # Chargement lazy (méthode de classe — nécessite storage)
    store = await ColaigIndex.load_store(storage, faiss, meta)
    # → FaissStore | None
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, ClassVar

from colaig import paths

if TYPE_CHECKING:
    from colaig.protocols import StorageProtocol
    from colaig.rag.faiss_store import FaissStore

logger = logging.getLogger(__name__)


class ColaigIndex:
    """Source de vérité des clés de registry et des chemins de persistance FAISS.

    Toutes les méthodes de clé/chemin sont statiques — aucune instance n'est
    nécessaire pour nommer un index.  load_store() est un utilitaire de classe
    partagé pour éviter la duplication du pattern download+deserialize.

    Convention de clés :
        Workspace  : "{ws_path.rstrip('/')}::{type}"
        User memory: "user::{ws_path.rstrip('/')}/{safe_uid}"
        Fédération : "federation::workspaces"  (constante de classe)
    """

    # ── Constantes de classe (index sans workspace) ─────────────────────────

    FEDERATION_KEY: ClassVar[str] = "federation::workspaces"
    FEDERATION_FAISS_PATH: ClassVar[str] = paths.federation_index_files()[0]
    FEDERATION_META_PATH: ClassVar[str] = paths.federation_index_files()[1]

    # ── Clés de registry ────────────────────────────────────────────────────

    @staticmethod
    def docs_key(ws_path: str) -> str:
        """Clé registry pour l'index RAG docs d'un workspace."""
        return f"{ws_path.rstrip('/')}::docs"

    @staticmethod
    def behaviors_key(ws_path: str) -> str:
        """Clé registry pour l'index behaviors d'un workspace."""
        return f"{ws_path.rstrip('/')}::behaviors"

    @staticmethod
    def skills_key(ws_path: str) -> str:
        """Clé registry pour l'index skills d'un workspace."""
        return f"{ws_path.rstrip('/')}::skills"

    @staticmethod
    def user_memory_key(ws_path: str, safe_uid: str) -> str:
        """Clé registry pour la mémoire sémantique d'un utilisateur."""
        return f"user::{ws_path.rstrip('/')}::{safe_uid}"

    @staticmethod
    def knowledge_key(ws_path: str) -> str:
        """Clé registry pour la cartographie sémantique d'un workspace (Bloc D)."""
        return f"{ws_path.rstrip('/')}::knowledge"

    # ── Chemins de persistance ───────────────────────────────────────────────

    @staticmethod
    def docs_paths(ws_path: str) -> tuple[str, str]:
        """(faiss_path, meta_path) pour l'index RAG docs."""
        return (paths.index_file(ws_path, "index.faiss"),
                paths.index_file(ws_path, "metadata.pkl"))

    @staticmethod
    def behaviors_paths(ws_path: str) -> tuple[str, str]:
        """(faiss_path, meta_path) pour l'index behaviors."""
        return (paths.index_file(ws_path, "behaviors.faiss"),
                paths.index_file(ws_path, "behaviors.pkl"))

    @staticmethod
    def skills_paths(ws_path: str) -> tuple[str, str]:
        """(faiss_path, meta_path) pour l'index skills."""
        return (paths.index_file(ws_path, "skills.faiss"),
                paths.index_file(ws_path, "skills.pkl"))

    @staticmethod
    def user_memory_paths(ws_path: str, safe_uid: str) -> tuple[str, str]:
        """(faiss_path, meta_path) pour la mémoire sémantique d'un utilisateur."""
        return (paths.user_file(ws_path, safe_uid, "memory.faiss"),
                paths.user_file(ws_path, safe_uid, "memory.pkl"))

    @staticmethod
    def user_profile_path(ws_path: str, safe_uid: str) -> str:
        """Chemin du profil JSON d'un utilisateur."""
        return paths.user_file(ws_path, safe_uid, "profile.json")

    @staticmethod
    def user_dir(ws_path: str, safe_uid: str) -> str:
        """Dossier parent des fichiers d'un utilisateur (pour mkdir)."""
        return paths.user_dir(ws_path, safe_uid)

    @staticmethod
    def knowledge_json_path(ws_path: str) -> str:
        """Chemin du fichier JSON de cartographie sémantique (Bloc D)."""
        return paths.workspace_knowledge_file(ws_path)

    # ── Utilitaire de chargement partagé ────────────────────────────────────

    @staticmethod
    async def load_store(
        storage: StorageProtocol,
        faiss_path: str,
        meta_path: str,
        dimension: int = 1024,
    ) -> FaissStore | None:
        """Charge un FaissStore depuis le storage (retourne None si absent).

        Utilisé comme loader pour FaissIndexRegistry.get_or_load().
        Désérialisation via asyncio.to_thread pour ne pas bloquer la boucle.

        Args:
            storage: Backend de stockage (StorageProtocol).
            faiss_path: Chemin du fichier .faiss sur le storage.
            meta_path: Chemin du fichier .pkl sur le storage.
            dimension: Dimension des vecteurs (1024 pour Albert/BGE-M3).

        Returns:
            FaissStore chargé, ou None si les fichiers sont absents ou corrompus.
        """
        try:
            faiss_bytes = await storage.download(faiss_path)
            meta_bytes = await storage.download(meta_path)
            from colaig.rag.faiss_store import FaissStore
            store = FaissStore(dimension=dimension)
            await asyncio.to_thread(store.deserialize, faiss_bytes, meta_bytes)
            logger.debug(
                "colaig_index: store chargé %s (%d vecteurs)", faiss_path, store.count
            )
            return store
        except Exception as exc:
            logger.debug("colaig_index: store absent ou illisible %s — %s", faiss_path, exc)
            return None
