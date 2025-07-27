# Architecture de Résolution du Contexte Global pour Colaig

## Vue d'ensemble

Cette documentation décrit l'interface complète avec l'ensemble des mécanismes qui participent à la résolution du contexte global pour chaque message traité par Colaig. Le système est conçu en couches interconnectées qui collaborent pour fournir un contexte riche et intelligent.

## 🏗️ Architecture en Couches

### 1. Couche Matrix/Tchap (Interface d'entrée)

**Composants principaux :**
- **EventParser** : Parse les événements Matrix et extrait les métadonnées
- **MessageEventParser** : Spécialisé pour les messages texte et commandes  
- **MatrixClient** : Client Matrix/Tchap pour les communications
- **Callbacks** : Gestionnaire d'événements avec anti-blocage

**Responsabilités :**
- Réception des événements Matrix/Tchap
- Parse initial des messages et commandes
- Gestion des types d'événements (message, invitation, etc.)
- Protection contre les timeouts et erreurs

### 2. Couche Contexte Tchap (Intelligence contextuelle)

**Composants principaux :**
- **TchapContextResolver** : Résout le contexte Tchap intelligent
- **TchapContext** : Dataclass avec contexte résolu et décision
- **TchapContextType** : Types de contexte (DM, SALON_GENERAL, THREAD)

**Responsabilités :**
- Détection du type de contexte Tchap
- Analyse des mentions (@user:domain)
- Vérification de la participation aux threads
- Décision intelligente de réponse

### 3. Couche Contexte Persistant (Mémoire conversationnelle)

**Composants principaux :**
- **ContextManager** : Gestionnaire principal avec persistance WebDAV
- **SessionContext** : Historique de conversation utilisateur/salon
- **RoomContext** : État global du salon et participants
- **UserContext** : Préférences et permissions utilisateur
- **RequestContext/ResponseContext** : Contexte de requête/réponse

**Responsabilités :**
- Persistance des contextes sur WebDAV
- Cache en mémoire avec TTL
- Gestion des références croisées entre contextes
- Préservation de l'historique conversationnel

### 4. Couche Services (Capacités fonctionnelles)

**Composants principaux :**
- **WebDAVService** : Accès aux espaces documentaires
- **IndexService** : Indexation et recherche sémantique
- **BehaviorManager** : Gestion des comportements et workflows
- **EmbeddingService** : Services d'embeddings
- **LockService** : Gestion des verrous concurrents

**Responsabilités :**
- Fournir les capacités métier
- Gestion des ressources externes
- Services d'IA et de traitement
- Synchronisation et verrous

### 5. Couche Initialisation (Orchestration des services)

**Composants principaux :**
- **ServiceRegistry** : Registre des services avec priorités
- **initialization.py** : Initialisation ordonnée des services
- **imports.py** : Imports dynamiques et résolution des dépendances

**Responsabilités :**
- Initialisation ordonnée des services
- Gestion du cycle de vie
- Résolution des dépendances
- Mode dégradé en cas d'échec

### 6. Couche Commandes (Interface utilisateur)

**Composants principaux :**
- **CommandRegistry** : Registre des commandes disponibles
- **Décorateurs** : @tchap_contextual, @tchap_thread_command, etc.
- **NotificationFormatter** : Formatage unifié des réponses

**Responsabilités :**
- Enregistrement et découverte des commandes
- Orchestration du traitement
- Formatage et envoi des réponses
- Gestion des threads et mentions

## 🔄 Flux de Résolution du Contexte Global

### Phase 1 : Réception et Parse Initial

```
Message Tchap → EventParser → MessageEventParser
                     ↓
              Extraction métadonnées :
              - Type d'événement
              - Salon/DM
              - Expéditeur
              - Contenu
              - Thread info
```

### Phase 2 : Résolution du Contexte Tchap

```
MessageEventParser → TchapContextResolver
                           ↓
                    Analyse contextuelle :
                    - Type : DM/SALON/THREAD
                    - Mentions détectées
                    - Participation aux threads
                    - Décision de réponse
```

### Phase 3 : Récupération du Contexte Persistant

```
ContextManager → Récupération/Création :
                - SessionContext (room_id + user_id)
                - RoomContext (participants, état)
                - UserContext (préférences, permissions)
                ↓
              Mise à jour last_activity
              Préservation historique
```

### Phase 4 : Initialisation des Services

```
ServiceRegistry → Vérification/Initialisation :
                 - WebDAVService (espaces documentaires)
                 - IndexService (recherche sémantique)
                 - BehaviorManager (workflows)
                 - EmbeddingService (IA)
```

### Phase 5 : Exécution de la Commande

```
Décorateur (@tchap_contextual) → Orchestration :
                                - Vérification autorisation
                                - Injection des services
                                - Gestion timeout/erreurs
                                - Préservation contexte
```

### Phase 6 : Formatage et Réponse

```
NotificationFormatter → Réponse contextuelle :
                       - Thread ID automatique
                       - Formatage unifié
                       - Gestion des mentions
                       - Envoi via MatrixClient
```

## 🔗 Interfaces Clés

### Interface EventParser

```python
class EventParser:
    # Propriétés de base
    room: MatrixRoom
    event: Event  
    matrix_client: MatrixClient
    
    # Méthodes contextuelles Tchap
    async def get_tchap_context() -> TchapContext
    async def should_respond_in_context() -> bool
    async def get_response_thread_id() -> Optional[str]
    
    # Méthodes Matrix standard
    def room_is_direct_message() -> bool
    def sender_id() -> str
    def is_from_this_bot() -> bool
```

### Interface ContextManager

```python
class ContextManager:
    # Gestion des contextes
    async def get_context(context_id: str, context_type: ContextType) -> BaseContext
    async def create_context(context_id: str, context_type: ContextType, data: Dict) -> BaseContext
    async def update_context(context_id: str, context_type: ContextType, data: Dict) -> None
    
    # Contextes spécialisés
    async def get_or_create_session_context(room_id: str, user_id: str) -> SessionContext
    async def get_or_create_room_context(room_id: str, room_name: str, is_direct: bool) -> RoomContext
    
    # Persistence
    async def flush_pending_saves() -> None
    async def cleanup_old_contexts(max_age_days: int) -> None
```

### Interface de Services

```python
# Fonctions d'accès unifiées
async def get_context_manager(config: Config) -> ContextManager
async def get_unified_session_context(config: Config, room_id: str, user_id: str) -> SessionContext
async def get_behavior_manager_for_context(config: Config, room_id: str, user_id: str) -> BehaviorManager

# Initialisation des services
async def initialize_services(config: Config) -> None
async def shutdown_services() -> None
def register_service(name: str, init_func: Callable, shutdown_func: Callable, priority: int) -> None
```

### Interface de Décorateurs

```python
# Décorateur principal avec contexte Tchap intelligent
@tchap_contextual(
    group: str,
    command: Optional[str] = None,
    auto_format: bool = True,
    preserve_context: bool = True,
    timeout: Optional[float] = None,
    include_authorization: bool = True
)

# Décorateur pour commandes avec thread
@tchap_thread_command(
    thread_name: str,
    group: str, 
    command: str,
    auto_format: bool = True,
    preserve_context: bool = True
)
```

## 📊 Résolution des Dépendances

### Graphe de Dépendances des Services

```
Config
├── MatrixClient (prio 5)
├── WebDAVService (prio 10)
│   └── ContextManager (prio 20)
│       └── SessionContext, RoomContext, UserContext
├── IndexService (prio 30)
├── BehaviorManager (prio 40)
│   └── EmbeddingService
└── TchapContextResolver (runtime)
    └── NotificationFormatter (runtime)
```

### Ordre d'Initialisation

1. **MatrixClient** (prio 5) - Client Matrix/Tchap
2. **WebDAVService** (prio 10) - Accès aux espaces documentaires
3. **ContextManager** (prio 20) - Gestion des contextes persistants
4. **IndexService** (prio 30) - Indexation et recherche
5. **BehaviorManager** (prio 40) - Workflows et comportements
6. **Services runtime** - TchapContextResolver, NotificationFormatter (à la demande)

## 🛠️ Modes de Fonctionnement

### Mode Normal

Tous les services sont disponibles :
- Contexte Tchap intelligent activé
- Persistance WebDAV fonctionnelle
- Indexation et recherche disponibles
- Comportements et workflows activés

### Mode Dégradé

En cas de problème avec WebDAV ou autres services :
- Contexte en mémoire seulement
- Fonctionnalités de base préservées
- Tentatives de reconnexion automatiques
- Logs détaillés des problèmes

### Mode Développement

Pour les tests et le développement :
- Services mockés possibles
- Logs de débogage activés
- Timeout réduits pour les tests
- Rechargement à chaud

## 🔍 Points d'Extension

### Ajout de Nouveaux Types de Contexte

1. Définir dans `ContextType` enum
2. Créer la classe héritant de `BaseContext`
3. Enregistrer dans `ContextManager.CONTEXT_CLASSES`
4. Implémenter `to_dict()` et `from_dict()`

### Ajout de Nouveaux Services

1. Créer la classe de service
2. Implémenter les méthodes `initialize()` et `close()`
3. Enregistrer via `register_service()`
4. Ajouter les interfaces d'accès dans `app/commands/__init__.py`

### Ajout de Nouveaux Décorateurs

1. Implémenter dans `app/commands/decorators.py`
2. Utiliser `_inject_services()` pour l'injection de dépendances
3. Gérer les timeouts et erreurs via `_with_timeout()`
4. Documenter dans `utilisation_decorateurs_tchap.md`

## 📈 Métriques et Observabilité

### Logs Structurés

- **Niveau DEBUG** : Détails des résolutions de contexte
- **Niveau INFO** : Initialisation des services, commandes exécutées
- **Niveau WARNING** : Mode dégradé, reconnexions
- **Niveau ERROR** : Échecs d'initialisation, erreurs de traitement

### Métriques Clés

- Temps de résolution du contexte global
- Taux de succès des services
- Utilisation du cache de contexte
- Latence des commandes par type
- Fréquence des modes dégradés

Cette architecture fournit une base solide et extensible pour le traitement intelligent des messages dans Colaig, avec une séparation claire des responsabilités et une gestion robuste des erreurs. 