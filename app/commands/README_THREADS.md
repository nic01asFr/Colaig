# Guide d'utilisation du système de thread pour les commandes

Ce guide explique comment créer des commandes utilisant le système de thread dans Albert. Ce système permet de gérer facilement des interactions complexes où l'utilisateur doit fournir plusieurs réponses à une commande.

## Concepts clés

### Qu'est-ce qu'un thread de commande ?

Un **thread de commande** est une séquence d'interactions entre l'utilisateur et le bot, qui commence par une commande initiale et se poursuit avec des réponses de l'utilisateur. Le thread maintient un contexte partagé entre ces interactions, permettant de construire des commandes complexes en plusieurs étapes.

### Cycle de vie d'un thread de commande

1. **Début** : Le thread commence quand l'utilisateur lance une commande.
2. **Interactions** : L'utilisateur répond aux questions ou prompts du bot.
3. **Fin** : Le thread se termine quand la tâche est achevée ou annulée.

## Comment créer une commande avec thread

### 1. Importer les outils nécessaires

```python
from app.commands.registry import (
    register_feature,
    only_allowed_user,
    threaded_command,
    thread_response,
    CommandThread
)
```

### 2. Créer la commande principale

```python
@register_feature(
    group="votre_groupe",
    onEvent=RoomMessageText,
    command="votre_commande",
    help="!votre_commande - Description de votre commande"
)
@only_allowed_user
@threaded_command("votre_commande")  # Indiquez le nom du thread
async def votre_commande(ep: EventParser, matrix_client: MatrixClient):
    """Votre fonction de commande principale."""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Vos traitements ici...
    
    # Pour stocker des données dans le thread:
    await CommandThread.update_state(
        room_id, sender, config,
        action="votre_action",  # État courant du thread
        vos_donnees="valeur"    # Données personnalisées
    )
    
    # Envoyer un message à l'utilisateur
    await matrix_client.send_markdown_message(
        room_id,
        "Votre message ici...",
        msgtype="m.notice"
    )
    
    # Si la commande se termine immédiatement:
    # await CommandThread.end(room_id, sender, "votre_commande", config)
    # return
    
    # Sinon, le thread reste actif pour attendre les réponses
```

### 3. Créer le gestionnaire de réponses

```python
@register_feature(
    group="votre_groupe",
    onEvent=RoomMessageText,
    command="",  # Pas de commande, car c'est une réponse
    help=""
)
@only_allowed_user
@thread_response("votre_commande")  # Nom du thread associé
async def handle_votre_commande_response(ep: EventParser, matrix_client: MatrixClient):
    """Gère les réponses de l'utilisateur."""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Récupérer la réponse de l'utilisateur
    response = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Récupérer le contexte du thread
    from app.commands import get_unified_session_context
    session_context = await get_unified_session_context(config, room_id, sender)
    conversation_state = session_context.conversation_state
    
    # Récupérer l'état actuel
    current_action = conversation_state.get("action", "")
    
    # Traiter la réponse selon l'état actuel
    if current_action == "votre_action":
        # Traitement spécifique...
        
        # Mettre à jour l'état du thread
        await CommandThread.update_state(
            room_id, sender, config,
            action="nouvelle_action"
        )
        
        # Envoyer message à l'utilisateur
        await matrix_client.send_markdown_message(
            room_id,
            "Votre réponse",
            msgtype="m.notice"
        )
    
    # Quand la commande est terminée:
    if response.lower() == "fin" or current_action == "derniere_etape":
        await CommandThread.end(
            room_id, sender, "votre_commande", config,
            action="terminé",
            resultat="succès"
        )
```

## API de CommandThread

La classe `CommandThread` fournit 4 méthodes principales:

### 1. `start(room_id, user_id, command_name, config, **thread_data)`
- Démarre un nouveau thread
- `**thread_data` permet de stocker des données initiales

### 2. `end(room_id, user_id, command_name, config, **final_data)`
- Termine le thread
- `**final_data` stocke les données finales de résultats

### 3. `is_active(room_id, user_id, config) -> (bool, str)`
- Vérifie si l'utilisateur est dans un thread actif
- Retourne un tuple (est_actif, nom_de_commande)

### 4. `update_state(room_id, user_id, config, **state_data)`
- Met à jour l'état du thread
- `**state_data` : les données à stocker/mettre à jour

## Bonnes pratiques

1. **Utilisez toujours `action` dans les données d'état** pour indiquer où en est le thread
2. **Terminez toujours les threads** avec `CommandThread.end()` dans tous les cas (succès ou erreur)
3. **Validez les entrées utilisateur** avant de les traiter
4. **Gérez proprement les erreurs** pour éviter que des threads restent bloqués
5. **Nommez vos commandes de façon explicite** pour faciliter le debugging

## Exemple complet

Pour un exemple complet, consultez le fichier `app/commands/example_command.py` qui implémente une commande `!sondage` avec le système de thread.

## Migration depuis l'ancien système

Si vous utilisez l'ancien décorateur `@command_with_thread`, vous pouvez facilement migrer vers le nouveau système:

1. Remplacez `@command_with_thread` par `@threaded_command("nom_de_votre_commande")`
2. Remplacez `await mark_command_thread_end(...)` par `await CommandThread.end(...)`
3. Pour les handlers de réponse, ajoutez le décorateur `@thread_response("nom_de_votre_commande")`

## Débogage

Pour déboguer un thread:

1. Consultez les logs qui contiennent des messages détaillés sur l'état du thread
2. Vérifiez les marqueurs `[THREAD DEBUG]` dans les logs
3. Utilisez `logger.debug` pour ajouter vos propres messages de debug

N'hésitez pas à consulter les exemples existants comme `app/commands/document_commands/attachment.py` pour mieux comprendre l'implémentation. 