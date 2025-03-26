# Guide Utilisateur du Système de Comportement

## Introduction

Le système de comportement de Colaig vous permet de personnaliser l'assistant selon vos besoins spécifiques. Ce guide vous explique comment utiliser et configurer les différents aspects du système.

## Comportements par Défaut

Par défaut, Colaig fonctionne comme un assistant RAG (Retrieval-Augmented Generation) qui vous aide à :
- Rechercher des informations dans votre documentation
- Classer et organiser vos documents
- Répondre à vos questions en se basant sur le contenu disponible

Cette configuration de base est toujours disponible et ne nécessite aucune action de votre part.

## Mode Configuration

### Activation du Mode Configuration

Pour personnaliser Colaig, vous devez d'abord activer le mode configuration en envoyant la commande :

```
!config
```

Cette commande fait passer Colaig en mode configuration, où il vous guidera pour créer des comportements personnalisés.

### Utilisation du Mode Configuration

Une fois en mode configuration, Colaig vous posera une série de questions pour comprendre vos besoins. Par exemple :

1. **Évaluation initiale** : "Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?"
2. **Identification des composants** : Colaig identifiera les composants nécessaires à votre configuration
3. **Génération de la configuration** : Colaig créera les fichiers de configuration nécessaires

### Exemples de Configurations Possibles

#### 1. Configuration d'une Action Personnalisée

```
Utilisateur: !config
Colaig: Je suis en mode configuration. Je vais vous guider pas à pas dans la personnalisation de Colaig selon vos besoins. Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?

Utilisateur: Je voudrais créer une action pour classer automatiquement les documents par thème.

Colaig: D'accord, je comprends que vous souhaitez créer une action de classification automatique des documents par thème. Pour cela, nous allons devoir configurer :
1. Une action de classification
2. Des outils d'analyse de contenu
3. Des règles de catégorisation

Commençons par l'action principale. Comment souhaitez-vous nommer cette action ?
```

#### 2. Configuration d'un Prompt Personnalisé

```
Utilisateur: !config
Colaig: Je suis en mode configuration. Je vais vous guider pas à pas dans la personnalisation de Colaig selon vos besoins. Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?

Utilisateur: Je voudrais que Colaig adopte un style plus formel dans ses réponses.

Colaig: Je comprends que vous souhaitez personnaliser le style de mes réponses pour qu'elles soient plus formelles. Nous allons configurer un prompt personnalisé.

Voici les options disponibles pour le style formel :
1. Style administratif (très formel, adapté aux communications officielles)
2. Style professionnel (formel mais accessible, adapté au contexte professionnel)
3. Style académique (formel avec vocabulaire technique)

Quel style préférez-vous ?
```

### Désactivation du Mode Configuration

Le mode configuration se désactive automatiquement après une période d'inactivité (par défaut 60 minutes), ou vous pouvez le désactiver manuellement en envoyant :

```
!exit
```

## Configurations Personnalisées

### Types de Configurations

Vous pouvez personnaliser quatre types de comportements :

1. **Actions** : Comportements principaux de Colaig (ex: classification, recherche avancée)
2. **Outils** : Utilitaires et fonctionnalités spécifiques (ex: gestion du contexte, intégration WebDAV)
3. **Prompts** : Templates de réponse et styles de communication
4. **Règles** : Règles de comportement et de traitement des réponses

### Structure d'une Configuration

Chaque configuration est stockée dans un fichier JSON avec la structure suivante :

```json
{
  "type": "action",
  "description": "Description de la configuration",
  "priority": 0.8,
  "configuration": {
    // Paramètres spécifiques à cette configuration
  }
}
```

### Priorités des Configurations

Les configurations ont des priorités qui déterminent leur importance :
- **1.0** : Priorité critique (toujours appliquée si pertinente)
- **0.8** : Priorité haute (généralement appliquée)
- **0.5** : Priorité moyenne (appliquée si particulièrement pertinente)
- **0.2** : Priorité basse (appliquée uniquement si très spécifique à la requête)

## Exemples Pratiques

### Exemple 1 : Assistant de Recherche Juridique

```
Utilisateur: !config
Colaig: Je suis en mode configuration. Pour commencer, pouvez-vous me décrire ce que vous souhaitez configurer ?

Utilisateur: Je voudrais configurer Colaig pour m'aider dans mes recherches juridiques.

Colaig: [Guide l'utilisateur à travers la configuration]

// Résultat : Configuration personnalisée pour la recherche juridique
// - Action "legal_research" créée
// - Outils de recherche juridique configurés
// - Prompts spécifiques au domaine juridique
```

### Exemple 2 : Gestionnaire de Documentation Technique

```
Utilisateur: !config
Colaig: Je suis en mode configuration. Pour commencer, pouvez-vous me décrire ce que vous souhaitez configurer ?

Utilisateur: Je voudrais que Colaig m'aide à gérer ma documentation technique.

Colaig: [Guide l'utilisateur à travers la configuration]

// Résultat : Configuration personnalisée pour la gestion de documentation technique
// - Action "tech_doc_manager" créée
// - Outils de classification technique configurés
// - Règles de formatage spécifiques
```

## Stratégie de Fallback

Le système utilise une stratégie de fallback intelligente :

1. Si une configuration personnalisée existe et est pertinente (score > 0.6), elle est utilisée
2. Sinon, le comportement par défaut (RAG standard) est utilisé

Cette approche garantit que Colaig reste toujours fonctionnel, même si les configurations personnalisées ne sont pas adaptées à une requête spécifique.

## Bonnes Pratiques

1. **Commencez Simple** : Créez d'abord des configurations simples avant de passer à des comportements plus complexes
2. **Testez Régulièrement** : Vérifiez que vos configurations fonctionnent comme prévu
3. **Utilisez des Descriptions Claires** : Les descriptions aident Colaig à comprendre quand utiliser chaque configuration
4. **Ajustez les Priorités** : Modifiez les priorités pour affiner le comportement de Colaig

## Dépannage

### Problème : Colaig n'utilise pas ma configuration personnalisée

**Solutions possibles** :
- Vérifiez que la configuration a une priorité suffisante (≥ 0.8)
- Assurez-vous que la description est claire et pertinente
- Utilisez des mots-clés spécifiques dans vos requêtes qui correspondent à la configuration

### Problème : Le mode configuration ne s'active pas

**Solutions possibles** :
- Vérifiez que vous utilisez la commande exacte (`!config`)
- Assurez-vous que Colaig est correctement initialisé
- Vérifiez les logs pour d'éventuelles erreurs

### Problème : Erreurs dans les configurations

**Solutions possibles** :
- Utilisez le mode configuration pour recréer la configuration problématique
- Vérifiez la syntaxe JSON de vos fichiers de configuration
- Assurez-vous que tous les champs requis sont présents

## Conclusion

Le système de comportement de Colaig vous offre une grande flexibilité pour adapter l'assistant à vos besoins spécifiques. En utilisant le mode configuration, vous pouvez créer des comportements personnalisés sans avoir besoin de compétences techniques avancées.

N'hésitez pas à expérimenter avec différentes configurations pour découvrir tout le potentiel de Colaig ! 