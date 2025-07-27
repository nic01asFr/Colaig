# Exemple d'Adaptation : Commande !pj avec Contexte Tchap Intelligent

## Vue d'ensemble

Cet exemple montre comment adapter la commande `!pj` existante pour utiliser le nouveau système de contexte Tchap intelligent, permettant à Colaig de se comporter naturellement selon le contexte.

## Comportement attendu selon le contexte

### 📱 **En message direct (DM)**
```
Utilisateur → Colaig (DM): "!pj" + pièce jointe
Colaig → Utilisateur (DM): "✅ Analyse du document en cours..."
Colaig → Utilisateur (DM): "📊 Voici les options de classement proposées..."
```

### 👥 **En salon général**
```
Utilisateur → Salon: "@colaig !pj" + pièce jointe
Colaig → Thread depuis ce message: "✅ Analyse du document en cours..."
Colaig → Thread: "📊 Voici les options de classement proposées..."
Utilisateur → Thread: "Option 2 semble bien"
Colaig → Thread: "✅ Document classé dans le dossier choisi"
```

### 🧵 **En thread existant (où Colaig participe)**
```
[Dans un thread où Colaig a été mentionné initialement]
Utilisateur → Thread: "!pj" + pièce jointe (sans mention)
Colaig → Thread: "✅ Analyse du document en cours..."
```

## Code avant adaptation

```python
@register_feature(
    group="documents", 
    onEvent=RoomMessageText,
    command="pj",
    help="📎 `!pj` : analyser une pièce jointe"
)
@only_allowed_user  
async def handle_attachments_adapted_command(ep: EventParser, matrix_client: MatrixClient):
    """Gère la commande !pj pour analyser une pièce jointe et proposer un classement intelligent."""
    
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # Détection manuelle du thread (ancien système)
    content = ep.event.source.get('content', {})
    is_in_matrix_thread = False
    matrix_thread_id = None
    
    if 'm.relates_to' in content and content['m.relates_to'].get('rel_type') == 'm.thread':
        is_in_matrix_thread = True
        matrix_thread_id = content['m.relates_to'].get('event_id')
    
    # Message d'aide sans contexte
    if not hasattr(ep.event, 'source') or 'content' not in ep.event.source:
        await matrix_client.send_markdown_message(
            room_id,
            "Pour classer intelligemment un document, veuillez utiliser cette commande en **réponse** à un message contenant une pièce jointe.",
            msgtype="m.notice",
            thread_root=matrix_thread_id if is_in_matrix_thread else None
        )
        return
    
    # Traitement...
    
    # Réponse sans contexte unifié
    await matrix_client.send_markdown_message(
        room_id, 
        final_message, 
        thread_root=matrix_thread_id if is_in_matrix_thread else None
    )
```

## Code après adaptation

```python
@register_feature(
    group="documents", 
    onEvent=RoomMessageText,
    command="pj",
    help="📎 `!pj` : analyser une pièce jointe"
)
@only_allowed_user  
async def handle_attachments_adapted_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Gère la commande !pj pour analyser une pièce jointe et proposer un classement intelligent.
    
    Comportement contextuel intelligent :
    - DM : Répond directement
    - Salon : Crée un thread depuis le message de commande
    - Thread : Continue dans le thread existant
    """
    
    # ✨ NOUVELLE LOGIQUE CONTEXTUELLE
    # Vérifier si on doit traiter cette commande selon le contexte
    if not await ep.should_respond_in_context():
        logger.info("[PJ] Commande ignorée - contexte ne nécessite pas de réponse")
        return
    
    # Obtenir le contexte pour le formatage et les logs
    context = await ep.get_tchap_context()
    logger.info(f"[PJ] Contexte résolu: {context.context_type.value}, "
                f"mentionné: {context.is_mentioned}")
    
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # ✨ NOUVEAU SYSTÈME DE THREADING AUTOMATIQUE
    # Plus besoin de détection manuelle, le contexte gère tout
    response_thread_id = await ep.get_response_thread_id()
    
    # Importation du formateur unifié
    from app.services.notification_formatter import NotificationFormatter
    
    # Vérification de la pièce jointe avec message contextuel
    if not hasattr(ep.event, 'source') or 'content' not in ep.event.source:
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            room_id,
            "Pour classer intelligemment un document, veuillez utiliser cette commande en **réponse** à un message contenant une pièce jointe.",
            context,
            "info",
            ep.event.event_id
        )
        return
    
    # Démarrage du traitement avec notification contextuelle
    await NotificationFormatter.send_formatted_message(
        matrix_client,
        room_id,
        "🔍 Analyse du document en cours...",
        context,
        "processing",
        ep.event.event_id
    )
    
    try:
        # Traitement de l'analyse (logique métier inchangée)
        analysis_result = await analyze_attachment(ep, config)
        
        # Message de résultat avec formatage unifié
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            room_id,
            analysis_result,
            context,
            "success",
            ep.event.event_id
        )
        
        # Démarrage du thread de commande pour les interactions suivantes
        from app.commands.registry import CommandThread
        await CommandThread.start(
            room_id=room_id,
            user_id=ep.sender,
            command_name="pj",
            config=config,
            analysis_data=analysis_result,
            context=context  # ✨ NOUVEAU : Passage du contexte
        )
        
    except Exception as e:
        logger.error(f"[PJ] Erreur lors de l'analyse: {str(e)}")
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            room_id,
            f"❌ Erreur lors de l'analyse du document: {str(e)}",
            context,
            "error",
            ep.event.event_id
        )


# ✨ ADAPTATION DU GESTIONNAIRE DE RÉPONSE
@thread_response(command_name="pj")
async def handle_pj_response(ep: EventParser, matrix_client: MatrixClient):
    """Gestionnaire de réponse pour la commande pj avec contexte intelligent"""
    
    # Vérifier le contexte (important pour les threads)
    if not await ep.should_respond_in_context():
        return
    
    context = await ep.get_tchap_context()
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    
    # Récupération des données du thread
    thread_data = await CommandThread.get_data(ep.room.room_id, ep.sender, config)
    
    if not thread_data:
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            ep.room.room_id,
            "⚠️ Aucune analyse en cours. Utilisez `!pj` pour commencer.",
            context,
            "warning",
            ep.event.event_id
        )
        return
    
    user_response = ep.event.body.strip()
    
    try:
        # Traitement de la réponse utilisateur
        result = await process_user_choice(user_response, thread_data)
        
        # Notification de succès avec contexte
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            ep.room.room_id,
            result,
            context,
            "success",
            ep.event.event_id
        )
        
        # Fin du thread de commande
        await CommandThread.end(
            room_id=ep.room.room_id,
            user_id=ep.sender,
            command_name="pj",
            config=config,
            final_result=result
        )
        
    except Exception as e:
        await NotificationFormatter.send_formatted_message(
            matrix_client,
            ep.room.room_id,
            f"❌ Erreur lors du traitement: {str(e)}",
            context,
            "error",
            ep.event.event_id
        )
```

## Principales améliorations

### 🎯 **1. Contexte intelligent**
```python
# Avant : Logique manuelle complexe
is_in_matrix_thread = False
matrix_thread_id = None
if 'm.relates_to' in content and content['m.relates_to'].get('rel_type') == 'm.thread':
    is_in_matrix_thread = True
    matrix_thread_id = content['m.relates_to'].get('event_id')

# Après : Automatique et intelligent
if not await ep.should_respond_in_context():
    return
context = await ep.get_tchap_context()
response_thread_id = await ep.get_response_thread_id()
```

### 🎨 **2. Formatage unifié**
```python
# Avant : Messages sans contexte
await matrix_client.send_markdown_message(
    room_id, 
    message, 
    thread_root=matrix_thread_id if is_in_matrix_thread else None
)

# Après : Formatage contextuel automatique
await NotificationFormatter.send_formatted_message(
    matrix_client,
    room_id,
    message,
    context,
    "success",
    ep.event.event_id
)
```

### 📊 **3. Logs détaillés**
```python
logger.info(f"[PJ] Contexte résolu: {context.context_type.value}, "
            f"mentionné: {context.is_mentioned}")
```

## Tests de validation

### Test 1 : DM
```python
async def test_pj_command_dm():
    """Test commande !pj en DM"""
    # Setup DM avec pièce jointe
    ep = create_dm_event_parser("!pj", with_attachment=True)
    
    # Exécution
    await handle_attachments_adapted_command(ep, matrix_client)
    
    # Vérifications
    assert_message_sent_to_dm()
    assert_no_thread_created()
```

### Test 2 : Salon avec mention
```python
async def test_pj_command_salon_mention():
    """Test commande !pj en salon avec mention"""
    # Setup salon avec mention + pièce jointe
    ep = create_salon_event_parser("@colaig !pj", with_attachment=True)
    
    # Exécution
    await handle_attachments_adapted_command(ep, matrix_client)
    
    # Vérifications
    assert_thread_created_from_message()
    assert_response_in_thread()
```

### Test 3 : Thread participation continue
```python
async def test_pj_command_thread_participation():
    """Test commande !pj en thread où le bot participe"""
    # Setup thread où bot mentionné initialement
    ep = create_thread_event_parser("!pj", 
                                   thread_root_mentions_bot=True,
                                   with_attachment=True)
    
    # Exécution
    await handle_attachments_adapted_command(ep, matrix_client)
    
    # Vérifications
    assert_response_in_existing_thread()
    assert_no_new_thread_created()
```

## Résultat

Avec cette adaptation, la commande `!pj` :

✅ **Se comporte naturellement** selon le contexte Tchap
✅ **Utilise un formatage unifié** pour tous les messages
✅ **Gère automatiquement** les threads et références
✅ **Fournit des logs détaillés** pour le debugging
✅ **Maintient la compatibilité** avec l'existant

La commande devient ainsi **plus intuitive** pour les utilisateurs et **plus maintenable** pour les développeurs, tout en respectant parfaitement les conventions Tchap. 