# Configuration des Prompts

## Introduction

Les prompts définissent la manière dont Colaig communique avec les utilisateurs. Ils permettent de personnaliser le style, le ton et le format des réponses selon le contexte.

## Structure d'un Prompt

### Format de Base

```json
{
    "type": "prompt",
    "description": "Description du prompt",
    "priority": 0.8,
    "configuration": {
        "base_prompt": "Prompt de base",
        "style_variations": {},
        "context_adaptations": {},
        "formatting_rules": {}
    }
}
```

## Types de Prompts

### 1. Prompt Système RAG
```json
{
    "type": "prompt",
    "description": "Prompts système pour le RAG conversationnel",
    "priority": 0.8,
    "configuration": {
        "base_prompt": "Vous êtes Colaig, l'assistant de l'État français.",
        "style_variations": {
            "formal": {
                "description": "Style formel pour les échanges professionnels",
                "indicators": ["pourriez-vous", "s'il vous plaît", "merci"],
                "prompt_suffix": "Adoptez un ton formel et professionnel."
            },
            "casual": {
                "description": "Style décontracté pour les échanges informels",
                "indicators": ["salut", "hey", "ok"],
                "prompt_suffix": "Adoptez un ton cordial tout en restant professionnel."
            }
        }
    }
}
```

### 2. Prompt Assistant de Configuration
```json
{
    "type": "prompt",
    "description": "Prompts pour l'assistant de configuration",
    "priority": 0.9,
    "configuration": {
        "base_prompt": "Je suis en mode configuration. Je vais vous guider pas à pas dans la personnalisation de Colaig selon vos besoins.",
        "conversation_flows": {
            "initial_assessment": {
                "message": "Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?",
                "follow_up": {
                    "unclear": "Je ne suis pas sûr de bien comprendre votre besoin. Pouvez-vous me donner plus de détails ?",
                    "not_possible": "Je suis désolé, mais cette configuration n'est pas possible car : {reason}. Voici ce que je peux vous proposer à la place : {alternatives}",
                    "needs_clarification": "Pour mieux vous aider, j'aurais besoin de précisions sur : {points}"
                }
            }
        }
    }
}
```

## Personnalisation des Prompts

### 1. Styles de Communication

```json
{
    "style_variations": {
        "style_name": {
            "description": "Description du style",
            "indicators": ["mot1", "mot2"],
            "prompt_prefix": "Texte avant la réponse",
            "prompt_suffix": "Texte après la réponse",
            "formatting": {
                "use_emojis": true,
                "paragraph_breaks": true,
                "bullet_points": "•"
            }
        }
    }
}
```

### 2. Adaptations Contextuelles

```json
{
    "context_adaptations": {
        "technical": {
            "condition": "topic contains 'api' or 'configuration'",
            "adaptations": {
                "detail_level": "high",
                "include_examples": true,
                "use_technical_terms": true
            }
        },
        "novice": {
            "condition": "user_level == 'beginner'",
            "adaptations": {
                "detail_level": "basic",
                "include_explanations": true,
                "simplify_terms": true
            }
        }
    }
}
```

### 3. Règles de Formatage

```json
{
    "formatting_rules": {
        "response_structure": {
            "prefix": "🤖",
            "sections": {
                "answer": {
                    "prefix": "",
                    "style": "direct"
                },
                "examples": {
                    "prefix": "📝 Exemple :",
                    "style": "code_block"
                },
                "notes": {
                    "prefix": "💡 Note :",
                    "style": "italic"
                }
            }
        }
    }
}
```

## Gestion des Conversations

### 1. Flux de Conversation

```json
{
    "conversation_flows": {
        "flow_name": {
            "initial": "Message initial",
            "steps": [
                {
                    "trigger": "user_input contains 'oui'",
                    "response": "Réponse positive",
                    "next_step": "step2"
                },
                {
                    "trigger": "user_input contains 'non'",
                    "response": "Réponse négative",
                    "next_step": "end"
                }
            ]
        }
    }
}
```

### 2. Gestion des Erreurs

```json
{
    "error_handling": {
        "unclear_input": {
            "message": "Je n'ai pas bien compris. Pouvez-vous reformuler ?",
            "suggestions": [
                "Voici ce que vous pourriez essayer...",
                "Par exemple..."
            ]
        },
        "missing_info": {
            "message": "Il me manque des informations pour continuer.",
            "required_fields": ["champ1", "champ2"]
        }
    }
}
```

## Validation des Prompts

### 1. Règles de Validation

```python
def validate_prompt_config(config: Dict) -> bool:
    """
    Validation avec :
    - Vérification de la structure
    - Validation des styles
    - Contrôle des adaptations
    """
```

### 2. Tests de Cohérence

```python
async def test_prompt_variations(prompt_config: Dict):
    """
    Tests avec :
    - Vérification des styles
    - Test des adaptations
    - Validation du formatage
    """
```

## Bonnes Pratiques

### 1. Conception des Prompts

- Clarté et concision
- Cohérence du style
- Adaptabilité au contexte

### 2. Gestion des Variations

- Transitions fluides
- Maintien du contexte
- Cohérence des réponses

### 3. Performance

- Prompts optimisés
- Réutilisation des templates
- Cache des variations fréquentes

## Exemples d'Utilisation

### 1. Prompt Simple

```json
{
    "type": "prompt",
    "description": "Prompt de salutation",
    "priority": 0.7,
    "configuration": {
        "base_prompt": "Bonjour ! Comment puis-je vous aider ?",
        "style_variations": {
            "morning": {
                "condition": "time between 5:00 and 12:00",
                "prompt": "Bonjour ! Que puis-je faire pour vous en ce début de journée ?"
            },
            "evening": {
                "condition": "time between 18:00 and 23:00",
                "prompt": "Bonsoir ! En quoi puis-je vous être utile ?"
            }
        }
    }
}
```

### 2. Prompt Complexe

```json
{
    "type": "prompt",
    "description": "Assistant d'intégration API",
    "priority": 0.85,
    "configuration": {
        "base_prompt": "Je vais vous aider à intégrer une nouvelle API.",
        "conversation_flows": {
            "api_integration": {
                "steps": [
                    {
                        "id": "doc_request",
                        "message": "Pouvez-vous me fournir la documentation de l'API ?",
                        "validation": {
                            "required": true,
                            "format": "url|text|file"
                        }
                    },
                    {
                        "id": "feature_selection",
                        "message": "Quelles fonctionnalités souhaitez-vous intégrer ?",
                        "options": {
                            "type": "multiple",
                            "min_selections": 1
                        }
                    }
                ]
            }
        },
        "error_handling": {
            "invalid_doc": {
                "message": "La documentation fournie n'est pas valide.",
                "suggestions": [
                    "Vérifiez le format",
                    "Assurez-vous que l'URL est accessible"
                ]
            }
        }
    }
}
``` 