# Mémoire Sémantique per-user & FAISS Multithreading

Ce document décrit l'architecture de la mémoire sémantique per-user de Colaig
et le modèle de concurrence FAISS qui la sous-tend.

---

## 1. Vue d'ensemble

La mémoire user permet à Colaig de **se souvenir de l'utilisateur** à travers les conversations :
ses préférences, son rôle, ses projets, ses contraintes. Elle enrichit silencieusement
les réponses sans jamais bloquer le pipeline.

**Principes** :
- Toujours workspace-bound — jamais de mémoire globale cross-workspace
- Graceful degradation — la mémoire est un enrichissement, pas un prérequis
- Fire-and-forget — l'extraction ne bloque jamais la réponse à l'utilisateur

---

## 2. Structure de stockage

La mémoire est rangée **dans le workspace de l'utilisateur**, dans `.colaig/users/` :

```
{workspace_path}/
└── .colaig/
    └── users/
        └── {safe_user_id}/
            ├── memory.faiss    # Index vectoriel FAISS des faits
            ├── memory.pkl      # Métadonnées des chunks (MemoryFact)
            └── profile.json    # Profil consolidé (UserProfile)
```

**En mode DM** : le `workspace_path` est le workspace personnel de l'utilisateur,
créé automatiquement par le resolver à `/.colaig/personal/{safe_user_id}/`.
La mémoire vit donc dans `/.colaig/personal/{safe_user_id}/.colaig/users/{safe_user_id}/`.

---

## 3. Les trois rythmes

### Rythme 1 — Lecture temps réel (PreExecutionBuilder)

Déclenché avant chaque réponse, en parallèle avec les autres lookups :

```python
# Dans PreExecutionBuilder.build() — asyncio.gather()
facts = await user_memory.read(user_id, workspace_path, message_embedding, k=5)
# → list[MemoryFact] ([] si aucun ou erreur)
```

- Consultation du `FaissIndexRegistry` (en mémoire, <1ms)
- Si absent du registry : chargement lazy depuis storage (`_load_store`)
- Recherche FAISS via `asyncio.to_thread()` — non-bloquant
- Les faits sont injectés dans `fixed_context["user_memory"]` du PreExecutionCard

### Rythme 2 — Extraction fire-and-forget (post-turn)

Déclenché **après** l'envoi de la réponse, dans `handlers.py` :

```python
user_memory.schedule_extract(
    user_id, workspace_path, user_msg, assistant_msg, conversation_id
)
# → asyncio.create_task() — retour immédiat, exécution en arrière-plan
```

Pipeline interne :
1. Appel LLM léger (Ministral-3B) → JSON `{"facts": ["fait 1", "fait 2"]}`
2. `embed_texts()` en batch — 1 seul appel Albert pour N faits
3. `store.add_async()` — ajout avec verrou exclusif
4. `_persist_store()` — upload storage en fire-and-forget (autre create_task)

### Rythme 3 — Consolidation background (~1h)

Appelé par `run_user_memory_consolidation_loop()` dans `main.py` :

```python
await user_memory.consolidate(user_id, workspace_path)
```

Actions :
1. **REFINE_PROFILE** : synthèse LLM de tous les faits → `profile.json`
   (UserProfile : role, expertise_areas, preferences, constraints, active_projects)
2. **Rebuild** : compactage des suppressions lazy si `store.has_deletions()`

---

## 4. FaissIndexRegistry — Registre centralisé

`FaissIndexRegistry` (`colaig/rag/index_registry.py`) est le registre partagé
de **tous** les index FAISS en mémoire — workspace RAG et mémoire user.

### Chargement lazy avec double-check lock

```python
store = await registry.get_or_load("user::/ws/::alice", loader)
# → Si déjà en registry : retour immédiat
# → Si absent : loader() appelé UNE SEULE FOIS même en concurrence
#   (verrou per-clé via dict[str, asyncio.Lock])
```

### Recherche parallèle multi-index

```python
results = await registry.search_multi({
    "ws1::docs":      (query_embedding, 5),
    "ws1::behaviors": (query_embedding, 3),
    "user::/ws1/::alice": (query_embedding, 5),
})
# → asyncio.gather() sur N indexes en parallèle
# → dict[str, list[SearchResult]], clés absentes → []
```

### Convention de clés

| Usage | Format | Exemple |
|-------|--------|---------|
| Index RAG workspace | `"{path}::{name}"` | `"/espace-rh/::docs"` |
| Index behavior | `"{path}::behaviors"` | `"/espace-rh/::behaviors"` |
| Mémoire user | `"user::{path}::{safe_id}"` | `"user::/espace-rh/::alice_tchap.fr"` |
| Mémoire DM | `"user::/.colaig/personal/{id}/::alice_tchap.fr"` | |

---

## 5. FaissStore — API async et concurrence

### Modèle de concurrence

FAISS libère le GIL Python pour les opérations CPU-bound (recherche, normalisation).
`asyncio.to_thread()` exploite ce fait pour permettre la concurrence :

```
Thread principal (event loop asyncio)
    │
    ├── search_async(q1) → to_thread → [Thread pool] FAISS search ──→ résultat
    ├── search_async(q2) → to_thread → [Thread pool] FAISS search ──→ résultat
    └── search_async(q3) → to_thread → [Thread pool] FAISS search ──→ résultat
         ↑ Ces 3 recherches s'exécutent en parallèle réel (GIL libéré)
```

Les **écritures** (add, delete, rebuild) utilisent un `asyncio.Lock` exclusif
pour éviter les race conditions sur l'index.

### Résumé API

```python
# Lecture — concurrent, non-bloquant
results = await store.search_async(query_embedding, k=5)

# Écriture — sérialisée (verrou)
await store.add_async(embeddings, chunks)
deleted = await store.delete_by_source_async("/doc.pdf")
await store.rebuild_async()

# API publique (remplace accès directs à _metadata/_deleted)
store.has_deletions()         # bool
store.get_all_active_chunks() # list[DocumentChunk]
```

---

## 6. Workspace personnel (mode DM)

### Création automatique dans le resolver

```python
# resolver.py — branche DM sans workspace connu
workspace = await get_or_create_personal_workspace(storage, message.user_id)
# → /.colaig/personal/{safe_id}/ créé sur storage (idempotent)
# → enregistré dans le cache resolver immédiatement
```

### Propriétés du workspace personnel

```yaml
workspace_id: personal-alice_tchap.fr
name: "Personnel — @alice:tchap.fr"
storage_path: /.colaig/personal/alice_tchap.fr/
user_ids: ["@alice:tchap.fr"]
rag_enabled: false       # pas de RAG documentaire
storage_readonly: false  # mémoire user écrite dans ce workspace
```

### Idempotence

- Premier DM de l'utilisateur → `storage.exists(config_path)` = False → création
- DM suivants → `storage.exists(config_path)` = True → chargement direct
- Si storage indisponible à la création → workspace retourné en mémoire (sans persistance), pas d'erreur

---

## 7. UserProfile — Profil structuré

`UserProfile` est une dataclass typée (`colaig/models.py`) qui remplace les dicts bruts :

```python
@dataclass
class UserProfile:
    role: str = ""
    expertise_areas: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    active_projects: list[str] = field(default_factory=list)
    communication_style: str = ""
    consolidated_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile": ...
    def is_empty(self) -> bool: ...
```

`user_memory.load_profile()` retourne `Optional[UserProfile]` (None si absent).

---

## 8. Câblage dans main.py

```python
# Instantiation (une fois par stack client)
index_registry = FaissIndexRegistry()
user_memory = UserMemory(
    storage=storage,
    embeddings=embedding_service,
    registry=index_registry,
    albert_client=albert,
    light_model=config.albert_model_light,  # Ministral-3B pour extraction
    dimension=1024,
)

# Injection dans PreExecutionBuilder (Phase 6)
pre_exec_builder = PreExecutionBuilder(
    ...,
    index_registry=index_registry,
    user_memory=user_memory,
)

# Injection dans MessageHandler (tous modes)
handler = MessageHandler(
    ...,
    user_memory=user_memory,
)
```

---

*Implémenté le 2026-03-08. Tests : `tests/test_user_memory.py` (16 tests), `tests/test_index_registry.py` (20 tests).*
