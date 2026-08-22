"""
Colaig — Service DocumentIndex (métadonnées de fichiers décentralisé)

Un index par workspace, stocké dans {workspace}/.colaig/indexes/documents/.
Responsabilités :
- Tracker les fichiers (path, size, checksum, etag, mime_type, last_modified)
- Analyser chaque document via Albert (résumé, catégorie, entités, mots-clés)
- Indexer l'embedding du résumé dans documents/index.faiss (1 vecteur par doc)
- Servir de registre de sync pour l'Indexer (checksum, indexed_at, status)
- Permettre la recherche vectorielle et les requêtes structurées

Implémente DocumentIndexProtocol.
Dépend de : FaissStore, EmbeddingServiceProtocol, LLMClientProtocol, StorageProtocol.

Structure sur le storage :
    {workspace}/.colaig/indexes/documents/
        index.faiss      ← vecteurs (1 par document, embedding du résumé IA)
        metadata.pkl     ← position FAISS → DocumentRecord
        registry.json    ← path → DocumentRecord (requêtes structurées)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from colaig.exceptions import DocumentAnalysisError
from colaig.models import (
    ColaigConfig,
    DocumentIndexSearchResult,
    DocumentRecord,
    DocumentStatus,
)
from colaig.rag.classifier import ClassificationEngine
from colaig.rag.faiss_store import FaissStore
from colaig.utils.text import extract_text, is_supported
from colaig import paths

logger = logging.getLogger(__name__)

# Dossier des index documents dans le workspace
_DOCUMENTS_SUBDIR = "documents"

# Troncature du texte envoyé à Albert pour l'analyse
_MAX_TEXT_FOR_ANALYSIS = 4000


@dataclass
class _IndexState:
    """État en mémoire d'un index DocumentIndex pour un workspace."""
    faiss_store: FaissStore
    # registry : path → DocumentRecord (permet les requêtes structurées)
    registry: dict[str, DocumentRecord]
    loaded_at: float       # timestamp monotonic (pour TTL)
    dirty: bool = False    # True si des modifications ne sont pas encore sauvegardées


class DocumentIndex:
    """Service de métadonnées de fichiers décentralisé.

    Un index FAISS + registry JSON par workspace, stocké dans le storage
    sous {workspace}/.colaig/indexes/documents/.

    Args:
        storage: Backend de stockage (StorageProtocol).
        embedding_service: Service d'embeddings (EmbeddingServiceProtocol).
        albert: Client Albert API (LLMClientProtocol).
        config: Configuration globale (optionnel).
        index_cache_ttl: Durée de vie du cache en mémoire (secondes).
    """

    def __init__(
        self,
        storage,
        embedding_service,
        albert,
        config: ColaigConfig | None = None,
        index_cache_ttl: int = 300,
    ) -> None:
        self._storage = storage
        self._embeddings = embedding_service
        self._albert = albert
        self._config = config
        self._cache_ttl = index_cache_ttl
        # Cache : workspace_path normalisé → _IndexState
        self._loaded: dict[str, _IndexState] = {}
        # Cache ClassificationEngine par workspace_path normalisé (lazy-loaded)
        self._classifiers: dict[str, ClassificationEngine] = {}

    # ── Interface publique ────────────────────────────────────────────

    async def scan_workspace(self, workspace_path: str) -> int:
        """Scanne le workspace et enregistre les fichiers nouveaux/modifiés.

        Compare les etags du storage avec le registry interne.
        - Nouveau fichier → DocumentRecord(status=PENDING)
        - Etag changé → status=PENDING, checksum/size/etag mis à jour
        - Fichier disparu → supprimé du registry

        Returns:
            Nombre de fichiers nouveaux ou modifiés.
        """
        state = await self._ensure_loaded(workspace_path)

        files = await self._storage.list_files(workspace_path, recursive=True)
        current_paths: set[str] = set()
        changed = 0

        for f in files:
            if f.is_directory:
                continue
            if not is_supported(f.name):
                continue
            # Ignorer les fichiers dans .colaig/
            if paths.is_instance_path(f.path):
                continue

            current_paths.add(f.path)
            existing = state.registry.get(f.path)

            if existing is None:
                # Nouveau fichier
                state.registry[f.path] = DocumentRecord(
                    path=f.path,
                    name=f.name,
                    size=f.size,
                    etag=f.etag,
                    mime_type=f.content_type,
                    last_modified=f.last_modified,
                    status=DocumentStatus.PENDING,
                    indexed_at=datetime.now(UTC),
                )
                changed += 1
                logger.debug("document_index nouveau fichier: %s", f.path)

            elif existing.etag != f.etag:
                # Fichier modifié
                existing.etag = f.etag
                existing.size = f.size
                existing.last_modified = f.last_modified
                existing.status = DocumentStatus.PENDING
                existing.error_message = ""
                changed += 1
                logger.debug("document_index fichier modifié: %s", f.path)

        # Supprimer les fichiers qui n'existent plus
        removed_paths = set(state.registry.keys()) - current_paths
        for path in removed_paths:
            record = state.registry.pop(path)
            # Marquer le vecteur comme supprimé dans FAISS (lazy delete)
            if record.faiss_doc_id >= 0:
                state.faiss_store._deleted.add(record.faiss_doc_id)
            logger.debug("document_index supprimé: %s", path)

        if changed or removed_paths:
            state.dirty = True
            logger.info(
                "scan_workspace %s : %d nouveaux/modifiés, %d supprimés",
                workspace_path, changed, len(removed_paths),
            )

        return changed

    async def analyze_pending(
        self,
        workspace_path: str,
        max_docs: int = 10,
    ) -> int:
        """Analyse IA des documents en statut PENDING.

        Pour chaque document : download → extract_text → Albert → embedding → FAISS.

        Args:
            max_docs: Nombre maximum de documents à analyser par appel.

        Returns:
            Nombre de documents analysés avec succès.
        """
        if self._config and not self._config.document_index_ai_analysis:
            return 0

        state = await self._ensure_loaded(workspace_path)

        pending = [
            r for r in state.registry.values()
            if r.status == DocumentStatus.PENDING
        ][:max_docs]

        analyzed = 0
        for record in pending:
            record.status = DocumentStatus.ANALYZING
            try:
                await self._analyze_document(record, state, workspace_path=workspace_path)
                analyzed += 1
                state.dirty = True
            except Exception as exc:
                record.status = DocumentStatus.ERROR
                record.error_message = str(exc)
                state.dirty = True
                logger.warning("erreur analyse %s : %s", record.path, exc)

        if analyzed:
            logger.info(
                "analyze_pending %s : %d/%d analysés",
                workspace_path, analyzed, len(pending),
            )

        return analyzed

    async def search(
        self,
        query: str,
        workspace_path: str,
        k: int = 5,
        filters: dict | None = None,
    ) -> list[DocumentIndexSearchResult]:
        """Recherche sémantique + filtres structurés.

        Args:
            query: Texte de recherche.
            workspace_path: Workspace cible.
            k: Nombre de résultats maximum.
            filters: Filtres optionnels, ex: {"ai_category": "guide", "status": "analyzed"}.

        Returns:
            List[DocumentIndexSearchResult] triés par score décroissant.
        """
        state = await self._ensure_loaded(workspace_path)

        if not query.strip():
            # Sans requête : liste filtrée
            docs = _apply_filters(list(state.registry.values()), filters)
            docs.sort(key=lambda r: r.analyzed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
            return [
                DocumentIndexSearchResult(record=r, score=0.0, rank=i)
                for i, r in enumerate(docs[:k])
            ]

        if state.faiss_store.count == 0:
            return []

        # Calculer l'embedding de la requête
        query_vec = await self._embeddings.embed_text(query)

        # Recherche FAISS — sur-échantillonner pour le post-filtrage
        raw_results = state.faiss_store.search(query_vec, k=k * 5)

        # Post-filtrage sur les métadonnées du registry
        results: list[DocumentIndexSearchResult] = []
        for sr in raw_results:
            # sr.chunk est ici un DocumentRecord (pas un DocumentChunk)
            record = sr.chunk  # type: ignore[assignment]
            if not isinstance(record, DocumentRecord):
                continue
            if filters and not _match_filters(record, filters):
                continue
            results.append(DocumentIndexSearchResult(
                record=record,
                score=sr.score,
                rank=len(results),
            ))
            if len(results) >= k:
                break

        return results

    async def list_documents(
        self,
        workspace_path: str,
        filters: dict | None = None,
        limit: int = 50,
    ) -> list[DocumentRecord]:
        """Liste les documents avec filtres optionnels.

        Filtres supportés : status, ai_category, ai_language,
                            name_contains, path_contains.
        Tri : analyzed_at décroissant, puis indexed_at.

        Returns:
            List[DocumentRecord].
        """
        state = await self._ensure_loaded(workspace_path)
        docs = _apply_filters(list(state.registry.values()), filters)
        docs.sort(
            key=lambda r: r.analyzed_at or r.indexed_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return docs[:limit]

    async def get_document(
        self,
        workspace_path: str,
        doc_path: str,
    ) -> DocumentRecord | None:
        """Récupère le DocumentRecord d'un fichier par son path exact.

        Returns:
            DocumentRecord ou None.
        """
        state = await self._ensure_loaded(workspace_path)
        return state.registry.get(doc_path)

    async def save(self, workspace_path: str) -> None:
        """Persiste index.faiss + metadata.pkl + registry.json sur le storage."""
        key = _norm(workspace_path)
        state = self._loaded.get(key)
        if state is None:
            return

        remote = _remote_dir(workspace_path)
        await self._storage.mkdir(remote)

        # Sérialiser et uploader le FaissStore
        index_bytes, meta_bytes = state.faiss_store.serialize()
        await self._storage.upload(f"{remote}/index.faiss", index_bytes)
        await self._storage.upload(f"{remote}/metadata.pkl", meta_bytes)

        # Sérialiser et uploader le registry JSON
        registry_data = {path: record.to_dict() for path, record in state.registry.items()}
        registry_bytes = json.dumps(registry_data, ensure_ascii=False, indent=2).encode("utf-8")
        await self._storage.upload(f"{remote}/registry.json", registry_bytes)

        state.dirty = False
        logger.info(
            "document_index sauvegardé: %s (%d docs, %d vecteurs)",
            workspace_path, len(state.registry), state.faiss_store.count,
        )

    async def inject_analyzed_record(
        self,
        workspace_path: str,
        record: DocumentRecord,
    ) -> None:
        """Injecte un DocumentRecord déjà analysé (ex : depuis MCP sampling).

        Bypass le pipeline Albert — les champs IA doivent être pré-remplis
        dans le record (ai_summary, ai_category, etc.).
        Calcule l'embedding du résumé et l'ajoute au FaissStore.

        Args:
            workspace_path: Workspace cible.
            record: DocumentRecord avec au minimum path, name, ai_summary.
        """
        state = await self._ensure_loaded(workspace_path)

        # Embedding du résumé (fallback sur le nom)
        text_to_embed = record.ai_summary or record.name
        embedding = await self._embeddings.embed_text(text_to_embed)

        # Supprimer l'ancien vecteur FAISS si le doc existait déjà
        existing = state.registry.get(record.path)
        if existing and existing.faiss_doc_id >= 0:
            state.faiss_store._deleted.add(existing.faiss_doc_id)

        # Ajouter le nouveau vecteur
        new_idx = state.faiss_store._index.ntotal
        state.faiss_store.add([embedding], [record])  # type: ignore[arg-type]
        record.faiss_doc_id = new_idx

        # Finaliser le statut
        record.status = DocumentStatus.ANALYZED
        if not record.analyzed_at:
            record.analyzed_at = datetime.now(UTC)

        state.registry[record.path] = record
        state.dirty = True

        logger.debug(
            "inject_analyzed_record %s → faiss_id=%d, catégorie=%s",
            record.name, record.faiss_doc_id, record.ai_category,
        )

    async def load(self, workspace_path: str) -> bool:
        """Charge l'index depuis le storage.

        Returns:
            True si chargé avec succès, False si inexistant.
        """
        remote = _remote_dir(workspace_path)
        faiss_path = f"{remote}/index.faiss"
        meta_path = f"{remote}/metadata.pkl"
        registry_path = f"{remote}/registry.json"

        try:
            index_bytes = await self._storage.download(faiss_path)
            meta_bytes = await self._storage.download(meta_path)
            registry_bytes = await self._storage.download(registry_path)
        except Exception:
            logger.debug("pas d'index document existant: %s", workspace_path)
            return False

        dimension = self._embeddings.dimension
        store = FaissStore(dimension=dimension)
        store.deserialize(index_bytes, meta_bytes)

        # Le FaissStore stocke des DocumentRecord (pas des DocumentChunk)
        # On les récupère depuis le registry JSON (source de vérité)
        registry_data = json.loads(registry_bytes.decode("utf-8"))
        registry = {path: DocumentRecord.from_dict(d) for path, d in registry_data.items()}

        # Reconstruire les métadonnées FAISS avec les DocumentRecord
        # (les métadonnées pickle contiennent déjà les DocumentRecord si sauvés par nous)
        # On s'assure que le store._metadata pointe vers les bons records
        for idx, meta_item in list(store._metadata.items()):
            if isinstance(meta_item, dict):
                # Ancienne version — convertir
                doc_path = meta_item.get("path", "")
                if doc_path in registry:
                    store._metadata[idx] = registry[doc_path]

        key = _norm(workspace_path)
        self._loaded[key] = _IndexState(
            faiss_store=store,
            registry=registry,
            loaded_at=time.monotonic(),
        )

        logger.info(
            "document_index chargé: %s (%d docs, %d vecteurs)",
            workspace_path, len(registry), store.count,
        )
        return True

    # ── Méthodes privées ─────────────────────────────────────────────

    async def _ensure_loaded(self, workspace_path: str) -> _IndexState:
        """Charge l'état si absent ou TTL expiré."""
        key = _norm(workspace_path)
        state = self._loaded.get(key)

        if state is not None:
            age = time.monotonic() - state.loaded_at
            if age < self._cache_ttl:
                return state

        # Tenter de charger depuis le storage
        loaded = await self.load(workspace_path)
        if not loaded:
            # Créer un état vide
            dimension = self._embeddings.dimension
            self._loaded[key] = _IndexState(
                faiss_store=FaissStore(dimension=dimension),
                registry={},
                loaded_at=time.monotonic(),
            )

        return self._loaded[key]

    async def _ensure_classifier(self, workspace_path: str) -> ClassificationEngine:
        """Retourne un ClassificationEngine chargé pour ce workspace (lazy-loading)."""
        key = _norm(workspace_path)
        if key not in self._classifiers:
            engine = ClassificationEngine(self._storage, workspace_path)
            await engine.load_rules()
            self._classifiers[key] = engine
        return self._classifiers[key]

    async def _load_workspace_profile(self, workspace_path: str):
        """Charge WorkspaceProfile depuis {workspace}/.colaig/profile/identity.yaml.

        Returns:
            WorkspaceProfile si le fichier existe, None sinon.
        """
        import yaml as _yaml

        from colaig.models import DocumentMapConfig, VocabularyConfig, WorkspaceProfile
        profile_path = paths.identity_file(workspace_path)
        try:
            raw = await self._storage.download(profile_path)
            data = _yaml.safe_load(raw.decode("utf-8"))
            doc_map_data = data.get("document_map", {})
            vocab_data = data.get("vocabulary", {})
            return WorkspaceProfile(
                name=data.get("name", ""),
                domain=data.get("domain", ""),
                sub_domain=data.get("sub_domain", ""),
                language=data.get("language", "fr"),
                tone=data.get("tone", "formel"),
                vocabulary=VocabularyConfig(
                    terms=vocab_data.get("terms", []),
                    abbreviations=vocab_data.get("abbreviations", {}),
                ),
                document_map=DocumentMapConfig(
                    categorization_taxonomy=doc_map_data.get("categorization_taxonomy", []),
                    entity_types_to_extract=doc_map_data.get("entity_types_to_extract", []),
                    metadata_enrichment_instructions=doc_map_data.get(
                        "metadata_enrichment_instructions", ""
                    ),
                ),
            )
        except Exception:
            return None

    async def _analyze_document(
        self,
        record: DocumentRecord,
        state: _IndexState,
        workspace_path: str = "",
    ) -> None:
        """Pipeline d'analyse IA d'un document.

        download → extract_text
        → ClassificationEngine.build_rules_context() (si workspace_path)
        → Albert (prompt rule-aware + profile-aware)
        → embedding du résumé → FaissStore.add
        → ClassificationEngine.classify()
        → record mis à jour (ai_*, virtual_path, rule_applied)
        """
        # 1. Télécharger le contenu
        content = await self._storage.download(record.path)
        if not content:
            raise DocumentAnalysisError(f"contenu vide: {record.path}")

        # 2. Mettre à jour le checksum
        record.checksum = _checksum(content)

        # 3. Extraire le texte
        text = extract_text(content, record.name)
        if not text.strip():
            record.ai_summary = f"[{record.name} — aucun texte extractible]"
            record.ai_category = "autre"
            record.ai_entities = {}
            record.status = DocumentStatus.ANALYZED
            record.analyzed_at = datetime.now(UTC)
            return

        # 4. Charger le classifieur et construire le contexte règles
        rules_context = ""
        extra_entity_vars: set = set()
        if workspace_path:
            try:
                classifier = await self._ensure_classifier(workspace_path)
                rules_context, extra_entity_vars = classifier.build_rules_context()
            except Exception as exc:
                logger.warning("Impossible de charger le classifieur pour %s : %s", workspace_path, exc)

        # 5. Charger le profil workspace (identity.yaml) si disponible
        workspace_profile = None
        if workspace_path:
            try:
                workspace_profile = await self._load_workspace_profile(workspace_path)
            except Exception:
                pass

        # 6. Analyse IA via Albert — prompt rule-aware + profile-aware
        analysis = await self._call_albert_analysis(
            text=text[:_MAX_TEXT_FOR_ANALYSIS],
            filename=record.name,
            rules_context=rules_context,
            extra_entity_vars=extra_entity_vars,
            workspace_profile=workspace_profile,
        )

        # 7. Peupler le DocumentRecord
        record.ai_summary = analysis.get("summary", "")
        record.ai_category = analysis.get("category", "")
        raw_entities = analysis.get("entities", {})
        record.ai_entities = raw_entities if isinstance(raw_entities, dict) else {}
        record.ai_keywords = analysis.get("keywords", [])
        record.ai_language = analysis.get("language", "fr")
        record.ai_doc_type = analysis.get("doc_type", "")
        record.virtual_path = analysis.get("virtual_path") or ""
        record.virtual_filename = analysis.get("virtual_filename") or ""

        # 8. Calculer l'embedding du résumé (fallback sur le début du texte)
        text_to_embed = record.ai_summary or text[:500]
        embedding = await self._embeddings.embed_text(text_to_embed)

        # 9. Supprimer l'ancien vecteur FAISS si présent
        if record.faiss_doc_id >= 0:
            state.faiss_store._deleted.add(record.faiss_doc_id)

        # 10. Ajouter le nouveau vecteur
        new_idx = state.faiss_store._index.ntotal
        state.faiss_store.add([embedding], [record])  # type: ignore[arg-type]
        record.faiss_doc_id = new_idx

        # 11. Appliquer ClassificationEngine (déterministe — confirme ou corrige suggestion IA)
        if workspace_path:
            try:
                classifier = await self._ensure_classifier(workspace_path)
                record = await classifier.classify(record)
            except Exception as exc:
                logger.warning("Classification échouée pour %s : %s", record.name, exc)

        # 12. Finaliser
        record.status = DocumentStatus.ANALYZED
        record.analyzed_at = datetime.now(UTC)

        logger.debug(
            "analysé %s → catégorie=%s, entities=%s, virtual_path=%s, règle=%s",
            record.name, record.ai_category,
            list((record.ai_entities or {}).keys()),
            record.virtual_path or "(aucune)",
            record.rule_applied or "(aucune)",
        )

    async def _call_albert_analysis(
        self,
        text: str,
        filename: str,
        rules_context: str = "",
        extra_entity_vars: set | None = None,
        workspace_profile=None,
    ) -> dict:
        """Appelle Albert pour analyser un document (version rule-aware + profile-aware).

        Fallback systématique : si la réponse n'est pas du JSON valide,
        retourne un dict minimal avec le début du texte comme résumé.
        """
        # ── Section profil workspace (taxonomie, vocabulaire) ─────────────
        profile_section = ""
        if workspace_profile is not None:
            dm = workspace_profile.document_map
            if dm.categorization_taxonomy:
                taxonomy = ", ".join(dm.categorization_taxonomy)
                profile_section += f"Catégories autorisées (utilise strictement l'une d'elles) : [{taxonomy}]\n"
            if dm.entity_types_to_extract:
                entity_types = ", ".join(dm.entity_types_to_extract)
                profile_section += f"Types d'entités à extraire : [{entity_types}]\n"
            if workspace_profile.vocabulary.terms:
                vocab = ", ".join(workspace_profile.vocabulary.terms[:20])
                profile_section += f"Vocabulaire métier : {vocab}\n"
            if dm.metadata_enrichment_instructions:
                profile_section += f"\n{dm.metadata_enrichment_instructions}\n"

        # ── Section règles ────────────────────────────────────────────────
        rules_section = ""
        if rules_context:
            rules_section = (
                "\nRÈGLES DE CLASSIFICATION DISPONIBLES :\n"
                f"{rules_context}\n\n"
                "IMPORTANT : Si le document correspond à une règle, utilise son template pour\n"
                "virtual_path et virtual_filename. Extrais les entités référencées dans les templates.\n"
                "Si aucune règle ne correspond, propose un classement logique.\n"
            )

        # ── Bloc entities dynamique ───────────────────────────────────────
        if workspace_profile and workspace_profile.document_map.entity_types_to_extract:
            base_types = workspace_profile.document_map.entity_types_to_extract
        else:
            base_types = ["supplier", "customer", "amount", "date", "location"]

        entity_lines = [
            f'    "{et}": "valeur si présente, sinon null"'
            for et in base_types
        ]
        if extra_entity_vars:
            for var in sorted(extra_entity_vars):
                if var not in base_types:
                    entity_lines.append(f'    "{var}": "valeur de {var} si présente, sinon null"')

        entities_block = ",\n".join(entity_lines)

        prompt = (
            f"{profile_section}"
            f"{rules_section}"
            f"Analyse ce document français. Retourne UNIQUEMENT un JSON valide (pas de texte autour) :\n\n"
            f'{{\n'
            f'  "summary": "2-3 phrases résumant le document",\n'
            f'  "category": "catégorie parmi : procédure|rapport|guide|formulaire|compte-rendu|circulaire|note|courrier|facture|contrat|autre",\n'
            f'  "entities": {{\n{entities_block}\n  }},\n'
            f'  "keywords": ["mot-clé1", "mot-clé2", "mot-clé3"],\n'
            f'  "language": "fr",\n'
            f'  "doc_type": "type précis en quelques mots",\n'
            f'  "virtual_path": "chemin de classement suggéré (ex: /Factures/EDF/2024/) ou null",\n'
            f'  "virtual_filename": "nom normalisé suggéré (ex: 2024-01_EDF_125€.pdf) ou null"\n'
            f'}}\n\n'
            f"Document ({filename}) :\n{text}"
        )

        try:
            response = await self._albert.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
            )
            return _parse_json_from_response(response)
        except Exception as exc:
            logger.warning("erreur appel Albert pour %s : %s", filename, exc)
            return {
                "summary": text[:200].strip(),
                "category": "autre",
                "entities": {},
                "keywords": [],
                "language": "fr",
                "doc_type": "",
                "virtual_path": None,
                "virtual_filename": None,
            }


# ── Helpers ───────────────────────────────────────────────────────────


def _norm(workspace_path: str) -> str:
    """Normalise un chemin workspace (supprime le slash final)."""
    return workspace_path.rstrip("/")


def _remote_dir(workspace_path: str) -> str:
    """Chemin du dossier documents dans le storage."""
    return paths.index_file(workspace_path, _DOCUMENTS_SUBDIR)


def _checksum(content: bytes) -> str:
    """SHA256 du contenu binaire."""
    return hashlib.sha256(content).hexdigest()


def _parse_json_from_response(response: str) -> dict:
    """Extrait et parse le JSON d'une réponse Albert (peut contenir du texte autour)."""
    # Tentative directe
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Chercher le premier { et le dernier }
    start = response.find("{")
    end = response.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(response[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Fallback : dict minimal
    return {
        "summary": response[:200].strip(),
        "category": "autre",
        "entities": [],
        "keywords": [],
        "language": "fr",
        "doc_type": "",
    }


def _match_filters(record: DocumentRecord, filters: dict) -> bool:
    """Vérifie qu'un DocumentRecord satisfait les filtres structurés.

    Filtres supportés :
        status                  : valeur exacte (str ou DocumentStatus)
        ai_category             : containment case-insensitive
        ai_language             : valeur exacte
        name_contains           : sous-chaîne dans record.name (case-insensitive)
        path_contains           : sous-chaîne dans record.path (case-insensitive)
        virtual_path_contains   : sous-chaîne dans record.virtual_path (case-insensitive)
        has_virtual_path        : True si virtual_path non vide
        rule_applied            : nom exact de règle ou "ai_suggestion"
        entity.{key}            : containment dans record.ai_entities[key]
    """
    for key, value in filters.items():
        if key == "status":
            target = value.value if isinstance(value, DocumentStatus) else value
            if record.status.value != target:
                return False
        elif key == "ai_category":
            if value.lower() not in (record.ai_category or "").lower():
                return False
        elif key == "ai_language":
            if record.ai_language != value:
                return False
        elif key == "name_contains":
            if value.lower() not in record.name.lower():
                return False
        elif key == "path_contains":
            if value.lower() not in record.path.lower():
                return False
        elif key == "virtual_path_contains":
            if value.lower() not in (record.virtual_path or "").lower():
                return False
        elif key == "has_virtual_path":
            if value and not record.virtual_path:
                return False
        elif key == "rule_applied":
            if record.rule_applied != value:
                return False
        elif key.startswith("entity."):
            entity_key = key[len("entity."):]
            actual = (record.ai_entities or {}).get(entity_key, "")
            if value.lower() not in str(actual).lower():
                return False
    return True


def _apply_filters(
    records: list[DocumentRecord],
    filters: dict | None,
) -> list[DocumentRecord]:
    """Applique les filtres sur une liste de DocumentRecord."""
    if not filters:
        return records
    return [r for r in records if _match_filters(r, filters)]
