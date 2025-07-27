# Exemples d'Actions

## 1. Action RAG Standard

Cette action est le comportement par défaut pour la recherche et la génération de réponses.

```json
{
    "type": "action",
    "description": "Action de recherche et réponse standard",
    "priority": 1.0,
    "configuration": {
        "search_params": {
            "include_behavior": true,
            "include_documents": false,
            "behavior_type": "conversation",
            "limit": 10
        },
        "response_generation": {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "embedding_model": "BAAI/bge-m3",
            "max_history": 2
        }
    }
}
```

### Utilisation
```python
# Récupérer l'action RAG standard
rag_action = await behavior_manager.get_behavior(
    "standard_rag",
    "actions"
)

# Utiliser dans une recherche
results = await behavior_manager.index.search(
    query="Comment configurer Colaig ?",
    behavior_type="actions"
)
```

## 2. Action de Configuration

Cette action gère le mode de configuration de Colaig.

```json
{
    "type": "action",
    "description": "Assistant de configuration Colaig",
    "priority": 0.9,
    "configuration": {
        "capabilities": {
            "webdav_integration": true,
            "api_integration": true,
            "custom_actions": true
        },
        "configuration_steps": {
            "analyze_request": {
                "description": "Analyse la demande",
                "parameters": ["query", "context"]
            },
            "identify_components": {
                "description": "Identifie les composants",
                "parameters": ["request_type", "requirements"]
            }
        }
    }
}
```

### Utilisation
```python
# Activer le mode configuration
config = await behavior_manager.activate_config_mode(room_id)

# Vérifier si le mode est actif
is_active = await behavior_manager.is_config_mode_active(room_id)
```

## 3. Action d'Intégration API

Action pour intégrer des API externes.

```json
{
    "type": "action",
    "description": "Intégration API externe",
    "priority": 0.8,
    "configuration": {
        "api_config": {
            "base_url": "https://api.example.com/v1",
            "endpoints": {
                "search": "/search",
                "retrieve": "/documents/{id}"
            },
            "auth": {
                "type": "bearer",
                "token_env": "API_TOKEN"
            }
        },
        "response_mapping": {
            "title": "$.data.title",
            "content": "$.data.content",
            "metadata": "$.data.meta"
        }
    }
}
```

### Utilisation
```python
# Créer une nouvelle intégration API
api_action = {
    "type": "action",
    "description": "Intégration API personnalisée",
    "priority": BehaviorPriority.HIGH,
    "configuration": {
        "api_config": {
            "base_url": "https://api.custom.com",
            "endpoints": {
                "data": "/data"
            }
        }
    }
}

# Sauvegarder l'action
await behavior_manager.save_behavior(
    "custom_api",
    "actions",
    api_action
)
``` 