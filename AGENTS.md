# COLAIG — Équipe Claude Code

## Vue d'ensemble

4 agents travaillent en 3 phases séquentielles. Chaque agent a la propriété exclusive de ses fichiers — aucun chevauchement.

```
PHASE 0 ──── Agent SOCLE ─────────────────────────────────────────────
             (scaffolding, config, protocols, Docker, CI)
             BLOQUE les autres — doit finir d'abord

PHASE 1 ──── Agent CONNEXIONS ──────── Agent RAG ────────────────────
             (storage backends,          (chunker, embeddings, faiss,
              albert, messaging)          retriever, indexer)
             EN PARALLÈLE

PHASE 2 ──── Agent CERVEAU ──────────────────────────────────────────
             (context resolver, generator, handlers, main, web)
             ASSEMBLE tout, tests d'intégration, documentation

PHASE 3 ──── Agent ABSTRACTION ──────────────────────────────────────
             (StorageProtocol refactor, BigfolderStorage, LocalStorage,
              MessagingProtocol refactor, migration tests)
```

---

## Agent SOCLE — Fondations

### Mission
Créer le squelette du projet, les interfaces partagées, la configuration, et l'infrastructure Docker. Tout le monde dépend de ce travail.

### Fichiers sous sa responsabilité
```
colaig/
├── __init__.py
├── config.py              # Chargement configuration (env + YAML + defaults)
├── models.py              # TOUTES les dataclasses partagées
├── protocols.py           # TOUTES les Protocol classes (contrats inter-modules)
├── exceptions.py          # Hiérarchie d'exceptions
├── bot/__init__.py
├── context/__init__.py
├── rag/__init__.py
├── integrations/__init__.py
├── storage/__init__.py
├── storage/cache.py       # Cache in-memory avec TTL
├── web/__init__.py
├── utils/__init__.py
├── utils/logging.py       # Configuration logging structuré
├── utils/text.py          # Extraction texte (PDF, DOCX, ODT, TXT, MD)
config/
├── default.yml            # Configuration par défaut
├── .env.example           # Template variables d'environnement
tests/
├── __init__.py
├── conftest.py            # Fixtures partagées (fake WebDAV, fake Albert, etc.)
Dockerfile
docker-compose.yml
requirements.txt
pyproject.toml
README.md
```

### Critères de validation
- [ ] `pip install -e .` fonctionne sans erreur
- [ ] `python -c "from colaig.models import *; from colaig.protocols import *"` OK
- [ ] `docker build .` produit une image fonctionnelle
- [ ] `pytest tests/` passe (même si vide, la structure est là)
- [ ] Chaque Protocol class a une docstring claire avec exemples d'usage
- [ ] `config.py` charge env → YAML → defaults avec validation Pydantic ou dataclass

### Livrables critiques

#### models.py — Les dataclasses que TOUT LE MONDE utilise
```python
# Minimum requis dans models.py :
@dataclass
class ColaigConfig: ...          # Config globale de l'instance
@dataclass
class WorkspaceConfig: ...       # Config d'un workspace (.colaig/config.yaml)
@dataclass
class IncomingMessage: ...       # Message reçu (user_id, conversation_id, body, conversation_type, ...)
@dataclass
class WorkspaceContext: ...      # Contexte résolu (workspace + 5 couches)
@dataclass
class DocumentChunk: ...         # Chunk de document (text, metadata, embedding?)
@dataclass
class SearchResult: ...          # Résultat de recherche RAG (chunk, score, source)
@dataclass
class GeneratedResponse: ...     # Réponse générée (text, sources, confidence)
@dataclass
class StorageFile: ...           # Fichier dans le storage (path, etag, size, last_modified)
```

#### protocols.py — Les contrats que CHAQUE MODULE implémente
```python
# Minimum requis dans protocols.py :
# --- Couches d'abstraction fondamentales ---
class StorageProtocol(Protocol): ...          # list_files, download, upload, mkdir, exists, delete
class MessagingProtocol(Protocol): ...        # connect, run, send, send_typing, on_message
class LLMClientProtocol(Protocol): ...     # chat, embed, embed_batch

# --- RAG Pipeline ---
class EmbeddingServiceProtocol(Protocol): ... # embed_text, embed_texts
class VectorStoreProtocol(Protocol): ...      # add, search, save, load, delete
class ChunkerProtocol(Protocol): ...          # chunk_document
class RetrieverProtocol(Protocol): ...        # retrieve

# --- Contexte et génération ---
class ContextResolverProtocol(Protocol): ...  # resolve
class GeneratorProtocol(Protocol): ...        # generate
```

---

## Agent CONNEXIONS — Clients Externes

### Mission
Implémenter les backends de stockage, le client LLM, et les backends de messagerie. Chaque implémentation respecte le Protocol correspondant (StorageProtocol, LLMClientProtocol, MessagingProtocol).

### Fichiers sous sa responsabilité
```
colaig/
├── integrations/
│   ├── storage/
│   │   ├── webdav.py      # WebDAVStorage (implémente StorageProtocol)
│   │   ├── local.py       # LocalStorage (implémente StorageProtocol)
│   │   ├── bigfolder.py   # BigfolderStorage (implémente StorageProtocol)
│   │   └── s3.py          # S3Storage (implémente StorageProtocol)
│   └── albert.py          # Client Albert API complet
├── messaging/
│   └── matrix.py          # MatrixMessaging (implémente MessagingProtocol)
tests/
├── test_webdav_storage.py
├── test_local_storage.py
├── test_albert.py
├── test_matrix_messaging.py
```

### Spécifications détaillées

#### storage/webdav.py — WebDAVStorage
```
Implémente : StorageProtocol
Bibliothèque : httpx (async)
Protocole : WebDAV (HTTP PROPFIND, GET, PUT, DELETE, MKCOL)

Méthodes requises (interface StorageProtocol) :
- list_files(path, recursive?) → List[StorageFile]    # PROPFIND
- download(path) → bytes                               # GET fichier
- upload(path, content) → None                          # PUT fichier
- mkdir(path) → None                                    # MKCOL
- exists(path) → bool                                   # HEAD
- get_etag(path) → str                                  # PROPFIND etag uniquement
- download_if_changed(path, known_etag) → Optional[bytes]  # Conditionnel
- delete(path) → None                                   # DELETE

Spécificités :
- Authentification Basic HTTP
- Retry avec backoff sur 429/503
- Timeout configurable (default 30s)
- Gestion propre des erreurs 404, 401, 507
- Logging structuré de chaque opération
```

#### storage/local.py — LocalStorage
```
Implémente : StorageProtocol
Bibliothèque : aiofiles / pathlib

Lit/écrit directement sur le filesystem local.
Utilisé pour le développement, les tests, et les démos.

Le paramètre base_path définit le répertoire racine.
Les chemins relatifs (ex: "/espace/doc.pdf") sont résolus par rapport à base_path.
L'etag est calculé à partir du hash SHA256 du contenu.
```

#### storage/bigfolder.py — BigfolderStorage
```
Implémente : StorageProtocol
Bibliothèque : httpx (async)
Endpoint : API REST Archivist (Bigfolder)

Parle à l'API Bigfolder pour accéder aux documents.
Bigfolder gère la complexité multi-provider (OneDrive, Box, WebDAV, S3...).
Colaig voit un seul espace de fichiers unifié.

Pour les fichiers internes (.colaig/), utilise un sous-backend configurable
(local ou WebDAV) car Bigfolder ne gère pas les fichiers techniques.
```

#### albert.py — AlbertClient
```
Implémente : LLMClientProtocol
Bibliothèque : httpx (async)
Endpoint : https://albert.api.etalab.gouv.fr

Méthodes requises :
- chat(messages, model?, temperature?, max_tokens?) → str
- chat_stream(messages, ...) → AsyncIterator[str]     # Streaming
- embed(text) → List[float]                           # Un seul texte
- embed_batch(texts, batch_size=32) → List[List[float]]  # Batch
- ocr(file_bytes, mime_type) → str                    # Si disponible

Spécificités :
- Header : Authorization: Bearer {ALBERT_API_KEY}
- Format OpenAI-compatible (/v1/chat/completions, /v1/embeddings)
- Retry avec backoff sur 429 (rate limit)
- Fallback SentenceTransformer local si embed échoue
- Timeout 60s pour chat, 30s pour embed
```

#### messaging/matrix.py — MatrixMessaging
```
Implémente : MessagingProtocol
Bibliothèque : matrix-nio (AsyncClient)

Méthodes requises (interface MessagingProtocol) :
- connect() → None                                    # Login + sync initial
- run() → None                                        # Boucle d'écoute infinie
- send(conversation_id, text) → None                  # Envoi texte formaté
- send_typing(conversation_id) → None                 # Indicateur frappe
- on_message(callback) → None                         # Enregistre handler

Spécificités :
- Auto-join quand invité dans un salon
- Réagit aux mentions @colaig ET aux DMs
- Ignore ses propres messages
- Gestion reconnexion automatique
- Mapping : conversation_id = room_id Matrix
- Mapping : conversation_type déduit de room properties
```

### Critères de validation
- [ ] Chaque implémentation a des tests unitaires avec mocks httpx/matrix
- [ ] Chaque implémentation gère les erreurs réseau gracieusement
- [ ] Chaque implémentation respecte fidèlement son Protocol
- [ ] `test_webdav_storage.py` : mock PROPFIND/GET/PUT → assertions sur parsing XML → retourne StorageFile
- [ ] `test_local_storage.py` : tmpdir → create/read/list/delete fichiers → assertions correctes
- [ ] `test_albert.py` : mock /v1/chat/completions → assertion réponse parsée
- [ ] `test_matrix_messaging.py` : mock nio events → assertion handlers appelés avec IncomingMessage

---

## Agent RAG — Pipeline de Recherche Documentaire

### Mission
Implémenter le pipeline RAG complet : du document brut à la recherche sémantique. L'Agent RAG ne connaît PAS le contexte Tchap — il travaille avec des fichiers et des requêtes textuelles.

### Fichiers sous sa responsabilité
```
colaig/
├── rag/
│   ├── chunker.py         # Découpage intelligent
│   ├── embeddings.py      # Service d'embeddings
│   ├── faiss_store.py     # Gestion complète index FAISS
│   ├── retriever.py       # Recherche hybride + reranking MMR
│   └── indexer.py         # Orchestration indexation complète
tests/
├── test_chunker.py
├── test_embeddings.py
├── test_faiss_store.py
├── test_retriever.py
├── test_indexer.py
├── fixtures/              # Fichiers test (petit PDF, DOCX, TXT)
```

### Spécifications détaillées

#### chunker.py
```
Implémente : ChunkerProtocol

Stratégies selon type de document :
- Markdown : split par sections (titres #)
- PDF/DOCX texte : split par paragraphes avec overlap
- Texte brut : sliding window (800 tokens, overlap 100)
- Tableau : chaque ligne = un chunk avec headers

Chaque chunk → DocumentChunk(text, metadata={source, page, section, position})
Overlap configurable. Chunks trop courts fusionnés. Chunks trop longs re-découpés.
```

#### embeddings.py
```
Implémente : EmbeddingServiceProtocol
Utilise : LLMClientProtocol (injection)

- embed_text(text) → List[float]
- embed_texts(texts) → List[List[float]]  # Batch avec gestion rate limit

Cache d'embeddings en mémoire (dict hash_text → embedding).
Fallback SentenceTransformer("BAAI/bge-m3") si Albert indisponible.
Normalisation L2 des vecteurs pour similarité cosinus.
```

#### faiss_store.py — LE COMPOSANT CRITIQUE
```
Implémente : VectorStoreProtocol

Opérations :
- create(dimension) → crée un index vide (IndexFlatIP pour cosinus)
- add(embeddings, metadata_list) → ajoute des vecteurs avec métadonnées
- search(query_embedding, k) → List[SearchResult] avec scores
- save(path) → sérialise index (.faiss) + métadonnées (.pkl) sur disque
- load(path) → désérialise depuis disque
- save_to_webdav(webdav_client, remote_path) → upload sérialisé
- load_from_webdav(webdav_client, remote_path) → download + désérialise
- delete_by_source(source_path) → supprime tous les chunks d'un document
- count() → nombre de vecteurs dans l'index
- rebuild() → reconstruction complète (après suppressions)

Stockage métadonnées :
- Dict[int, DocumentChunk] indexé par position FAISS
- Sérialisé en pickle (.pkl) à côté du .faiss
- Contient : texte du chunk, source, page, date, hash du document source

IMPORTANT :
- faiss.IndexFlatIP (Inner Product) avec vecteurs normalisés = cosinus
- Score = produit scalaire (0→1, plus élevé = plus similaire)
- Pas d'IndexIVF pour la Phase 1 (pas assez de données pour justifier)
```

#### retriever.py
```
Implémente : RetrieverProtocol
Utilise : VectorStoreProtocol + EmbeddingServiceProtocol

- retrieve(query, workspace_path, k=5) → List[SearchResult]

Pipeline :
1. Embed la query
2. Search FAISS (k * 2 résultats candidats)
3. Reranking MMR (Maximum Marginal Relevance) pour diversité
4. Boost métier : documents récents +20%, même service +30%
5. Filtrage par score minimum (threshold configurable)
6. Retourne top-k résultats finaux
```

#### indexer.py
```
Utilise : WebDAVClientProtocol + ChunkerProtocol + EmbeddingServiceProtocol + VectorStoreProtocol

Orchestration :
- index_workspace(workspace_path) → indexe tous les documents
- index_document(doc_path, workspace_path) → indexe un document
- check_updates(workspace_path) → compare etags, ré-indexe si changé
- reindex_workspace(workspace_path) → reconstruction complète

Gestion incrémentale :
- Stocke hash + etag de chaque document indexé
- Compare au scan WebDAV suivant
- N'indexe que les documents modifiés/nouveaux
- Supprime les entrées des documents supprimés
```

### Critères de validation
- [ ] `test_faiss_store.py` : create → add 100 vecteurs → search → résultats corrects → save → load → re-search identique
- [ ] `test_chunker.py` : document markdown → chunks cohérents avec metadata
- [ ] `test_retriever.py` : query → résultats ordonnés par pertinence → MMR diversifie
- [ ] `test_indexer.py` : mock WebDAV avec 3 docs → index créé → 1 doc modifié → ré-indexation incrémentale
- [ ] Aucune dépendance à Matrix, Tchap, ou au contexte conversationnel

---

## Agent CERVEAU — Assemblage & Intelligence

### Mission
Implémenter le Context Resolver (cœur logique), le générateur de réponses, les handlers de messages, le main.py qui orchestre tout (y compris la factory des backends), et l'interface web admin. C'est l'agent qui assemble les pièces des 3 autres agents.

### Fichiers sous sa responsabilité
```
colaig/
├── main.py                # Point d'entrée — factory backends + orchestre messaging + web + indexation
├── context/
│   ├── resolver.py        # Message → workspace (utilise StorageProtocol)
│   ├── workspace.py       # Chargement workspace (utilise StorageProtocol)
│   └── layers.py          # Construction des 5 couches contextuelles
├── rag/
│   └── generator.py       # Prompt engineering + appel Albert + formatage réponse
├── messaging/
│   └── handlers.py        # Réception message → resolver → retriever → generator → réponse
├── web/
│   ├── routes.py          # Dashboard admin FastAPI
│   └── templates/         # HTMX pages
tests/
├── test_resolver.py
├── test_workspace.py
├── test_generator.py
├── test_handlers.py
├── test_integration.py    # Tests end-to-end avec tous les mocks
```

### Spécifications détaillées

#### Pipeline Phase 1 (simple)
```
Message → Resolver → Retriever → Generator → Réponse
                                     (1 seul appel Albert)
```
Le pipeline multi-agent (Analyseur → Orchestrateur → Synthétiseur) est
préparé dans protocols.py et models.py mais NON implémenté en Phase 1.
Les fichiers agents/analyser.py, agents/orchestrator.py, agents/synthesiser.py
seront implémentés en Phase 2 quand les MCP tools le justifieront.

#### context/resolver.py — LE CERVEAU
```
Implémente : ContextResolverProtocol
Utilise : StorageProtocol

resolve(message: IncomingMessage) → WorkspaceContext

Algorithme :
1. Lister tous les workspaces connus (scan /.colaig/ via StorageProtocol)
2. Chercher si conversation_id est mappé à un workspace (dans config.yaml de chaque workspace)
3. Si trouvé → charger ce workspace
4. Sinon :
   a. Si conversation groupe → mode CHATBOT (workspace par défaut, explique Colaig)
   b. Si DM → mode PERSONNEL (agrège tous les workspaces partagés avec cet user)
   c. Si groupe non mappé → mode CHATBOT + invitation à configurer
5. Construire WorkspaceContext avec les 5 couches

Cache des mappings en mémoire (invalidé périodiquement via etag StorageProtocol).
```

#### context/workspace.py
```
- load_workspace(storage, path) → WorkspaceConfig
- list_workspaces(storage) → List[WorkspaceConfig]
- find_workspace_for_conversation(conversation_id) → Optional[WorkspaceConfig]
- find_workspaces_for_user(user_id) → List[WorkspaceConfig]
- create_default_workspace() → WorkspaceConfig  # Mode chatbot
```

#### context/layers.py — Les 5 couches contextuelles
```
build_context(workspace, message, history) → WorkspaceContext

Couche 1 - Comportement : system prompt, ton, expertise (depuis config.yaml)
Couche 2 - Capacités : outils disponibles (depuis config.yaml capabilities)
Couche 3 - Conversation : derniers N messages de l'historique
Couche 4 - Connaissances : résultats RAG pertinents
Couche 5 - Profil : infos utilisateur connues
```

#### rag/generator.py
```
Implémente : GeneratorProtocol
Utilise : LLMClientProtocol

generate(query, context: WorkspaceContext, search_results: List[SearchResult]) → GeneratedResponse

1. Construit le prompt système depuis les couches contextuelles
2. Injecte les résultats RAG comme contexte documentaire
3. Appelle Albert API /v1/chat/completions
4. Post-traitement : extraction sources, formatage Markdown pour Tchap
5. Retourne GeneratedResponse(text, sources, confidence)
```

#### messaging/handlers.py
```
handle_message(message: IncomingMessage) → None

Pipeline Phase 1 (simple, 1 appel Albert) :
1. Log message reçu
2. Envoyer indicateur de frappe (typing ON) via MessagingProtocol
3. Résoudre contexte (ContextResolverProtocol)
4. Si workspace avec RAG → rechercher (RetrieverProtocol)
5. Générer réponse (GeneratorProtocol) — 1 seul appel Albert
6. Envoyer réponse via MessagingProtocol
7. Typing OFF + sauvegarder historique (StorageProtocol)

Phase 2+ : remplacer étapes 4-5 par AnalyserProtocol → OrchestratorProtocol → SynthesiserProtocol
```

#### main.py
```
async def main():
    # 1. Charger config (STORAGE_BACKEND, MESSAGING_BACKEND, etc.)
    # 2. Instancier le StorageProtocol concret (factory pattern)
    # 3. Instancier le MessagingProtocol concret (factory pattern)
    # 4. Instancier Albert client
    # 5. Initialiser composants (resolver, retriever, generator)
    # 6. Lancer indexation initiale des workspaces
    # 7. Lancer messaging (boucle d'écoute)
    # 8. Lancer serveur FastAPI (interface admin)
    # 9. Lancer tâche périodique de ré-indexation

Utilise asyncio.gather() pour exécuter messaging + web + indexation en parallèle.

Factory pattern pour les backends :
  storage = create_storage(config)     # → WebDAVStorage | LocalStorage | BigfolderStorage
  messaging = create_messaging(config) # → MatrixMessaging | SlackMessaging | ...
```

### Critères de validation
- [ ] `test_resolver.py` : message salon mappé → bon workspace ; DM → mode personnel ; salon inconnu → mode chatbot
- [ ] `test_generator.py` : context + search results → prompt bien formé → réponse avec sources
- [ ] `test_handlers.py` : message mock → pipeline complet → réponse envoyée
- [ ] `test_integration.py` : scénario complet avec tous les mocks assemblés
- [ ] `main.py` démarre sans erreur avec configuration valide

---

## Règles de collaboration inter-agents

### Dépendances strictes
- Agent SOCLE doit terminer AVANT que CONNEXIONS et RAG commencent
- Agent CERVEAU commence quand CONNEXIONS et RAG ont terminé leurs Protocols
- L'Agent CERVEAU peut commencer le resolver pendant que RAG finit l'indexer

### Contrat d'interface
- TOUT passe par `protocols.py` — jamais d'import direct d'implémentation
- Exemple : `handlers.py` importe `RetrieverProtocol`, pas `retriever.py`
- L'injection de dépendances se fait dans `main.py`

### Convention de nommage des branches
```
socle/initial-scaffolding
socle/protocols-and-models
connexions/webdav-storage
connexions/local-storage
connexions/albert-client
connexions/matrix-messaging
rag/chunker
rag/faiss-store
rag/retriever-indexer
cerveau/context-resolver
cerveau/generator-handlers
cerveau/main-assembly
cerveau/web-admin
abstraction/storage-protocol
abstraction/messaging-protocol
abstraction/bigfolder-storage
abstraction/migration-tests
```

### Merge order
```
1. socle/initial-scaffolding → main
2. socle/protocols-and-models → main
3. connexions/* → main (parallel merge)
4. rag/* → main (parallel merge)
5. cerveau/context-resolver → main
6. cerveau/generator-handlers → main
7. cerveau/main-assembly → main
8. cerveau/web-admin → main
9. abstraction/storage-protocol → main
10. abstraction/messaging-protocol → main
11. abstraction/bigfolder-storage → main
```
