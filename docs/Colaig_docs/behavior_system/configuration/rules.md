# Configuration des Règles

## Introduction

Les règles définissent les contraintes et les comportements de base du système Colaig. Elles assurent la cohérence, la sécurité et la qualité des interactions.

## Structure d'une Règle

### Format de Base

```json
{
    "type": "rule",
    "description": "Description de la règle",
    "priority": 0.8,
    "configuration": {
        "conditions": {},
        "actions": {},
        "constraints": {},
        "validation": {}
    }
}
```

## Types de Règles

### 1. Règles de Traitement des Réponses
```json
{
    "type": "rule",
    "description": "Règles de traitement et formatage des réponses",
    "priority": 0.7,
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

### 2. Règles du Mode Configuration
```json
{
    "type": "rule",
    "description": "Règles pour le mode configuration",
    "priority": 0.9,
    "configuration": {
        "mode_detection": {
            "keywords": ["configurer", "paramétrer", "personnaliser", "adapter"],
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

## Définition des Règles

### 1. Conditions

```json
{
    "conditions": {
        "condition_name": {
            "type": "simple|complex",
            "operator": "equals|contains|greater_than|less_than",
            "value": "valeur_attendue",
            "combine_with": "AND|OR"
        },
        "complex_condition": {
            "type": "complex",
            "conditions": [
                {
                    "field": "user_role",
                    "operator": "equals",
                    "value": "admin"
                },
                {
                    "field": "request_type",
                    "operator": "in",
                    "value": ["config", "admin"]
                }
            ],
            "combine_with": "AND"
        }
    }
}
```

### 2. Actions

```json
{
    "actions": {
        "action_name": {
            "type": "modify|validate|restrict",
            "target": "response|request|behavior",
            "parameters": {
                "param1": "value1",
                "param2": "value2"
            }
        }
    }
}
```

### 3. Contraintes

```json
{
    "constraints": {
        "constraint_name": {
            "type": "limit|require|forbid",
            "scope": "global|session|request",
            "parameters": {
                "max_value": 100,
                "min_value": 0,
                "allowed_values": ["val1", "val2"]
            }
        }
    }
}
```

## Validation des Règles

### 1. Règles de Validation

```python
def validate_rule_config(config: Dict) -> bool:
    """
    Validation avec :
    - Vérification de la structure
    - Validation des conditions
    - Contrôle des actions
    - Vérification des contraintes
    """
```

### 2. Tests de Cohérence

```python
async def test_rule_application(rule_config: Dict):
    """
    Tests avec :
    - Vérification des conditions
    - Test des actions
    - Validation des contraintes
    """
```

## Application des Règles

### 1. Ordre d'Application

```python
async def apply_rules(context: Dict, rules: List[Dict]):
    """
    Application avec :
    1. Tri par priorité
    2. Vérification des conditions
    3. Exécution des actions
    4. Validation des contraintes
    """
```

### 2. Gestion des Conflits

```json
{
    "conflict_resolution": {
        "strategy": "priority|first_match|all_match",
        "tie_breaker": "most_specific|most_recent",
        "fallback": {
            "action": "skip|error|default",
            "default_value": "valeur_par_défaut"
        }
    }
}
```

## Bonnes Pratiques

### 1. Conception des Règles

- Règles atomiques et ciblées
- Conditions claires et précises
- Actions bien définies

### 2. Gestion de la Complexité

- Limiter les règles imbriquées
- Éviter les dépendances circulaires
- Documenter les cas complexes

### 3. Performance

- Optimiser l'évaluation des conditions
- Mettre en cache les résultats fréquents
- Limiter la profondeur des règles

## Exemples d'Utilisation

### 1. Règle Simple

```json
{
    "type": "rule",
    "description": "Règle de limitation des requêtes",
    "priority": 0.8,
    "configuration": {
        "conditions": {
            "request_count": {
                "type": "simple",
                "operator": "greater_than",
                "value": 100,
                "period": "1h"
            }
        },
        "actions": {
            "throttle": {
                "type": "restrict",
                "parameters": {
                    "delay": 60,
                    "message": "Trop de requêtes, veuillez patienter."
                }
            }
        }
    }
}
```

### 2. Règle Complexe

```json
{
    "type": "rule",
    "description": "Règle de sécurité pour l'accès aux API",
    "priority": 0.95,
    "configuration": {
        "conditions": {
            "api_access": {
                "type": "complex",
                "conditions": [
                    {
                        "field": "user_role",
                        "operator": "in",
                        "value": ["admin", "api_user"]
                    },
                    {
                        "field": "api_key",
                        "operator": "exists",
                        "value": true
                    },
                    {
                        "field": "request_type",
                        "operator": "equals",
                        "value": "api_integration"
                    }
                ],
                "combine_with": "AND"
            }
        },
        "actions": {
            "validate_access": {
                "type": "validate",
                "steps": [
                    {
                        "check": "api_key_format",
                        "error": "Format de clé API invalide"
                    },
                    {
                        "check": "api_key_validity",
                        "error": "Clé API expirée ou invalide"
                    },
                    {
                        "check": "permission_scope",
                        "error": "Permissions insuffisantes"
                    }
                ]
            }
        },
        "constraints": {
            "rate_limit": {
                "type": "limit",
                "scope": "session",
                "parameters": {
                    "max_requests": 1000,
                    "period": "1h",
                    "per_endpoint": true
                }
            }
        }
    }
}
``` 