"""
Colaig — ProfileService (Phase 6)

Charge et met en cache le profil déclaratif d'un workspace :
- identity.yaml → WorkspaceProfile
- behaviors/*.yaml → list[BehaviorConfig]
- behaviors.faiss → activation sémantique (si disponible)
"""

from __future__ import annotations

import logging

from colaig.models import (
    BehaviorActivationConfig,
    BehaviorAgentConfig,
    BehaviorConfig,
    BehaviorToolsConfig,
    DocumentMapConfig,
    VocabularyConfig,
    WorkspaceProfile,
)
from colaig.protocols import EmbeddingServiceProtocol, StorageProtocol

logger = logging.getLogger(__name__)


class ProfileService:
    """Charge et met en cache le profil déclaratif d'un workspace.

    - identity.yaml → WorkspaceProfile
    - behaviors/*.yaml → list[BehaviorConfig]
    - behaviors.faiss → activation sémantique (fallback si absent)

    Pas de cache persistant : rechargement à chaque appel (TTL géré par le caller si besoin).
    Les erreurs de chargement retournent des valeurs vides (comportement Phase 5 inchangé).

    Args:
        storage: Backend de stockage.
        embeddings: Service d'embeddings (pour behaviors.faiss).
    """

    def __init__(
        self,
        storage: StorageProtocol,
        embeddings: EmbeddingServiceProtocol | None = None,
        index_registry=None,   # FaissIndexRegistry — cache behaviors.faiss entre les messages
    ) -> None:
        self._storage = storage
        self._embeddings = embeddings
        self._index_registry = index_registry

    async def load_profile(self, workspace_path: str) -> WorkspaceProfile | None:
        """Charge le WorkspaceProfile depuis identity.yaml.

        Retourne None si identity.yaml absent (comportement Phase 5 inchangé).
        """
        try:
            import yaml
        except ImportError:
            logger.warning("pyyaml non installé, profil workspace non chargeable")
            return None

        path = f"{workspace_path}/.colaig/profile/identity.yaml"
        try:
            data = await self._storage.download(path)
            raw = yaml.safe_load(data.decode("utf-8")) or {}
            profile = self._parse_profile(raw)
            logger.debug("profil chargé depuis %s", path)
            return profile
        except Exception as e:
            logger.debug("identity.yaml absent ou invalide (%s): %s", path, e)
            return None

    async def load_behaviors(self, workspace_path: str) -> list[BehaviorConfig]:
        """Charge tous les behaviors/*.yaml.

        Retourne [] si le répertoire est absent ou vide.
        """
        try:
            import yaml
        except ImportError:
            return []

        dir_path = f"{workspace_path}/.colaig/profile/behaviors"
        try:
            files = await self._storage.list_files(dir_path)
        except Exception:
            return []

        behaviors: list[BehaviorConfig] = []
        for f in files:
            if not (f.name.endswith(".yaml") or f.name.endswith(".yml")):
                continue
            try:
                data = await self._storage.download(f.path)
                raw = yaml.safe_load(data.decode("utf-8")) or {}
                behaviors.append(self._parse_behavior(raw))
            except Exception as e:
                logger.warning("behavior %s ignoré (parse error): %s", f.name, e)

        logger.debug("%d behaviors chargés depuis %s", len(behaviors), dir_path)
        return behaviors

    async def resolve_behavior(
        self,
        message_embedding: list[float],
        behaviors: list[BehaviorConfig],
        workspace_path: str,
        active_behavior_name: str | None = None,
    ) -> tuple[BehaviorConfig | None, float]:
        """Résout le behavior actif par similarité sémantique.

        Priorité :
        1. Si active_behavior_name fourni (depuis trame), le réutilise directement.
        2. Si behaviors.faiss disponible, recherche par similarité sur les triggers.
        3. Fallback : retourne (None, 0.0).

        Args:
            message_embedding: Embedding du message utilisateur (déjà calculé).
            behaviors: Liste des behaviors chargés.
            workspace_path: Chemin du workspace (pour behaviors.faiss).
            active_behavior_name: Behavior déjà résolu dans la trame courante.

        Returns:
            Tuple (BehaviorConfig ou None, score cosinus 0→1).
        """
        if not behaviors:
            return None, 0.0

        # Réutiliser le behavior de la trame si déjà établi
        if active_behavior_name:
            for b in behaviors:
                if b.name == active_behavior_name:
                    logger.debug("behavior réutilisé depuis trame: %s", active_behavior_name)
                    return b, 1.0

        # Tentative avec behaviors.faiss — via registry si disponible (cache), sinon direct
        from colaig.rag.colaig_index import ColaigIndex
        faiss_path, meta_path = ColaigIndex.behaviors_paths(workspace_path)
        try:
            if self._index_registry is not None:
                key = ColaigIndex.behaviors_key(workspace_path)
                store = await self._index_registry.get_or_load(
                    key,
                    lambda: ColaigIndex.load_store(
                        self._storage, faiss_path, meta_path, len(message_embedding)
                    ),
                )
            else:
                store = await ColaigIndex.load_store(
                    self._storage, faiss_path, meta_path, len(message_embedding)
                )

            if store is None:
                raise FileNotFoundError("behaviors.faiss absent")

            results = store.search(message_embedding, k=5)

            if not results:
                return None, 0.0

            # Agréger par behavior_name (section field), prendre le meilleur score
            scores_by_name: dict[str, list[float]] = {}
            for r in results:
                name = getattr(r.chunk, "section", "")
                if name:
                    scores_by_name.setdefault(name, []).append(r.score)

            if not scores_by_name:
                return None, 0.0

            best_name = max(scores_by_name, key=lambda n: max(scores_by_name[n]))
            best_score = max(scores_by_name[best_name])

            for b in behaviors:
                if b.name == best_name:
                    if best_score >= b.activation.min_similarity:
                        logger.debug(
                            "behavior activé: %s (score=%.3f)", best_name, best_score
                        )
                        return b, best_score
                    else:
                        logger.debug(
                            "behavior %s sous le seuil (%.3f < %.3f)",
                            best_name, best_score, b.activation.min_similarity,
                        )

        except Exception as e:
            logger.debug("behaviors.faiss non disponible (%s)", e)

        return None, 0.0

    # -------------------------------------------------------------------------
    # Parsers YAML
    # -------------------------------------------------------------------------

    def _parse_profile(self, raw: dict) -> WorkspaceProfile:
        vocab_raw = raw.get("vocabulary", {})
        vocab = VocabularyConfig(
            terms=vocab_raw.get("terms", []),
            abbreviations=vocab_raw.get("abbreviations", {}),
        )
        docmap_raw = raw.get("document_map", {})
        doc_map = DocumentMapConfig(
            categorization_taxonomy=docmap_raw.get("categorization_taxonomy", []),
            entity_types_to_extract=docmap_raw.get("entity_types_to_extract", []),
            metadata_enrichment_instructions=docmap_raw.get(
                "metadata_enrichment_instructions", ""
            ),
        )
        return WorkspaceProfile(
            name=raw.get("name", ""),
            domain=raw.get("domain", ""),
            sub_domain=raw.get("sub_domain", ""),
            language=raw.get("language", "fr"),
            tone=raw.get("tone", "formel"),
            vocabulary=vocab,
            document_map=doc_map,
        )

    def _parse_behavior(self, raw: dict) -> BehaviorConfig:
        act_raw = raw.get("activation", {})
        activation = BehaviorActivationConfig(
            semantic_triggers=act_raw.get("semantic_triggers", []),
            intent_types=act_raw.get("intent_types", []),
            min_similarity=act_raw.get("min_similarity", 0.75),
        )
        agents: dict[str, BehaviorAgentConfig] = {}
        for agent_name, cfg in raw.get("agents", {}).items():
            agents[agent_name] = BehaviorAgentConfig(
                temperature=cfg.get("temperature"),
                format=cfg.get("format", ""),
                max_tokens=cfg.get("max_tokens"),
                tools_priority=cfg.get("tools_priority", []),
            )
        tools_raw = raw.get("tools", {})
        tools = BehaviorToolsConfig(disable=tools_raw.get("disable", []))
        return BehaviorConfig(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            activation=activation,
            agents=agents,
            skills_boost=raw.get("skills_boost", []),
            tools=tools,
            output=raw.get("output", {}),
        )
