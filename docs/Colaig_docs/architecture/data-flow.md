# Flux de Données

## Flux Principal

```mermaid
sequenceDiagram
    participant User
    participant TchapService
    participant Orchestrator
    participant Executor
    participant AlbertClient
    participant IndexManager
    participant Storage

    User->>TchapService: Envoie message
    TchapService->>Orchestrator: Analyse intention
    
    Orchestrator->>Orchestrator: Détection intention
    Note over Orchestrator: Création du workflow
    
    alt Recherche RAG
        Orchestrator->>Executor: Exécute recherche
        Executor->>IndexManager: Recherche similaire
        IndexManager->>Storage: Récupère chunks
        Storage-->>IndexManager: Chunks pertinents
        IndexManager-->>Executor: Résultats
        Executor->>AlbertClient: Génération réponse
        AlbertClient-->>Executor: Réponse générée
    else Requête directe
        Orchestrator->>Executor: Exécute requête
        Executor->>AlbertClient: Requête directe
        AlbertClient-->>Executor: Réponse
    end
    
    Executor-->>Orchestrator: Résultat final
    Orchestrator-->>TchapService: Réponse formatée
    TchapService-->>User: Envoie réponse
```

## Flux d'Indexation

```mermaid
sequenceDiagram
    participant Admin
    participant API
    participant IndexingProcess
    participant DocumentLoader
    participant Parser
    participant EmbeddingManager
    participant IndexManager
    participant Storage

    Admin->>API: Demande indexation
    API->>IndexingProcess: Lance indexation
    
    loop Pour chaque document
        IndexingProcess->>DocumentLoader: Charge document
        DocumentLoader->>Storage: Récupère document
        Storage-->>DocumentLoader: Document
        DocumentLoader-->>IndexingProcess: Document chargé
        
        IndexingProcess->>Parser: Parse document
        Parser->>Parser: Crée chunks
        Parser-->>IndexingProcess: Chunks
        
        loop Pour chaque lot de chunks
            IndexingProcess->>EmbeddingManager: Génère embeddings
            EmbeddingManager-->>IndexingProcess: Embeddings
            IndexingProcess->>IndexManager: Ajoute à l'index
            IndexManager->>Storage: Sauvegarde index
        end
    end
    
    IndexingProcess-->>API: Statut indexation
    API-->>Admin: Résultat final
```

## Flux de Stockage

```mermaid
sequenceDiagram
    participant Component
    participant Storage
    participant WebDAVClient
    participant WebDAVServer

    Component->>Storage: Sauvegarde document
    Storage->>Storage: Prépare structure
    
    Storage->>WebDAVClient: Upload fichier
    WebDAVClient->>WebDAVServer: PUT request
    WebDAVServer-->>WebDAVClient: Confirmation
    
    Storage->>WebDAVClient: Sauvegarde métadonnées
    WebDAVClient->>WebDAVServer: PUT metadata
    WebDAVServer-->>WebDAVClient: Confirmation
    
    Storage-->>Component: Résultat opération
```

## Flux de Recherche

```mermaid
sequenceDiagram
    participant User
    participant Executor
    participant EmbeddingManager
    participant IndexManager
    participant Storage
    participant AlbertClient

    User->>Executor: Requête recherche
    
    Executor->>EmbeddingManager: Génère embedding
    EmbeddingManager->>AlbertClient: Requête embedding
    AlbertClient-->>EmbeddingManager: Embedding
    
    EmbeddingManager->>IndexManager: Recherche similaire
    IndexManager->>Storage: Récupère chunks
    Storage-->>IndexManager: Chunks
    
    IndexManager->>IndexManager: Calcul similarité
    IndexManager-->>Executor: Résultats triés
    
    Executor->>AlbertClient: Génération réponse
    AlbertClient-->>Executor: Réponse finale
    
    Executor-->>User: Résultats formatés
```

## Flux de Messages Tchap

```mermaid
sequenceDiagram
    participant User
    participant TchapService
    participant TchapClient
    participant Orchestrator
    participant MessageQueue

    User->>TchapService: Nouveau message
    TchapService->>TchapClient: Reçoit message
    
    TchapService->>MessageQueue: Enqueue message
    
    loop Message Processing
        TchapService->>MessageQueue: Dequeue message
        TchapService->>Orchestrator: Traite message
        Orchestrator-->>TchapService: Réponse
        TchapService->>TchapClient: Envoie réponse
        TchapClient-->>User: Délivre réponse
    end
```

## Types de Données

### Messages
```python
class Message:
    id: str
    content: str
    sender: str
    timestamp: datetime
    role: str
    metadata: Dict
```

### Documents
```python
class Document:
    id: str
    title: str
    content: str
    chunks: List[DocumentChunk]
    metadata: Dict
```

### Embeddings
```python
class Embedding:
    vector: List[float]  # Dimension 768
    metadata: Dict
```

### Résultats de Recherche
```python
class SearchResult:
    chunk: DocumentChunk
    score: float
    metadata: Dict
```

## Gestion des Erreurs

```mermaid
sequenceDiagram
    participant Component
    participant ErrorHandler
    participant Monitoring
    participant Storage
    participant Admin

    Component->>ErrorHandler: Erreur détectée
    
    ErrorHandler->>ErrorHandler: Classifie erreur
    
    alt Erreur récupérable
        ErrorHandler->>Component: Retry strategy
        Component->>Component: Retry opération
    else Erreur critique
        ErrorHandler->>Monitoring: Log erreur
        ErrorHandler->>Storage: Sauvegarde état
        ErrorHandler->>Admin: Notification
    end
```

## Persistance des Données

### Structure WebDAV
```
/colaig/
├── system/
│   └── status.json
├── index/
│   ├── faiss.bin
│   └── metadata.pkl
├── conversations/
│   └── {conversation_id}/
│       ├── metadata.json
│       └── messages/
└── documents/
    └── {document_id}/
        ├── metadata.json
        └── chunks/
```

### Format des Métadonnées
```json
{
    "system_status": {
        "last_indexing": "2024-02-20T14:30:00",
        "document_count": 1000,
        "conversation_count": 50
    },
    "document_metadata": {
        "id": "doc123",
        "title": "Document Title",
        "created_at": "2024-02-20T14:30:00",
        "chunk_count": 10
    }
}
``` 