# Exemples de Behaviors

## Actions

### 1. Action RAG Standard
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

### 2. Action API Personnalisée
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

## Tools

### 1. Outil de Gestion de Contexte
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

### 2. Outil WebDAV Personnalisé
```json
{
    "type": "tool",
    "description": "Opérations WebDAV étendues",
    "priority": 0.8,
    "configuration": {
        "operations": {
            "sync": {
                "method": "SYNC",
                "required_params": ["source", "target"],
                "options": {
                    "recursive": true,
                    "delete_extra": false
                }
            },
            "search": {
                "method": "SEARCH",
                "required_params": ["query"],
                "options": {
                    "max_depth": 3,
                    "file_types": [".pdf", ".doc", ".txt"]
                }
            }
        }
    }
}
```

## Prompts

### 1. Prompt Formel
```json
{
    "type": "prompt",
    "description": "Style formel pour communications officielles",
    "priority": 0.8,
    "configuration": {
        "base_prompt": "En tant qu'assistant officiel de l'État français, je m'exprime de manière formelle et professionnelle.",
        "style_variations": {
            "formal": {
                "greeting": "Bonjour,\n\n",
                "closing": "\n\nCordialement,\nColaig",
                "response_format": "{greeting}{content}{closing}"
            }
        },
        "tone_modifiers": {
            "empathetic": "Je comprends votre situation et",
            "assertive": "Je dois vous informer que",
            "helpful": "Je peux vous aider à"
        }
    }
}
```

### 2. Prompt Technique
```json
{
    "type": "prompt",
    "description": "Style technique pour documentation",
    "priority": 0.7,
    "configuration": {
        "base_prompt": "Je vais vous fournir des informations techniques précises.",
        "formatting": {
            "code_blocks": true,
            "syntax_highlighting": true,
            "include_examples": true
        },
        "sections": {
            "description": "Description générale",
            "prerequisites": "Prérequis",
            "steps": "Étapes détaillées",
            "examples": "Exemples d'utilisation",
            "troubleshooting": "Résolution des problèmes"
        }
    }
}
```

## Rules

### 1. Règle de Validation
```json
{
    "type": "rule",
    "description": "Validation des entrées utilisateur",
    "priority": 0.9,
    "configuration": {
        "validation_rules": {
            "paths": {
                "pattern": "^[a-zA-Z0-9_/.-]+$",
                "max_length": 255,
                "forbidden_chars": ["<", ">", "|", "*", "?"]
            },
            "api_keys": {
                "min_length": 32,
                "required_prefix": "sk_",
                "entropy_check": true
            }
        },
        "actions": {
            "on_invalid": "reject",
            "on_suspicious": "warn",
            "on_valid": "proceed"
        }
    }
}
```

### 2. Règle de Sécurité
```json
{
    "type": "rule",
    "description": "Règles de sécurité pour les opérations sensibles",
    "priority": 1.0,
    "configuration": {
        "security_checks": {
            "file_operations": {
                "allowed_extensions": [".txt", ".pdf", ".doc"],
                "max_size": 10485760,
                "scan_content": true
            },
            "api_calls": {
                "rate_limit": {
                    "requests": 100,
                    "period": 3600
                },
                "allowed_domains": [
                    "api.example.com",
                    "api.service.gouv.fr"
                ]
            }
        },
        "logging": {
            "level": "INFO",
            "include_user": true,
            "include_timestamp": true
        }
    }
}
``` 