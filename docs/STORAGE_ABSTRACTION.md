# Abstraction Storage & Messaging — Spécification technique

## Contexte et motivation

Colaig v3 a été initialement construit autour de WebDAV/Nextcloud comme unique backend de stockage, et Matrix/Tchap comme unique canal de messagerie. Cette spécification décrit l'évolution vers une architecture **provider-agnostic** où le code métier (RAG, indexer, resolver, generator) est identique quel que soit le backend.

### Pourquoi ce changement ?

1. **Bigfolder (Archivist)** — Une plateforme de gestion documentaire multi-provider (OneDrive, Box, Google Drive, WebDAV, S3) existe déjà. Plutôt que de ré-implémenter chaque connecteur dans Colaig, on peut utiliser Bigfolder comme passerelle.
2. **Testabilité** — Un `LocalStorage` sur filesystem simplifie les tests (plus besoin de mocks WebDAV complexes).
3. **Flexibilité déploiement** — Administration publique = WebDAV/Nextcloud. Entreprise privée = OneDrive/SharePoint. Dev = filesystem local.
4. **Séparation des préoccupations** — Colaig s'occupe de l'intelligence conversationnelle (RAG + contexte + réponses). Le stockage est un problème orthogonal.

### Principe fondamental

**Le code métier de Colaig ne change pas.** Il appelle les mêmes méthodes (`list_files`, `download`, `upload`) qu'aujourd'hui. Ce qui change, c'est l'implémentation concrète injectée au démarrage.

---

## StorageProtocol

### Interface

```python
@runtime_checkable
class StorageProtocol(Protocol):
    """Interface abstraite pour tout backend de stockage.

    Remplace WebDAVClientProtocol. Le code métier de Colaig (RAG, indexer,
    resolver, workspace) utilise UNIQUEMENT cette interface.

    L'implémentation concrète (WebDAV, Bigfolder, Local, S3) est injectée
    dans main.py selon la configuration STORAGE_BACKEND.
    """

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        """Liste les fichiers et répertoires à un chemin.

        Args:
            path: Chemin du répertoire (ex: "/espace-projet/documents/")
            recursive: Si True, liste aussi les sous-répertoires

        Returns:
            Liste de StorageFile (nom, chemin, taille, date, etag, is_directory)
        """
        ...

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier.

        Args:
            path: Chemin du fichier (ex: "/espace-projet/documents/guide.pdf")

        Returns:
            Contenu du fichier en bytes

        Raises:
            StorageFileNotFoundError: Fichier inexistant
            StorageError: Erreur de communication avec le backend
        """
        ...

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        """Télécharge seulement si le fichier a changé (etag différent).

        Returns:
            bytes si changé, None si inchangé
        """
        ...

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier (crée ou écrase).

        Args:
            path: Chemin destination
            content: Contenu en bytes

        Raises:
            StorageError: Erreur d'écriture
        """
        ...

    async def mkdir(self, path: str) -> None:
        """Crée un répertoire (et parents si nécessaire)."""
        ...

    async def exists(self, path: str) -> bool:
        """Vérifie si un chemin (fichier ou répertoire) existe."""
        ...

    async def get_etag(self, path: str) -> str | None:
        """Retourne l'etag d'un fichier (None si inexistant).

        L'etag sert à détecter les modifications pour l'indexation incrémentale.
        """
        ...

    async def delete(self, path: str) -> None:
        """Supprime un fichier ou répertoire."""
        ...
```

### Modèle de données

```python
@dataclass
class StorageFile:
    """Fichier ou répertoire dans le backend de stockage.

    Remplace WebDAVFile. Même structure, nom générique.
    """
    path: str                           # Chemin complet (ex: "/espace-projet/doc.pdf")
    name: str                           # Nom du fichier (ex: "doc.pdf")
    is_directory: bool = False
    size: int = 0                       # Taille en bytes
    etag: str | None = None             # Hash pour détection de modifications
    last_modified: datetime | None = None
    content_type: str | None = None     # MIME type (ex: "application/pdf")
```

### Implémentations

#### WebDAVStorage — Nextcloud/Bnum

Backend historique. Parle directement à Nextcloud via le protocole WebDAV (HTTP PROPFIND/GET/PUT/DELETE/MKCOL).

```
STORAGE_BACKEND=webdav
WEBDAV_URL=https://bnum.din.gouv.fr/remote.php/dav/files/colaig/
WEBDAV_USERNAME=colaig
WEBDAV_PASSWORD=xxx
```

**Quand l'utiliser** : Déploiement administration publique française avec Nextcloud/Bnum.

**Migration depuis le code actuel** : Le `WebDAVClient` existant (`integrations/webdav.py`) est renommé `WebDAVStorage` et implémente `StorageProtocol` au lieu de `WebDAVClientProtocol`. Les méthodes sont les mêmes — seul le typage change (`WebDAVFile` → `StorageFile`).

#### BigfolderStorage — API Archivist (multi-provider)

Bigfolder **remplace WebDAV à l'identique**. C'est un proxy de stockage transparent qui expose des opérations fichier (list, read, write, delete, mkdir) à travers son API REST, quel que soit le provider derrière (OneDrive, Box, WebDAV, S3...).

Un workspace Bigfolder est l'équivalent exact d'un espace WebDAV : il contient les documents métier ET le dossier `.colaig/` avec les index FAISS, la config, l'historique. Colaig ne voit aucune différence.

```
STORAGE_BACKEND=bigfolder
BIGFOLDER_API_URL=http://localhost:8002
BIGFOLDER_API_KEY=ark_xxxxx
BIGFOLDER_WORKSPACE_ID=xxx
```

**Quand l'utiliser** : Quand les documents sont répartis sur plusieurs providers (OneDrive + Nextcloud + SharePoint), ou quand on veut bénéficier de la classification IA et du delta sync de Bigfolder.

**Principe** : Bigfolder expose les mêmes opérations fichier que WebDAV, mais à travers une API REST. Ses connecteurs internes (`BaseConnector`) supportent déjà `list_recursive`, `download`, `upload`, `delete`, `create_folder`, `exists` sur chaque provider. L'API REST de Bigfolder doit exposer ces opérations au niveau du workspace.

**Authentification** :
```
Header: Authorization: Bearer {BIGFOLDER_API_KEY}
Clés API au format: ark_xxxxxxxxxxxxx
```

**API Fichiers (à exposer dans Bigfolder)** :

Bigfolder doit exposer des endpoints de manipulation de fichiers bruts au niveau workspace, en plus de ses endpoints de gestion documentaire existants :

```
# Opérations fichier brutes (proxy vers le provider sous-jacent)
GET    /api/workspaces/{id}/files?path=/&recursive=false   → list_files
GET    /api/workspaces/{id}/files/download?path=/doc.pdf   → download
PUT    /api/workspaces/{id}/files/upload?path=/doc.pdf     → upload
DELETE /api/workspaces/{id}/files?path=/doc.pdf             → delete
POST   /api/workspaces/{id}/files/mkdir?path=/dossier      → mkdir
HEAD   /api/workspaces/{id}/files?path=/doc.pdf             → exists
GET    /api/workspaces/{id}/files/etag?path=/doc.pdf        → get_etag
```

Ces endpoints sont des proxys directs vers les méthodes `BaseConnector` du workspace. Ils permettent à Colaig de lire/écrire n'importe quel fichier dans le workspace — documents métier comme fichiers `.colaig/`.

**Mapping StorageProtocol → API Bigfolder** :

| StorageProtocol | API Bigfolder |
|-----------------|---------------|
| `list_files(path, recursive)` | `GET /api/workspaces/{id}/files?path=X&recursive=Y` |
| `download(path)` | `GET /api/workspaces/{id}/files/download?path=X` |
| `upload(path, content)` | `PUT /api/workspaces/{id}/files/upload?path=X` |
| `delete(path)` | `DELETE /api/workspaces/{id}/files?path=X` |
| `mkdir(path)` | `POST /api/workspaces/{id}/files/mkdir?path=X` |
| `exists(path)` | `HEAD /api/workspaces/{id}/files?path=X` → 200/404 |
| `get_etag(path)` | `GET /api/workspaces/{id}/files/etag?path=X` |
| `download_if_changed(path, etag)` | `GET .../download?path=X` + `If-None-Match: etag` → 304/200 |

**Réponse list_files** :
```json
{
  "files": [
    {
      "path": "/espace-projet/documents/guide.pdf",
      "name": "guide.pdf",
      "is_directory": false,
      "size": 1024000,
      "etag": "abc123def456",
      "last_modified": "2025-02-22T14:30:00Z",
      "content_type": "application/pdf"
    },
    {
      "path": "/espace-projet/.colaig/",
      "name": ".colaig",
      "is_directory": true,
      "size": 0,
      "etag": null,
      "last_modified": "2025-02-20T10:00:00Z",
      "content_type": null
    }
  ]
}
```

**Ce que ça implique pour Bigfolder** : Les endpoints fichier sont des wrappers légers autour du `BaseConnector` du workspace. Bigfolder a déjà les connecteurs WebDAV, OneDrive, Box, S3 avec les méthodes `list_recursive`, `download`, `upload`, `delete`, `create_folder`, `exists`. Il suffit d'exposer ces méthodes via des routes REST au niveau workspace.

**Avantage** : Bigfolder apporte en bonus la sync multi-provider, le delta sync, la classification IA — mais Colaig n'est pas obligé de les utiliser. Il voit juste des fichiers.

**Workspace Bigfolder = Workspace Colaig** :
```
Workspace Bigfolder (provider: OneDrive, root_path: "/Mon espace")
│
└── /Mon espace/                     ← root_path du workspace
    ├── documents/                   ← Documents métier (synchronisés + classifiés par Bigfolder)
    │   ├── procedures/
    │   └── guides/
    ├── .colaig/                     ← Géré par Colaig (lu/écrit via StorageProtocol)
    │   ├── config.yaml
    │   ├── indexes/
    │   │   ├── documents.faiss
    │   │   └── documents.pkl
    │   └── conversations/
    │       └── room-xxx.json
    └── (tout autre contenu)
```

#### LocalStorage — Filesystem

Lit/écrit directement sur le système de fichiers local. Aucune dépendance réseau.

```
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=/app/data/storage
```

**Quand l'utiliser** :
- Développement local
- Tests automatisés (remplace les mocks WebDAV)
- Démo sans infrastructure
- Déploiement minimaliste (documents copiés localement)

**Avantage test** : Plus besoin du `MockWebDAVClient` complexe dans `conftest.py`. Les tests créent un répertoire temporaire et utilisent `LocalStorage` directement.

#### S3Storage — MinIO/S3

Parle à un bucket S3 ou MinIO via boto3 (async).

```
STORAGE_BACKEND=s3
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=admin
S3_SECRET_KEY=password
S3_BUCKET=colaig
```

**Quand l'utiliser** : Déploiement cloud, stockage objet, archivage longue durée.

---

## MessagingProtocol

### Interface

```python
@runtime_checkable
class MessagingProtocol(Protocol):
    """Interface abstraite pour tout canal de communication.

    Remplace MatrixBotProtocol. Le code métier de Colaig (handlers)
    utilise UNIQUEMENT cette interface.
    """

    async def connect(self) -> None:
        """Connexion au service de messagerie."""
        ...

    async def run(self) -> None:
        """Boucle d'écoute infinie des messages entrants."""
        ...

    async def send(self, conversation_id: str, text: str, formatted: str | None = None) -> None:
        """Envoie un message dans une conversation.

        Args:
            conversation_id: Identifiant de la conversation (room_id Matrix, channel Slack, etc.)
            text: Texte brut du message
            formatted: Version HTML/Markdown formatée (optionnel)
        """
        ...

    async def send_typing(self, conversation_id: str, typing: bool = True) -> None:
        """Envoie/arrête l'indicateur de frappe."""
        ...

    def on_message(self, callback) -> None:
        """Enregistre un callback appelé pour chaque message reçu.

        Le callback reçoit un IncomingMessage.
        """
        ...
```

### Modèle de données

```python
@dataclass
class IncomingMessage:
    """Message reçu depuis n'importe quel canal.

    Champs génériques, indépendants du provider.
    """
    user_id: str                        # Identifiant utilisateur (format libre)
    conversation_id: str                # Identifiant conversation (room_id, channel, etc.)
    body: str                           # Contenu texte du message
    timestamp: datetime                 # Date/heure du message
    conversation_type: str = "group"    # "group" | "direct" | "channel"
    display_name: str | None = None     # Nom affiché de l'utilisateur
    message_id: str | None = None       # Identifiant unique du message
    reply_to: str | None = None         # ID du message auquel on répond (thread)

    # Métadonnées spécifiques au provider (pour debug/logging)
    raw_metadata: dict | None = None
```

### Implémentations

#### MatrixMessaging — Tchap

Implémentation existante (`bot/client.py`) adaptée au nouveau Protocol.

```
MESSAGING_BACKEND=matrix
MATRIX_HOMESERVER=https://matrix.agent.tchap.gouv.fr
MATRIX_USERNAME=@colaig:agent.tchap.gouv.fr
MATRIX_PASSWORD=xxx
```

**Mapping** :
| MessagingProtocol | Matrix |
|-------------------|--------|
| `conversation_id` | `room_id` |
| `send(id, text)` | `room_send(room_id, m.room.message)` |
| `send_typing(id)` | `room_typing(room_id)` |
| `conversation_type` | Déduit de `room.is_direct` / `room.join_rules` |

#### SlackMessaging (futur)

```
MESSAGING_BACKEND=slack
SLACK_BOT_TOKEN=xoxb-xxx
SLACK_APP_TOKEN=xapp-xxx
```

#### WebChatMessaging (futur)

WebSocket intégré dans l'interface FastAPI/HTMX.

```
MESSAGING_BACKEND=webchat
# Pas de config supplémentaire — intégré dans le serveur web
```

---

## Migration depuis le code actuel

### Ce qui change

| Avant | Après |
|-------|-------|
| `WebDAVClientProtocol` | `StorageProtocol` |
| `WebDAVFile` | `StorageFile` |
| `WebDAVClient` | `WebDAVStorage` (implémente `StorageProtocol`) |
| `MatrixBotProtocol` | `MessagingProtocol` |
| `MatrixBot` | `MatrixMessaging` (implémente `MessagingProtocol`) |
| `IncomingMessage.room_id` | `IncomingMessage.conversation_id` |
| `IncomingMessage.room_type` | `IncomingMessage.conversation_type` |
| `bot/client.py` + `bot/handlers.py` | `messaging/matrix.py` + `messaging/handlers.py` |

### Ce qui ne change PAS

- Le pipeline RAG (chunker, embeddings, faiss_store, retriever, indexer, generator)
- Le context resolver (resolver, workspace, layers) — sauf renommage des champs
- Le cache in-memory
- Albert API client
- L'interface web admin
- Les tests unitaires RAG (ils n'utilisent pas de storage directement)

### Stratégie de migration

1. **Créer `StorageProtocol`** et `StorageFile` dans `protocols.py`
2. **Renommer** `WebDAVClient` → `WebDAVStorage`, implémenter `StorageProtocol`
3. **Créer `LocalStorage`** pour les tests
4. **Adapter** `conftest.py` : remplacer `MockWebDAVClient` par `LocalStorage` avec un tmpdir
5. **Renommer** dans tout le code métier : `webdav` → `storage`, `WebDAVFile` → `StorageFile`
6. **Créer `MessagingProtocol`** dans `protocols.py`
7. **Renommer** `MatrixBot` → `MatrixMessaging`, `bot/` → `messaging/`
8. **Adapter** `IncomingMessage` : `room_id` → `conversation_id`, `room_type` → `conversation_type`
9. **Adapter** `main.py` : factory pattern pour instancier le bon backend selon la config
10. **Plus tard** : Implémenter `BigfolderStorage`, `S3Storage`, `SlackMessaging`

---

## Intégration Bigfolder (Archivist)

### Principe

Bigfolder est un **remplacement transparent de WebDAV**. Du point de vue de Colaig, c'est un `StorageProtocol` comme un autre — il expose les mêmes opérations fichier (list, read, write, delete, mkdir). Ce qui est derrière (OneDrive, Box, WebDAV, S3) est invisible.

Un workspace Bigfolder = un workspace Colaig. Il contient tout :
- Les documents métier
- Le dossier `.colaig/` avec config, index FAISS, historiques
- Tout autre contenu

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        COLAIG                           │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Messaging    │  │   Context    │  │     RAG      │  │
│  │  Protocol     │  │   Resolver   │  │   Pipeline   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│         │     ┌───────────▼──────────────────▼───┐     │
│         │     │       StorageProtocol             │     │
│         │     └───────────┬──────────────────────┘     │
└─────────┼─────────────────┼────────────────────────────┘
          │                 │
          │          ┌──────▼──────┐
          │          │ Quel backend│
          │          │ configuré ? │
          │          └──────┬──────┘
          │          ┌──────┼──────────────┐
          │          │      │              │
          │     ┌────▼──┐ ┌▼────────┐ ┌───▼─────────────────────┐
          │     │WebDAV │ │ Local   │ │ BIGFOLDER (Archivist)   │
          │     │Storage│ │ Storage │ │                         │
          │     └───────┘ └─────────┘ │  API REST fichiers      │
          │                           │  ┌───────┐ ┌──────┐    │
          │                           │  │OneDrive│ │ Box  │... │
          │                           │  └───────┘ └──────┘    │
          │                           └─────────────────────────┘
          │
    ┌─────▼──────────────────────┐
    │   Matrix/Tchap Homeserver  │
    └────────────────────────────┘
```

### Ce qui passe par Bigfolder (tout)

| Opération Colaig | Via Bigfolder |
|------------------|---------------|
| Lister les documents métier | `list_files("/documents/")` → API fichiers |
| Télécharger un PDF pour indexation | `download("/documents/guide.pdf")` → API fichiers |
| Sauvegarder un index FAISS | `upload("/.colaig/indexes/documents.faiss", bytes)` → API fichiers |
| Lire la config workspace | `download("/.colaig/config.yaml")` → API fichiers |
| Sauvegarder l'historique | `upload("/.colaig/conversations/room-x.json", bytes)` → API fichiers |
| Détecter les modifications | `get_etag("/documents/guide.pdf")` → API fichiers |

### Qui fait quoi ?

| Responsabilité | Colaig | Bigfolder |
|----------------|--------|-----------|
| Écouter les messages (Tchap, Slack...) | x | |
| Comprendre l'intention | x | |
| Pipeline RAG (FAISS, Albert API) | x | |
| Générer la réponse (Albert API souverain) | x | |
| Gérer le contexte conversationnel (5 couches) | x | |
| Stocker les index FAISS (via StorageProtocol) | x | via l'API fichiers |
| Stocker la config/historique (via StorageProtocol) | x | via l'API fichiers |
| Sync multi-provider (OneDrive, Box...) | | x |
| Gestion des tokens OAuth | | x |
| Delta sync | | x |
| Classification IA des documents (bonus) | | x |

### Ce que Bigfolder doit exposer pour Colaig

Les endpoints fichier (`/api/workspaces/{id}/files/*`) décrits dans la section BigfolderStorage ci-dessus. Ce sont des wrappers directs autour des méthodes `BaseConnector` existantes dans Archivist :

```python
# Archivist a déjà ces méthodes dans BaseConnector :
connector.list_recursive(path)    # → exposer via GET  .../files?path=X
connector.download(path)          # → exposer via GET  .../files/download?path=X
connector.upload(path, data)      # → exposer via PUT  .../files/upload?path=X
connector.delete(path)            # → exposer via DELETE .../files?path=X
connector.create_folder(path)     # → exposer via POST .../files/mkdir?path=X
connector.exists(path)            # → exposer via HEAD .../files?path=X
```

L'effort côté Bigfolder est minimal : ~100 lignes de routes FastAPI qui délèguent au connecteur du workspace.

---

## FAQ

### Est-ce que Colaig fonctionne sans Bigfolder ?
**Oui.** Bigfolder est un backend optionnel. Avec `STORAGE_BACKEND=webdav` ou `STORAGE_BACKEND=local`, Colaig fonctionne exactement comme avant, sans aucune dépendance à Bigfolder.

### Est-ce que Bigfolder fonctionne sans Colaig ?
**Oui.** Bigfolder est un projet indépendant avec sa propre interface (React), son propre RAG (pgvector), et ses propres agents IA. Colaig est un client de son API, comme un autre.

### Pourquoi ne pas utiliser le RAG de Bigfolder directement ?
Bigfolder utilise pgvector (PostgreSQL) pour le RAG. Colaig utilise FAISS (fichiers stockés via StorageProtocol). Les deux approches sont valides, mais Colaig a besoin de son propre RAG pour :
- Rester Zero Database (pas de dépendance directe à PostgreSQL)
- Utiliser Albert API (souveraineté) au lieu de Mistral/OpenAI
- Construire un contexte conversationnel riche (5 couches) impossible avec une simple recherche pgvector
- Stocker les index FAISS comme fichiers dans le workspace (via StorageProtocol → Bigfolder → provider)

### Et si Bigfolder est down ?
Même comportement que si WebDAV est down : Colaig répond "service de stockage temporairement indisponible" et continue de fonctionner avec son cache local (index FAISS en mémoire, dernière version connue).

### Comment migrer de WebDAV vers Bigfolder ?
Changer `STORAGE_BACKEND=webdav` en `STORAGE_BACKEND=bigfolder` + configurer `BIGFOLDER_API_URL`, `BIGFOLDER_API_KEY`, `BIGFOLDER_WORKSPACE_ID`. Les index FAISS seront reconstruits automatiquement au premier démarrage. Aucune modification de code — c'est juste un autre `StorageProtocol`.

### Bigfolder stocke-t-il les index FAISS de Colaig ?
**Oui.** Bigfolder est un proxy de stockage transparent. Quand Colaig fait `upload("/.colaig/indexes/documents.faiss", bytes)`, Bigfolder écrit ce fichier sur le provider sous-jacent (WebDAV, OneDrive, S3...) exactement comme le ferait un client WebDAV direct. Bigfolder ne sait pas que c'est un index FAISS — c'est juste un fichier.

### Faut-il modifier Bigfolder pour supporter Colaig ?
**Oui, mais peu.** Il faut ajouter des routes API fichiers (`/api/workspaces/{id}/files/*`) qui exposent les méthodes `BaseConnector` existantes. C'est ~100 lignes de code côté Bigfolder. Les connecteurs (WebDAV, OneDrive, Box, S3) supportent déjà toutes les opérations nécessaires.
