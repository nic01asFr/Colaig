"""
Colaig — Répertoire vectoriel des workspaces

Index FAISS des identités de workspaces (name + description + topics).
Permet le routage sémantique cross-workspace : trouver quel(s) workspace(s)
interroger sans connaître les IDs a priori.

Périmètre :
- Workspaces locaux visibles (visibility="federation"|"public")
- Workspaces distants découverts via FederationService (peers.yaml + mcp_connectors)

Chaque entrée dans le FAISS encode :
  source_path  = workspace_id
  source_name  = workspace_name
  text         = identity text (name + description + topics)
  section      = mcp_url du peer si distant, "" si local
  source_hash  = auth_token du peer si distant, "" si local
  doc_type     = "workspace_local" | "workspace_remote"

Persistance : /.colaig/federation/workspaces.faiss + .pkl (rebuild à chaque démarrage
ou lorsque les workspaces/peers changent — lecture seule du FAISS existant au démarrage).

Usage :
    directory = WorkspaceDirectory(storage, embedding_service, index_registry)

    # Construction avec fédération (après initial_indexation)
    await directory.load_or_build(all_workspaces, federation_service=fed_service)

    # Recherche sémantique — retourne local + distant
    candidates = await directory.search("procédures marchés publics", k=3)
    # → [WorkspaceDirectoryEntry(workspace_id="espace-juridique", mcp_url="", score=0.91), ...]
    # → [WorkspaceDirectoryEntry(workspace_id="rh-distant", mcp_url="https://peer.fr/mcp", score=0.87)]

    # Lookup par workspace_id (pour run_rag_delegate niveau 4)
    entry = directory.find_by_id("rh-distant")
    if entry and entry.mcp_url:
        # Routage vers le peer via MCP
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from colaig.models import DocumentChunk
from colaig.rag.colaig_index import ColaigIndex
from colaig.rag.faiss_store import FaissStore

logger = logging.getLogger(__name__)

_FAISS_PATH = ColaigIndex.FEDERATION_FAISS_PATH
_META_PATH = ColaigIndex.FEDERATION_META_PATH
_REGISTRY_KEY = ColaigIndex.FEDERATION_KEY


@dataclass
class WorkspaceDirectoryEntry:
    """Résultat d'une recherche dans le répertoire vectoriel des workspaces."""
    workspace_id: str
    workspace_name: str
    description: str
    score: float
    mcp_url: str = ""       # Non vide si workspace distant (peer)
    auth_token: str = ""    # Token Bearer pour le peer (si requis)
    source: str = "local"   # "local" | "remote"


class WorkspaceDirectory:
    """Répertoire vectoriel des workspaces fédérés (locaux + distants).

    Index FAISS léger (un vecteur par workspace visible) pour le routage
    sémantique cross-workspace. Agrège workspaces locaux et distants.
    Persiste dans /.colaig/federation/ du storage.

    Args:
        storage: Backend de stockage (StorageProtocol).
        embedding_service: Service d'embeddings (EmbeddingService).
        registry: FaissIndexRegistry partagé — clé "federation::workspaces".
    """

    def __init__(self, storage, embedding_service, registry) -> None:
        self._storage = storage
        self._embeddings = embedding_service
        self._registry = registry
        # Lookup rapide workspace_id → entry (sans passer par FAISS)
        self._id_index: dict[str, WorkspaceDirectoryEntry] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Helpers

    def _identity_text(self, name: str, description: str = "", topics: list = ()) -> str:
        """Texte représentatif pour l'embedding d'identité d'un workspace."""
        parts = [name]
        if description:
            parts.append(description)
        if topics:
            parts.extend(topics)
        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────────────
    # Construction

    async def build(
        self,
        workspaces: list,
        federation_service=None,
    ) -> None:
        """Construit ou reconstruit l'index (workspaces locaux + distants).

        Workspaces locaux indexés : visibility="federation" | "public" uniquement.
        Workspaces distants : tous ceux retournés par FederationService.list_remote_workspaces().

        Args:
            workspaces: Liste complète des WorkspaceConfig locaux.
            federation_service: FederationService optionnel — si fourni,
                agrège aussi les workspaces distants des peers déclarés.
        """
        chunks: list[DocumentChunk] = []
        texts: list[str] = []
        entries_meta: list[WorkspaceDirectoryEntry] = []

        # ── Workspaces locaux visibles ─────────────────────────────────────
        for ws in workspaces:
            if getattr(ws, "visibility", "private") not in ("federation", "public"):
                continue
            if not ws.workspace_id or not ws.name:
                continue
            text = self._identity_text(
                ws.name, ws.description, getattr(ws, "topics", [])
            )
            texts.append(text)
            chunks.append(DocumentChunk(
                text=text,
                source_path=ws.workspace_id,
                source_name=ws.name,
                section="",          # mcp_url vide → local
                source_hash="",      # auth_token vide → local
                doc_type="workspace_local",
            ))
            entries_meta.append(WorkspaceDirectoryEntry(
                workspace_id=ws.workspace_id,
                workspace_name=ws.name,
                description=ws.description,
                score=0.0,
                mcp_url="",
                auth_token="",
                source="local",
            ))

        # ── Workspaces distants via FederationService ──────────────────────
        if federation_service is not None:
            try:
                all_connectors = [
                    c for ws in workspaces
                    for c in getattr(ws, "mcp_connectors", [])
                ]
                peers = await federation_service.load_peers(all_connectors)
                remote_workspaces = await federation_service.list_remote_workspaces(peers)
                for rws in remote_workspaces:
                    if not rws.workspace_id or not rws.name or not rws.rag_enabled:
                        continue
                    text = self._identity_text(rws.name, rws.description, rws.topics)
                    texts.append(text)
                    chunks.append(DocumentChunk(
                        text=text,
                        source_path=rws.workspace_id,
                        source_name=rws.name,
                        section=rws.peer.url,          # mcp_url du peer
                        source_hash=rws.peer.auth_token,  # auth_token du peer
                        doc_type="workspace_remote",
                    ))
                    entries_meta.append(WorkspaceDirectoryEntry(
                        workspace_id=rws.workspace_id,
                        workspace_name=rws.name,
                        description=rws.description,
                        score=0.0,
                        mcp_url=rws.peer.url,
                        auth_token=rws.peer.auth_token,
                        source="remote",
                    ))
            except Exception:
                logger.exception("workspace_directory: erreur federation — workspaces distants ignorés")

        if not texts:
            logger.debug("workspace_directory: aucun workspace à indexer")
            return

        # ── Embedding batch ────────────────────────────────────────────────
        try:
            embeddings = await self._embeddings.embed_texts(texts)
        except Exception:
            logger.exception("workspace_directory: erreur embedding — build abandonné")
            return

        # ── Construction du FaissStore ─────────────────────────────────────
        store = FaissStore(dimension=self._embeddings.dimension)
        store.add(embeddings, chunks)

        # ── Persistance ────────────────────────────────────────────────────
        try:
            index_bytes, meta_bytes = await asyncio.to_thread(store.serialize)
            await self._storage.upload(_FAISS_PATH, index_bytes)
            await self._storage.upload(_META_PATH, meta_bytes)
        except Exception:
            logger.warning("workspace_directory: persistance échouée (index gardé en mémoire)")

        # ── Registre en mémoire ────────────────────────────────────────────
        self._registry.set(_REGISTRY_KEY, store)
        self._id_index = {e.workspace_id: e for e in entries_meta}

        n_local = sum(1 for e in entries_meta if e.source == "local")
        n_remote = sum(1 for e in entries_meta if e.source == "remote")
        logger.debug(
            "workspace_directory: %d local + %d distant(s) indexés",
            n_local, n_remote,
        )

    async def load_or_build(
        self,
        workspaces: list,
        federation_service=None,
    ) -> None:
        """Charge depuis le storage, ou reconstruit si absent/invalide.

        Après chargement du FAISS, reconstruit toujours l'id_index depuis les chunks
        pour que find_by_id() fonctionne.

        Args:
            workspaces: Liste complète des WorkspaceConfig locaux.
            federation_service: FederationService optionnel pour les workspaces distants.
        """
        loaded = False
        try:
            faiss_bytes = await self._storage.download(_FAISS_PATH)
            meta_bytes = await self._storage.download(_META_PATH)
            store = FaissStore(dimension=self._embeddings.dimension)
            await asyncio.to_thread(store.deserialize, faiss_bytes, meta_bytes)
            self._registry.set(_REGISTRY_KEY, store)
            # Reconstruire id_index depuis les chunks du store chargé
            self._id_index = {
                chunk.source_path: WorkspaceDirectoryEntry(
                    workspace_id=chunk.source_path,
                    workspace_name=chunk.source_name or chunk.source_path,
                    description=chunk.text,
                    score=0.0,
                    mcp_url=chunk.section if (chunk.section and chunk.section.startswith("http")) else "",
                    auth_token=chunk.source_hash,
                    source="remote" if chunk.doc_type == "workspace_remote" else "local",
                )
                for chunk in store.get_all_active_chunks()
                if chunk.source_path
            }
            loaded = True
            logger.debug(
                "workspace_directory: index chargé (%d workspaces)",
                store.count,
            )
        except Exception:
            pass

        if not loaded:
            logger.debug("workspace_directory: index absent — reconstruction")
            await self.build(workspaces, federation_service=federation_service)

    # ──────────────────────────────────────────────────────────────────────
    # Recherche

    async def search(
        self,
        query: str,
        k: int = 5,
    ) -> list[WorkspaceDirectoryEntry]:
        """Trouve les workspaces les plus pertinents pour une requête sémantique.

        Retourne les entrées locales ET distantes, triées par pertinence.
        Les entrées distantes portent mcp_url non vide — run_rag_delegate
        les route via le peer correspondant (niveau 4).

        Args:
            query: Requête en langage naturel.
            k: Nombre maximum de résultats.

        Returns:
            Liste triée par score décroissant. Retourne [] si index absent.
        """
        store = self._registry.get(_REGISTRY_KEY)
        if store is None:
            return []

        try:
            query_embedding = await self._embeddings.embed_text(query)
            results = await store.search_async(query_embedding, k)
        except Exception:
            logger.warning("workspace_directory: erreur recherche")
            return []

        output = []
        for r in results:
            if not r.chunk.source_path:
                continue
            # Enrichir depuis id_index si disponible (mcp_url, auth_token)
            cached = self._id_index.get(r.chunk.source_path)
            output.append(WorkspaceDirectoryEntry(
                workspace_id=r.chunk.source_path,
                workspace_name=r.chunk.source_name or r.chunk.source_path,
                description=r.chunk.text,
                score=round(r.score, 3),
                mcp_url=cached.mcp_url if cached else (
                    r.chunk.section if (r.chunk.section and r.chunk.section.startswith("http")) else ""
                ),
                auth_token=cached.auth_token if cached else r.chunk.source_hash,
                source=cached.source if cached else (
                    "remote" if r.chunk.doc_type == "workspace_remote" else "local"
                ),
            ))
        return output

    def find_by_id(self, workspace_id: str) -> Optional[WorkspaceDirectoryEntry]:
        """Retourne l'entrée d'un workspace par son ID (lookup O(1)).

        Utilisé par run_rag_delegate niveau 4 pour résoudre un workspace_id
        dont le mcp_url est connu via le répertoire.

        Args:
            workspace_id: Identifiant du workspace à chercher.

        Returns:
            WorkspaceDirectoryEntry ou None si absent du répertoire.
        """
        return self._id_index.get(workspace_id)

    def is_loaded(self) -> bool:
        """Retourne True si l'index est en mémoire."""
        return self._registry.get(_REGISTRY_KEY) is not None
