# API Albert

L'API Albert est le composant central de COLAIG, fournissant les capacités de traitement du langage naturel et de génération d'embeddings.

## Points d'Entrée de l'API

### Base URL
```
https://api.albert.fr/v1
```

### Authentification
```http
Authorization: Bearer votre_token_api
```

## Endpoints

### 1. Embeddings

#### Génération d'Embeddings
```http
POST /embeddings
Content-Type: application/json

{
    "model": "albert-embedding-v1",
    "input": ["Votre texte à encoder"],
    "encoding_format": "float"
}
```

Réponse :
```json
{
    "data": [
        {
            "embedding": [0.123, 0.456, ...],
            "index": 0
        }
    ],
    "model": "albert-embedding-v1",
    "usage": {
        "prompt_tokens": 10,
        "total_tokens": 10
    }
}
```

#### Génération d'Embeddings par Lots
```http
POST /embeddings/batch
Content-Type: application/json

{
    "model": "albert-embedding-v1",
    "input": [
        "Premier texte",
        "Deuxième texte",
        "Troisième texte"
    ],
    "encoding_format": "float"
}
```

Réponse :
```json
{
    "data": [
        {
            "embedding": [0.123, 0.456, ...],
            "index": 0
        },
        {
            "embedding": [0.789, 0.012, ...],
            "index": 1
        },
        {
            "embedding": [0.345, 0.678, ...],
            "index": 2
        }
    ],
    "model": "albert-embedding-v1",
    "usage": {
        "prompt_tokens": 30,
        "total_tokens": 30
    }
}
```

### 2. Chat

#### Complétion de Chat
```http
POST /chat/completions
Content-Type: application/json

{
    "model": "albert-chat-v1",
    "messages": [
        {
            "role": "system",
            "content": "Tu es un assistant IA nommé COLAIG."
        },
        {
            "role": "user",
            "content": "Quelle est la procédure pour X ?"
        }
    ],
    "temperature": 0.7,
    "max_tokens": 500
}
```

Réponse :
```json
{
    "id": "chat-abc123",
    "object": "chat.completion",
    "created": 1677858242,
    "model": "albert-chat-v1",
    "usage": {
        "prompt_tokens": 50,
        "completion_tokens": 120,
        "total_tokens": 170
    },
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Voici la procédure pour X..."
            },
            "finish_reason": "stop",
            "index": 0
        }
    ]
}
```

### 3. Collections

#### Création de Collection
```http
POST /collections
Content-Type: application/json

{
    "name": "ma_collection",
    "description": "Description de la collection",
    "metadata": {
        "type": "documentation",
        "language": "fr"
    }
}
```

Réponse :
```json
{
    "id": "col_abc123",
    "name": "ma_collection",
    "description": "Description de la collection",
    "metadata": {
        "type": "documentation",
        "language": "fr"
    },
    "created_at": "2024-02-20T10:00:00Z"
}
```

#### Ajout de Documents
```http
POST /collections/{collection_id}/documents
Content-Type: application/json

{
    "documents": [
        {
            "text": "Contenu du document",
            "metadata": {
                "title": "Document 1",
                "source": "manuel.pdf"
            }
        }
    ]
}
```

Réponse :
```json
{
    "document_ids": ["doc_abc123"],
    "status": "success",
    "errors": []
}
```

#### Recherche dans les Collections
```http
POST /collections/search
Content-Type: application/json

{
    "query": "Recherche de documents pertinents",
    "collections": ["col_abc123"],
    "k": 5,
    "method": "semantic",
    "filter": {
        "metadata.language": "fr"
    }
}
```

Réponse :
```json
{
    "results": [
        {
            "document_id": "doc_abc123",
            "score": 0.89,
            "text": "Extrait pertinent...",
            "metadata": {
                "title": "Document 1",
                "source": "manuel.pdf"
            }
        }
    ],
    "total": 1
}
```

## Limites et Rate Limiting

### Limites Générales
- Taille maximale des textes : 8192 tokens
- Dimension des embeddings : 768
- Taille maximale des lots : 100 textes

### Rate Limiting
```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1677858242
```

- Embeddings : 60 requêtes/minute
- Chat : 30 requêtes/minute
- Collections : 100 requêtes/minute

## Gestion des Erreurs

### Format des Erreurs
```json
{
    "error": {
        "code": "rate_limit_exceeded",
        "message": "Rate limit dépassé",
        "type": "api_error",
        "param": null,
        "details": {
            "reset_at": "2024-02-20T10:01:00Z"
        }
    }
}
```

### Codes d'Erreur Communs

| Code | Description |
|------|-------------|
| `invalid_request` | Requête mal formée |
| `authentication_error` | Erreur d'authentification |
| `rate_limit_exceeded` | Rate limit dépassé |
| `model_not_found` | Modèle non trouvé |
| `context_length_exceeded` | Contexte trop long |
| `server_error` | Erreur serveur |

## Implémentation

### Initialisation du Client
```python
from colaig.tools.albert_client import AlbertClient

client = AlbertClient(
    api_key="votre_clé_api",
    api_url="https://api.albert.fr/v1",
    rate_limit=60
)
```

### Génération d'Embeddings
```python
async def generate_embeddings(texts: List[str]) -> List[List[float]]:
    try:
        # Vérification du rate limit
        await self.rate_limiter.wait()
        
        # Appel API
        response = await self._post(
            "/embeddings/batch",
            json={
                "model": "albert-embedding-v1",
                "input": texts
            }
        )
        
        # Traitement de la réponse
        return [item["embedding"] for item in response["data"]]
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération d'embeddings: {e}")
        raise
```

### Chat Completion
```python
async def chat_completion(
    self,
    messages: List[Dict[str, str]],
    temperature: float = 0.7
) -> str:
    try:
        # Vérification du rate limit
        await self.rate_limiter.wait()
        
        # Appel API
        response = await self._post(
            "/chat/completions",
            json={
                "model": "albert-chat-v1",
                "messages": messages,
                "temperature": temperature
            }
        )
        
        # Traitement de la réponse
        return response["choices"][0]["message"]["content"]
        
    except Exception as e:
        logger.error(f"Erreur lors de la complétion de chat: {e}")
        raise
```

### Gestion des Collections
```python
async def search_documents(
    self,
    query: str,
    collections: List[str],
    k: int = 5
) -> List[Dict]:
    try:
        # Vérification du rate limit
        await self.rate_limiter.wait()
        
        # Appel API
        response = await self._post(
            "/collections/search",
            json={
                "query": query,
                "collections": collections,
                "k": k,
                "method": "semantic"
            }
        )
        
        # Traitement de la réponse
        return response["results"]
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}")
        raise
```

## Bonnes Pratiques

1. **Rate Limiting**
   - Utiliser le rate limiter fourni
   - Implémenter une stratégie de retry
   - Monitorer l'utilisation

2. **Gestion des Erreurs**
   - Gérer tous les codes d'erreur
   - Logger les erreurs avec contexte
   - Implémenter des retries appropriés

3. **Performance**
   - Utiliser le traitement par lots
   - Mettre en cache les résultats fréquents
   - Optimiser la taille des requêtes

4. **Sécurité**
   - Ne jamais exposer la clé API
   - Valider les entrées utilisateur
   - Utiliser HTTPS uniquement 