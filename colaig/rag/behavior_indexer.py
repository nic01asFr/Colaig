"""
colaig/rag/behavior_indexer.py — Indexation des comportements déclaratifs

Lit les fichiers .colaig/profile/behaviors/*.yaml, embed chaque behavior
(name + description + semantic_triggers), et construit behaviors.faiss +
behaviors.pkl dans .colaig/indexes/.

Utilisé par ProfileService.resolve_behavior() pour la résolution sémantique
au premier tour de conversation (avant que trame.active_behavior soit défini).

Architecture :
    BehaviorIndexer(storage, embeddings)
        └── index_workspace(workspace_path) → int (nombre de behaviors indexés)

Persistance : behaviors.faiss + behaviors.pkl dans .colaig/indexes/
Le champ DocumentChunk.section = nom du behavior (clé de lookup de ProfileService).
"""

from __future__ import annotations

import logging

import yaml

from colaig.models import DocumentChunk
from colaig.rag.faiss_store import FaissStore
from colaig import paths

logger = logging.getLogger(__name__)

_BEHAVIORS_DIR = "profile/behaviors"
_FAISS_NAME = "behaviors.faiss"
_META_NAME = "behaviors.pkl"


class BehaviorIndexer:
    """Indexeur des comportements déclaratifs workspace.

    Lit les behaviors/*.yaml et construit un index sémantique léger
    (typiquement < 20 vecteurs) permettant à ProfileService de résoudre
    le behavior le plus pertinent par similarité cosinus.

    Args:
        storage: Backend de stockage (StorageProtocol).
        embeddings: Service d'embeddings (EmbeddingServiceProtocol).
    """

    def __init__(self, storage, embeddings) -> None:
        self._storage = storage
        self._embeddings = embeddings

    async def index_workspace(self, workspace_path: str) -> int:
        """Lit behaviors/*.yaml, les embed et sauvegarde behaviors.faiss.

        Reconstruit l'index complet à chaque appel (index petit, rapide).
        L'EmbeddingService met en cache les embeddings identiques → pas de
        re-appel Albert si le contenu n'a pas changé.

        Args:
            workspace_path: Chemin racine du workspace dans le storage.

        Returns:
            Nombre de behaviors indexés (0 si dossier absent ou vide).
        """
        ws = workspace_path.rstrip("/")
        behaviors_dir = paths.instance_subdir(ws, _BEHAVIORS_DIR)

        try:
            files = await self._storage.list_files(behaviors_dir)
        except Exception:
            logger.debug("pas de dossier behaviors: %s", behaviors_dir)
            return 0

        yaml_files = [f for f in files if not f.is_directory and f.name.endswith(".yaml")]
        if not yaml_files:
            return 0

        texts: list[str] = []
        chunks: list[DocumentChunk] = []

        for f in yaml_files:
            try:
                raw = await self._storage.download(f.path)
                data = yaml.safe_load(raw.decode("utf-8")) or {}
            except Exception:
                logger.warning("behavior illisible: %s", f.path)
                continue

            name = data.get("name", f.name.removesuffix(".yaml"))
            description = data.get("description", "")
            activation = data.get("activation", {})
            triggers: list[str] = activation.get("semantic_triggers", [])

            # Texte à embedder : name + description + triggers sémantiques
            parts = [name]
            if description:
                parts.append(description)
            if triggers:
                parts.extend(triggers)
            embed_text = ". ".join(parts)

            texts.append(embed_text)
            chunks.append(DocumentChunk(
                text=embed_text,
                source_path=f.path,
                source_name=f.name,
                section=name,       # Clé de lookup utilisée par ProfileService.resolve_behavior()
                position=0,
                doc_type="yaml",
            ))

        if not texts:
            return 0

        embeddings = await self._embeddings.embed_texts(texts)

        dim = len(embeddings[0]) if embeddings else 1024
        store = FaissStore(dimension=dim)
        store.add(embeddings, chunks)

        # Persistance sur le storage
        index_bytes, meta_bytes = store.serialize()
        indexes_dir = paths.indexes_dir(ws)
        await self._storage.mkdir(indexes_dir)
        await self._storage.upload(paths.index_file(ws, _FAISS_NAME), index_bytes)
        await self._storage.upload(paths.index_file(ws, _META_NAME), meta_bytes)

        logger.info(
            "behaviors indexés: %d → %s/%s",
            len(chunks), indexes_dir, _FAISS_NAME,
        )
        return len(chunks)
