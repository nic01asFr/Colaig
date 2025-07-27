# Guide Pratique : Utilisation des Décorateurs dans l'Architecture de Contexte Global

## 🎯 Objectif

Ce guide montre comment utiliser efficacement le système de décorateurs Tchap dans le cadre de l'architecture complète de résolution du contexte global. Il illustre les meilleures pratiques et les patterns d'utilisation recommandés.

## 🏗️ Vue d'ensemble de l'Intégration

### Architecture Simplifiée pour le Développeur

```
Votre Commande
      ↓
@tchap_contextual ← Décorateur intelligent
      ↓
┌─────────────────────────────────────────┐
│ Résolution Automatique du Contexte     │
│ ├── Contexte Tchap (DM/salon/thread)   │
│ ├── Contexte Session (historique)      │
│ ├── Services injectés (WebDAV, Index)  │
│ └── Formatage unifié des réponses      │
└─────────────────────────────────────────┘
      ↓
Exécution de votre logique métier
      ↓
Réponse automatiquement formatée et envoyée
```

## 🚀 Utilisation Pratique

### 1. Commande de Conversation Intelligente

**Cas d'usage** : Gérer les conversations générales avec logique contextuelle

```python
from app.commands.decorators import tchap_contextual
from app.matrix_bot.eventparser import MessageEventParser
from app.matrix_bot.client import MatrixClient

@tchap_contextual(
    group="conversation",
    command=None,  # Toutes les conversations non-commandes
    help_text="Gestion intelligente des conversations",
    auto_format=True,
    preserve_context=True,
    timeout=30.0,
    include_authorization=True
)
async def handle_intelligent_conversation(
    ep: MessageEventParser,
    matrix_client: MatrixClient,
    session_context,  # ← Injecté automatiquement
    webdav_service,   # ← Injecté automatiquement
    index_service,    # ← Injecté automatiquement
    behavior_manager  # ← Injecté automatiquement
):
    """
    Gestion des conversations avec contexte Tchap intelligent.
    
    Le décorateur se charge automatiquement de :
    - Vérifier si le bot doit répondre selon le contexte
    - Injecter tous les services nécessaires
    - Préserver l'historique de conversation
    - Formater la réponse selon le contexte
    """
    
    # ✅ Le contexte Tchap a déjà été vérifié par le décorateur
    # ✅ Les services sont déjà initialisés et injectés
    # ✅ L'historique de session est disponible
    
    message = ep.event.body
    sender = ep.sender_id()
    room_id = ep.room.room_id
    
    # Accès direct à l'historique de conversation
    conversation_history = session_context.history
    
    # Utiliser les services injectés
    if len(conversation_history) > 10:
        # Recherche dans l'index pour le contexte
        relevant_docs = await index_service.search(
            query=message,
            limit=3,
            context=session_context
        )
    
    # Générer la réponse avec le behavior_manager
    response = await behavior_manager.generate_response(
        message=message,
        context=session_context,
        relevant_docs=relevant_docs if 'relevant_docs' in locals() else []
    )
    
    # ✅ Le décorateur se charge automatiquement de :
    # - Déterminer le thread_id de réponse
    # - Formater avec NotificationFormatter
    # - Envoyer via MatrixClient
    # - Sauvegarder le contexte mis à jour
    
    return response  # Retour simple, formatage automatique
```

### 2. Commande avec Thread et Workflow

**Cas d'usage** : Analyse de document avec suivi de progression

```python
@tchap_thread_command(
    thread_name="document_analysis",
    group="document",
    command="analyze",
    aliases=["analyse"],
    help_text="Analyse complète d'un document",
    auto_format=True,
    preserve_context=True,
    timeout=120.0  # Timeout plus long pour l'analyse
)
async def analyze_document_threaded(
    ep: MessageEventParser,
    matrix_client: MatrixClient,
    session_context,
    webdav_service,
    index_service,
    behavior_manager,
    notification_formatter  # ← Service de formatage injecté
):
    """
    Analyse de document avec workflow thread.
    """
    
    command = ep.get_command()
    if len(command) < 2:
        return "❌ Veuillez spécifier le nom du document à analyser."
    
    document_name = " ".join(command[1:])
    room_id = ep.room.room_id
    
    # ✅ Le décorateur a automatiquement :
    # - Créé un thread pour cette analyse
    # - Vérifié le contexte Tchap
    # - Injecté tous les services
    
    # Étape 1 : Notification de début (formatage automatique)
    await notification_formatter.send_progress_update(
        matrix_client,
        room_id,
        f"🔍 Début de l'analyse du document : {document_name}",
        thread_id=await ep.get_response_thread_id()
    )
    
    # Étape 2 : Recherche du document
    try:
        document = await webdav_service.get_document(
            room_id=room_id,
            document_name=document_name
        )
        
        if not document:
            return f"❌ Document '{document_name}' non trouvé."
            
    except Exception as e:
        return f"❌ Erreur lors de la récupération : {str(e)}"
    
    # Étape 3 : Indexation si nécessaire
    await notification_formatter.send_progress_update(
        matrix_client,
        room_id,
        f"📚 Indexation du document en cours...",
        thread_id=await ep.get_response_thread_id()
    )
    
    await index_service.index_document(document, room_id)
    
    # Étape 4 : Analyse avec le behavior_manager
    analysis_result = await behavior_manager.analyze_document(
        document=document,
        context=session_context
    )
    
    # ✅ Le décorateur gère automatiquement :
    # - Le formatage de la réponse finale
    # - L'envoi dans le bon thread
    # - La sauvegarde du contexte
    
    return f"""✅ **Analyse terminée pour {document_name}**

📊 **Résumé :**
{analysis_result.get('summary', 'Résumé non disponible')}

🔍 **Points clés :**
{chr(10).join(f"• {point}" for point in analysis_result.get('key_points', []))}

💡 **Recommandations :**
{chr(10).join(f"• {rec}" for rec in analysis_result.get('recommendations', []))}"""
```

### 3. Commande Utilitaire Simple

**Cas d'usage** : Commande d'aide contextuelle

```python
@tchap_aware_command(
    group="utility",
    command="help",
    aliases=["aide", "h"],
    help_text="Affiche l'aide contextuelle",
    auto_format=False,  # Formatage manuel pour l'aide
    include_authorization=False  # Pas d'autorisation requise
)
async def contextual_help(
    ep: MessageEventParser,
    matrix_client: MatrixClient,
    session_context,
    behavior_manager
):
    """
    Aide contextuelle intelligente selon le salon et l'utilisateur.
    """
    
    command = ep.get_command()
    room_id = ep.room.room_id
    user_id = ep.sender_id()
    
    # ✅ Le décorateur a vérifié le contexte Tchap
    # ✅ Accès au contexte de session pour personnalisation
    
    # Aide spécifique selon le contexte
    if ep.room_is_direct_message():
        help_context = "dm"
        intro = "En **message privé**, vous pouvez :"
    else:
        help_context = "salon"
        intro = f"Dans ce **salon**, mentionnez-moi (@albert) pour :"
    
    # Récupérer les commandes disponibles selon le contexte
    available_commands = await behavior_manager.get_available_commands(
        user_id=user_id,
        room_id=room_id,
        context=help_context
    )
    
    # Personnalisation selon l'historique
    if len(session_context.history) > 0:
        last_commands = [
            msg.get('content', '') for msg in session_context.history[-5:]
            if msg.get('role') == 'user' and msg.get('content', '').startswith('!')
        ]
        if last_commands:
            recent_commands = ", ".join(set(last_commands))
            intro += f"\n\n💡 *Commandes récentes : {recent_commands}*"
    
    # Construction de l'aide
    help_text = f"""{intro}

📋 **Commandes disponibles :**

{chr(10).join(f"• `{cmd['name']}` - {cmd['description']}" for cmd in available_commands[:10])}

ℹ️ **Navigation :**
• `!help <commande>` - Aide détaillée sur une commande
• `!commands` - Liste complète des commandes

💬 **Conversation naturelle :**
Vous pouvez aussi me parler naturellement, je comprendrai !"""

    # ✅ Retour avec formatage automatique même si auto_format=False
    # Le décorateur utilise le formatage de base
    return help_text
```

### 4. Gestion Avancée des Services

**Cas d'usage** : Commande nécessitant plusieurs services avec gestion d'erreurs

```python
@tchap_contextual(
    group="advanced",
    command="search",
    help_text="Recherche avancée avec multiple services",
    required_services=["webdav_service", "index_service", "embedding_service"],
    auto_format=True,
    preserve_context=True,
    timeout=45.0
)
async def advanced_search(
    ep: MessageEventParser,
    matrix_client: MatrixClient,
    session_context,
    webdav_service,
    index_service,
    embedding_service,
    behavior_manager
):
    """
    Recherche avancée utilisant plusieurs services.
    """
    
    command = ep.get_command()
    if len(command) < 2:
        return "❌ Veuillez spécifier votre recherche."
    
    query = " ".join(command[1:])
    room_id = ep.room.room_id
    
    # ✅ Tous les services requis sont garantis disponibles
    # ✅ Le décorateur a vérifié leur initialisation
    
    try:
        # Recherche multi-services en parallèle
        import asyncio
        
        # Lancement des recherches en parallèle
        tasks = [
            webdav_service.search_documents(room_id, query),
            index_service.semantic_search(query, room_id),
            embedding_service.find_similar(query, room_id)
        ]
        
        # Attendre les résultats avec timeout individuel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        webdav_results, index_results, embedding_results = results
        
        # Fusionner les résultats
        combined_results = []
        
        # WebDAV results
        if not isinstance(webdav_results, Exception):
            combined_results.extend([
                {"source": "WebDAV", "type": "document", **result}
                for result in webdav_results[:5]
            ])
        
        # Index results  
        if not isinstance(index_results, Exception):
            combined_results.extend([
                {"source": "Index", "type": "semantic", **result}
                for result in index_results[:5]
            ])
        
        # Embedding results
        if not isinstance(embedding_results, Exception):
            combined_results.extend([
                {"source": "Embedding", "type": "similar", **result}
                for result in embedding_results[:5]
            ])
        
        if not combined_results:
            return f"❌ Aucun résultat trouvé pour '{query}'"
        
        # Classement intelligent des résultats
        ranked_results = await behavior_manager.rank_search_results(
            results=combined_results,
            query=query,
            context=session_context
        )
        
        # Formatage des résultats
        response = f"🔍 **Résultats pour '{query}'** ({len(ranked_results)} trouvés)\n\n"
        
        for i, result in enumerate(ranked_results[:8], 1):
            source_icon = {
                "WebDAV": "📄",
                "Index": "🧠", 
                "Embedding": "🔗"
            }.get(result["source"], "📌")
            
            response += f"{source_icon} **{i}.** {result.get('title', 'Sans titre')}\n"
            if result.get('snippet'):
                response += f"   *{result['snippet'][:100]}...*\n"
            response += f"   Score: {result.get('score', 0):.2f} | Source: {result['source']}\n\n"
        
        return response
        
    except asyncio.TimeoutError:
        return "⏱️ Recherche interrompue (timeout) - essayez une requête plus spécifique."
    except Exception as e:
        # ✅ Le décorateur gère automatiquement les erreurs
        # mais on peut les personnaliser ici
        return f"❌ Erreur lors de la recherche : {str(e)}"
```

## 💡 Bonnes Pratiques

### 1. Choix du Bon Décorateur

| Situation | Décorateur Recommandé | Raison |
|-----------|----------------------|---------|
| Conversation générale | `@tchap_contextual` | Logique contextuelle complète |
| Commande avec progression | `@tchap_thread_command` | Thread automatique + suivi |
| Commande simple/utilitaire | `@tchap_aware_command` | Léger, contextuel minimal |
| Migration d'existant | `@tchap_contextual` | Compatible avec l'existant |

### 2. Gestion des Services

```python
# ✅ BON : Laisser l'injection automatique
@tchap_contextual(group="test", command="good")
async def good_function(session_context, webdav_service):
    # Services injectés automatiquement
    pass

# ❌ MAUVAIS : Import manuel des services
@tchap_contextual(group="test", command="bad")
async def bad_function(ep, matrix_client):
    from app.services.context.instance import get_context_manager
    # Redondant avec l'injection automatique
    pass
```

### 3. Gestion des Timeouts

```python
# ✅ BON : Timeout adapté au type de commande
@tchap_contextual(
    command="quick_task",
    timeout=10.0  # Court pour tâches rapides
)
async def quick_task(): pass

@tchap_thread_command(
    command="long_analysis", 
    timeout=300.0  # Long pour analyses complexes
)
async def long_analysis(): pass

# ✅ BON : Pas de timeout pour commandes très longues
@tchap_contextual(
    command="rebuild_index",
    timeout=None  # Aucun timeout
)
async def rebuild_index(): pass
```

### 4. Formatage des Réponses

```python
# ✅ BON : Laisser le formatage automatique
@tchap_contextual(auto_format=True)
async def auto_formatted():
    return "Simple string response"  # Formaté automatiquement
    
# ✅ BON : Formatage manuel si nécessaire
@tchap_contextual(auto_format=False)
async def manual_formatted(notification_formatter, matrix_client, ep):
    await notification_formatter.send_formatted_message(
        matrix_client,
        ep.room.room_id,
        "Custom formatted message",
        message_type="success",
        thread_id=await ep.get_response_thread_id()
    )
    return None  # Pas de retour car envoi manuel
```

## 🔧 Debugging et Troubleshooting

### Logs Utiles

Le système génère des logs détaillés pour le debugging :

```python
# Activer les logs de debug pour les décorateurs
import logging
logging.getLogger("app.commands.decorators").setLevel(logging.DEBUG)

# Logs typiques générés :
# [DEBUG] Contexte Tchap résolu: DM, should_respond=True
# [DEBUG] Services injectés: context_manager, webdav_service, index_service
# [DEBUG] Commande exécutée en 2.3s
# [DEBUG] Contexte sauvegardé avec succès
```

### Vérification des Services

```python
@tchap_contextual(required_services=["webdav_service", "index_service"])
async def check_services(webdav_service, index_service):
    """Les services sont garantis disponibles si la fonction s'exécute"""
    
    # Vérification additionnelle si nécessaire
    if not await webdav_service.is_connected():
        return "⚠️ WebDAV temporairement indisponible"
    
    # Continuer normalement
    pass
```

Cette architecture offre une expérience de développement simplifiée tout en maintenant la puissance et la flexibilité nécessaires pour des fonctionnalités avancées. 