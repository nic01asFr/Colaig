# COLAIG — Instructions Claude Code

## Identité du projet

Colaig est un assistant IA conversationnel personnel et décentralisé. C'est un "collègue virtuel" qui s'intègre dans les outils de communication et de stockage de l'utilisateur — quel que soit le provider. Il écoute via un canal de messagerie (Tchap, Slack, web chat...), accède aux documents via un backend de stockage (Nextcloud, Bigfolder, filesystem...), et répond aux questions en s'appuyant sur les documents partagés.

### Contexte administration publique française
Le cas d'usage principal reste l'administration publique française (Tchap + Nextcloud/Bnum + Albert API), mais l'architecture est **provider-agnostic** : Colaig ne sait pas et ne se soucie pas d'où viennent les fichiers ni par quel canal arrive le message.

## Principes inviolables

### Zero Database
Colaig n'utilise AUCUNE base de données propre. La persistence passe par le `StorageProtocol` :
- Documents métier → backend de stockage (WebDAV, Bigfolder, filesystem...)
- Configuration → fichiers YAML dans `/.colaig/`
- Index vectoriels → fichiers FAISS binaires dans `/.colaig/indexes/`
- Métadonnées → fichiers JSON/pickle dans `/.colaig/indexes/`
- Historiques → fichiers JSON dans `/.colaig/conversations/`
- Cache local → éphémère, reconstructible au restart

**Jamais** de PostgreSQL, SQLite, Redis, Qdrant, ChromaDB comme dépendance directe de Colaig.
(Note : Bigfolder utilise PostgreSQL en interne — c'est son affaire, pas celle de Colaig.)

### Provider-Agnostic (Storage + Messaging)
Colaig est **aveugle au provider**. Toute I/O passe par des Protocols abstraits :
- **`StorageProtocol`** — interface unique pour tout backend de stockage (WebDAV, Bigfolder API, filesystem, S3)
- **`MessagingProtocol`** — interface unique pour tout canal de communication (Matrix/Tchap, Slack, web chat, CLI)

Le code métier (RAG, indexer, resolver, generator) appelle les mêmes méthodes (`list_files`, `download`, `upload`, `send_message`) quel que soit le backend concret. L'implémentation est injectée au démarrage via `main.py`.

### Souveraineté
- LLM : Albert API uniquement (Etalab/DINUM) — endpoint `https://albert-api.etalab.gouv.fr`
- Embeddings : Albert API `/embeddings` uniquement (pas de fallback local)
- Aucune dépendance à des services cloud non-souverains (pas d'OpenAI, Anthropic API, AWS, etc.)

### Simplicité de déploiement
- Un seul container Docker (Colaig seul)
- `docker-compose up` et c'est tout
- Empreinte : 1-2 vCPU, 2-4GB RAM, 10-20GB disk
- Les services externes (Bigfolder, Nextcloud, Matrix) sont des dépendances d'infrastructure, pas des composants Colaig

## Stack technique

```
Python 3.11+
FastAPI          — API web + interface admin
matrix-nio       — Client Matrix/Tchap (implémentation MessagingProtocol)
faiss-cpu        — Index vectoriel (bibliothèque, pas un serveur)
httpx            — Client HTTP async (WebDAV, Bigfolder API, Albert API)
pyyaml           — Configuration
python-multipart — Upload fichiers
jinja2           — Templates web
uvicorn          — Serveur ASGI
mcp[cli]         — SDK MCP (Model Context Protocol) — serveur streamable HTTP
ffmpeg           — Conversion audio OGG Opus → WAV (dépendance système apt-get, pas pip)
                   Requis car Albert Whisper n'accepte que mp3/wav, Tchap envoie OGG Opus chiffré E2E
```

## Architecture

```
colaig/
├── main.py              # Point d'entrée, orchestre messaging + web + indexation
├── config.py            # Chargement config (env + YAML)
├── models.py            # Dataclasses partagées (Message, Workspace, Chunk, etc.)
├── protocols.py         # Interfaces (Protocol classes) — CONTRATS entre modules
├── exceptions.py        # Hiérarchie d'exceptions
│
├── agents/              # Pipeline multi-agent contextualisé (Phase 4)
│   ├── context_builder.py # Construction AgentContext par rôle
│   ├── analyser.py      # Analyse intention + directives ciblées (1 appel Albert)
│   ├── orchestrator.py  # Planification + exécution séquentielle (0 LLM)
│   └── synthesiser.py   # Synthèse réponse finale sourcée (1 appel Albert)
│
├── mcp/                 # Serveur MCP streamable HTTP (Phase 4)
│   └── server.py        # ColaigMCPServer — tools, resources, prompts
│
├── messaging/           # Canaux de communication (Protocol-based)
│   ├── protocol.py      # MessagingProtocol — interface abstraite
│   ├── matrix.py        # Implémentation Matrix/Tchap
│   └── handlers.py      # Routage messages → context resolver → réponse
│
├── context/             # Context Resolver (cœur logique)
│   ├── resolver.py      # Message reçu → identification workspace
│   ├── workspace.py     # Chargement/gestion des workspaces
│   └── layers.py        # Construction des 5 couches contextuelles
│
├── rag/                 # Pipeline RAG complet
│   ├── chunker.py       # Découpage documents en chunks
│   ├── embeddings.py    # Calcul embeddings (Albert API / local)
│   ├── faiss_store.py   # Index FAISS : create/load/save/search
│   ├── retriever.py     # Recherche hybride + reranking
│   ├── indexer.py       # Orchestration indexation (storage → chunks → embeddings → FAISS)
│   └── generator.py     # Construction prompt + appel Albert → réponse formatée
│
├── integrations/        # Clients externes (implémentations concrètes)
│   ├── storage/         # Implémentations StorageProtocol
│   │   ├── protocol.py  # StorageProtocol — interface abstraite
│   │   ├── webdav.py    # WebDAVStorage (Nextcloud/Bnum)
│   │   ├── bigfolder.py # BigfolderStorage (API Archivist — multi-provider)
│   │   ├── local.py     # LocalStorage (filesystem — dev/tests)
│   │   └── s3.py        # S3Storage (MinIO/S3 — optionnel)
│   └── albert.py        # Client Albert API (chat, embeddings, OCR)
│
├── storage/             # Cache en mémoire
│   └── cache.py         # Cache in-memory avec TTL
│
├── web/                 # Interface admin
│   ├── routes.py        # Routes FastAPI
│   └── templates/       # Jinja2 + HTMX
│
└── utils/
    ├── logging.py       # Config logging structuré
    └── text.py          # Helpers extraction texte (PDF, DOCX, etc.)
```

## Couche d'abstraction Storage

Le code métier n'importe jamais `webdav.py` ou `bigfolder.py` directement. Il utilise `StorageProtocol` :

```python
# Ce que le code métier voit — toujours la même interface
storage: StorageProtocol
files = await storage.list_files("/espace-projet/documents/")
content = await storage.download("/espace-projet/documents/guide.pdf")
await storage.upload("/espace-projet/.colaig/indexes/docs.faiss", index_bytes)
```

Ce qui est derrière dépend de la configuration au démarrage :
- `STORAGE_BACKEND=webdav` → `WebDAVStorage` parle à Nextcloud directement
- `STORAGE_BACKEND=bigfolder` → `BigfolderStorage` parle à l'API Archivist (qui gère OneDrive, Box, S3, WebDAV...)
- `STORAGE_BACKEND=local` → `LocalStorage` lit/écrit sur le filesystem (dev, tests)
- `STORAGE_BACKEND=s3` → `S3Storage` parle à MinIO/S3

## Couche d'abstraction Messaging

Le code métier n'importe jamais `matrix.py` directement. Il utilise `MessagingProtocol` :

```python
# Ce que le code métier voit — toujours la même interface
messaging: MessagingProtocol
await messaging.send("conversation-123", "Voici la réponse...")
await messaging.send_typing("conversation-123")
```

Implémentations possibles :
- `MESSAGING_BACKEND=matrix` → Matrix/Tchap (cas principal administration)
- `MESSAGING_BACKEND=slack` → Slack (entreprise privée)
- `MESSAGING_BACKEND=webchat` → WebSocket via FastAPI (interface web intégrée)

## Conventions de code

### Style
- Python 3.11+ avec type hints partout
- `async/await` pour toute I/O (storage, Albert API, messaging)
- Dataclasses pour les modèles de données, Protocol pour les interfaces
- Docstrings Google style
- Logging structuré via `structlog` ou `logging` standard
- Noms en anglais dans le code, commentaires en français OK

### Gestion d'erreurs
- Exceptions custom dans chaque module (`class StorageError(ColaigError)`)
- Jamais de `except Exception: pass` — toujours logger
- Graceful degradation : si le storage est down, le bot répond "service temporairement indisponible"
- Retry avec backoff exponentiel sur les appels réseau (Albert API, storage, messaging)

### Tests
- `pytest` + `pytest-asyncio`
- Tests unitaires par module dans `tests/test_<module>.py`
- `LocalStorage` comme backend de test (pas besoin de mock WebDAV complexe)
- Un `tests/conftest.py` avec fixtures partagées
- Minimum 80% de couverture sur les modules critiques (context, rag)

### Fichiers interdits
- Aucun fichier `database.py`, `db.py`, `orm.py`
- Aucun import de `sqlalchemy`, `sqlite3`, `psycopg2`, `qdrant_client`, `chromadb`
- Aucun `docker-compose.yml` avec service `postgres`, `redis`, `qdrant` comme dépendance Colaig

## Interfaces critiques (protocols.py)

Tous les modules communiquent via des Protocol classes définies dans `colaig/protocols.py`. Un agent qui implémente un module DOIT respecter le Protocol correspondant. Un agent qui consomme un module ne dépend QUE du Protocol, jamais de l'implémentation concrète.

Les 3 Protocols fondamentaux :
- **`StorageProtocol`** — accès fichiers (list, download, upload, delete, exists, mkdir)
- **`MessagingProtocol`** — communication (listen, send, send_typing)
- **`AlbertClientProtocol`** — LLM (chat, embed, embed_batch)

## Flux principal

### Phase 1 (generator seul — backward compatible)

```
1. Message arrive via MessagingProtocol (Tchap, Slack, web...)
2. handlers.py extrait user_id, conversation_id, message
3. context/resolver.py identifie le workspace (ou mode chatbot/personnel)
4. context/layers.py construit les 5 couches contextuelles
5. rag/retriever.py cherche les documents pertinents dans l'index FAISS du workspace
6. rag/generator.py construit le prompt et appelle Albert API
7. handlers.py formate et envoie la réponse via MessagingProtocol
```

### Phase 2 (pipeline agents — si `COLAIG_AGENTS_ENABLED=true`)

```
1. Message arrive via MessagingProtocol (ou MCP tool colaig_ask)
2. handlers.py extrait user_id, conversation_id, message
3. context/resolver.py identifie le workspace
4. THINKING   : agents/analyser.py analyse l'intention → Intent + AgentDirectives
5. RETRIEVING : agents/orchestrator.py exécute le plan (RAG, storage, tools) → ExecutionPlan
6. SYNTHESIZING : agents/synthesiser.py formule la réponse → GeneratedResponse + ContextCard
7. COMPLETE   : handlers.py envoie la réponse via MessagingProtocol
```

### MCP (si `COLAIG_MCP_ENABLED=true`)

Le endpoint `/mcp` expose Colaig comme serveur MCP streamable HTTP. Tout client MCP peut appeler `colaig_ask`, `colaig_search`, `colaig_list_workspaces`, `colaig_reindex`.

## Variables d'environnement requises

```bash
# === Backends (choix du provider) ===
STORAGE_BACKEND=webdav          # webdav | bigfolder | local | s3
MESSAGING_BACKEND=matrix        # matrix | slack | webchat

# === Storage : WebDAV (si STORAGE_BACKEND=webdav) ===
WEBDAV_URL=https://bnum.din.gouv.fr/remote.php/dav/files/colaig/
WEBDAV_USERNAME=colaig
WEBDAV_PASSWORD=xxx

# === Storage : Bigfolder (si STORAGE_BACKEND=bigfolder) ===
BIGFOLDER_API_URL=http://localhost:8002
BIGFOLDER_API_KEY=ark_xxxxx
BIGFOLDER_WORKSPACE_ID=xxx

# === Storage : Local (si STORAGE_BACKEND=local) ===
LOCAL_STORAGE_PATH=/app/data/storage

# === Messaging : Matrix/Tchap (si MESSAGING_BACKEND=matrix) ===
MATRIX_HOMESERVER=https://matrix.agent.tchap.gouv.fr
MATRIX_USERNAME=@colaig:agent.tchap.gouv.fr
MATRIX_PASSWORD=xxx

# === LLM (toujours Albert API) ===
ALBERT_API_URL=https://albert-api.etalab.gouv.fr
ALBERT_API_KEY=xxx
ALBERT_MODEL_CHAT=AgentPublic/llama3-instruct
ALBERT_MODEL_EMBED=BAAI/bge-m3

# === Application ===
COLAIG_LOG_LEVEL=INFO
COLAIG_DATA_DIR=/app/data

# === Pipeline agents (Phase 2) ===
COLAIG_AGENTS_ENABLED=false        # true pour activer le pipeline multi-agent
COLAIG_MCP_ENABLED=false           # true pour activer le serveur MCP
COLAIG_ANALYSER_TEMPERATURE=0.1    # Température LLM de l'Analyseur
COLAIG_SYNTHESISER_TEMPERATURE=0.3 # Température LLM du Synthétiseur

# === Phase 5 : Orchestrateur agentique + Mémoire sémantique ===
COLAIG_ORCHESTRATOR_MAX_ITERATIONS=5     # Max itérations boucle agentique (défaut : 5)
COLAIG_ORCHESTRATOR_TEMPERATURE=0.1      # Température LLM de l'Orchestrateur agentique
COLAIG_CONVERSATION_MEMORY_MAX_STORED=100    # Max messages stockés par conversation
COLAIG_CONVERSATION_MEMORY_MAX_RETRIEVED=10  # Max messages récupérés par requête sémantique
COLAIG_ANALYSER_USE_TOOL_CALLING=false   # Activer mode tool calling structuré pour l'Analyseur

# === Administration réflexive + droits (owners) ===
COLAIG_ADMIN_USER_IDS=@alice:tchap.fr,@ops:agent.gouv.fr  # admins globaux (config en DM)
# owners par workspace : champ owners: dans .colaig/config.yaml (créateur = owner)

# === Ops / Observabilité ===
# /live, /ready (teste storage+LLM, 503 si KO), /metrics (JSON), /metrics/prometheus
# request_id : middleware (x-request-id / traceparent W3C) → structlog.contextvars

# === Robustesse / Backends ===
S3_SESSION_TOKEN=                          # credentials STS temporaires (SSP Cloud/MinIO)
COLAIG_LOCAL_EMBEDDINGS=false              # fallback embeddings local (SentenceTransformer)

# === Auto-spécialisation (opt-in) ===
COLAIG_AUTO_SPECIALIZE_ENABLED=false       # dérive persona/vocabulaire du corpus (dry-run)
COLAIG_AUTO_SPECIALIZE_APPLY=false         # écrit la config dérivée (sinon knowledge.json seul)
```

Voir aussi `docs/REFLEXIF_ET_OPS.md` (admin réflexive, ops, sécurité, auto-spé).

## Commandes

```bash
# Développement
pip install -e ".[dev]"
pytest
python -m colaig.main

# Production
docker build -t colaig .
docker-compose up -d
```
