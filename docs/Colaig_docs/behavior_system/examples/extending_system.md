# Guide d'Extension du Système de Behavior

## Introduction

Ce guide explique comment étendre le système de behavior de Colaig pour ajouter de nouvelles fonctionnalités ou personnaliser le comportement existant.

## 1. Enregistrement d'un Nouveau Type de Behavior

```python
from services.behavior_manager import BehaviorManager

# Enregistrer un nouveau type
BehaviorManager.register_behavior_type(
    type_name="analytics",
    description="Comportements d'analyse de données",
    default_files=["basic_stats.json", "data_viz.json"],
    required=False
)
```

## 2. Création d'un Nouveau Behavior

### Structure de Base
```json
{
    "type": "your_type",
    "description": "Description détaillée",
    "priority": 0.8,
    "configuration": {
        // Configuration spécifique
    }
}
```

### Étapes de Création

1. **Définir le Type**
   - Choisir un type existant ou en créer un nouveau
   - Définir la priorité appropriée

2. **Configurer les Paramètres**
   - Identifier les paramètres requis
   - Définir les valeurs par défaut
   - Documenter chaque paramètre

3. **Tester l'Intégration**
   - Valider la configuration
   - Tester les interactions
   - Vérifier la priorité

## 3. Extension des Fonctionnalités Existantes

### Ajout de Nouveaux Outils
```python
# Exemple d'ajout d'un nouvel outil
new_tool = {
    "type": "tool",
    "description": "Nouvel outil personnalisé",
    "priority": 0.7,
    "configuration": {
        "operations": {
            "custom_op": {
                "method": "POST",
                "required_params": ["data"]
            }
        }
    }
}

# Sauvegarder l'outil
await behavior_manager.save_behavior(
    behavior_id="custom_tool",
    behavior_type="tools",
    behavior_data=new_tool
)
```

### Modification des Prompts
```python
# Exemple d'ajout d'un nouveau style
new_prompt = {
    "type": "prompt",
    "description": "Nouveau style de communication",
    "priority": 0.6,
    "configuration": {
        "style_variations": {
            "custom_style": {
                "description": "Style personnalisé",
                "template": "Format personnalisé: {content}"
            }
        }
    }
}
```

## 4. Bonnes Pratiques

### Validation
- Toujours valider les configurations avant sauvegarde
- Utiliser les constantes de priorité appropriées
- Tester les interactions avec les behaviors existants

### Documentation
- Documenter clairement l'objectif du behavior
- Spécifier les dépendances
- Fournir des exemples d'utilisation

### Sécurité
- Valider les entrées utilisateur
- Respecter les règles de sécurité existantes
- Gérer correctement les erreurs

## 5. Exemple Complet

```python
# 1. Créer le behavior
custom_behavior = {
    "type": "analytics",
    "description": "Analyse statistique basique",
    "priority": BehaviorPriority.MEDIUM,
    "configuration": {
        "operations": {
            "basic_stats": {
                "metrics": ["mean", "median", "std"],
                "output_format": "json"
            }
        },
        "validation": {
            "input_type": "numeric",
            "min_samples": 10
        }
    }
}

# 2. Valider la configuration
if behavior_type not in behavior_manager.behavior_types:
    BehaviorManager.register_behavior_type(
        "analytics",
        "Analyses statistiques",
        ["basic_stats.json"],
        required=False
    )

# 3. Sauvegarder
success = await behavior_manager.save_behavior(
    "basic_stats",
    "analytics",
    custom_behavior
)

# 4. Vérifier l'intégration
if success:
    # Tester la recherche
    results = await behavior_manager.index.search(
        "analyse statistique",
        behavior_type="analytics"
    )
    
    # Vérifier les résultats
    if results:
        print("Behavior intégré avec succès")
``` 