"""
Colaig — Orchestration d'indexation

Orchestre : scan storage → compare etags → extract text → chunk → (contextualise) → embed → FAISS add → (BM25 add) → save.
Gestion incrémentale : ne ré-indexe que les documents modifiés/nouveaux.

Extensions hybride :
- contextualizer : génère des préfixes LLM par chunk (Contextual Retrieval)
- bm25_store : maintient un index BM25 en parallèle du FAISS
"""

from __future__ import annotations

import json
import logging

from colaig import paths
from colaig.integrations.llm.capabilities import motif_absence, supporte
from colaig.models import ColaigConfig
from colaig.utils.text import extract_text, is_indexable, needs_ocr

logger = logging.getLogger(__name__)


class Indexer:
    """Orchestrateur d'indexation des documents d'un workspace.

    Args:
        storage: Backend de stockage (StorageProtocol).
        chunker: Service de chunking (ChunkerProtocol).
        embeddings: Service d'embeddings (EmbeddingServiceProtocol).
        store: Index vectoriel FAISS (VectorStoreProtocol).
        config: Configuration globale.
        albert_client: Client LLM optionnel (OCR des PDFs scannés).
        bm25_store: Index BM25 optionnel (hybrid search).
        contextualizer: ChunkContextualizer optionnel (Contextual Retrieval).
        workspace_name: Nom du workspace (pour la contextualisation).
        workspace_description: Description du workspace (pour la contextualisation).
        workspace_system_prompt: System prompt du workspace (pour la contextualisation).
    """

    def __init__(
        self,
        storage,       # StorageProtocol
        chunker,       # ChunkerProtocol
        embeddings,    # EmbeddingServiceProtocol
        store,         # VectorStoreProtocol (FaissStore)
        config: ColaigConfig | None = None,
        albert_client=None,    # AlbertClient | None — pour OCR des PDFs scannés
        bm25_store=None,       # BM25Store | None — index lexical hybride
        contextualizer=None,   # ChunkContextualizer | None — préfixes contextuels
        workspace_name: str = "",
        workspace_description: str = "",
        workspace_system_prompt: str = "",
        workspace_vocabulary: list[str] | None = None,
    ) -> None:
        self._storage = storage
        self._chunker = chunker
        self._embeddings = embeddings
        self._store = store
        self._albert_client = albert_client
        self._bm25_store = bm25_store
        self._contextualizer = contextualizer
        self._workspace_name = workspace_name
        self._workspace_description = workspace_description
        self._workspace_system_prompt = workspace_system_prompt
        self._workspace_vocabulary = workspace_vocabulary or []
        # Tracking des etags : source_path → etag
        self._known_etags: dict[str, str] = {}
        # Documents rencontrés mais non indexés, avec le motif. Un document à zéro
        # chunk était jusqu'ici invisible : `logger.debug` puis `return False`. Ni
        # l'exploitant ni l'utilisateur ne savait qu'une partie du corpus n'était pas
        # interrogeable — silencieusement absent est le pire état possible.
        self._ignores: dict[str, str] = {}

    async def index_workspace(self, workspace_path: str) -> int:
        """Indexe tous les documents supportés d'un workspace.

        Args:
            workspace_path: Chemin dans le storage (ex: /espace-test/).

        Returns:
            Nombre de documents indexés.
        """
        files = await self._storage.list_files(workspace_path, recursive=True)
        count = 0

        for f in files:
            if f.is_directory:
                continue
            if not is_indexable(f.name):
                continue
            # Ignorer les fichiers dans .colaig/
            if paths.is_instance_path(f.path):
                continue

            try:
                indexed = await self.index_document(f.path, f.etag)
                if indexed:
                    count += 1
            except Exception:
                logger.exception("erreur indexation %s", f.path)

        logger.info("workspace %s : %d documents indexés", workspace_path, count)
        return count

    async def index_document(self, doc_path: str, etag: str = "") -> bool:
        """Indexe un document unique.

        Args:
            doc_path: Chemin du document dans le storage.
            etag: Etag actuel du document.

        Returns:
            True si le document a été indexé (nouveau ou modifié).
        """
        # Vérifier si déjà indexé avec le même etag
        if etag and self._known_etags.get(doc_path) == etag:
            return False

        # Télécharger le document
        content = await self._storage.download(doc_path)
        if not content:
            return False

        filename = doc_path.rstrip("/").split("/")[-1]

        # Extraire le texte. Extraction native d'abord — pymupdf pour les PDF :
        # gratuite, instantanée. L'OCR n'intervient qu'en second, si elle ne rend rien.
        text = extract_text(content, filename)

        # OCR pour tout ce dont on ne tire rien nativement : PDF scanné **et images**.
        # La condition portait sur `.pdf` seul, donc une image n'y arrivait jamais —
        # et de toute façon `is_supported()` l'avait déjà écartée en amont (L1.3b).
        ocr_utile = needs_ocr(filename) or filename.lower().endswith(".pdf")
        ocr_disponible = supporte(self._albert_client, "ocr")
        if not text.strip() and ocr_utile and ocr_disponible:
            logger.info("aucun texte natif, tentative OCR : %s", doc_path)
            try:
                text = await self._albert_client.ocr(content, filename)
                if text.strip():
                    logger.info("OCR réussi pour %s (%d caractères)", doc_path, len(text))
                else:
                    self._signaler_ignore(doc_path, "OCR sans résultat")
            except Exception:
                logger.warning("OCR échoué pour %s", doc_path, exc_info=True)
                self._signaler_ignore(doc_path, "OCR en échec")

        if not text.strip():
            # Ne pas écraser un motif déjà posé par la branche OCR : « OCR en échec »
            # dit où chercher, « aucun texte extrait » enverrait examiner le document.
            if doc_path not in self._ignores:
                if ocr_utile and not ocr_disponible:
                    # `motif_absence` distingue « aucun client configuré » de
                    # « ce backend n'a pas d'OCR ». Avant, les deux cas finissaient
                    # en AttributeError rapporté comme « OCR en échec », ce qui
                    # envoyait chercher du côté du modèle.
                    self._signaler_ignore(
                        doc_path,
                        "document sans texte natif — "
                        + motif_absence(self._albert_client, "ocr"),
                    )
                else:
                    self._signaler_ignore(doc_path, "aucun texte extrait")
            return False

        # Déterminer le type de document
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "text"

        # Supprimer l'ancienne version si elle existe
        self._store.delete_by_source(doc_path)
        if self._bm25_store is not None:
            self._bm25_store.delete_by_source(doc_path)

        # Chunker
        chunks = self._chunker.chunk_document(
            content=text,
            source_path=doc_path,
            doc_type=ext,
        )
        if not chunks:
            # Du texte a été extrait, mais rien n'en subsiste après découpage — un
            # document sous le seuil de 50 caractères, par exemple. Cas rare, mais
            # aussi invisible que les autres jusqu'ici.
            self._signaler_ignore(doc_path, "aucun chunk après découpage")
            return False

        # Contextualisation (Contextual Retrieval) — enrichit les chunks avec un préfixe LLM
        if self._contextualizer is not None:
            try:
                chunks = await self._contextualizer.enrich_batch(
                    chunks,
                    workspace_name=self._workspace_name,
                    workspace_description=self._workspace_description,
                    workspace_system_prompt=self._workspace_system_prompt,
                    workspace_vocabulary=self._workspace_vocabulary or None,
                )
            except Exception:
                logger.warning("contextualizer: erreur batch, fallback chunks originaux", exc_info=True)

        # Embeddings — utilise le texte enrichi si contextual_prefix présent
        texts = [
            f"{c.contextual_prefix}\n\n{c.text}" if c.contextual_prefix else c.text
            for c in chunks
        ]
        embeddings = await self._embeddings.embed_texts(texts)

        # Ajouter dans FAISS
        self._store.add(embeddings, chunks)

        # Ajouter dans BM25 (hybrid search)
        if self._bm25_store is not None:
            self._bm25_store.add(chunks)

        # Mémoriser l'etag
        if etag:
            self._known_etags[doc_path] = etag

        # Le document est indexé : s'il figurait parmi les ignorés d'une passe
        # précédente, il n'y a plus sa place. Sans cela un document réparé — OCR
        # devenu disponible, fichier remplacé — resterait signalé indéfiniment, et
        # le signalement perdrait sa valeur.
        self._ignores.pop(doc_path, None)

        logger.debug("indexé %s → %d chunks", doc_path, len(chunks))
        return True

    async def handle_event(self, event: StorageEvent) -> bool:
        """Traite un StorageEvent — point d'entrée unique pour tout changement de fichier.

        Utilisé par check_updates() (polling) et, à terme, par tout provider
        émettant des événements natifs (SSE, inotify, delta query...).

        Args:
            event: StorageEvent avec type "file.created", "file.modified" ou "file.deleted".

        Returns:
            True si l'index a été modifié.
        """
        if event.type in ("file.created", "file.modified"):
            if not is_indexable(event.path.split("/")[-1]):
                return False
            if paths.is_instance_path(event.path):
                return False
            etag = event.metadata.get("etag", "")
            return await self.index_document(event.path, etag)
        elif event.type == "file.deleted":
            self._store.delete_by_source(event.path)
            if self._bm25_store is not None:
                self._bm25_store.delete_by_source(event.path)
            self._known_etags.pop(event.path, None)
            logger.debug("supprimé de l'index via event: %s", event.path)
            return True
        return False

    async def check_updates(self, workspace_path: str) -> UpdateSummary:
        """Compare les etags et ré-indexe les documents modifiés.

        Produit des StorageEvent pour chaque changement détecté et les traite
        via handle_event() — même chemin que les providers avec events natifs.

        Returns:
            UpdateSummary rétrocompatible avec les comparaisons entières.
        """
        from colaig.models import StorageEvent, UpdateSummary
        files = await self._storage.list_files(workspace_path, recursive=True)
        events: list[StorageEvent] = []
        current_paths: set[str] = set()

        for f in files:
            if f.is_directory or not is_indexable(f.name):
                continue
            if paths.is_instance_path(f.path):
                continue
            current_paths.add(f.path)
            if self._known_etags.get(f.path) == f.etag:
                continue
            event_type = "file.created" if f.path not in self._known_etags else "file.modified"
            event = StorageEvent(type=event_type, path=f.path, metadata={"etag": f.etag})
            try:
                if await self.handle_event(event):
                    events.append(event)
            except Exception:
                logger.exception("erreur ré-indexation %s", f.path)

        # Suppressions : calculé avant la boucle de suppression pour stabilité du dict
        removed_paths = set(self._known_etags.keys()) - current_paths
        for path in removed_paths:
            event = StorageEvent(type="file.deleted", path=path)
            await self.handle_event(event)
            events.append(event)

        count = sum(1 for e in events if e.type != "file.deleted")
        if events:
            logger.info(
                "workspace %s : %d mis à jour, %d supprimés",
                workspace_path, count, len(removed_paths),
            )

        return UpdateSummary(
            count=count,
            changed_paths=[e.path for e in events if e.type != "file.deleted"],
            removed_paths=removed_paths,
            events=events,
        )

    async def save_to_storage(self, remote_path: str) -> None:
        """Sauvegarde l'index sérialisé sur le storage.

        Args:
            remote_path: Chemin de destination (ex: /espace/.colaig/indexes/).
        """
        index_bytes, meta_bytes = self._store.serialize()
        etags_bytes = json.dumps(self._known_etags, ensure_ascii=False).encode("utf-8")
        await self._storage.mkdir(remote_path)
        await self._storage.upload(f"{remote_path.rstrip('/')}/index.faiss", index_bytes)
        await self._storage.upload(f"{remote_path.rstrip('/')}/metadata.pkl", meta_bytes)
        await self._storage.upload(f"{remote_path.rstrip('/')}/etags.json", etags_bytes)

        # Sauvegarder l'index BM25 si présent
        if self._bm25_store is not None:
            bm25_bytes = self._bm25_store.serialize()
            await self._storage.upload(f"{remote_path.rstrip('/')}/bm25.pkl", bm25_bytes)

        logger.info("index sauvegardé sur le storage: %s", remote_path)

    # Alias de rétrocompatibilité
    save_to_webdav = save_to_storage

    async def load_from_storage(self, remote_path: str) -> bool:
        """Charge l'index depuis le storage.

        Returns:
            True si l'index a été chargé, False si inexistant.
        """
        faiss_path = f"{remote_path.rstrip('/')}/index.faiss"
        meta_path = f"{remote_path.rstrip('/')}/metadata.pkl"
        etags_path = f"{remote_path.rstrip('/')}/etags.json"

        try:
            index_bytes = await self._storage.download(faiss_path)
            meta_bytes = await self._storage.download(meta_path)
        except Exception:
            logger.debug("pas d'index existant sur le storage: %s", remote_path)
            return False

        self._store.deserialize(index_bytes, meta_bytes)

        # Restaurer les etags
        try:
            etags_bytes = await self._storage.download(etags_path)
            self._known_etags = json.loads(etags_bytes.decode("utf-8"))
            logger.debug("etags restaurés: %d documents", len(self._known_etags))
        except Exception:
            logger.debug("pas d'etags.json sur le storage (première fois)")

        # Restaurer l'index BM25 si présent
        if self._bm25_store is not None:
            bm25_path = f"{remote_path.rstrip('/')}/bm25.pkl"
            try:
                bm25_bytes = await self._storage.download(bm25_path)
                self._bm25_store.deserialize(bm25_bytes)
                logger.debug("bm25 restauré: %d chunks", self._bm25_store.count)
            except Exception:
                logger.debug("pas de bm25.pkl sur le storage (première indexation)")

        logger.info("index chargé depuis le storage: %s (%d vecteurs)", remote_path, self._store.count)
        return True

    # Alias de rétrocompatibilité
    load_from_webdav = load_from_storage

    def reset(self) -> None:
        """Réinitialise l'index en mémoire (etags + FAISS + BM25).

        Utile avant une ré-indexation forcée : les etags sont effacés,
        forçant `index_workspace` à re-traiter tous les documents.
        """
        self._known_etags = {}
        self._store.reset()
        if self._bm25_store is not None:
            self._bm25_store.reset()
        logger.info("indexer: reset complet (etags + FAISS + BM25 vidés)")

    def _signaler_ignore(self, doc_path: str, motif: str) -> None:
        """Enregistre et **journalise** un document non indexé.

        Niveau `warning` délibérément : un document présent dans l'espace mais absent
        de l'index est une anomalie de couverture, pas un détail de mise au point.
        Mesuré sur un corpus réel de 59 documents, 8 étaient dans ce cas — 29 % du
        poids — sans que rien ne l'indique.
        """
        self._ignores[doc_path] = motif
        logger.warning("document non indexé (%s) : %s", motif, doc_path)

    @property
    def documents_ignores(self) -> dict[str, str]:
        """Chemin → motif, pour les documents rencontrés mais non indexés."""
        return dict(self._ignores)

    def get_status(self) -> dict:
        """Retourne l'état courant de l'index.

        Returns:
            Dict avec vector_count, document_count et bm25_count si applicable.
        """
        status = {
            "vector_count": self._store.count,
            "document_count": len(self._known_etags),
            # Couverture : ce qui manque compte autant que ce qui est là.
            "ignored_count": len(self._ignores),
            "ignored_documents": dict(self._ignores),
        }
        if self._bm25_store is not None:
            status["bm25_count"] = self._bm25_store.count
        return status
