# Documentation du Système de Commandes Albert

## Vue d'ensemble

Le système de commandes d'Albert est composé de plusieurs composants qui travaillent ensemble pour détecter, exécuter et gérer les commandes. Cette documentation explique comment organiser les commandes existantes et en ajouter de nouvelles pour une intégration avec le système de détection d'intention.

## Structure du Système de Commandes

Le système de commandes est organisé comme suit:

1. **Registry** (`app/commands/registry.py`): Un registre central qui maintient toutes les commandes disponibles
2. **Décorateurs** (`app/commands/decorators.py`): Des décorateurs unifiés pour définir facilement les commandes
3. **Commandes par domaine**: Les commandes sont organisées par domaine fonctionnel dans des sous-modules dédiés
4. **Système de Behavior**: Un système pour lier les commandes avec la détection d'intention

## Types de Commandes

Il existe deux types principaux de commandes:

1. **Commandes simples**: Exécutent une action et retournent une réponse
   - Utilisent le décorateur `@albert_command`
   - Une seule interaction avec l'utilisateur

2. **Commandes avec thread**: Initient une conversation interactive 
   - Utilisent `@albert_thread_command` et `@albert_thread_response`
   - Permettent plusieurs interactions avec suivi de contexte

## Comment Ajouter une Nouvelle Commande

### 1. Créer une Commande Simple

```python
from app.commands.decorators import albert_command
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

@albert_command(
    group="utils",                 # Groupe fonctionnel de la commande
    command="exemple",             # Nom de la commande (sans le !)
    aliases=["ex"],                # Alias optionnels
    help_text="!exemple - Description de la commande",
    for_geek=False,                # Commande avancée ou non
    preserve_context=True,         # Conserver le contexte de conversation
    timeout=10.0                   # Timeout en secondes
)
async def exemple_command(ep: EventParser, matrix_client: MatrixClient):
    """Implémentation de la commande."""
    # Récupérer les arguments
    args = ep.args_str
    
    # Accéder à la configuration
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    
    # Logique de la commande
    # ...
    
    # Renvoyer le résultat
    return "Résultat de la commande exemple"
```

### 2. Créer une Commande avec Thread

#### Initialisation du Thread:

```python
from app.commands.decorators import albert_thread_command
from app.commands.registry import CommandThread
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

@albert_thread_command(
    thread_name="mon_thread",      # Identifiant unique du thread
    group="interaction",           # Groupe fonctionnel
    command="interactif",          # Nom de la commande
    help_text="!interactif - Commence une interaction en plusieurs étapes",
    preserve_context=True,
    timeout=30.0
)
async def interactif_command(ep: EventParser, matrix_client: MatrixClient):
    """Démarre un thread d'interaction."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Initialiser l'état du thread
    await CommandThread.update_state(
        room_id, sender, config,
        action="attente_reponse",
        data={}  # Données à conserver pendant le thread
    )
    
    # Message d'instructions
    return "Commande interactive démarrée. Quelle est votre question?"
```

#### Gestion des Réponses:

```python
@albert_thread_response(
    thread_name="mon_thread",       # Doit correspondre au thread_command
    preserve_context=True,
    timeout=60.0
)
async def interactif_response(ep: EventParser, matrix_client: MatrixClient):
    """Traite les réponses dans le thread."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Récupérer la réponse utilisateur
    response = ep.event.body.strip()
    
    # Terminer le thread si demandé
    if response.lower() in ["exit", "quit", "fin"]:
        await CommandThread.end(room_id, sender, "mon_thread", config)
        return "Thread terminé."
    
    # Traiter la réponse
    # ...
    
    # Mettre à jour l'état si besoin
    await CommandThread.update_state(
        room_id, sender, config,
        action="nouvelle_étape",
        data={}  # Nouvelles données
    )
    
    return "Voici la réponse à votre question..."
```

## Intégration avec le Système de Behavior

Pour que les commandes puissent être détectées et exécutées par le système d'intention, elles doivent être intégrées au système de Behavior. Voici comment procéder:

### 1. Créer une Configuration d'Action pour la Commande

Créez un fichier JSON dans le dossier `actions` du système de behavior:

```json
{
  "type": "action",
  "id": "commande_exemple",
  "description": "Exécute la commande exemple quand l'utilisateur veut...",
  "priority": 0.8,
  "configuration": {
    "command": "exemple",
    "examples": [
      "Je voudrais utiliser la fonction exemple",
      "Comment puis-je faire un exemple?",
      "Lance l'exemple s'il te plaît"
    ],
    "parameters": [],
    "response_template": "Exécution de la commande exemple: {{result}}"
  }
}
```

### 2. Adapter la Commande pour l'Intégration avec le Behavior

```python
@albert_command(
    group="utils",
    command="exemple",
    aliases=["ex"],
    help_text="!exemple - Description de la commande",
    preserve_context=True
)
async def exemple_command(ep: EventParser, matrix_client: MatrixClient):
    """Version adaptée pour l'intégration avec le behavior."""
    # Code standard de la commande
    # ...
    
    # Retourner un résultat structuré pour le behavior
    return {
        "status": "success",
        "result": "Résultat de la commande",
        "metadata": {
            "type": "text",
            "action": "exemple"
        }
    }
```

### 3. Enregistrer le Behavior dans le Système

Pour que le système de détection d'intention puisse trouver et exécuter la commande, il faut l'enregistrer dans le système de behavior:

```python
async def register_command_behaviors(behavior_manager):
    """Enregistre les behaviors liés aux commandes."""
    # Charger la configuration depuis le fichier JSON
    config_path = os.path.join("behaviors", "actions", "commande_exemple.json")
    with open(config_path, "r") as f:
        config = json.load(f)
    
    # Enregistrer dans le behavior manager
    await behavior_manager.save_behavior(
        behavior_id="commande_exemple",
        behavior_type="actions",
        behavior_data=config
    )
```

## Organisation des Commandes pour la Détection d'Intention

Pour organiser efficacement les commandes pour la détection d'intention:

1. **Grouper par fonctionnalité**: Organisez les commandes par domaine fonctionnel
2. **Définir des exemples clairs**: Pour chaque commande, définissez des exemples d'intention variés
3. **Standardiser les retours**: Utilisez un format de retour cohérent pour faciliter l'intégration
4. **Documenter les paramètres**: Documentez clairement les paramètres attendus par chaque commande

## Architecture Proposée pour l'Intégration

```
┌──────────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│                  │     │                     │     │                  │
│  Entrée User     │────▶│  Analyse d'Intention │────▶│  Behavior System │
│                  │     │                     │     │                  │
└──────────────────┘     └─────────────────────┘     └────────┬─────────┘
                                                              │
                                                              ▼
┌──────────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│                  │     │                     │     │                  │
│  Réponse User    │◀────│  Formatage Réponse  │◀────│  Commande Albert │
│                  │     │                     │     │                  │
└──────────────────┘     └─────────────────────┘     └──────────────────┘
```

## Prochaines étapes recommandées

1. **Cataloguer les commandes existantes**: Répertorier toutes les commandes actives
2. **Créer des behaviors pour chaque commande**: Définir les configurations de behavior
3. **Adapter les commandes si nécessaire**: Standardiser les retours des commandes
4. **Implémenter le lien entre intention et commandes**: Créer le mécanisme qui lie la détection d'intention à l'exécution des commandes
5. **Tester le système intégré**: Vérifier que l'analyse d'intention déclenche correctement les commandes

## Conclusion

Ce système permettra à Albert de comprendre naturellement les intentions de l'utilisateur et d'exécuter les commandes appropriées sans que l'utilisateur ait besoin d'utiliser explicitement la syntaxe de commande. À terme, cela rendra l'interaction avec Albert plus naturelle et intuitive. 