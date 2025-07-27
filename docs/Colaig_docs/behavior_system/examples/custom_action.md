# Création d'une Action Personnalisée

## Introduction

Ce guide vous montre comment créer une action personnalisée pour Colaig, en utilisant l'exemple d'un assistant de traduction qui utilise une API externe.

## Étapes de Création

### 1. Définition des Besoins

Nous allons créer un assistant de traduction qui :
- Détecte automatiquement la langue source
- Traduit vers la langue cible demandée
- Fournit des explications sur les choix de traduction
- Gère plusieurs services de traduction

### 2. Structure de l'Action

```json
{
    "type": "action",
    "description": "Assistant de traduction multilingue",
    "priority": 0.85,
    "configuration": {
        "base": {
            "parameters": {
                "default_target_language": "fr",
                "translation_service": "default",
                "include_explanations": true
            }
        },
        "tools": {
            "language_detector": {
                "type": "ml_tool",
                "config": {
                    "model": "language_detection_v2",
                    "confidence_threshold": 0.8
                }
            },
            "translator": {
                "type": "api_tool",
                "config": {
                    "services": {
                        "default": {
                            "api_url": "https://api.translation-service.com",
                            "auth_type": "api_key",
                            "timeout": 30
                        },
                        "fallback": {
                            "api_url": "https://backup-translator.com",
                            "auth_type": "oauth2"
                        }
                    }
                }
            }
        },
        "prompts": {
            "base_prompt": "Je suis votre assistant de traduction. Je peux traduire vos textes et expliquer les choix de traduction.",
            "style_variations": {
                "formal": {
                    "prompt": "Je vais vous aider à traduire ce texte de manière professionnelle.",
                    "response_template": "Traduction : {translation}\n\nExplications : {explanations}"
                },
                "casual": {
                    "prompt": "Je vais traduire ça pour vous !",
                    "response_template": "Voici la traduction : {translation}\n\nPetite explication : {explanations}"
                }
            }
        }
    }
}
```

### 3. Configuration des Outils

#### Détecteur de Langue
```json
{
    "type": "tool",
    "description": "Outil de détection de langue",
    "priority": 0.8,
    "configuration": {
        "operations": {
            "detect": {
                "description": "Détecte la langue d'un texte",
                "required_params": ["text"],
                "optional_params": {
                    "mode": "fast|accurate",
                    "min_confidence": 0.5
                }
            }
        }
    }
}
```

#### Service de Traduction
```json
{
    "type": "tool",
    "description": "Service de traduction",
    "priority": 0.8,
    "configuration": {
        "operations": {
            "translate": {
                "description": "Traduit un texte",
                "required_params": [
                    "text",
                    "target_language"
                ],
                "optional_params": {
                    "source_language": "auto",
                    "mode": "standard|professional",
                    "domain": "general|technical|legal"
                }
            }
        }
    }
}
```

### 4. Configuration des Prompts

```json
{
    "type": "prompt",
    "description": "Prompts pour l'assistant de traduction",
    "priority": 0.8,
    "configuration": {
        "conversation_flows": {
            "translation_request": {
                "initial": "Que souhaitez-vous traduire ?",
                "language_confirmation": "Je vais traduire de {source_lang} vers {target_lang}. Est-ce correct ?",
                "translation_result": "Voici la traduction :\n\n{translation}\n\nSouhaitez-vous des explications sur certains choix de traduction ?"
            },
            "explanation_request": {
                "prompt": "Sur quels aspects de la traduction souhaitez-vous des explications ?",
                "response_template": "Voici les explications pour {aspect} :\n\n{explanation}"
            }
        }
    }
}
```

### 5. Règles de Validation

```json
{
    "type": "rule",
    "description": "Règles pour l'assistant de traduction",
    "priority": 0.8,
    "configuration": {
        "validation": {
            "text_length": {
                "max_chars": 5000,
                "error_message": "Le texte est trop long. Maximum 5000 caractères."
            },
            "supported_languages": {
                "source": ["auto", "en", "fr", "es", "de", "it"],
                "target": ["fr", "en", "es", "de", "it"],
                "error_message": "Langue non supportée."
            }
        },
        "rate_limiting": {
            "requests_per_hour": 100,
            "error_message": "Limite de traductions atteinte."
        }
    }
}
```

## Intégration

### 1. Installation des Fichiers

```bash
.colaig/
├── actions/
│   └── translation_assistant.json
├── tools/
│   ├── language_detector.json
│   └── translation_service.json
├── prompts/
│   └── translation_prompts.json
└── rules/
    └── translation_rules.json
```

### 2. Vérification de l'Installation

```python
async def verify_translation_setup():
    """
    Vérifie l'installation avec :
    1. Validation des configurations
    2. Test des outils
    3. Vérification des intégrations
    """
```

## Utilisation

### 1. Exemple Simple

```python
# Demande de traduction
user: "Pouvez-vous traduire 'Hello world' en français ?"

# Réponse de Colaig
assistant: "🤖 Bien sûr ! Voici la traduction :

'Bonjour le monde'

Voulez-vous des explications sur les choix de traduction ?"
```

### 2. Exemple Avancé

```python
# Demande complexe
user: "J'ai besoin de traduire ce texte technique en anglais, avec des explications sur les termes spécifiques."

# Réponse de Colaig
assistant: "🤖 Je vais vous aider avec cette traduction technique.

1. Détection de la langue source... Français détecté (confiance: 0.95)
2. Analyse du domaine... Domaine technique identifié
3. Traduction en cours...

Traduction :
[Texte traduit]

Explications des termes techniques :
- Terme 1 : [Explication]
- Terme 2 : [Explication]

Souhaitez-vous des explications supplémentaires sur certains aspects ?"
```

## Personnalisation

### 1. Ajout de Services de Traduction

```json
{
    "tools": {
        "translator": {
            "services": {
                "new_service": {
                    "api_url": "https://new-translation-api.com",
                    "auth_type": "oauth2",
                    "priority": 0.7
                }
            }
        }
    }
}
```

### 2. Modification des Prompts

```json
{
    "prompts": {
        "style_variations": {
            "technical": {
                "prompt": "Je vais traduire ce texte technique avec précision.",
                "response_template": "Traduction technique :\n{translation}\n\nNotes techniques :\n{technical_notes}"
            }
        }
    }
}
```

## Bonnes Pratiques

1. **Validation**
   - Toujours valider les entrées
   - Vérifier les limites de caractères
   - Contrôler les langues supportées

2. **Performance**
   - Utiliser le cache pour les traductions fréquentes
   - Implémenter un système de fallback
   - Optimiser les requêtes API

3. **Maintenance**
   - Journaliser les erreurs
   - Surveiller l'utilisation
   - Mettre à jour les services régulièrement 