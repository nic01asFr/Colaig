# Index Comportemental (BehaviorIndex)

## Introduction

L'Index Comportemental est un composant central du système de comportement de Colaig. Il hérite de `DocumentIndex` et étend ses fonctionnalités pour gérer spécifiquement les comportements personnalisés.

## Structure des Données

### BehaviorChunk

```python
@dataclass
class BehaviorChunk(DocumentChunk):
    behavior_type: str  # actions, tools, prompts, rules
    priority: float = 1.0  # Priorité du comportement
```

Le `BehaviorChunk` représente une unité de comportement indexée avec :
- Un type spécifique (action, tool, prompt, rule)
- Une priorité qui influence son importance dans le système

## Organisation des Fichiers

```
.colaig/
├── actions/
│   ├── config_assistant.json
│   ├── standard_rag.json
│   └── api_integration.json
├── tools/
│   ├── context_handler.json
│   ├── webdav_crud.json
│   └── tchap_messaging.json
├── prompts/
│   ├── rag_system.json
│   └── config_assistant.json
└── rules/
    ├── response_handling.json
    └── config_mode.json
```

## Initialisation

```python
async def initialize(self) -> None:
    """
    1. Création des dossiers nécessaires
    2. Vérification/création des configurations par défaut
    3. Initialisation de l'index FAISS
    """
```

## Fonctionnalités Principales

### 1. Recherche de Comportements

```python
async def search(
    self,
    query: str,
    behavior_type: Optional[str] = None,
    limit: int = 5
) -> List[BehaviorChunk]:
    """
    Recherche sémantique dans les comportements avec :
    - Filtrage par type
    - Tri par priorité et score
    - Limitation des résultats
    """
```

### 2. Analyse d'Intention

```python
async def analyze_intent(
    self,
    query: str,
    session_context: Optional[Dict] = None,
    room_context: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Analyse complète de l'intention avec :
    1. Recherche d'actions pertinentes
    2. Analyse du contexte
    3. Récupération des configurations
    4. Scoring des intentions
    5. Sélection du meilleur comportement
    """
```

### 3. Analyse du Contexte

```python
async def _analyze_context(
    self,
    query: str,
    session_context: Optional[Dict],
    room_context: Optional[Dict]
) -> Dict[str, Any]:
    """
    Analyse contextuelle incluant :
    - Extraction des topics actifs
    - Détection du style de conversation
    - Récupération des règles pertinentes
    - Gestion des paramètres personnalisés
    """
```

## Gestion des Configurations

### 1. Configuration par Défaut

```python
def _get_default_config(self) -> Dict[str, Any]:
    """
    Configuration RAG standard avec :
    - Paramètres de recherche
    - Paramètres de génération de réponse
    - Modèles utilisés
    """
```

### 2. Configurations Personnalisées

```python
async def _get_intent_configurations(
    self,
    action_chunks: List[BehaviorChunk],
    context_info: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Assemblage des configurations avec :
    1. Configuration de base
    2. Outils associés
    3. Prompts correspondants
    4. Paramètres contextuels
    """
```

## Scoring et Sélection

### 1. Calcul des Scores

```python
async def _score_intents(
    self,
    intent_configs: List[Dict[str, Any]],
    query: str,
    context_info: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Scoring multi-critères :
    - Score de base (priorité)
    - Score des topics
    - Score du style
    - Score des règles
    """
```

### 2. Sélection du Meilleur Intent

```python
async def _select_best_intent(
    self,
    scored_intents: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Sélection avec :
    - Vérification du score minimum
    - Fallback sur RAG standard si nécessaire
    """
```

## Gestion des Erreurs

Le système intègre une gestion robuste des erreurs :
1. Logging détaillé des erreurs
2. Fallback sur le comportement par défaut
3. Récupération gracieuse en cas de problème

## Performance et Optimisation

### Stratégies de Cache

- Mise en cache des index FAISS
- Cache des configurations fréquemment utilisées
- Nettoyage périodique du cache

### Optimisations de Recherche

- Limitation intelligente des résultats
- Filtrage précoce par type
- Tri optimisé par priorité

## Exemples d'Utilisation

### 1. Recherche Simple

```python
chunks = await behavior_index.search(
    query="configurer l'API",
    behavior_type="action",
    limit=3
)
```

### 2. Analyse d'Intention Complète

```python
intent_result = await behavior_index.analyze_intent(
    query="aide-moi à configurer une nouvelle API",
    session_context={"history": [...]},
    room_context={"custom_config": {...}}
)
```

## Bonnes Pratiques

1. **Configuration**
   - Utiliser des priorités cohérentes
   - Documenter les configurations
   - Maintenir des descriptions claires

2. **Performance**
   - Limiter la taille des chunks
   - Nettoyer régulièrement le cache
   - Optimiser les recherches

3. **Sécurité**
   - Valider les configurations
   - Gérer les permissions
   - Surveiller les ressources 