# SPDX-License-Identifier: MIT
"""
Index sémantique unifié des ressources Colaig par workspace.

Ce module remplit l'**Opération A** de la trinité sémantique :
*trouver le bon artefact Colaig à activer pour un message*. Il fusionne
ce qui était dispersé dans `BehaviorIndex` (pour les behaviors) et
`tool_filter_embed.py` (pour les descriptions d'outils MCP), en un
seul index FAISS scopé par workspace.

Il NE FAIT PAS l'Opération B (RAG documentaire sur les fichiers
utilisateur, qui reste dans `DocumentIndex`) ni l'Opération C (search
externe MCP, déléguée au serveur distant).

Architecture :
- Une instance `WorkspaceResourceIndex` par workspace
- Stockage : in-memory + cache global keyed par workspace_root
- FAISS FlatL2 (volumes petits, ~10-100 entrées par workspace)
- Réutilise `EmbeddingService` partagé pour ne pas recalculer les embeddings

Sources indexées :
1. Descriptions des outils internes (search_documents, etc.)
2. Descriptions des outils MCP du workspace
3. Frontmatter + body court des skills .md du workspace
4. (Optionnel) Behaviors actions du workspace pour intent matching

Le tout dans un index FAISS unique, requêté par similarité sémantique.

Mécanique de mise à jour :
- Reconstruction lors de la première requête après chargement du workspace
- Invalidation à chaque changement de :
  * Liste d'outils MCP (signalée par MCPRegistry)
  * Set de skills (mtime cache des skills)
  * Outils internes (statique sur la durée du process)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.matrix_bot.config import logger


@dataclass
class ResourceEntry:
    """Une entrée indexée dans le WorkspaceResourceIndex.

    kind : 'tool_internal' | 'tool_mcp' | 'skill' | 'behavior'
    name : identifiant unique (ex: 'search_documents', 'datagouv__search_datasets',
           'instruction_opah')
    description : texte descriptif utilisé pour générer l'embedding
    payload : référence opaque vers l'objet d'origine (ToolDef, Skill, ...)
    """
    kind: str
    name: str
    description: str
    payload: Any = None


@dataclass
class WorkspaceResourceIndex:
    """Index sémantique unifié des ressources d'un workspace.

    Construit de manière paresseuse : la première requête déclenche le calcul
    des embeddings et la construction de l'index FAISS. Reconstructions
    suivantes uniquement si l'inventaire des ressources change.
    """
    workspace_root: str
    entries: List[ResourceEntry] = field(default_factory=list)
    # Empreinte de l'inventaire (hash des noms) pour invalider lors de changements
    _inventory_etag: str = ""
    # Embeddings : List[List[float]] aligné sur entries
    _embeddings: List[List[float]] = field(default_factory=list)
    _ready: bool = False

    def add_entry(self, entry: ResourceEntry) -> None:
        """Ajoute une entrée à l'inventaire (avant build)."""
        self.entries.append(entry)
        self._ready = False

    def inventory_etag(self) -> str:
        """Calcule un etag déterministe de l'inventaire courant."""
        import hashlib
        sig = "|".join(f"{e.kind}:{e.name}" for e in self.entries)
        return hashlib.md5(sig.encode("utf-8")).hexdigest()

    async def build(self, config) -> bool:
        """Calcule les embeddings et construit l'index si nécessaire.

        Returns:
            True si reconstruit, False si déjà à jour ou erreur.
        """
        new_etag = self.inventory_etag()
        if self._ready and new_etag == self._inventory_etag:
            return False

        if not self.entries:
            self._embeddings = []
            self._inventory_etag = new_etag
            self._ready = True
            return True

        # Construire les textes à embedder : nom + description courte
        texts = [
            f"{e.name}: {e.description[:300]}"
            for e in self.entries
        ]

        try:
            embeddings = await _compute_embeddings(texts, config)
        except Exception as e:
            logger.warning(
                f"[WS-RES] {self.workspace_root!r}: embeddings échoués ({e}), "
                f"index inutilisable"
            )
            self._embeddings = []
            self._ready = False
            return False

        self._embeddings = embeddings
        self._inventory_etag = new_etag
        self._ready = True
        logger.info(
            f"[WS-RES] {self.workspace_root!r}: index construit "
            f"({len(self.entries)} entrées)"
        )
        return True

    async def search(
        self,
        query: str,
        config,
        top_k: int = 6,
        kinds: Optional[List[str]] = None,
        min_similarity: float = 0.0,
    ) -> List[Tuple[ResourceEntry, float]]:
        """Recherche les ressources les plus pertinentes pour une requête.

        Args:
            query: Texte utilisateur.
            config: Config Colaig (pour calculer l'embedding requête).
            top_k: Nombre max de résultats.
            kinds: Filtrer par type de ressource (None = tous).
            min_similarity: Seuil de similarité cosine minimum.

        Returns:
            Liste de (ResourceEntry, score) triée par score décroissant.
        """
        if not self._ready or not self._embeddings:
            await self.build(config)
        if not self._embeddings:
            return []

        # Embedder la requête
        try:
            q_embeddings = await _compute_embeddings([query], config)
            if not q_embeddings:
                return []
            q_emb = q_embeddings[0]
        except Exception as e:
            logger.debug(f"[WS-RES] embed query échoué: {e}")
            return []

        # Calcul des scores cosine
        scored: List[Tuple[ResourceEntry, float]] = []
        for entry, emb in zip(self.entries, self._embeddings):
            if kinds and entry.kind not in kinds:
                continue
            sim = _cosine(q_emb, emb)
            if sim >= min_similarity:
                scored.append((entry, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# ─── Helpers embeddings (réutilise EmbeddingService partagé) ─────────────────

async def _compute_embeddings(texts: List[str], config) -> List[List[float]]:
    """Calcule des embeddings via le service partagé EmbeddingService.

    Préfère le service partagé pour bénéficier du cache LRU global plutôt
    que de réinventer un cache local par workspace.
    """
    try:
        from app.services.embedding_service import get_embedding_service
        svc = await get_embedding_service(config)
        if svc is not None and hasattr(svc, "get_embeddings"):
            embs = await svc.get_embeddings(texts)
            return embs or []
    except Exception:
        pass

    # Fallback : appel direct à AlbertApiClient
    from app.core_llm import AlbertApiClient
    aclient = AlbertApiClient(
        base_url=config.albert_api_url,
        api_key=config.albert_api_token,
    )
    try:
        return await aclient.get_embeddings(
            texts=texts,
            model=config.albert_model_embedding,
        )
    finally:
        await aclient.close()


def _cosine(a: List[float], b: List[float]) -> float:
    """Similarité cosine entre deux vecteurs."""
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ─── Cache global par workspace ──────────────────────────────────────────────

_cache: Dict[str, WorkspaceResourceIndex] = {}


def get_or_create_index(workspace_root: str) -> WorkspaceResourceIndex:
    """Retourne l'index du workspace, en le créant si nécessaire."""
    if workspace_root not in _cache:
        _cache[workspace_root] = WorkspaceResourceIndex(workspace_root=workspace_root)
    return _cache[workspace_root]


def invalidate_workspace_index(workspace_root: Optional[str] = None) -> None:
    """Invalide l'index d'un workspace (ou tout)."""
    if workspace_root is None:
        _cache.clear()
        logger.info("[WS-RES] Cache global vidé")
    else:
        _cache.pop(workspace_root, None)
        logger.debug(f"[WS-RES] Cache vidé pour {workspace_root!r}")


# ─── Construction de l'inventaire à partir des sources Colaig ────────────────

def populate_index(
    workspace_root: str,
    *,
    internal_tools: List[Any] = None,
    mcp_tools: List[Any] = None,
    skills: List[Any] = None,
) -> WorkspaceResourceIndex:
    """Peuple l'index d'un workspace avec ses ressources connues.

    Reconstruit l'inventaire (mais pas les embeddings tant que .build() pas appelé).
    Si l'inventaire change, build() recalculera les embeddings au prochain search().

    Args:
        workspace_root: identifiant du workspace
        internal_tools: liste de ToolDef (outils internes Colaig)
        mcp_tools: liste de MCPTool ou ToolDef MCP-wrappés
        skills: liste de Skill du workspace
    """
    idx = get_or_create_index(workspace_root)
    new_entries: List[ResourceEntry] = []

    if internal_tools:
        for t in internal_tools:
            new_entries.append(ResourceEntry(
                kind="tool_internal",
                name=getattr(t, "name", str(t)),
                description=getattr(t, "description", ""),
                payload=t,
            ))

    if mcp_tools:
        for t in mcp_tools:
            # mcp_tools peut être MCPTool (qualified_name) ou ToolDef wrappé
            name = getattr(t, "qualified_name", None) or getattr(t, "name", "")
            new_entries.append(ResourceEntry(
                kind="tool_mcp",
                name=name,
                description=getattr(t, "description", ""),
                payload=t,
            ))

    if skills:
        for s in skills:
            # Pour les skills, on indexe nom + description + intro courte
            intro = ""
            body = getattr(s, "body", "")
            if body:
                lines = body.split("\n")
                # Premières lignes non vides du body, max 200 chars
                buf = []
                for line in lines:
                    line = line.strip()
                    if line:
                        buf.append(line)
                    if sum(len(b) for b in buf) > 200:
                        break
                intro = " ".join(buf)[:200]
            desc = getattr(s, "description", "")
            full = f"{desc} — {intro}" if intro else desc
            new_entries.append(ResourceEntry(
                kind="skill",
                name=getattr(s, "name", ""),
                description=full or getattr(s, "name", ""),
                payload=s,
            ))

    # Si l'inventaire est identique à l'existant, ne rien refaire
    new_etag_sig = "|".join(f"{e.kind}:{e.name}" for e in new_entries)
    import hashlib
    new_etag = hashlib.md5(new_etag_sig.encode("utf-8")).hexdigest()

    if idx._inventory_etag == new_etag and idx._ready:
        # Inventaire inchangé, on garde l'index existant
        return idx

    # Sinon on reconstruit l'inventaire (les embeddings seront recalculés au prochain build)
    idx.entries = new_entries
    idx._ready = False
    idx._embeddings = []
    idx._inventory_etag = ""  # forcer le rebuild

    return idx


def get_cache_stats() -> dict:
    """Stats d'observabilité."""
    return {
        "size": len(_cache),
        "workspaces": list(_cache.keys()),
        "total_entries": sum(len(idx.entries) for idx in _cache.values()),
        "ready": sum(1 for idx in _cache.values() if idx._ready),
    }
