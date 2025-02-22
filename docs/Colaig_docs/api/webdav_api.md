# API WebDAV

L'API WebDAV est utilisée pour le stockage et la gestion des fichiers dans COLAIG. Elle permet de stocker les documents, les embeddings, les métadonnées et les index.

## Configuration

### Base URL
```
https://webdav.example.gouv.fr/colaig/
```

### Authentification
```http
Authorization: Basic base64(username:password)
```

## Structure des Dossiers

```
/colaig/
├── documents/
│   ├── {document_id}/
│   │   ├── content.pdf
│   │   └── metadata.json
├── embeddings/
│   ├── {document_id}/
│   │   └── embeddings.npy
├── index/
│   ├── faiss/
│   │   └── index.faiss
│   └── mapping/
│       └── id_mapping.json
└── conversations/
    └── {conversation_id}/
        ├── messages.json
        └── context.json
```

## Opérations

### 1. Gestion des Documents

#### Upload d'un Document
```http
PUT /colaig/documents/{document_id}/content.pdf
Content-Type: application/pdf

[Contenu binaire du document]
```

#### Upload des Métadonnées
```http
PUT /colaig/documents/{document_id}/metadata.json
Content-Type: application/json

{
    "id": "doc_123",
    "title": "Document Title",
    "type": "pdf",
    "created_at": "2024-03-14T12:00:00Z",
    "updated_at": "2024-03-14T12:00:00Z",
    "size": 1234567,
    "chunks": [
        {
            "id": "chunk_1",
            "start": 0,
            "end": 1000,
            "text": "Contenu du chunk"
        }
    ]
}
```

#### Récupération d'un Document
```http
GET /colaig/documents/{document_id}/content.pdf
```

#### Récupération des Métadonnées
```http
GET /colaig/documents/{document_id}/metadata.json
```

### 2. Gestion des Embeddings

#### Stockage des Embeddings
```http
PUT /colaig/embeddings/{document_id}/embeddings.npy
Content-Type: application/octet-stream

[Données binaires des embeddings]
```

#### Récupération des Embeddings
```http
GET /colaig/embeddings/{document_id}/embeddings.npy
```

### 3. Gestion de l'Index

#### Sauvegarde de l'Index FAISS
```http
PUT /colaig/index/faiss/index.faiss
Content-Type: application/octet-stream

[Données binaires de l'index FAISS]
```

#### Sauvegarde du Mapping
```http
PUT /colaig/index/mapping/id_mapping.json
Content-Type: application/json

{
    "0": "doc_123_chunk_1",
    "1": "doc_123_chunk_2"
}
```

### 4. Gestion des Conversations

#### Sauvegarde des Messages
```http
PUT /colaig/conversations/{conversation_id}/messages.json
Content-Type: application/json

{
    "messages": [
        {
            "id": "msg_1",
            "timestamp": "2024-03-14T12:00:00Z",
            "sender": "user",
            "content": "Question de l'utilisateur"
        },
        {
            "id": "msg_2",
            "timestamp": "2024-03-14T12:00:01Z",
            "sender": "assistant",
            "content": "Réponse de l'assistant"
        }
    ]
}
```

## Implémentation

### Initialisation du Client
```python
from colaig.tools.webdav_client import WebDAVClient

client = WebDAVClient(
    base_url="https://webdav.example.gouv.fr/colaig",
    username="user",
    password="pass"
)
```

### Gestion des Documents
```python
async def upload_document(
    self,
    document_id: str,
    content: bytes,
    metadata: Dict
) -> None:
    try:
        # Upload du contenu
        await self._put(
            f"/documents/{document_id}/content.pdf",
            data=content,
            headers={"Content-Type": "application/pdf"}
        )
        
        # Upload des métadonnées
        await self._put(
            f"/documents/{document_id}/metadata.json",
            json=metadata
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de l'upload du document: {e}")
        raise

async def get_document(
    self,
    document_id: str
) -> Tuple[bytes, Dict]:
    try:
        # Récupération du contenu
        content = await self._get(
            f"/documents/{document_id}/content.pdf"
        )
        
        # Récupération des métadonnées
        metadata = await self._get(
            f"/documents/{document_id}/metadata.json"
        )
        
        return content, metadata
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du document: {e}")
        raise
```

### Gestion des Embeddings
```python
async def save_embeddings(
    self,
    document_id: str,
    embeddings: np.ndarray
) -> None:
    try:
        # Conversion en bytes
        embeddings_bytes = embeddings.tobytes()
        
        # Upload des embeddings
        await self._put(
            f"/embeddings/{document_id}/embeddings.npy",
            data=embeddings_bytes,
            headers={"Content-Type": "application/octet-stream"}
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde des embeddings: {e}")
        raise

async def load_embeddings(
    self,
    document_id: str
) -> np.ndarray:
    try:
        # Récupération des embeddings
        embeddings_bytes = await self._get(
            f"/embeddings/{document_id}/embeddings.npy"
        )
        
        # Conversion en numpy array
        embeddings = np.frombuffer(embeddings_bytes, dtype=np.float32)
        
        return embeddings.reshape(-1, 1536)  # Dimension des embeddings Albert
        
    except Exception as e:
        logger.error(f"Erreur lors du chargement des embeddings: {e}")
        raise
```

### Gestion de l'Index
```python
async def save_index(
    self,
    index: faiss.Index,
    id_mapping: Dict[int, str]
) -> None:
    try:
        # Sauvegarde de l'index FAISS
        index_bytes = faiss.serialize_index(index)
        await self._put(
            "/index/faiss/index.faiss",
            data=index_bytes,
            headers={"Content-Type": "application/octet-stream"}
        )
        
        # Sauvegarde du mapping
        await self._put(
            "/index/mapping/id_mapping.json",
            json=id_mapping
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde de l'index: {e}")
        raise

async def load_index(self) -> Tuple[faiss.Index, Dict[int, str]]:
    try:
        # Chargement de l'index FAISS
        index_bytes = await self._get("/index/faiss/index.faiss")
        index = faiss.deserialize_index(index_bytes)
        
        # Chargement du mapping
        id_mapping = await self._get("/index/mapping/id_mapping.json")
        
        return index, id_mapping
        
    except Exception as e:
        logger.error(f"Erreur lors du chargement de l'index: {e}")
        raise
```

## Gestion des Erreurs

### Codes d'Erreur HTTP

| Code | Description |
|------|-------------|
| 401 | Non autorisé |
| 403 | Interdit |
| 404 | Non trouvé |
| 409 | Conflit |
| 507 | Espace insuffisant |

### Gestion des Erreurs
```python
class WebDAVError(Exception):
    def __init__(self, message: str, status_code: int):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class WebDAVClient:
    async def _handle_error(self, response: aiohttp.ClientResponse) -> None:
        if response.status >= 400:
            error_text = await response.text()
            raise WebDAVError(
                f"Erreur WebDAV: {error_text}",
                response.status
            )
```

## Bonnes Pratiques

1. **Gestion des Fichiers**
   - Utiliser des chunks pour les gros fichiers
   - Implémenter une vérification MD5
   - Gérer la compression si nécessaire

2. **Performance**
   - Mettre en cache les fichiers fréquemment accédés
   - Utiliser des opérations batch quand possible
   - Optimiser la taille des requêtes

3. **Sécurité**
   - Utiliser HTTPS
   - Valider les permissions
   - Nettoyer les métadonnées sensibles

4. **Maintenance**
   - Implémenter un système de nettoyage
   - Monitorer l'espace disque
   - Gérer les versions des fichiers 