# Architecture de COLAIG

## Vue d'Ensemble

COLAIG est construit sur une architecture modulaire asynchrone, utilisant le pattern RAG (Retrieval-Augmented Generation) pour fournir des réponses contextuelles précises.

## Architecture Technique

```mermaid
graph TB
    subgraph Interface
        A[API FastAPI]
        B[Service Tchap]
    end

    subgraph Core
        C[Orchestrator]
        D[Executor]
        E[Factory]
    end

    subgraph Storage
        F[WebDAV Storage]
    end

    subgraph Indexing
        G[Index Manager]
        H[Embedding Manager]
        I[Document Parsers]
    end

    subgraph External
        J[Albert API]
        K[WebDAV Server]
        L[Tchap API]
    end

    A --> C
    B --> C
    C --> D
    D --> G
    D --> J
    G --> H
    H --> J
    G --> F
    F --> K
    B --> L
```

## Composants Principaux

### 1. Interface Utilisateur
- **API FastAPI** : Points d'entrée REST
- **Service Tchap** : Interface de messagerie

### 2. Core
- **Orchestrator** : Gestion des workflows et intentions
- **Executor** : Exécution des actions
- **Factory** : Injection de dépendances

### 3. Storage
- **WebDAV Storage** : Stockage persistant
  - Documents
  - Conversations
  - Index

### 4. Indexing
- **Index Manager** : Gestion de l'index FAISS
- **Embedding Manager** : Gestion des embeddings
- **Document Parsers** : Traitement des documents

### 5. Services Externes
- **Albert API** : LLM et embeddings
- **WebDAV Server** : Stockage distant
- **Tchap API** : Messagerie gouvernementale

## Flux de Données

```mermaid
sequenceDiagram
    participant Client
    participant Interface
    participant Core
    participant Storage
    participant Indexing
    participant External

    Client->>Interface: Requête
    Interface->>Core: Détection intention
    Core->>Storage: Récupération contexte
    Core->>Indexing: Recherche documents
    Indexing->>External: Génération embeddings
    Indexing->>Storage: Récupération chunks
    Core->>External: Génération réponse
    Core->>Interface: Réponse formatée
    Interface->>Client: Réponse finale
```

## Gestion Asynchrone

COLAIG utilise `asyncio` pour la gestion asynchrone des opérations :

```mermaid
sequenceDiagram
    participant Operation1
    participant Operation2
    participant Operation3
    participant RateLimiter

    Operation1->>RateLimiter: Demande token
    RateLimiter->>Operation1: Token accordé
    Operation2->>RateLimiter: Demande token
    Operation3->>RateLimiter: Demande token
    RateLimiter->>Operation2: Token accordé
    RateLimiter->>Operation3: Token accordé
```

## Sécurité et Rate Limiting

- Authentification par token
- Rate limiting par service
- Gestion des sessions
- Protection contre les accès concurrents

## Extensibilité

Le système est conçu pour être facilement extensible :

1. Nouveaux parsers de documents
2. Nouveaux services de stockage
3. Nouveaux types d'actions
4. Nouveaux clients externes

## Configuration

La configuration se fait via :
- Variables d'environnement
- Fichiers de configuration
- Injection de dépendances 