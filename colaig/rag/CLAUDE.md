# rag/ — Pipeline de Recherche Documentaire

## Propriétaire
- `colaig_index.py`, `chunker.py`, `embeddings.py`, `faiss_store.py`, `index_registry.py`, `retriever.py`, `indexer.py`, `bm25_store.py`, `contextualizer.py`, `notifier.py`, `workspace_directory.py`, `federation_service.py` : Agent RAG
- `generator.py` : Agent CERVEAU

## Principe fondamental

Le module RAG est **indépendant du contexte Tchap**. Il travaille avec des fichiers texte (input), des embeddings vectoriels (traitement), et des résultats de recherche (output). Il ne sait RIEN de Matrix, des salons, ou des utilisateurs.

## colaig_index.py — Source de vérité des clés FAISS

Source de vérité unique pour **toutes** les clés du `FaissIndexRegistry`. Aucune instance requise — méthodes statiques uniquement.

> **Depuis L0.2 :** les *chemins* de persistance ne sont plus construits ici. Toutes les
> méthodes `*_paths()` délèguent à `colaig.paths`, source unique des chemins `.colaig/`
> pour l'ensemble du projet. Les valeurs de retour sont inchangées — l'API publique
> décrite ci-dessous reste valable. Ce qui change : un chemin `.colaig/` ne se construit
> plus nulle part ailleurs que dans `colaig/paths.py`, et un test de contrat le vérifie.

```python
from colaig.rag.colaig_index import ColaigIndex

# ── Clés de registry ────────────────────────────────────────────────────
ColaigIndex.docs_key("/espace-rh/")           # → "/espace-rh::docs"
ColaigIndex.behaviors_key("/ws/")             # → "/ws::behaviors"
ColaigIndex.skills_key("/ws/")                # → "/ws::skills"
ColaigIndex.knowledge_key("/ws/")             # → "/ws::knowledge"
ColaigIndex.user_memory_key("/ws/", "alice")  # → "user::/ws::alice"

ColaigIndex.FEDERATION_KEY                    # "federation::workspaces"

# ── Chemins de persistance ───────────────────────────────────────────────
faiss, meta = ColaigIndex.docs_paths("/ws/")
# → ("/ws/.colaig/indexes/index.faiss", "/ws/.colaig/indexes/metadata.pkl")

faiss, meta = ColaigIndex.behaviors_paths("/ws/")
# → ("/ws/.colaig/indexes/behaviors.faiss", "/ws/.colaig/indexes/behaviors.pkl")

faiss, meta = ColaigIndex.skills_paths("/ws/")
# → ("/ws/.colaig/indexes/skills.faiss", "/ws/.colaig/indexes/skills.pkl")

faiss, meta = ColaigIndex.user_memory_paths("/ws/", "alice")
# → ("/ws/.colaig/users/alice/memory.faiss", "/ws/.colaig/users/alice/memory.pkl")

ColaigIndex.user_profile_path("/ws/", "alice")
# → "/ws/.colaig/users/alice/profile.json"

ColaigIndex.user_dir("/ws/", "alice")
# → "/ws/.colaig/users/alice/"

ColaigIndex.knowledge_json_path("/ws/")
# → "/ws/.colaig/workspace_knowledge.json"

ColaigIndex.FEDERATION_FAISS_PATH             # "/.colaig/federation/workspaces.faiss"
ColaigIndex.FEDERATION_META_PATH              # "/.colaig/federation/workspaces.pkl"

# ── Chargement partagé (async) ───────────────────────────────────────────
store = await ColaigIndex.load_store(storage, faiss_path, meta_path, dimension=1024)
# → FaissStore | None (None si absent ou corrompu)
```

### Convention de nommage
- `ws_path` est toujours `rstrip('/')` → trailing slash toléré en entrée
- Clés workspace : `"{ws_path}::{type}"` — ex: `"/espace-rh::docs"`
- Clé mémoire user : `"user::{ws_path}::{safe_uid}"` — ex: `"user::/espace-rh::alice_tchap.fr"`
- Clé fédération : constante `"federation::workspaces"` (sans workspace)

### Consommateurs
- `profile_service.py` — behaviors.faiss via `get_or_load(behaviors_key, ...)`
- `pre_execution.py` — skills.faiss via `get_or_load(skills_key, ...)`
- `user_memory.py` — memory.faiss paths + registry keys
- `workspace_directory.py` — federation constants
- `workspace_delegate.py` — `docs_key()` pour cross-workspace RAG

---

## faiss_store.py — Gestion d'un index FAISS

### Index FAISS
```python
# Création : IndexFlatIP = Inner Product (cosinus si vecteurs normalisés L2)
store = FaissStore(dimension=1024)

# API synchrone
store.add(embeddings, metadata)              # list[list[float]], list[DocumentChunk]
store.search(query_embedding, k=5)           # → list[SearchResult]
store.search_batch(query_embeddings, k=5)    # → list[list[SearchResult]] — batch vectorisé C++
store.delete_by_source(source_path)          # lazy delete → marque _deleted
store.rebuild()                              # compacte les suppressions lazy
store.get_all_vectors()                      # → np.ndarray (n_actifs, dim) via reconstruct_n

# API async — libère le GIL via asyncio.to_thread()
await store.add_async(embeddings, metadata)        # verrou exclusif _write_lock
await store.search_async(query_embedding, k)       # non-bloquant, concurrent OK
await store.search_batch_async(query_embeddings, k) # batch async
await store.delete_by_source_async(path)           # verrou exclusif
await store.rebuild_async()                        # verrou exclusif
await store.get_all_vectors_async()                # non-bloquant

# API publique (accès aux internals sans toucher _metadata/_deleted)
store.count                                  # vecteurs actifs
store.has_deletions()                        # True si suppressions en attente
store.get_all_active_chunks()                # list[DocumentChunk] actifs

# Persistance
store.save(directory)                        # → index.faiss + metadata.pkl
store.load(directory)
index_bytes, meta_bytes = store.serialize()  # → bytes (upload storage)
store.deserialize(index_bytes, meta_bytes)   # (download storage)
```

### Règles importantes
- Vecteurs **normalisés L2** avant ajout (`faiss.normalize_L2` appliqué automatiquement)
- Suppression = **lazy** (marquage) + rebuild périodique
- Écriture protégée par `asyncio.Lock` — lectures concurrentes OK
- Les objets dans `_metadata` doivent être **picklables** (pour `serialize()`)
- `rebuild()` utilise `reconstruct_n(0, ntotal)` — 1 appel C++ + numpy fancy indexing (5x vs N appels Python→C)

## index_registry.py — Registre centralisé des index FAISS

```python
registry = FaissIndexRegistry()

# Lecture / chargement lazy
store = registry.get("ws1::docs")           # None si absent
store = await registry.get_or_load("ws1::docs", loader)  # double-check lock

# Écriture
registry.set("ws1::docs", store)
registry.evict("ws1::docs")
registry.evict_prefix("ws1::")              # → int (nb supprimés)
n = registry.count()

# Recherche async
results = await registry.search("ws1::docs", query_embedding, k=5)
results_map = await registry.search_multi({
    "ws1::docs": (query, 5),
    "ws1::behaviors": (query, 3),
    "ws1::missing": (query, 2),   # absent → [] sans erreur
})  # → dict[str, list[SearchResult]], asyncio.gather() en parallèle

# Persistance via StorageProtocol
await registry.save("ws1::docs", storage, "/ws1/.colaig/indexes/docs.faiss", "/ws1/.colaig/indexes/docs.pkl")
```

### Convention de clés
- Workspace RAG : `"{workspace_path}::{index_name}"` (ex: `"/espace-rh/::docs"`)
- Mémoire user : `"user::{workspace_path}::{safe_user_id}"` (ex: `"user::/espace-rh/::alice_tchap.fr"`)

## bm25_store.py — Index lexical BM25 (hybrid search)

Complément au FAISS vectoriel : capture les correspondances exactes de termes (acronymes, codes, noms propres) que les embeddings manquent.

```python
store = BM25Store()

store.add(chunks)                    # list[DocumentChunk]
store.search(query, k=10)            # → list[tuple[DocumentChunk, float]]
store.delete_by_source(source_path)  # lazy delete
store.rebuild()                      # compacte
store.count                          # chunks actifs
store.serialize() → bytes            # upload storage → bm25.pkl
store.deserialize(data)              # download storage
```

### Notes
- Requiert `rank-bm25` (dans pyproject.toml)
- Scores BM25 peuvent être négatifs (IDF~0 si terme dans tous les docs) → utilisés pour le **rang** dans RRF, pas la valeur absolue
- Même pattern lazy-delete que FaissStore
- Persisté dans `bm25.pkl` côté `.colaig/indexes/`

## contextualizer.py — Contextualisation LLM workspace-aware

Technique Anthropic "Contextual Retrieval" : génère un préfixe de 1-2 phrases par chunk au moment de l'indexation, ancré dans le domaine du workspace.

Le texte embedé devient : `{contextual_prefix}\n\n{chunk.text}`

```python
ctx = ChunkContextualizer(llm_client, model="mistralai/Ministral-3-8B-Instruct-2512", max_concurrent=8)

enriched = await ctx.enrich_batch(
    chunks,
    workspace_name="Marchés publics",
    workspace_description="Procédures administratives CEREMA",
    workspace_system_prompt="Tu es un expert en marchés publics...",
)
# → list[DocumentChunk] avec contextual_prefix rempli
```

### Workspace-awareness
Le prompt LLM reçoit `name`, `description`, `system_prompt` du workspace (`WorkspaceConfig`) → préfixes ancrés dans le domaine métier.

### Graceful fallback
- Erreur LLM → chunk retourné sans préfixe (les autres continuent)
- Réponse vide ou > 500 chars → rejetée
- `max_concurrent=8` : sémaphore asyncio pour parallélisme contrôlé

### Variables d'env
```bash
COLAIG_CONTEXTUAL_CHUNKING_ENABLED=true
COLAIG_CONTEXTUAL_MODEL=mistralai/Ministral-3-8B-Instruct-2512  # vide = albert_model_light
```

## chunker.py
- **Markdown** : split sur titres (`#`), chaque section = 1 chunk
- **PDF/DOCX** : split par double saut de ligne, overlap configurable
- **Texte brut** : sliding window (800 chars, overlap 100)
- Chunk min 50 chars, max 2000 chars

## embeddings.py
- Utilise `LLMClientProtocol`, cache dict `{hash(text): embedding}`
- **Toujours normaliser L2** les vecteurs
- Fallback `SentenceTransformer("BAAI/bge-m3")` si Albert down

## retriever.py — Pipeline hybride complet

```
1. (opt) HyDE : LLM génère réponse hypothétique → embedding combiné (1-w)*query + w*hyde
2. FAISS dense search (k*2 candidats)
3. (opt) BM25 lexical search (k*2) → RRF fusion : Σ 1/(k_constant + rang)
4. MMR reranking (λ=0.7) — diversité inter-sources
5. (opt) Reranking cross-encoder Albert → scores cross-encoder remplacent FAISS/RRF
6. Filtrage score (0.3 sans reranker, 0.001 après Albert — bge-reranker-v2-m3 retourne des scores ~0.001-0.005)
7. Top-k
```

### RRF — Reciprocal Rank Fusion
```
score_RRF(doc) = Σ 1 / (k_constant + rang(doc, index))
```
Fusionne FAISS + BM25 sans normalisation des scores (seuls les rangs comptent). k=60 = recommandation standard (Cormack et al., 2009). Un document présent dans les deux index obtient un score cumulé supérieur.

### HyDE — Hypothetical Document Embeddings
LLM génère une réponse hypothétique → `(1-w)*query_emb + w*hyde_emb`, normalisé L2. Fallback si LLM échoue.

### Variables d'env
```bash
COLAIG_HYBRID_SEARCH_ENABLED=true   # BM25 + RRF (workspace_bm25_stores peuplés)
COLAIG_HYDE_ENABLED=true            # HyDE query expansion
COLAIG_HYDE_QUERY_WEIGHT=0.5        # Poids embedding HyDE (0→1)
COLAIG_RRF_K_CONSTANT=60            # Constante k du RRF
```

### API
```python
results = await retriever.retrieve(
    query="procédure marché public",
    k=5,
    score_threshold=0.3,
    store=workspace_faiss_store,     # isolation workspace
    bm25_store=workspace_bm25_store, # hybrid search (None = désactivé)
)
```

## indexer.py — Orchestration d'indexation

Orchestre : scan storage → compare etags → chunk → (contextualise) → embed → FAISS add → (BM25 add) → save

```python
indexer = Indexer(
    storage, chunker, embeddings, store,
    albert_client=albert,           # OCR PDF scannés
    bm25_store=bm25,                # index lexical parallèle
    contextualizer=ctx,             # préfixes LLM workspace-aware
    workspace_name=ws.name,
    workspace_description=ws.description,
    workspace_system_prompt=ws.system_prompt,
)
```

### Persistance (4 fichiers sur storage)
```
.colaig/indexes/
├── index.faiss      # vecteurs FAISS
├── metadata.pkl     # DocumentChunk par position
├── etags.json       # etags pour indexation incrémentale
└── bm25.pkl         # index BM25 (si hybrid_search_enabled)
```

### Flux d'indexation enrichi
1. `extract_text()` (pymupdf) → text ; si vide ET PDF ET albert_client → OCR Albert (PDF scanné)
2. `chunk_document()` → `list[DocumentChunk]`
3. `contextualizer.enrich_batch()` → `DocumentChunk.contextual_prefix` rempli
4. embed `"{prefix}\n\n{text}"` ou `"{text}"` si pas de préfixe
5. `faiss_store.add(embeddings, chunks)`
6. `bm25_store.add(chunks)` — texte original (sans préfixe, pour correspondances exactes)
7. `save_to_storage()` → 4 fichiers

## notifier.py — Notifications proactives de changements documentaires

Formate les messages proactifs envoyés via MessagingProtocol lors de la détection de nouveaux/modifiés documents dans `run_indexation_loop`.

### Deux modes transparents

**Mode A** — sans contextual_prefix (ou `store=None`) :
- Liste des noms de fichiers nouveaux/modifiés/supprimés
- Zéro dépendance LLM
- Toujours disponible même si `COLAIG_CONTEXTUAL_CHUNKING_ENABLED=false`

**Mode B** — avec `contextual_prefix` (si `COLAIG_CONTEXTUAL_CHUNKING_ENABLED=true`) :
- Enrichissement sémantique : description 1-2 phrases par document
- Extraite du **premier chunk actif** du document dans le store déjà en mémoire
- **Zéro appel LLM supplémentaire** — préfixes calculés lors de l'indexation
- Dégrade gracieusement vers Mode A si le store est indisponible ou les préfixes vides

```python
from colaig.rag.notifier import format_notification
from colaig.models import UpdateSummary

update = UpdateSummary(count=2, changed_paths=["/ws/a.pdf", "/ws/b.pdf"])
msg = format_notification(
    workspace_name="Conception Routière",
    update=update,
    store=ws_faiss_store,   # None → Mode A
    language="fr",          # "fr" | "en"
)
# → "📄 **Conception Routière** — mise à jour documentaire\n\n**2 documents mis à jour**\n• **a.pdf** — …\n• **b.pdf** — …"
```

### Activation par workspace (config.yaml)
```yaml
proactive_notifications: true     # false par défaut (opt-in)
notification_channels:            # vide = toutes les conversations du workspace
  - "!roomid:server"
```

### Câblage dans run_indexation_loop (main.py)
Après chaque `check_updates()` + `save_to_storage()` :
- Si `ws.proactive_notifications=True` et `update.changed_paths` non vide
- Appelle `format_notification()` et envoie via `messaging.send()` sur les canaux configurés
- Erreurs silencieuses (warning log, jamais crash de la boucle)

### UpdateSummary — rétrocompatibilité int
`check_updates()` retourne désormais un `UpdateSummary` (au lieu de `int`) :
```python
update = await indexer.check_updates(ws.storage_path)
if update > 0: ...          # toujours valide (__gt__)
assert update == 3          # toujours valide (__eq__)
update.changed_paths        # liste des chemins nouveaux/modifiés
update.removed_paths        # ensemble des chemins supprimés
```

## workspace_directory.py — Répertoire vectoriel des workspaces

Index FAISS léger (1 vecteur par workspace) pour le routage sémantique cross-workspace. Agrège workspaces locaux et distants via `FederationService`.

```python
directory = WorkspaceDirectory(storage, embedding_service, index_registry)

# Construction (workspaces locaux visibility="federation"|"public" + distants via peers)
await directory.build(all_workspaces, federation_service=fed_service)

# Chargement depuis storage, ou reconstruction si absent
await directory.load_or_build(all_workspaces, federation_service=fed_service)

# Recherche sémantique
candidates = await directory.search("procédures marchés publics", k=3)
# → [WorkspaceDirectoryEntry(workspace_id="espace-juridique", mcp_url="", score=0.91), ...]

# Lookup O(1) par ID (pour run_rag_delegate niveau 4)
entry = directory.find_by_id("rh-distant")
# → WorkspaceDirectoryEntry(mcp_url="https://peer.fr/mcp", auth_token="...", source="remote")

directory.is_loaded()  # → bool
```

### WorkspaceDirectoryEntry
```python
@dataclass
class WorkspaceDirectoryEntry:
    workspace_id: str
    workspace_name: str
    description: str
    score: float
    mcp_url: str = ""       # Non vide si workspace distant (peer)
    auth_token: str = ""    # Token Bearer pour le peer
    source: str = "local"   # "local" | "remote"
```

### Stockage
Persisté dans `/.colaig/federation/workspaces.faiss` + `.pkl` (via `ColaigIndex.FEDERATION_*` constants).

---

## generator.py (Agent CERVEAU)
Construit le prompt avec system_prompt + documents RAG + historique, appelle Albert, formate la réponse avec sources.

## specializer.py — Auto-spécialisation (opt-in)

`WorkspaceSpecializer` dérive domaine/vocabulaire/ton/expertise/system_prompt depuis
un échantillon du corpus indexé (LLM léger). Hook post-indexation dans main.py si
`COLAIG_AUTO_SPECIALIZE_ENABLED`. Dry-run par défaut (écrit `workspace_knowledge.json`) ;
écrit la config si `COLAIG_AUTO_SPECIALIZE_APPLY` et n'écrase jamais un prompt manuel.
