"""
colaig/rag/skill_indexer.py — Indexation des skills workspace

Lit les fichiers .colaig/skills/*.md, embed chaque skill
(name + incipit 500 chars), et construit skills.faiss + skills.pkl
dans .colaig/indexes/.

Utilisé par PreExecutionBuilder._load_skills() pour le lazy-loading
sémantique : top-3 skills les plus proches de la question courante,
au lieu de charger tous les skills en bloc dans le prompt.

Architecture :
    SkillIndexer(storage, embeddings)
        └── index_workspace(workspace_path) → int (nombre de skills indexés)

Persistance : skills.faiss + skills.pkl dans .colaig/indexes/
Le champ DocumentChunk.section = nom du skill (clé de lookup de PreExecutionBuilder).
"""

from __future__ import annotations

import logging

from colaig import paths
from colaig.models import DocumentChunk
from colaig.rag.faiss_store import FaissStore

logger = logging.getLogger(__name__)

_SKILLS_DIR = "skills"
_FAISS_NAME = "skills.faiss"
_META_NAME = "skills.pkl"
_EMBED_MAX_CHARS = 500   # Incipit du skill pour l'embedding (name contextualise le reste)


class SkillIndexer:
    """Indexeur des skills déclaratifs workspace.

    Lit les skills/*.md et construit un index sémantique léger permettant
    à PreExecutionBuilder de sélectionner les top-k skills pertinents
    (par défaut 3) plutôt que de les charger tous en bloc dans le prompt.

    Args:
        storage: Backend de stockage (StorageProtocol).
        embeddings: Service d'embeddings (EmbeddingServiceProtocol).
    """

    def __init__(self, storage, embeddings) -> None:
        self._storage = storage
        self._embeddings = embeddings

    async def index_workspace(self, workspace_path: str) -> int:
        """Lit skills/*.md, les embed et sauvegarde skills.faiss.

        Reconstruit l'index complet à chaque appel (index petit, rapide).
        L'EmbeddingService met en cache les embeddings identiques → pas de
        re-appel Albert si le contenu n'a pas changé.

        Args:
            workspace_path: Chemin racine du workspace dans le storage.

        Returns:
            Nombre de skills indexés (0 si dossier absent ou vide).
        """
        ws = workspace_path.rstrip("/")
        skills_dir = paths.instance_subdir(ws, _SKILLS_DIR)

        try:
            files = await self._storage.list_files(skills_dir)
        except Exception:
            logger.debug("pas de dossier skills: %s", skills_dir)
            return 0

        md_files = [f for f in files if not f.is_directory and f.name.endswith(".md")]
        if not md_files:
            return 0

        texts: list[str] = []
        chunks: list[DocumentChunk] = []

        for f in md_files:
            try:
                raw = await self._storage.download(f.path)
                content = raw.decode("utf-8").strip()
            except Exception:
                logger.warning("skill illisible: %s", f.path)
                continue

            name = f.name.removesuffix(".md")
            # Texte à embedder : name + incipit du contenu pour le contexte sémantique
            embed_text = f"{name}. {content[:_EMBED_MAX_CHARS]}"

            texts.append(embed_text)
            chunks.append(DocumentChunk(
                text=embed_text,
                source_path=f.path,
                source_name=f.name,
                section=name,       # Clé de lookup utilisée par PreExecutionBuilder._load_skills()
                position=0,
                doc_type="markdown",
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
            "skills indexés: %d → %s/%s",
            len(chunks), indexes_dir, _FAISS_NAME,
        )
        return len(chunks)
