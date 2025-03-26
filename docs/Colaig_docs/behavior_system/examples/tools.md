# Exemples d'Outils

## 1. Gestionnaire de Contexte

Cet outil gère le contexte des conversations et le suivi des sujets.

```json
{
    "type": "tool",
    "description": "Gestion du contexte conversationnel",
    "priority": 0.9,
    "configuration": {
        "history_management": {
            "max_length": 10,
            "memory_duration": 3600,
            "cleanup_interval": 300
        },
        "topic_tracking": {
            "relevance_threshold": 0.3,
            "max_topics": 5
        }
    }
}
```

### Utilisation
```python
# Récupérer l'outil de gestion de contexte
context_tool = await behavior_manager.get_behavior(
    "context_handler",
    "tools"
)

# Extraire les topics d'une conversation
topics = behavior_manager.index._extract_topics(messages)
```

## 2. Client WebDAV

Outil pour les opérations CRUD sur WebDAV.

```json
{
    "type": "tool",
    "description": "Opérations WebDAV",
    "priority": 0.8,
    "configuration": {
        "operations": {
            "create": {
                "method": "PUT",
                "required_params": ["path", "content"]
            },
            "read": {
                "method": "GET",
                "required_params": ["path"]
            },
            "update": {
                "method": "PUT",
                "required_params": ["path", "content"]
            },
            "delete": {
                "method": "DELETE",
                "required_params": ["path"]
            }
        },
        "security": {
            "check_permissions": true,
            "validate_paths": true
        }
    }
}
```

### Utilisation
```python
# Créer un nouveau dossier
await webdav_service.create_directory(path)

# Lire un document
content = await webdav_service.read_document(path)
```

## 3. Outil d'Analyse

Outil pour l'analyse de données et la génération de statistiques.

```json
{
    "type": "tool",
    "description": "Analyse de données",
    "priority": 0.7,
    "configuration": {
        "analysis_types": {
            "basic_stats": {
                "metrics": ["mean", "median", "std"],
                "grouping": ["day", "week", "month"]
            },
            "trends": {
                "window_size": 7,
                "metrics": ["slope", "seasonality"]
            }
        },
        "visualization": {
            "types": ["line", "bar", "scatter"],
            "formats": ["png", "svg", "pdf"]
        }
    }
}
```

### Utilisation
```python
# Créer un outil d'analyse personnalisé
analysis_tool = {
    "type": "tool",
    "description": "Analyse personnalisée",
    "priority": BehaviorPriority.MEDIUM,
    "configuration": {
        "analysis_types": {
            "custom_analysis": {
                "method": "custom",
                "parameters": ["data", "options"]
            }
        }
    }
}

# Sauvegarder l'outil
await behavior_manager.save_behavior(
    "custom_analysis",
    "tools",
    analysis_tool
)
``` 