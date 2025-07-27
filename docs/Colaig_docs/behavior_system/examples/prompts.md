# Exemples de Prompts

## 1. Prompt Système RAG

Le prompt de base pour le système RAG.

```json
{
    "type": "prompt",
    "description": "Prompt système pour RAG",
    "priority": 1.0,
    "configuration": {
        "base_prompt": "Vous êtes Colaig, l'assistant de l'État français.",
        "style_variations": {
            "formal": {
                "description": "Style formel pour les échanges professionnels",
                "prompt_suffix": "Adoptez un ton formel et professionnel.",
                "examples": [
                    "Bonjour, je vous remercie de votre question.",
                    "Je me permets de vous apporter les précisions suivantes."
                ]
            },
            "technical": {
                "description": "Style technique pour documentation",
                "prompt_suffix": "Fournissez des informations techniques précises.",
                "examples": [
                    "La documentation technique indique que...",
                    "Voici les étapes à suivre pour..."
                ]
            }
        }
    }
}
```

### Utilisation
```python
# Récupérer le prompt système
system_prompt = await behavior_manager.get_behavior(
    "rag_system",
    "prompts"
)

# Utiliser un style spécifique
style = system_prompt["configuration"]["style_variations"]["formal"]
```

## 2. Prompt Assistant de Configuration

Prompt spécialisé pour le mode configuration.

```json
{
    "type": "prompt",
    "description": "Assistant de configuration",
    "priority": 0.9,
    "configuration": {
        "base_prompt": "Je suis en mode configuration. Je vais vous guider pas à pas.",
        "conversation_flows": {
            "initial_assessment": {
                "message": "Pour commencer, que souhaitez-vous configurer ?",
                "follow_up": {
                    "unclear": "Pouvez-vous préciser votre besoin ?",
                    "not_possible": "Cette configuration n'est pas possible car : {reason}",
                    "needs_clarification": "J'ai besoin de précisions sur : {points}"
                }
            },
            "configuration_steps": {
                "start": "Commençons la configuration de {feature}",
                "progress": "Étape {current}/{total} : {description}",
                "completion": "Configuration terminée avec succès"
            }
        }
    }
}
```

### Utilisation
```python
# Créer un nouveau prompt personnalisé
custom_prompt = {
    "type": "prompt",
    "description": "Prompt personnalisé",
    "priority": BehaviorPriority.HIGH,
    "configuration": {
        "base_prompt": "Prompt personnalisé pour {context}",
        "style_variations": {
            "custom_style": {
                "description": "Style sur mesure",
                "template": "{greeting}\n{content}\n{closing}"
            }
        }
    }
}

# Sauvegarder le prompt
await behavior_manager.save_behavior(
    "custom_prompt",
    "prompts",
    custom_prompt
)
```

## 3. Prompt de Documentation

Prompt spécialisé pour la génération de documentation.

```json
{
    "type": "prompt",
    "description": "Générateur de documentation",
    "priority": 0.8,
    "configuration": {
        "base_prompt": "Je vais générer la documentation technique.",
        "templates": {
            "api": {
                "structure": [
                    "## {endpoint}",
                    "**Méthode:** {method}",
                    "**Description:** {description}",
                    "### Paramètres",
                    "{parameters}",
                    "### Réponse",
                    "{response}"
                ]
            },
            "guide": {
                "structure": [
                    "# {title}",
                    "## Introduction",
                    "{introduction}",
                    "## Prérequis",
                    "{prerequisites}",
                    "## Étapes",
                    "{steps}",
                    "## Exemples",
                    "{examples}"
                ]
            }
        },
        "formatting": {
            "code_blocks": true,
            "syntax_highlighting": true,
            "include_toc": true
        }
    }
}
```

### Utilisation
```python
# Générer de la documentation
doc_prompt = await behavior_manager.get_behavior(
    "documentation",
    "prompts"
)

# Utiliser un template
api_doc = doc_prompt["configuration"]["templates"]["api"]
``` 