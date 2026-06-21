"""
context/user_memory.py — Mémoire sémantique per-user.

Trois rythmes :
  1. Lecture temps réel   : query FAISS via FaissIndexRegistry (non-bloquant)
  2. Écriture fire-and-forget : extraction LLM (Ministral-3B) + embed + store
     → asyncio.create_task(), ne bloque jamais la réponse
  3. Consolidation background  : loop ~1h dans main.py (REFINE_PROFILE, MAP)

Structure de stockage — toujours workspace-bound :
  {workspace_path}/.colaig/users/{safe_user_id}/memory.faiss + .pkl + profile.json

En mode DM, workspace_path est le workspace personnel de l'utilisateur
(/.colaig/personal/{safe_user_id}/), créé automatiquement par le resolver.
La mémoire est donc toujours isolée par workspace — jamais globale.

Chaque vecteur est un MemoryFact (texte court, tags, ts, conversation_id).

Graceful degradation : toute erreur est loggée + ignorée. Les agents
ne dépendent pas de UserMemory pour répondre — c'est un enrichissement.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from colaig.models import SearchResult, UserProfile

logger = logging.getLogger(__name__)

# Nombre max de faits stockés par user (au-delà, les plus anciens sont évincés)
MAX_FACTS_PER_USER = 500

# Prompt Ministral-3B pour extraction de faits
_EXTRACT_PROMPT = """\
Extrait les faits importants sur l'utilisateur depuis cet échange conversationnel.
Un fait = une information stable sur la personne : préférences, contraintes, compétences, rôle, projets en cours, décisions prises.
Ignore les informations purement documentaires ou génériques.

Échange :
USER: {user_msg}
ASSISTANT: {assistant_msg}

Réponds UNIQUEMENT avec un JSON valide :
{{"facts": ["fait 1", "fait 2"]}}
Si aucun fait utile, réponds {{"facts": []}}"""

# Prompt de consolidation profil
_CONSOLIDATE_PROMPT = """\
Voici les faits connus sur un utilisateur (liste non ordonnée, potentiellement redondante) :

{facts_text}

Synthétise en un profil structuré JSON :
{{
  "role": "...",
  "expertise_areas": ["..."],
  "preferences": ["..."],
  "constraints": ["..."],
  "active_projects": ["..."],
  "communication_style": "..."
}}
Sois concis et factuel. Champs vides = []."""


def _safe_user_id(user_id: str) -> str:
    """Transforme un user_id en nom de dossier sûr.

    Délègue à personal_workspace_slug() — source unique de vérité partagée
    avec workspace.py et auth/tokens.py pour garantir des chemins cohérents.
    """
    from colaig.context.workspace import personal_workspace_slug
    return personal_workspace_slug(user_id)


@dataclass
class MemoryFact:
    """Unité de mémoire per-user.

    Stockée comme chunk dans un FaissStore dédié par user.
    Compatible DocumentChunk pour réutiliser FaissStore sans modification.
    """
    text: str                          # Texte du fait (indexé + affiché)
    tags: list[str] = field(default_factory=list)  # Catégories : "preference", "role", ...
    ts: str = ""                       # ISO timestamp de création
    conversation_id: str = ""          # Conversation source
    workspace_id: str = ""

    # Attributs fantômes pour compatibilité FaissStore (qui attend DocumentChunk)
    # FaissStore stocke tout dans .metadata — ces champs permettent .source_path, .section, etc.
    @property
    def source_path(self) -> str:
        return f"memory::{self.conversation_id}"

    @property
    def source_name(self) -> str:
        return "user_memory"

    @property
    def section(self) -> str:
        return ", ".join(self.tags)


class _ChunkCompat:
    """Wrapper picklable autour d'un MemoryFact — compatible FaissStore._metadata.

    Défini au niveau module (et non dans une closure) pour être picklable
    via le module pickle standard (requis par FaissStore.serialize()).
    """
    __slots__ = ("text", "source_path", "source_name", "section", "tags", "ts",
                 "conversation_id", "workspace_id")

    def __init__(self, fact: MemoryFact) -> None:
        self.text = fact.text
        self.source_path = fact.source_path
        self.source_name = fact.source_name
        self.section = fact.section
        self.tags = list(fact.tags)
        self.ts = fact.ts
        self.conversation_id = fact.conversation_id
        self.workspace_id = fact.workspace_id


class UserMemory:
    """Mémoire sémantique per-user — 3 rythmes.

    Args:
        storage: Backend de stockage (StorageProtocol).
        embeddings: Service d'embeddings (EmbeddingServiceProtocol).
        registry: FaissIndexRegistry partagé.
        albert_client: Client LLM léger pour extraction (AlbertClientProtocol).
        light_model: Modèle léger pour extraction (ex: Ministral-3B).
        dimension: Dimension des vecteurs (doit correspondre au modèle d'embedding).
        storage_root: Racine du storage pour la mémoire globale cross-workspace (mode DM).
            Si None, seule la mémoire par workspace est disponible.
    """

    def __init__(
        self,
        storage,
        embeddings,
        registry,
        albert_client=None,
        light_model: str | None = None,
        dimension: int = 1024,
    ) -> None:
        self._storage = storage
        self._embeddings = embeddings
        self._registry = registry
        self._albert = albert_client
        self._light_model = light_model
        self._dimension = dimension

    # ──────────────────────────────────────────────────────────────────
    # Rythme 1 : Lecture temps réel

    async def read(
        self,
        user_id: str,
        workspace_path: str,
        query_embedding: list[float],
        k: int = 5,
    ) -> list[MemoryFact]:
        """Rythme 1 — Lecture temps réel via FaissIndexRegistry.

        Appelé dans PreExecutionBuilder.build(), en parallèle avec
        les autres searches (behaviors, skills) via asyncio.gather().

        Args:
            user_id: Identifiant utilisateur.
            workspace_path: Chemin workspace pour isoler les mémoires.
            query_embedding: Vecteur de la requête courante (déjà calculé).
            k: Nombre de faits à retourner.

        Returns:
            Liste de MemoryFact pertinents ([] si aucun ou erreur).
        """
        key = self._registry_key(user_id, workspace_path)

        # Charger depuis storage si pas encore en mémoire
        store = await self._registry.get_or_load(key, lambda: self._load_store(user_id, workspace_path))
        if store is None or store.count == 0:
            return []

        results: list[SearchResult] = await self._registry.search(key, query_embedding, k)
        return self._results_to_facts(results)

    # ──────────────────────────────────────────────────────────────────
    # Rythme 2 : Écriture fire-and-forget

    def schedule_extract(
        self,
        user_id: str,
        workspace_path: str,
        user_msg: str,
        assistant_msg: str,
        conversation_id: str = "",
    ) -> None:
        """Rythme 2 — Planifie l'extraction en fire-and-forget.

        Appeler APRÈS avoir envoyé la réponse à l'utilisateur.
        Ne bloque jamais — asyncio.create_task() sur la coroutine d'extraction.
        """
        if not self._albert:
            return
        asyncio.create_task(
            self._extract_and_store(user_id, workspace_path, user_msg, assistant_msg, conversation_id),
            name=f"user_memory_extract:{user_id}",
        )

    async def _extract_and_store(
        self,
        user_id: str,
        workspace_path: str,
        user_msg: str,
        assistant_msg: str,
        conversation_id: str,
    ) -> None:
        """Extraction LLM → embed → ajout dans le registry + persistance."""
        facts = await self._extract_facts(user_msg, assistant_msg)
        if not facts:
            return

        now_ts = datetime.utcnow().isoformat()
        tags = _auto_tag(facts)

        memory_facts = [
            MemoryFact(
                text=f,
                tags=tags,
                ts=now_ts,
                conversation_id=conversation_id,
                workspace_id=workspace_path,
            )
            for f in facts
        ]

        # Embed tous les faits en un seul appel batch
        texts = [f.text for f in memory_facts]
        try:
            embeddings = await self._embeddings.embed_texts(texts)
        except Exception as e:
            logger.warning("user_memory: embed_texts échoué pour %s: %s", user_id, e)
            return

        # Charger (ou créer) le store
        key = self._registry_key(user_id, workspace_path)
        store = await self._registry.get_or_load(key, lambda: self._load_store(user_id, workspace_path))
        if store is None:
            from colaig.rag.faiss_store import FaissStore
            store = FaissStore(dimension=self._dimension)
            self._registry.set(key, store)

        # Déduplication sémantique : éviter d'ajouter des faits trop proches
        # de faits déjà stockés (même préférence répétée sur plusieurs conversations).
        # Seuil empirique 0.92 : cosine très élevé = contenu quasi-identique.
        DEDUP_THRESHOLD = 0.92
        filtered_facts: list[MemoryFact] = []
        filtered_embeddings: list[list[float]] = []

        if store.count > 0:
            for fact, emb in zip(memory_facts, embeddings):
                existing = store.search(emb, k=1)
                if existing and existing[0].score >= DEDUP_THRESHOLD:
                    logger.debug(
                        "user_memory: fait dupliqué ignoré (score=%.3f): %s",
                        existing[0].score, fact.text[:60],
                    )
                    continue
                filtered_facts.append(fact)
                filtered_embeddings.append(emb)
        else:
            filtered_facts = memory_facts
            filtered_embeddings = embeddings

        if not filtered_facts:
            return

        chunks = [_ChunkCompat(f) for f in filtered_facts]
        await store.add_async(filtered_embeddings, chunks)

        # Tronquer si dépassement
        if store.count > MAX_FACTS_PER_USER:
            await store.rebuild_async()

        # Persister en background (fire-and-forget sur le storage)
        asyncio.create_task(
            self._persist_store(key, user_id, workspace_path),
            name=f"user_memory_persist:{user_id}",
        )
        logger.debug(
            "user_memory: %d faits ajoutés pour %s (%d dupliqués ignorés)",
            len(filtered_facts), user_id, len(facts) - len(filtered_facts),
        )

    # ──────────────────────────────────────────────────────────────────
    # Rythme 3 : Consolidation background

    async def consolidate(self, user_id: str, workspace_path: str) -> None:
        """Rythme 3 — Consolidation (profil + map) appelée par le loop background.

        Actions :
        - REFINE_PROFILE : synthèse LLM des faits → profile.json
        - Rebuild de l'index pour compacter les suppressions lazy

        Appelé depuis run_user_memory_consolidation_loop() dans main.py.
        Cadence recommandée : ~1h par user actif.
        """
        if not self._albert:
            return

        key = self._registry_key(user_id, workspace_path)
        store = self._registry.get(key)
        if store is None or store.count == 0:
            return

        # Récupérer tous les faits (full scan des métadonnées)
        facts_text = self._all_facts_text(store)
        if not facts_text:
            return

        # REFINE_PROFILE : 1 appel LLM léger
        try:
            prompt = _CONSOLIDATE_PROMPT.format(facts_text=facts_text)
            model = self._light_model or "mistralai/Ministral-3-8B-Instruct-2512"
            messages = [{"role": "user", "content": prompt}]
            raw = await self._albert.chat(messages=messages, model=model, temperature=0.1, max_tokens=512)
            profile_text = raw.strip() if isinstance(raw, str) else ""
            if profile_text:
                await self._save_profile(user_id, workspace_path, profile_text)
                logger.debug("user_memory: profil consolidé pour %s", user_id)
        except Exception as e:
            logger.warning("user_memory: consolidation échouée pour %s: %s", user_id, e)

        # Rebuild pour compacter les suppressions lazy
        if store.has_deletions():
            await store.rebuild_async()
            await self._persist_store(key, user_id, workspace_path)

    async def load_profile(self, user_id: str, workspace_path: str) -> UserProfile | None:
        """Charge le profil consolidé d'un utilisateur.

        Returns None si absent.
        """
        path = self._profile_path(user_id, workspace_path)
        try:
            data = await self._storage.download(path)
            raw = json.loads(data.decode("utf-8"))
            return UserProfile.from_dict(raw)
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────────
    # Internals

    def _registry_key(self, user_id: str, workspace_path: str) -> str:
        from colaig.rag.colaig_index import ColaigIndex
        return ColaigIndex.user_memory_key(workspace_path, _safe_user_id(user_id))

    def _faiss_path(self, user_id: str, workspace_path: str) -> str:
        from colaig.rag.colaig_index import ColaigIndex
        return ColaigIndex.user_memory_paths(workspace_path, _safe_user_id(user_id))[0]

    def _meta_path(self, user_id: str, workspace_path: str) -> str:
        from colaig.rag.colaig_index import ColaigIndex
        return ColaigIndex.user_memory_paths(workspace_path, _safe_user_id(user_id))[1]

    def _profile_path(self, user_id: str, workspace_path: str) -> str:
        from colaig.rag.colaig_index import ColaigIndex
        return ColaigIndex.user_profile_path(workspace_path, _safe_user_id(user_id))

    async def _load_store(self, user_id: str, workspace_path: str):
        """Charge le FaissStore depuis le storage (retourne None si absent)."""
        faiss_path = self._faiss_path(user_id, workspace_path)
        meta_path = self._meta_path(user_id, workspace_path)
        try:
            faiss_bytes = await self._storage.download(faiss_path)
            meta_bytes = await self._storage.download(meta_path)
            from colaig.rag.faiss_store import FaissStore
            store = FaissStore(dimension=self._dimension)
            await asyncio.to_thread(store.deserialize, faiss_bytes, meta_bytes)
            logger.debug("user_memory: store chargé pour %s (%d faits)", user_id, store.count)
            return store
        except Exception:
            return None

    async def _persist_store(self, key: str, user_id: str, workspace_path: str) -> None:
        """Sérialise et uploade le store via StorageProtocol."""
        faiss_path = self._faiss_path(user_id, workspace_path)
        meta_path = self._meta_path(user_id, workspace_path)
        try:
            # Créer le dossier parent
            from colaig.rag.colaig_index import ColaigIndex
            user_dir = ColaigIndex.user_dir(workspace_path, _safe_user_id(user_id))
            await self._storage.mkdir(user_dir)
        except Exception:
            pass
        await self._registry.save(key, self._storage, faiss_path, meta_path)

    async def _extract_facts(self, user_msg: str, assistant_msg: str) -> list[str]:
        """Appel LLM léger pour extraire les faits d'un échange."""
        if not user_msg.strip():
            return []
        try:
            prompt = _EXTRACT_PROMPT.format(
                user_msg=user_msg[:1000],
                assistant_msg=assistant_msg[:500],
            )
            model = self._light_model or "mistralai/Ministral-3-8B-Instruct-2512"
            messages = [{"role": "user", "content": prompt}]
            raw = await self._albert.chat(messages=messages, model=model, temperature=0.0, max_tokens=256)
            text = raw.strip() if isinstance(raw, str) else ""
            # Parser le JSON
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                facts = data.get("facts", [])
                if isinstance(facts, list):
                    return [str(f).strip() for f in facts if str(f).strip()]
        except Exception as e:
            logger.debug("user_memory: extraction échouée: %s", e)
        return []

    async def _save_profile(self, user_id: str, workspace_path: str, profile_text: str) -> None:
        """Sauvegarde le profil JSON consolidé."""
        path = self._profile_path(user_id, workspace_path)
        try:
            await self._storage.upload(path, profile_text.encode("utf-8"))
        except Exception as e:
            logger.warning("user_memory: sauvegarde profil échouée: %s", e)

    def _all_facts_text(self, store) -> str:
        """Retourne tous les faits actifs du store sous forme de liste."""
        lines = []
        for chunk in store.get_all_active_chunks():
            text = getattr(chunk, "text", "")
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines)

    def _results_to_facts(self, results: list[SearchResult]) -> list[MemoryFact]:
        """Convertit des SearchResult en MemoryFact (reconstruit depuis le chunk)."""
        facts = []
        for r in results:
            chunk = getattr(r, "chunk", None)
            if chunk is None:
                continue
            text = getattr(chunk, "text", "")
            if not text:
                continue
            facts.append(MemoryFact(
                text=text,
                tags=getattr(chunk, "tags", []),
                ts=getattr(chunk, "ts", ""),
                conversation_id=getattr(chunk, "conversation_id", ""),
                workspace_id=getattr(chunk, "workspace_id", ""),
            ))
        return facts


def _auto_tag(facts: list[str]) -> list[str]:
    """Heuristique rapide pour tagger les faits sans LLM supplémentaire."""
    tags = []
    text = " ".join(facts).lower()
    if any(w in text for w in ["préfère", "aime", "préfèrent", "plutôt"]):
        tags.append("preference")
    if any(w in text for w in ["rôle", "poste", "responsable", "chef", "directeur", "agent"]):
        tags.append("role")
    if any(w in text for w in ["projet", "dossier", "mission", "chantier"]):
        tags.append("project")
    if any(w in text for w in ["contrainte", "délai", "limite", "impossible", "interdit"]):
        tags.append("constraint")
    if any(w in text for w in ["décision", "choix", "retenu", "validé", "approuvé"]):
        tags.append("decision")
    return tags or ["general"]


async def run_user_memory_consolidation_loop(
    user_memory: UserMemory,
    interval: int = 3600,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Loop background — consolide la mémoire de tous les users actifs (~1h).

    Pour chaque clé user:: dans le registry :
    - REFINE_PROFILE : synthèse LLM de tous les faits → profile.json
    - Rebuild FAISS si suppressions lazy en attente

    La consolidation est graceful : une erreur par user n'interrompt pas les autres.

    Args:
        user_memory: Instance UserMemory partagée.
        interval: Cadence en secondes (défaut 3600 = 1h).
        shutdown_event: Événement asyncio pour arrêt propre.
    """
    logger.info("consolidation mémoire user: démarrage (interval=%ds)", interval)
    while True:
        # Attendre l'intervalle (ou arrêt)
        try:
            if shutdown_event is not None:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                logger.info("consolidation mémoire user: arrêt demandé")
                return
            else:
                await asyncio.sleep(interval)
        except TimeoutError:
            pass

        # Parcourir toutes les clés user:: dans le registry
        keys = [k for k in user_memory._registry.keys() if k.startswith("user::")]
        if not keys:
            logger.debug("consolidation mémoire user: aucun store en mémoire")
            continue

        logger.info("consolidation mémoire user: %d store(s) à consolider", len(keys))
        for key in keys:
            # Extraire user_id et workspace_path depuis la clé
            # Format : "user::{workspace_path}::{safe_user_id}"
            parts = key.split("::")
            if len(parts) < 3:
                continue
            # workspace_path peut contenir "::" si le chemin en contient — on reconstruit
            safe_user_id = parts[-1]
            workspace_path = "::".join(parts[1:-1])

            # Retrouver un user_id réel depuis la registry (heuristique safe_id → user_id)
            # On utilise safe_user_id comme proxy — consolidate() n'a besoin que de la clé
            try:
                await user_memory.consolidate(safe_user_id, workspace_path)
                logger.debug("consolidation mémoire user: %s (%s) OK", safe_user_id, workspace_path)
            except Exception as e:
                logger.warning(
                    "consolidation mémoire user: erreur pour %s (%s): %s",
                    safe_user_id, workspace_path, e,
                )
