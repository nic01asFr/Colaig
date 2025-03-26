# Exemples de Règles

## 1. Règles de Traitement des Réponses

Règles pour le nettoyage et le formatage des réponses.

```json
{
    "type": "rule",
    "description": "Traitement des réponses",
    "priority": 0.9,
    "configuration": {
        "cleaning_rules": {
            "remove_patterns": [
                "Basé sur les documents fournis,",
                "D'après les documents,",
                "Selon les sources,"
            ],
            "split_markers": [
                "En regardant les documents",
                "En analysant les sources",
                "Je vais essayer de"
            ]
        },
        "formatting": {
            "standard": {
                "template": "🤖 {response}",
                "conditions": {"show_sources": false}
            },
            "detailed": {
                "template": "🤖 {response}\n\n💡 Sources :\n{sources}",
                "conditions": {"show_sources": true}
            }
        }
    }
}
```

### Utilisation
```python
# Récupérer les règles de traitement
response_rules = await behavior_manager.get_behavior(
    "response_handling",
    "rules"
)

# Appliquer le formatage
template = response_rules["configuration"]["formatting"]["standard"]
```

## 2. Règles de Sécurité

Règles pour la validation et la sécurité.

```json
{
    "type": "rule",
    "description": "Règles de sécurité",
    "priority": 1.0,
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
        }
    }
}
```

### Utilisation
```python
# Créer une nouvelle règle de sécurité
security_rule = {
    "type": "rule",
    "description": "Règle de sécurité personnalisée",
    "priority": BehaviorPriority.CRITICAL,
    "configuration": {
        "validation": {
            "custom_check": {
                "type": "regex",
                "pattern": "^[a-z]+$"
            }
        }
    }
}

# Sauvegarder la règle
await behavior_manager.save_behavior(
    "custom_security",
    "rules",
    security_rule
)
```

## 3. Règles de Mode Configuration

Règles spécifiques au mode configuration.

```json
{
    "type": "rule",
    "description": "Règles du mode configuration",
    "priority": 0.8,
    "configuration": {
        "mode_detection": {
            "keywords": ["configurer", "paramétrer", "personnaliser"],
            "context_indicators": ["configuration", "paramétrage", "setup"]
        },
        "conversation_rules": {
            "max_steps": 10,
            "confirmation_required": true,
            "allow_backtrack": true,
            "timeout": 3600
        },
        "validation_steps": {
            "syntax_check": {
                "enabled": true,
                "strict": true
            },
            "security_check": {
                "enabled": true,
                "checks": ["api_keys", "paths", "permissions"]
            }
        }
    }
}
```

### Utilisation
```python
# Vérifier si une commande est une commande de configuration
is_config = await behavior_manager.is_config_command(message)

# Activer le mode configuration avec les règles
if is_config:
    config = await behavior_manager.activate_config_mode(room_id) 