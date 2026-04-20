# Colaig v3 — Assistant IA personnel, provider-agnostic

**Colaig** est un assistant IA conversationnel personnel et decentralise. Il s'integre dans les outils de communication et de stockage de l'utilisateur — quels que soient les providers.

> *"Inviter Colaig, c'est comme inviter un collegue."*

## Principe

On donne a Colaig un acces a ses documents (Nextcloud, OneDrive, filesystem...), on lui parle via son canal de messagerie (Tchap, Slack, web chat...), et il repond aux questions en s'appuyant sur les documents partages — avec citation des sources.

## Provider-Agnostic

Colaig est **aveugle au provider**. Le code metier (RAG, contexte, reponses) utilise des interfaces abstraites. L'implementation concrete est choisie a la configuration :

| Couche | Options disponibles |
|--------|---------------------|
| **Storage** | WebDAV (Nextcloud), Bigfolder (multi-provider), Filesystem local, S3 |
| **Messaging** | Matrix/Tchap, Slack (futur), Web chat (futur) |
| **LLM** | Albert API (souverain, Etalab/DINUM) |

## Architecture

```
Zero Database — Persistence via StorageProtocol
├── Documents metier    → backend de stockage (WebDAV, Bigfolder, local...)
├── Configuration       → .colaig/config.yaml
├── Index vectoriels    → .colaig/indexes/*.faiss + *.pkl
├── Historiques         → .colaig/conversations/*.json
└── Cache local         → ephemere (perdu au restart, OK)
```

**Un seul container Docker.** Pas de base de donnees dans Colaig.

## Stack

| Composant | Technologie |
|-----------|-------------|
| LLM | Albert API (souverain, Etalab/DINUM) |
| Embeddings | Albert API + fallback SentenceTransformer |
| Vector store | FAISS (fichiers via StorageProtocol) |
| Messaging | MessagingProtocol (Matrix/Tchap, Slack, web chat) |
| Storage | StorageProtocol (WebDAV, Bigfolder, local, S3) |
| Web admin | FastAPI + Jinja2 + HTMX |

## Demarrage rapide

```bash
# 1. Configuration
cp config/.env.example .env
# Remplir : STORAGE_BACKEND, MESSAGING_BACKEND, credentials

# 2. Lancement
docker-compose up -d

# 3. C'est tout.
```

### Exemples de configuration

```bash
# Administration publique (Tchap + Nextcloud + Albert)
STORAGE_BACKEND=webdav
MESSAGING_BACKEND=matrix
WEBDAV_URL=https://bnum.din.gouv.fr/remote.php/dav/files/colaig/
MATRIX_HOMESERVER=https://matrix.agent.tchap.gouv.fr

# Multi-provider via Bigfolder (OneDrive + Nextcloud + Box)
STORAGE_BACKEND=bigfolder
MESSAGING_BACKEND=matrix
BIGFOLDER_API_URL=http://bigfolder:8002

# Developpement local (pas de services externes)
STORAGE_BACKEND=local
MESSAGING_BACKEND=webchat
LOCAL_STORAGE_PATH=./data/storage
```

## Structure du projet

```
colaig/
├── messaging/      # Canaux de communication (Matrix, Slack, web chat)
├── context/        # Context Resolver (cerveau)
├── rag/            # Pipeline RAG complet
├── integrations/   # Backends de stockage + Albert API
│   └── storage/    # WebDAV, Bigfolder, Local, S3
├── storage/        # Cache in-memory
├── web/            # Dashboard admin
├── models.py       # Dataclasses partagees
├── protocols.py    # Contrats d'interface (StorageProtocol, MessagingProtocol, etc.)
└── main.py         # Point d'entree + factory backends
```

## Integration Bigfolder

[Bigfolder (Archivist)](https://github.com/...) est une plateforme de gestion documentaire multi-provider. Quand `STORAGE_BACKEND=bigfolder`, Colaig utilise l'API Bigfolder pour acceder aux documents — qui peuvent etre sur OneDrive, Box, Google Drive, WebDAV, ou S3. Colaig ne sait pas et s'en fiche.

```
COLAIG (1 container)           BIGFOLDER (N containers)
┌──────────────┐               ┌──────────────────┐
│ StorageProto │──── API ────→ │ OneDrive, Box,   │
│ col          │               │ WebDAV, S3, ...  │
│ MessagingPro │               │ PostgreSQL +     │
│ tocol        │               │ pgvector         │
└──────────────┘               └──────────────────┘
```

## Documentation

- **`CLAUDE.md`** — Instructions maitres (principes, stack, conventions)
- **`AGENTS.md`** — Decomposition en agents avec specifications detaillees
- **`colaig/protocols.py`** — Contrats d'interface entre modules
- **`docs/ARCHITECTURE.md`** — Synthese architecturale complete
- **`docs/STORAGE_ABSTRACTION.md`** — Spec technique StorageProtocol + MessagingProtocol

## Les 5 niveaux d'evolution

1. **RAG** — Conseiller documentaire *(Phase 1 — ce repo)*
2. **Workflow** — Planificateur d'actions (MCP tools, n8n)
3. **Personnalisation** — Expert configurable
4. **Reseau** — Ecosysteme inter-instances
5. **Intelligence collective** — Systeme vivant

## Licence

Licence Ouverte 2.0 — CEREMA Mediterranee / GIDI
