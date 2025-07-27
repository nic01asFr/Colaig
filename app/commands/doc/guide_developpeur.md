# Guide du développeur pour les commandes Albert

Ce guide explique comment créer des commandes pour Albert en utilisant le nouveau système de décorateurs.

## Introduction

Les décorateurs Albert simplifient la création de commandes en unifiant toutes les fonctionnalités nécessaires dans une seule interface. Ils permettent de gérer :

- L'enregistrement des commandes
- La gestion des erreurs
- La préservation du contexte de conversation
- Les timeouts
- Les threads de conversation
- L'historique des commandes

## Types de commandes

Il existe deux types principaux de commandes :

1. **Commandes simples** : exécutent une action et retournent une réponse
2. **Commandes avec thread** : initient une conversation interactive avec l'utilisateur

## Utiliser les décorateurs

### Commandes simples

Pour créer une commande simple, utilisez le décorateur `@albert_command` :

```python
from app.commands.decorators import albert_command
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

@albert_command(
    group="utils",                 # Groupe de commandes (obligatoire)
    command="echo",                # Nom de la commande (obligatoire)
    aliases=["repete"],            # Alias optionnels
    help_text="!echo <texte> - Répète le texte envoyé",  # Texte d'aide
    for_geek=False,                # Si la commande est pour "geeks" uniquement
    preserve_context=True,         # Préserver le contexte de conversation
    timeout=10.0                   # Timeout en secondes (optionnel)
)
async def echo_command(ep: EventParser, matrix_client: MatrixClient):
    """Répète le texte envoyé par l'utilisateur."""
    # Récupérer le texte après la commande
    text = ep.args_str.strip()
    
    if not text:
        # Si pas de texte, afficher l'usage
        return "Usage : !echo <texte à répéter>"
    
    # Simuler un traitement
    await asyncio.sleep(1)
    
    # Retourner la réponse
    return f"🔊 Echo : {text}"
```

### Commandes avec thread

Pour créer une commande qui initie un thread de conversation, utilisez `@albert_thread_command` et `@albert_thread_response` :

1. **Initialisation du thread** :

```python
from app.commands.decorators import albert_thread_command, albert_thread_response
from app.commands.registry import CommandThread
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

@albert_thread_command(
    thread_name="calculatrice",    # Nom du thread (obligatoire)
    group="utils",                  # Groupe de commandes
    command="calculatrice",         # Nom de la commande
    help_text="!calculatrice - Effectue des calculs interactifs",
    preserve_context=True,
    timeout=30.0
)
async def calculatrice_command(ep: EventParser, matrix_client: MatrixClient):
    """Démarre un thread pour effectuer des calculs interactifs."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Initialiser l'état du thread
    await CommandThread.update_state(
        room_id, sender, config,
        action="attente_expression",
        history=[]  # Historique des calculs
    )
    
    # Message d'instructions
    return """🧮 **Calculatrice interactive**

Entrez une expression mathématique et je la calculerai.
Exemples: `2+2`, `(3*4)/2`, `sqrt(16)`

Pour quitter, tapez `exit` ou `quit`.
"""
```

2. **Gestion des réponses** :

```python
# Fonction de validation pour les réponses
def validate_calculatrice_input(text):
    """Vérifie si la réponse est une expression mathématique valide ou une commande de sortie."""
    return text.strip() != "" and (
        re.match(r"^[\d\s\+\-\*\/\(\)\.\,\^]+$", text) is not None or 
        text.lower() in ["exit", "quit", "sortir", "quitter"]
    )

@albert_thread_response(
    thread_name="calculatrice",     # Nom du thread (doit correspondre au thread_command)
    validate_format=validate_calculatrice_input,  # Fonction de validation (optionnelle)
    preserve_context=True,
    timeout=60.0
)
async def calculatrice_response(ep: EventParser, matrix_client: MatrixClient):
    """Traite les réponses pour la calculatrice."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire l'expression
    expression = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Gérer la sortie
    if expression.lower() in ["exit", "quit", "sortir", "quitter"]:
        # Terminer le thread
        await CommandThread.end(room_id, sender, "calculatrice", config)
        return "✅ Calculatrice fermée."
    
    # Récupérer l'état du thread
    from app.commands import get_unified_session_context
    session_context = await get_unified_session_context(config, room_id, sender)
    conversation_state = session_context.conversation_state
    
    # Récupérer l'historique des calculs
    history = conversation_state.get("history", [])
    
    try:
        # Évaluer l'expression (avec des précautions de sécurité)
        import math
        safe_globals = {
            "abs": abs, "round": round,
            "math": math, "sqrt": math.sqrt,
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "pi": math.pi, "e": math.e
        }
        result = eval(expression, {"__builtins__": {}}, safe_globals)
        
        # Formater le résultat
        formatted_result = f"{expression} = {result}"
        
        # Mettre à jour l'historique
        history.append(formatted_result)
        await CommandThread.update_state(
            room_id, sender, config,
            action="attente_expression",
            history=history
        )
        
        # Retourner le résultat
        return f"🔢 {formatted_result}"
    
    except Exception as e:
        # Gérer les erreurs d'évaluation
        return f"⚠️ Erreur: {str(e)}"
```

## Bonnes pratiques

1. **Préservation du contexte**
   - Utilisez `preserve_context=True` pour conserver l'historique de conversation et les informations importantes.

2. **Gestion des erreurs**
   - Les décorateurs interceptent automatiquement les exceptions, mais ajoutez des try/except pour gérer les erreurs spécifiques.

3. **Timeouts**
   - Définissez toujours un timeout raisonnable pour éviter qu'une commande ne bloque indéfiniment.

4. **États des threads**
   - Pour les commandes avec thread, utilisez toujours `CommandThread.update_state()` pour mettre à jour l'état et `CommandThread.end()` pour terminer proprement.

5. **Validation des entrées**
   - Pour les réponses de thread, utilisez `validate_format` pour rejeter immédiatement les réponses invalides.

## Exemples de référence

Pour des exemples complets, consultez :
- Commandes simples : [app/commands/examples/echo_command.py](../examples/echo_command.py)
- Commandes avec thread : [app/commands/examples/calculator_command.py](../examples/calculator_command.py)
- Adaptation de commandes existantes : [app/commands/document_commands/docquery_adapted.py](../document_commands/docquery_adapted.py)

## Migration des commandes existantes

Pour migrer des commandes existantes vers le nouveau système :

1. Remplacez les décorateurs existants (`@command`, `@thread_command`, etc.) par les nouveaux décorateurs unifiés.
2. Assurez-vous que la logique de gestion du contexte est compatible.
3. Ajoutez la gestion des timeouts si nécessaire.

## Intégration dans le système

Les nouvelles commandes seront automatiquement intégrées dans le système de routage de messages d'Albert. Aucune modification supplémentaire n'est nécessaire.

Pour toute question ou suggestion concernant le système de commandes, contactez l'équipe de développement. 