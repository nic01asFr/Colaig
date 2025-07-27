# Système de Heartbeat pour les Commandes Conversationnelles

## Introduction

Le système de heartbeat de Colaig est un mécanisme qui permet aux commandes conversationnelles de maintenir un contexte actif aussi longtemps que l'utilisateur interagit régulièrement, sans être interrompues par le système de nettoyage automatique des états.

## Fonctionnement

Le système repose sur trois composants principaux :

1. **Timestamp d'initialisation** : Lors du démarrage d'un thread, un timestamp `last_activity_time` est créé avec la valeur actuelle.

2. **Mise à jour automatique** : À chaque réponse de l'utilisateur dans le thread, ce timestamp est mis à jour.

3. **Vérification basée sur l'activité** : Le mécanisme de nettoyage vérifie l'inactivité plutôt que la durée totale du thread.

## Configuration des commandes conversationnelles

Les commandes qui nécessitent des interactions prolongées peuvent être configurées dans le dictionnaire `CONVERSATIONAL_COMMANDS` dans `app/commands/registry.py` :

```python
CONVERSATIONAL_COMMANDS = {
    "webhook": {
        "timeout": 600,  # 10 minutes en secondes
        "description": "Commandes webhook pour n8n"
    },
    "ma_nouvelle_commande": {
        "timeout": 1200,  # 20 minutes
        "description": "Description de ma commande"
    }
}
```

## Implémentation dans de nouvelles commandes

Pour créer une nouvelle commande profitant du système de heartbeat :

1. Créez votre commande avec le décorateur `@albert_thread_command` :

```python
@albert_thread_command(
    thread_name="ma_commande",
    group="mon_groupe",
    command="ma_commande",
    help_text="!ma_commande - Description",
    preserve_context=True,
    timeout=600.0  # Timeout de la commande principale
)
async def ma_commande_handler(ep: EventParser, matrix_client: MatrixClient):
    # Votre code ici
```

2. Créez le gestionnaire de réponses avec `@albert_thread_response` :

```python
@albert_thread_response(
    thread_name="ma_commande",
    validate_format=None,
    timeout=600.0  # Timeout pour le traitement des réponses
)
async def ma_commande_response(ep: EventParser, matrix_client: MatrixClient, thread_data: Dict[str, Any]):
    # Traitement des réponses utilisateur
    # Le heartbeat est automatiquement mis à jour
```

3. Ajoutez votre commande à `THREAD_COMMANDS` et `CONVERSATIONAL_COMMANDS` :

```python
# Dans app/commands/registry.py
THREAD_COMMANDS = [
    # Autres commandes...
    "ma_commande"
]

CONVERSATIONAL_COMMANDS = {
    # Autres commandes...
    "ma_commande": {
        "timeout": 900,  # 15 minutes
        "description": "Ma commande conversationnelle"
    }
}
```

## Bénéfices

- **Conversations naturelles** : Les utilisateurs peuvent prendre leur temps pour répondre sans perdre le contexte
- **Résilience** : Le système est tolérant aux pauses dans la conversation
- **Flexibilité** : Chaque commande peut avoir sa propre durée d'inactivité maximale
- **Performance** : Le mécanisme n'ajoute pas de surcoût notable au système existant

## Limites

- Le système ne prolonge pas automatiquement les threads sans interaction utilisateur
- Les commandes doivent toujours avoir un timeout maximal pour éviter les ressources orphelines

## Bonnes pratiques

- Définir des timeouts raisonnables en fonction de l'usage de la commande
- Ajouter des messages explicites lorsqu'un thread est sur le point d'expirer
- Utiliser des commandes raccourcies pour les opérations simples qui ne nécessitent pas de conversation prolongée 