# Utilisation des Décorateurs Tchap pour Colaig

## Vue d'ensemble

Le système de décorateurs Tchap permet d'intégrer facilement le contexte intelligent dans les commandes Colaig. Ces décorateurs gèrent automatiquement :
- La logique contextuelle Tchap (DM/salon/thread)
- Le formatage unifié des réponses
- La gestion des mentions et threads
- L'autorisation utilisateur
- La préservation du contexte conversationnel

## Décorateurs disponibles

### 1. `@tchap_contextual` - Commandes avec contexte intelligent

Le décorateur principal pour les commandes qui doivent respecter la logique contextuelle Tchap.

```python
from app.commands.decorators import tchap_contextual

@tchap_contextual(
    group="conversation",
    command=None,  # Pour handle_conversation
    help_text="Gestion des conversations générales",
    auto_format=True,
    include_authorization=True
)
async def handle_conversation(ep, matrix_client):
    """Cette fonction ne sera appelée que si on doit répondre selon le contexte Tchap"""
    # Votre logique ici
    return "Réponse contextuelle"
```

**Paramètres :**
- `group` : Groupe de la commande
- `command` : Nom de la commande (None pour les handlers généraux)
- `auto_format` : Active le formatage automatique avec NotificationFormatter
- `include_authorization` : Active la vérification d'autorisation
- `preserve_context` : Préserve l'historique conversationnel
- `timeout` : Timeout en secondes

### 2. `@tchap_thread_command` - Commandes avec thread et contexte

Pour les commandes qui initient un thread tout en respectant le contexte Tchap.

```python
from app.commands.decorators import tchap_thread_command

@tchap_thread_command(
    thread_name="sondage",
    group="interaction",
    command="sondage",
    help_text="!sondage <question> - Crée un sondage interactif",
    auto_format=True,
    timeout=300
)
async def create_poll_command(ep, matrix_client):
    """Crée un sondage avec contexte Tchap intelligent"""
    # Le thread est automatiquement créé
    # Le formatage et threading sont gérés automatiquement
    return "📊 Sondage créé ! Répondez avec 1, 2 ou 3."
```

### 3. `@tchap_aware_command` - Version simple

Pour les commandes simples qui n'ont besoin que du contexte basique.

```python
from app.commands.decorators import tchap_aware_command

@tchap_aware_command(
    group="utils",
    command="echo",
    help_text="!echo <texte> - Répète le texte",
    auto_format=False  # Formatage manuel
)
async def echo_command(ep, matrix_client):
    return f"Echo: {ep.args_str}"
```

## Logique contextuelle automatique

### Comportement selon le contexte

**Messages directs (DM) :**
```python
# Toujours répondre, pas de thread
@tchap_contextual(group="conversation", command=None)
async def handle_conversation(ep, matrix_client):
    # Sera toujours appelé en DM
    return "Réponse directe"
```

**Salon général :**
```python
# Répondre seulement si mentionné, créer un thread
@tchap_contextual(group="conversation", command=None)
async def handle_conversation(ep, matrix_client):
    # Sera appelé seulement si @colaig est mentionné
    # Réponse automatiquement threadée depuis le message original
    return "Réponse en thread"
```

**Fil de discussion :**
```python
# Répondre si mentionné OU si déjà participant
@tchap_contextual(group="conversation", command=None)
async def handle_conversation(ep, matrix_client):
    # Sera appelé si :
    # - Bot mentionné dans ce message
    # - OU bot mentionné dans le message racine du thread
    return "Réponse dans le thread existant"
```

## Formatage automatique

### Types de notification supportés

Le système détecte automatiquement le type de notification selon le contenu :

```python
@tchap_contextual(group="utils", command="test", auto_format=True)
async def test_command(ep, matrix_client):
    # Auto-détection du type :
    
    # Type "error"
    return "❌ Une erreur est survenue"
    
    # Type "success"  
    return "✅ Opération réussie"
    
    # Type "processing"
    return "📊 Traitement en cours..."
    
    # Type "info" (par défaut)
    return "ℹ️ Information générale"
```

### Formatage manuel

```python
@tchap_contextual(group="utils", command="custom", auto_format=False)
async def custom_command(ep, matrix_client):
    # Formatage manuel avec NotificationFormatter
    from app.services.notification_formatter import NotificationFormatter
    
    formatter = NotificationFormatter(matrix_client)
    tchap_context = await ep.get_tchap_context()
    response_thread_id = await ep.get_response_thread_id()
    
    await formatter.send_formatted_message(
        room_id=ep.room.room_id,
        message="Message personnalisé",
        notification_type="warning",
        thread_root=response_thread_id,
        context=tchap_context
    )
    
    # Retourner None car le message a été envoyé manuellement
    return None
```

## Exemples d'utilisation

### 1. Conversion d'une commande existante

**Avant (méthode traditionnelle) :**
```python
@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="recherche",
    help="!recherche <query> - Recherche dans les documents"
)
@only_allowed_user
async def search_documents(ep: EventParser, matrix_client: MatrixClient):
    # Logique contextuelle manuelle
    room_id = ep.room.room_id
    
    # Formatage manuel
    result = "Résultats de la recherche..."
    await matrix_client.send_markdown_message(room_id, result)
```

**Après (avec décorateur Tchap) :**
```python
@tchap_contextual(
    group="document",
    command="recherche",
    help_text="!recherche <query> - Recherche dans les documents",
    auto_format=True,
    timeout=30
)
async def search_documents(ep: EventParser, matrix_client: MatrixClient):
    # La logique contextuelle est automatique
    # Le formatage et threading sont automatiques
    return "📋 Résultats de la recherche..."
    # Le message sera automatiquement threadé si nécessaire
```

### 2. Handler de conversation intelligent

```python
@tchap_contextual(
    group="conversation",
    command=None,
    help_text="Gestion des conversations générales",
    preserve_context=True,
    timeout=60
)
async def handle_conversation(ep: EventParser, matrix_client: MatrixClient):
    """
    Handler intelligent qui :
    - Ne répond qu'en DM ou si mentionné
    - Thread automatiquement selon le contexte
    - Préserve l'historique conversationnel
    """
    
    # Récupérer le contexte conversationnel
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    from app.commands import get_unified_session_context
    
    session_context = await get_unified_session_context(
        config, ep.room.room_id, ep.sender
    )
    
    # Utiliser l'historique pour une réponse contextuelle
    user_message = ep.event.body.strip()
    
    # Votre logique de traitement ici...
    response = f"Je comprends votre message : {user_message}"
    
    return response
    # Le formatage et threading sont automatiques
```

### 3. Commande avec thread et gestion d'erreurs

```python
@tchap_thread_command(
    thread_name="calculation",
    group="tools",
    command="calc",
    help_text="!calc - Lance une calculatrice interactive",
    timeout=120,
    auto_format=True
)
async def calculator_command(ep: EventParser, matrix_client: MatrixClient):
    """Calculatrice interactive avec gestion d'erreurs automatique"""
    
    try:
        # Initialisation de la calculatrice
        return "🔢 Calculatrice activée ! Entrez une expression mathématique."
        
    except Exception as e:
        # Les erreurs sont automatiquement formatées et le thread terminé
        raise e
```

## Avantages des décorateurs Tchap

### 1. Simplification du code
- **Avant** : 50+ lignes de logique contextuelle par commande
- **Après** : 1 ligne de décorateur

### 2. Cohérence garantie
- Tous les messages respectent la logique Tchap
- Formatage unifié automatique
- Threading cohérent

### 3. Maintenance facilitée
- Logique centralisée dans les décorateurs
- Pas de duplication de code
- Évolution facile du comportement global

### 4. Robustesse
- Gestion d'erreurs intégrée
- Timeouts automatiques
- Logs détaillés

## Migration des commandes existantes

### Étapes de migration

1. **Identifier le type de commande :**
   - Commande simple → `@tchap_contextual`
   - Commande avec thread → `@tchap_thread_command`
   - Handler général → `@tchap_contextual` avec `command=None`

2. **Remplacer les décorateurs :**
   ```python
   # Remplacer :
   @register_feature(...)
   @only_allowed_user
   
   # Par :
   @tchap_contextual(...)
   ```

3. **Supprimer la logique manuelle :**
   - Logique contextuelle (mentions, threading)
   - Formatage des messages
   - Gestion des threads

4. **Adapter le retour :**
   ```python
   # Avant :
   await matrix_client.send_markdown_message(room_id, message)
   
   # Après :
   return message  # Envoi automatique
   ```

### Script de migration automatique

```python
# Exemple de script pour migrer une commande
def migrate_command(old_command_function):
    """Aide à la migration d'une commande existante"""
    
    # Analyser les décorateurs existants
    decorators = getattr(old_command_function, '__decorators__', [])
    
    # Suggérer le nouveau décorateur
    if 'threaded_command' in str(decorators):
        print("Utiliser @tchap_thread_command")
    else:
        print("Utiliser @tchap_contextual")
    
    # Analyser le corps de la fonction pour les patterns à supprimer
    # ... logique d'analyse ...
```

## Bonnes pratiques

### 1. Choix du décorateur
- `@tchap_contextual` : Pour la plupart des commandes
- `@tchap_thread_command` : Pour les interactions multi-étapes
- `@tchap_aware_command` : Pour les commandes très simples

### 2. Paramètres recommandés
```python
@tchap_contextual(
    group="votre_groupe",
    command="votre_commande",
    help_text="Description claire avec exemple",
    auto_format=True,  # Recommandé sauf cas spéciaux
    timeout=30,  # Adapter selon la complexité
    preserve_context=True  # Pour les conversations
)
```

### 3. Gestion des erreurs
```python
@tchap_contextual(...)
async def ma_commande(ep, matrix_client):
    try:
        # Votre logique
        return "✅ Succès"
    except SpecificError as e:
        # Erreurs spécifiques
        return f"⚠️ Erreur spécifique : {e}"
    # Les autres erreurs sont gérées automatiquement
```

### 4. Tests
```python
# Tester les différents contextes
async def test_command_contexts():
    # Test en DM
    # Test en salon sans mention
    # Test en salon avec mention  
    # Test en thread
    pass
```

Cette documentation fournit une base complète pour utiliser efficacement les décorateurs Tchap dans Colaig. 