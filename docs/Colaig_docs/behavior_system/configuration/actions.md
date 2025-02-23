# Configuration des Actions

## Introduction

Les actions sont les composants principaux du système de comportement de Colaig. Elles définissent comment le système doit réagir à différents types de requêtes utilisateur.

## Structure d'une Action

### Format de Base

```json
{
    "type": "action",
    "description": "Description détaillée de l'action",
    "priority": 0.8,
    "configuration": {
        "base": {
            "parameters": {},
            "requirements": []
        },
        "tools": {},
        "prompt": {},
        "context_specific": {}
    }
}
```

## Types d'Actions

### 1. Action RAG Standard
```json
{
    "type": "action",
    "description": "Action RAG standard pour la recherche et la génération de réponses",
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

### 2. Assistant de Configuration
```json
{
    "type": "action",
    "description": "Assistant de configuration pour Colaig",
    "priority": 0.95,
    "configuration": {
        "capabilities": {
            "webdav_integration": true,
            "api_integration": true,
            "custom_actions": true,
            "custom_tools": true,
            "custom_prompts": true
        },
        "configuration_steps": {
            "analyze_request": {
                "description": "Analyse la demande de configuration",
                "parameters": ["query", "context"]
            },
            "identify_components": {
                "description": "Identifie les composants nécessaires",
                "parameters": ["request_type", "requirements"]
            },
            "generate_config": {
                "description": "Génère la configuration appropriée",
                "parameters": ["components", "format"]
            }
        }
    }
}
```

### 3. Intégration API
```json
{
    "type": "action",
    "description": "Action pour intégrer une API externe",
    "priority": 0.9,
    "configuration": {
        "steps": {
            "analyze_doc": {
                "description": "Analyse de la documentation API",
                "tool": "config_manager",
                "operation": "analyze_api"
            },
            "generate_tools": {
                "description": "Génération des outils d'API",
                "tool": "config_manager",
                "operation": "generate_behavior"
            },
            "generate_actions": {
                "description": "Génération des actions utilisant l'API",
                "tool": "config_manager",
                "operation": "generate_behavior"
            }
        },
        "validation": {
            "required_fields": ["api_doc", "target_features"],
            "security_checks": ["api_key_handling", "url_validation"]
        }
    }
}
```

## Création d'une Action

### 1. Définition des Métadonnées

- **type**: Toujours "action"
- **description**: Description claire et concise
- **priority**: Valeur entre 0.0 et 1.0

### 2. Configuration de Base

```json
{
    "base": {
        "parameters": {
            "param1": {
                "type": "string|number|boolean|object",
                "description": "Description du paramètre",
                "required": true,
                "default": "valeur par défaut"
            }
        },
        "requirements": [
            "tool1",
            "tool2"
        ]
    }
}
```

### 3. Association d'Outils

```json
{
    "tools": {
        "tool1": {
            "type": "tool_type",
            "config": {}
        },
        "tool2": {
            "type": "tool_type",
            "config": {}
        }
    }
}
```

### 4. Configuration des Prompts

```json
{
    "prompt": {
        "base_prompt": "Prompt de base pour l'action",
        "variations": {
            "style1": "Variation 1 du prompt",
            "style2": "Variation 2 du prompt"
        }
    }
}
```

## Validation

### 1. Règles de Validation

- Présence des champs requis
- Types de données corrects
- Valeurs dans les plages autorisées
- Dépendances satisfaites

### 2. Exemple de Validation

```python
def validate_action_config(config: Dict) -> bool:
    required_fields = ["type", "description", "priority", "configuration"]
    if not all(field in config for field in required_fields):
        return False
        
    if not 0.0 <= config["priority"] <= 1.0:
        return False
        
    return True
```

## Bonnes Pratiques

### 1. Nommage et Organisation

- Utiliser des noms descriptifs
- Organiser logiquement les paramètres
- Documenter clairement les fonctionnalités

### 2. Priorités

- RAG standard : 1.0
- Actions système : 0.9-0.99
- Actions personnalisées : 0.5-0.89
- Actions de fallback : 0.1-0.49

### 3. Sécurité

- Valider toutes les entrées
- Gérer les informations sensibles
- Limiter les permissions

## Exemples

### 1. Action Simple

```json
{
    "type": "action",
    "description": "Action de salutation personnalisée",
    "priority": 0.8,
    "configuration": {
        "base": {
            "parameters": {
                "greeting_style": {
                    "type": "string",
                    "options": ["formal", "casual"],
                    "default": "formal"
                }
            }
        },
        "prompt": {
            "base_prompt": "Saluer l'utilisateur de manière {greeting_style}"
        }
    }
}
```

### 2. Action Complexe

```json
{
    "type": "action",
    "description": "Action d'intégration de service externe",
    "priority": 0.85,
    "configuration": {
        "base": {
            "parameters": {
                "service_url": {
                    "type": "string",
                    "required": true,
                    "validation": "url"
                },
                "api_key": {
                    "type": "string",
                    "required": true,
                    "secret": true
                }
            },
            "requirements": [
                "api_client",
                "data_validator"
            ]
        },
        "tools": {
            "api_client": {
                "type": "http_client",
                "config": {
                    "timeout": 30,
                    "retry_count": 3
                }
            },
            "data_validator": {
                "type": "schema_validator",
                "config": {
                    "schema_path": "schemas/service_data.json"
                }
            }
        },
        "prompt": {
            "base_prompt": "Gérer l'intégration du service externe",
            "variations": {
                "error": "Gérer les erreurs d'intégration",
                "success": "Confirmer l'intégration réussie"
            }
        }
    }
}
```

## Dépannage

### Problèmes Courants

1. **Priorité Incorrecte**
   - Symptôme : L'action n'est pas sélectionnée
   - Solution : Ajuster la priorité

2. **Outils Manquants**
   - Symptôme : Erreurs d'exécution
   - Solution : Vérifier les requirements

3. **Configuration Invalide**
   - Symptôme : Erreurs de validation
   - Solution : Vérifier le format JSON 