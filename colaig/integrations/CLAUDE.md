# integrations/ — Backends & Clients externes

## Propriétaire : Agent CONNEXIONS

## Règles

- Chaque client/backend utilise `httpx.AsyncClient` pour les requêtes HTTP
- Chaque implémentation respecte son Protocol défini dans `colaig/protocols.py`
- Aucune logique métier ici — juste des appels réseau/filesystem et du parsing de réponses
- Retry avec backoff exponentiel (base 1s, max 60s, jitter aléatoire) sur 429/503/timeout
- Logging structuré : chaque requête loggée (method, url, status, duration_ms)
- Les erreurs deviennent des exceptions custom (`StorageError`, `AlbertError`)

## storage/ — Implémentations StorageProtocol

Tous les backends implémentent `StorageProtocol` défini dans `protocols.py`.
Le code métier (RAG, indexer, resolver, workspace) utilise UNIQUEMENT `StorageProtocol`,
jamais une implémentation concrète.

### webdav.py — WebDAVStorage (Nextcloud/Bnum)

Protocole WebDAV via HTTP brut (pas de bibliothèque webdav, juste httpx) :
- `PROPFIND` avec `Depth: 1` pour lister, `Depth: 0` pour un fichier
- Parser le XML de réponse PROPFIND (`xml.etree.ElementTree`)
- Namespaces WebDAV : `DAV:` pour les propriétés standard
- Authentification Basic HTTP via `httpx.BasicAuth`
- `download_if_changed` utilise `If-None-Match` header avec etag → 304 = pas changé
- Retourne des `StorageFile` (pas `WebDAVFile`)

### local.py — LocalStorage (filesystem)

Backend sur filesystem local :
- `base_path` définit le répertoire racine
- Les chemins sont résolus par rapport à `base_path`
- Etag calculé via SHA256 du contenu du fichier
- Utilise `pathlib` et `aiofiles` pour les opérations async
- Idéal pour les tests (tmpdir) et le développement

### bigfolder.py — BigfolderStorage (API Archivist)

Backend vers l'API REST de Bigfolder/Archivist :
- Parle à l'API REST pour les documents métier (`GET /api/documents`, `GET /api/documents/{id}/download`)
- Utilise un sous-backend (local ou WebDAV) pour les fichiers internes Colaig (`.colaig/`)
- Mapping API Bigfolder → StorageProtocol (voir docs/STORAGE_ABSTRACTION.md)

### s3.py — S3Storage (MinIO/S3, optionnel)

Backend S3/MinIO via boto3 async :
- Compatible S3, MinIO, Wasabi
- Bucket configurable
- Pas prioritaire en Phase 1

## llm/ — Backends LLM

Clients LLM interchangeables, tous compatibles avec l'interface `LLMClientProtocol`.

### capability_chain.py — CapabilityChain

Chaîne de fallback entre providers LLM. Méthodes disponibles :
- `chat`, `chat_stream`, `chat_with_tools` — génération texte
- `embed`, `embed_batch` — embeddings vectoriels
- `rerank` — reranking cross-encoder. Retourne `[]` si le provider ne supporte pas (404/405) → l'appelant peut utiliser MMR comme fallback
- `transcribe` — transcription audio. Retourne `""` si le provider ne supporte pas → l'appelant gère le cas vide

Le fallback est gracieux : 404/405 = provider ne supporte pas la capacité → essaie le provider suivant dans la chaîne.

### openai_client.py — OpenAIClient

Implémente toutes les méthodes de CapabilityChain (chat / embed / rerank / transcribe) en utilisant l'API OpenAI-compatible.

## albert.py — Client Albert API

Format API OpenAI-compatible :
- POST `/v1/chat/completions` → `{"model": "...", "messages": [...], "temperature": 0.3}`
- POST `/v1/embeddings` → `{"model": "...", "input": "texte"}`
- Header : `Authorization: Bearer {api_key}`
- Streaming : `"stream": true` → SSE, parser ligne par ligne
- Batch embeddings : découper en lots de 32, respecter rate limits
