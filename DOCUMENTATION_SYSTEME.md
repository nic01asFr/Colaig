# Documentation du Système Colaig

## Introduction

Colaig est un assistant conversationnel intelligent basé sur Matrix/Tchap qui utilise des techniques de RAG (Retrieval Augmented Generation) et un système de comportement extensible pour fournir des réponses contextuelles et précises. Cette documentation présente l'architecture globale du système, ses composants principaux et leurs interactions.

## Architecture Globale

Le système Colaig s'articule autour de plusieurs composants majeurs qui interagissent pour fournir une expérience utilisateur cohérente et contextualisée :

```
┌─────────────────────────────┐
│   Interface Matrix (Tchap)  │
└───────────────┬─────────────┘
                │
┌───────────────▼─────────────┐
│     Gestionnaire de Bot     │
└───────────────┬─────────────┘
                │
     ┌──────────▼───────────┐
     │                      │
┌────▼────┐           ┌─────▼────┐
│ Système │           │  Système  │
│   RAG   │◄─────────►│Comportement│
└────┬────┘           └─────┬─────┘
     │                      │
     └──────────┬───────────┘
                │
┌───────────────▼─────────────┐
│   Gestionnaire d'Espaces    │
└───────────────┬─────────────┘
                │
┌───────────────▼─────────────┐
│     Stockage WebDAV         │
└─────────────────────────────┘
```

## Gestion des Espaces Documentaires

### Principe d'Isolation

Un aspect fondamental de Colaig est l'isolation des espaces documentaires et des contextes utilisateur. Chaque salon peut être associé à un espace documentaire spécifique, ce qui garantit que les utilisateurs n'ont accès qu'aux documents et aux informations qui leur sont destinés.

### Structure des Espaces

Les espaces documentaires sont organisés en dossiers isolés dans WebDAV :

```
WebDAV/
├── Espace1/
│   └── .albert/
│       ├── index/       # Index vectoriel
│       ├── contexts/    # Contextes de conversation
│       └── behavior/    # Comportements personnalisés
├── Espace2/
│   └── .albert/
...
```

### Association Salon-Espace

L'association entre un salon Matrix et un espace documentaire est gérée par le WebDAVContextManager :

1. Un utilisateur exécute la commande `!space link <id>` pour associer un salon
2. Le système enregistre cette association dans le contexte du salon
3. Toutes les recherches et actions effectuées dans ce salon sont automatiquement dirigées vers l'espace associé

```python
# Association d'un salon à un espace
async def link_space(matrix_client, room_id, space_id, webdav_manager):
    spaces = await webdav_manager.discover_documentation_spaces()
    result = await webdav_manager.set_room_webdav_context(room_id, spaces[space_id]['path'])
```

## Système RAG (Retrieval Augmented Generation)

Le système RAG est responsable de la recherche et de la génération de réponses basées sur les documents indexés.

### Indexation des Documents

1. Les documents sont chargés depuis l'espace documentaire
2. Ils sont découpés en chunks de taille appropriée
3. Des embeddings sont générés pour chaque chunk
4. Les chunks et leurs embeddings sont indexés dans FAISS
5. Les métadonnées sont stockées pour permettre la récupération

### Recherche Contextuelle

La recherche est automatiquement restreinte à l'espace documentaire associé au salon courant :

```python
# Recherche dans l'espace associé au salon
async def search(self, query, limit=10, filters=None, room_id=None, user_id=None):
    if room_id:
        room_context = await get_room_context(room_id)
        if room_context and room_context.webdav_context:
            space_path = room_context.webdav_context.current_path
            return await self.search_in_space(query, space_path, limit, filters)
```

## Système de Comportement

Le système de comportement permet d'adapter les réponses et les actions du bot en fonction du contexte et des besoins spécifiques.

### Types de Comportement

Quatre types principaux de comportement sont gérés :

1. **Actions** : Comportements principaux qui peuvent être déclenchés
2. **Tools** : Utilitaires réutilisables
3. **Prompts** : Templates de réponse
4. **Rules** : Contraintes et validations

### Détection d'Intention

Le système analyse chaque requête pour déterminer l'intention de l'utilisateur :

```python
async def detect_intent(self, message: str) -> Tuple[bool, str, float]:
    # Analyse de l'intention via le BehaviorIndex
    intent_analysis = await self.index.analyze_intent(message)
    
    # Déterminer si l'intention a été reconnue
    if intent_analysis and intent_analysis["confidence"] > self.config.intent_threshold:
        return True, intent_analysis["detected_intent"], intent_analysis["confidence"]
    
    return False, "", 0.0
```

### Isolation des Comportements par Espace

Chaque espace documentaire peut avoir ses propres configurations de comportement, stockées dans sa structure `.albert/behavior/`. Le système de comportement accède automatiquement aux comportements spécifiques à l'espace associé au salon courant.

## Gestion des Contextes

### Hiérarchie des Contextes

Le système maintient différents niveaux de contexte pour chaque interaction :

1. **Contexte Global** : Configuration et paramètres généraux
2. **Contexte d'Espace** : Spécifique à un espace documentaire
3. **Contexte de Salon** : Partagé par tous les utilisateurs d'un salon
4. **Contexte Utilisateur** : Spécifique à un utilisateur
5. **Contexte de Session** : Spécifique à un utilisateur dans un salon

### Stockage des Contextes

Les contextes sont stockés sous forme de fichiers JSON dans la structure WebDAV :

```
.albert/contexts/
├── room_123456.json                  # Contexte du salon
├── user_789012.json                  # Contexte utilisateur 
├── room_123456_user_789012_session.json  # Session utilisateur dans le salon
```

## Flux de Traitement d'une Requête

Le traitement d'une requête utilisateur suit le flux suivant :

1. L'utilisateur envoie un message dans un salon
2. Le gestionnaire de bot reçoit le message et détermine le contexte
3. Le système de comportement analyse l'intention de l'utilisateur
4. Si un comportement spécifique est identifié, il est exécuté
5. Sinon, le système RAG cherche dans l'espace associé au salon
6. Le système RAG génère une réponse basée sur les documents trouvés
7. La réponse est envoyée à l'utilisateur via l'interface Matrix

## Flux de Données pour le Système RAG

### Recherche Contextuelle

1. L'utilisateur envoie une requête dans un salon
2. Le `ContextManager` charge le contexte de session et le contexte du salon
3. Si le salon est associé à un espace documentaire, le système l'utilise automatiquement
4. `IndexService` effectue la recherche dans cet espace documentaire spécifique

```python
# Dans actions/rag_action.py
async def retrieve_relevant_chunks(self, limit: int = 5) -> List[Dict[str, Any]]:
    # Recherche des chunks pertinents avec le contexte du salon
    relevant_chunks = await self.index_service.search(
        self.query,
        limit=limit,
        room_id=self.room_id
    )
```

## Sécurité et Isolation

### Principe de Séparation

- Les utilisateurs dans un salon n'ont accès qu'à l'espace documentaire associé
- Les contextes de conversation sont strictement séparés par salon et utilisateur
- Les comportements personnalisés ne s'appliquent que dans leur espace respectif

### Gestion des Autorisations

- L'authentification Matrix/Tchap gère l'accès initial
- Les autorisations WebDAV déterminent l'accès aux documents
- Des règles de comportement peuvent ajouter des restrictions supplémentaires

## Extension du Système de Comportement

### Structure d'un Fichier de Comportement

Tous les comportements sont définis dans des fichiers JSON avec la structure suivante :

```json
{
  "id": "my_custom_behavior",
  "type": "action",
  "description": "Description de mon comportement personnalisé",
  "priority": 0.8,
  "triggers": [
    "mot clé 1",
    "mot clé 2"
  ],
  "configuration": {
    // Configuration spécifique au type de comportement
  }
}
```

### Exemple d'Action Personnalisée

```json
{
  "id": "custom_report_generation",
  "type": "action",
  "description": "Génère des rapports personnalisés",
  "priority": 0.7,
  "triggers": ["générer rapport", "créer un rapport"],
  "configuration": {
    "action_type": "report_generation",
    "required_parameters": ["type_rapport", "période"],
    "execution_mode": "async",
    "templates": {
      "confirmation": "Je vais générer un rapport de type {type_rapport} pour la période {période}.",
      "completion": "Votre rapport est prêt : {result_url}"
    },
    "allowed_report_types": ["financier", "activité", "performance"]
  }
}
```

## Conclusion

L'architecture de Colaig combine un système RAG puissant avec un système de comportement flexible, le tout organisé en espaces documentaires isolés. Cette combinaison permet :

1. Une séparation claire des données entre différentes équipes ou projets
2. Des réponses contextuelles basées sur l'espace associé au salon
3. Des comportements personnalisables selon les besoins spécifiques de chaque espace
4. Une sécurité renforcée par l'isolation des contextes 