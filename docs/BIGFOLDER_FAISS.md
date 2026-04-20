# Bigfolder : pgvector → FAISS — Analyse et feuille de route

## Contexte

Bigfolder (Archivist) utilise actuellement PostgreSQL + pgvector pour la recherche vectorielle. Colaig utilise FAISS (fichiers binaires stockés via StorageProtocol). Cette analyse évalue la pertinence d'aligner Bigfolder sur le modèle FAISS de Colaig.

## État actuel : pgvector dans Archivist

### Couplage identifié

| Composant | Dépendance pgvector |
|-----------|---------------------|
| `Document.embedding` | Colonne `Vector(1536)` fixe |
| Index HNSW | `USING hnsw (embedding vector_cosine_ops)` |
| RAG pipeline (`rag.py`) | SQL brut avec opérateur `<=>` + filtres combinés |
| Isolation multi-tenant | `WHERE tenant_id = :tenant_id` dans la même requête |
| Celery workers | Queue `ai` dédiée aux embeddings → PostgreSQL |
| Dimensions | Hard-codées à 1536, padding/truncation automatique |

### Pipeline actuel

```
Document upload → Celery AI worker → litellm.embedding() → Vector(1536) en DB
Query → embedding query → SQL (similarity + filtres) → résultats triés
```

---

## Analyse : FAISS vs pgvector pour Bigfolder

### Arguments POUR basculer en FAISS

| Avantage | Explication |
|----------|-------------|
| **Responsabilité des données** | L'index vectoriel vit AVEC les documents dans le workspace — pas dans une DB centralisée |
| **Isolation physique** | Chaque workspace a son propre fichier `.faiss` — isolation réelle, pas juste un `WHERE tenant_id` |
| **Portabilité** | Un workspace peut être sauvegardé/déplacé comme un dossier complet (docs + index) |
| **Alignement avec Colaig** | Même modèle architectural — les deux projets parlent le même "langage" vectoriel |
| **Déploiement simplifié** | Plus besoin de pgvector dans PostgreSQL pour les vecteurs |
| **Flexibilité dimensions** | Chaque workspace peut utiliser un modèle d'embedding différent sans migration de schéma |

### Arguments CONTRE

| Inconvénient | Explication |
|--------------|-------------|
| **Refactoring massif** | Le RAG pipeline est construit autour de SQL + pgvector |
| **Perte des filtres SQL combinés** | Aujourd'hui : vector search + filtres (date, catégorie, status) = 1 requête. Avec FAISS : pré/post-filtrage |
| **Concurrence** | PostgreSQL gère les accès concurrents nativement. FAISS nécessite un gestionnaire de verrous |
| **Mémoire** | FAISS charge les index en RAM. Avec 100+ workspaces actifs, ça pèse |
| **Investissement perdu** | Le pipeline pgvector est complet et fonctionne |

---

## Recommandation : approche hybride progressive

### Principe

PostgreSQL reste pour les données relationnelles (métadonnées, tenants, OAuth). FAISS remplace pgvector pour la recherche vectorielle uniquement.

### Architecture cible

```
Avant (actuel) :
  Document → embedding (Vector(1536)) → pgvector HNSW → SQL search

Après (proposé) :
  Document → embedding → FAISS file dans workspace → in-memory search
  Document → metadata → PostgreSQL → SQL filters (sans vecteurs)
```

La recherche devient 2 étapes :
1. **FAISS** : top-K par similarité vectorielle (en mémoire)
2. **Post-filtre Python** : appliquer les filtres métier sur les résultats

C'est exactement ce que Colaig fait avec son `retriever.py`.

### Structure workspace

```
Workspace Bigfolder (auto-contenu)
├── documents/              ← Fichiers métier
├── .colaig/                ← Données Colaig (si connecté)
│   └── indexes/
│       └── documents.faiss
└── .archivist/             ← Données Bigfolder internes
    ├── indexes/
    │   └── documents.faiss ← Index vectoriel Bigfolder (MÊME format)
    └── metadata.json
```

Les deux projets utilisent le même format d'index. À terme, ils pourraient **partager** le même index FAISS — évitant le double embedding.

---

## Feuille de route

| Phase | Action | Effort |
|-------|--------|--------|
| **1. Immédiat** | Ajouter les routes API fichiers pour Colaig dans Bigfolder | ~100 lignes |
| **2. Abstraction** | Créer `VectorStoreProtocol` interne dans Bigfolder (interface abstraite) | ~200 lignes |
| **3. Backend FAISS** | Implémenter un backend FAISS derrière cette abstraction | ~500 lignes |
| **4. Migration** | Migrer workspace par workspace de pgvector vers FAISS | Config |
| **5. Unification** | Potentiellement unifier l'index entre Colaig et Bigfolder | Recherche |

### Phase 1 : prioritaire (pas de changement pgvector)

Bigfolder expose des endpoints fichier REST au niveau workspace. Colaig peut lire/écrire via `BigfolderStorage`. pgvector reste inchangé.

### Phases 2-3 : abstraction + backend FAISS

Bigfolder introduit une abstraction vectorielle interne :

```python
class VectorStoreProtocol(Protocol):
    def add(self, embeddings, metadata) -> None: ...
    def search(self, query_embedding, k) -> list: ...
    def save(self) -> None: ...
    def load(self) -> None: ...

# Backends :
# - PgvectorStore (actuel, par défaut)
# - FaissFileStore (nouveau, stocke dans le workspace)
```

### Phase 4 : migration progressive

Configuration par workspace :
```yaml
# config workspace
vector_backend: pgvector   # ou "faiss"
```

Migration transparente : réindexer un workspace bascule ses vecteurs de pgvector vers FAISS.

### Phase 5 : index partagé Colaig/Bigfolder

Si les deux utilisent FAISS au même emplacement (`.indexes/documents.faiss`), Colaig pourrait réutiliser l'index de Bigfolder directement — zéro double embedding, zéro double stockage.
