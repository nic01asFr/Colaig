# Système de commandes unifié pour Albert

## Vue d'ensemble

Ce module fournit un système unifié de décorateurs pour créer des commandes dans Albert. Ce système simplifie la création de commandes en offrant une interface cohérente et une gestion automatique des erreurs, des timeouts et du contexte de conversation.

## Caractéristiques principales

- **Décorateurs unifiés** : Une interface simplifiée pour définir des commandes
- **Gestion des erreurs** : Capture automatique des exceptions et logs détaillés
- **Timeouts** : Protection contre les commandes qui bloquent indéfiniment
- **Préservation du contexte** : Maintien de l'historique de conversation
- **Threads de conversation** : Support pour des interactions en plusieurs étapes
- **Validation des entrées** : Filtrage des réponses invalides

## Structure des fichiers

- `decorators.py` - Définition des décorateurs unifiés
- `examples/` - Exemples de commandes utilisant les nouveaux décorateurs
  - `echo_command.py` - Exemple simple sans thread
  - `calculator_command.py` - Exemple avec thread de conversation
- `document_commands/` - Commandes adaptées pour la gestion de documents
  - `docquery_adapted.py` - Version adaptée de la commande docquery
  - `attachment_adapted.py` - Version adaptée de la commande pj
- `doc/` - Documentation pour les développeurs
  - `guide_developpeur.md` - Guide complet pour créer des commandes

## Comment utiliser

Pour créer une commande, choisissez le décorateur approprié :

- `@albert_command` - Pour des commandes simples (une seule interaction)
- `@albert_thread_command` et `@albert_thread_response` - Pour des commandes avec thread (plusieurs interactions)

Voir les exemples dans le dossier `examples/` pour des implémentations complètes.

## Migration depuis l'ancien système

Les commandes existantes peuvent être migrées vers le nouveau système en remplaçant les anciens décorateurs par les nouveaux. Le système maintient une compatibilité arrière pour faciliter la transition.

## Documentation

Pour une documentation complète, consultez le [Guide du développeur](doc/guide_developpeur.md).

## Exemple minimaliste

```python
from app.commands.decorators import albert_command
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

@albert_command(
    group="demo",
    command="salut",
    help_text="!salut - Dire bonjour"
)
async def hello_command(ep: EventParser, matrix_client: MatrixClient):
    """Une commande simple qui dit bonjour."""
    return f"👋 Bonjour {ep.sender} !"
```

## Avantages du système unifié

- **Simplicité** : Réduction du boilerplate et standardisation du code
- **Robustesse** : Gestion automatique des erreurs et timeouts
- **Maintenabilité** : Structure cohérente pour toutes les commandes
- **Extensibilité** : Facile à étendre avec de nouvelles fonctionnalités

## Auteurs

Équipe de développement Albert 