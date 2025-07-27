# Intégration de browser-use dans Albert-Tchap

Ce document explique comment Albert-Tchap utilise la bibliothèque [browser-use](https://github.com/browser-use/browser-use) pour améliorer l'exploration et l'analyse des pages web.

## Présentation

browser-use est une bibliothèque d'automatisation de navigateur conçue pour permettre aux agents IA de contrôler un navigateur web complet. Elle utilise Playwright (Chromium) pour naviguer sur le web et interagir avec les pages comme le ferait un utilisateur humain.

Dans Albert-Tchap, nous utilisons browser-use pour:
- Explorer et extraire le contenu des pages web
- Analyser et catégoriser automatiquement les liens
- Générer des descriptions et résumés de pages web

## Adaptation avec Albert API

Bien que browser-use soit conçu pour fonctionner avec OpenAI, nous avons développé un wrapper spécial qui permet d'utiliser l'API Albert à la place. Cette adaptation offre plusieurs avantages:
- Utilisation d'un seul modèle de langage (Albert) pour toutes les opérations
- Pas besoin d'une clé API OpenAI supplémentaire
- Cohérence dans les réponses et la gestion du contexte

## Avantages par rapport à l'approche précédente

La précédente implémentation utilisait l'API Albert pour rechercher du contenu web dans une collection "internet". Cette approche présentait plusieurs limitations:
- Dépendance à une collection de données pré-indexée
- Accès limité aux sites web récents ou non indexés
- Impossibilité d'accéder au contenu généré dynamiquement (JavaScript)

L'intégration de browser-use apporte les avantages suivants:
- Accès à tout le web sans limitation de collection
- Support complet du contenu JavaScript
- Navigation intelligente de l'agent dans les sites web
- Extraction plus précise du contenu pertinent
- Réduction des erreurs 404 et autres problèmes d'accès

## Prérequis

Pour utiliser browser-use, vous devez installer:
1. Python 3.10 ou supérieur
2. Les dépendances du projet (incluant browser-use et langchain-openai)
3. Playwright avec le navigateur Chromium

## Dépendances compatibles

Pour éviter les conflits de dépendances, vérifiez que vous utilisez ces versions:
```
faiss-cpu>=1.10.0
httpx>=0.27.2
langchain-openai>=0.3.11
pydantic>=2.10.4,<2.11.0
openai>=1.68.2,<2.0.0
```

## Installation

### Windows

Exécutez le script d'installation:
```
scripts\install_dependencies.bat
```

### Linux/macOS

Installez les dépendances:
```bash
pip install -e .
```

Installez Playwright:
```bash
python scripts/install_playwright.py
```

## Configuration

La configuration est simplifiée car nous utilisons uniquement l'API Albert:

1. Assurez-vous que la configuration Albert est correcte dans votre fichier `.env`:
```
ALBERT_API_URL=https://api.albert.etalab.gouv.fr/v1
ALBERT_API_TOKEN=votre_token_api
ALBERT_MODEL=meta-llama/Llama-3.1-8B-Instruct
```

2. Vous n'avez pas besoin de configurer une clé API OpenAI

## Fonctionnalités intégrées

### Explorer un lien web

La commande `!explorer_lien` utilise maintenant browser-use pour:
1. Extraire le contenu de la page web avec un navigateur complet piloté par Albert
2. Générer un résumé avec Albert basé sur le contenu extrait

Exemple:
```
!explorer_lien https://www.service-public.fr
```

### Ajouter un lien à la base de données

La commande `!ajouter_lien` utilise maintenant browser-use pour:
1. Extraire les métadonnées de la page (titre, contenu)
2. Classifier automatiquement le contenu en catégories
3. Générer une description concise

Exemple:
```
!ajouter_lien https://www.service-public.fr
```

## Architecture

L'intégration de browser-use est organisée autour de plusieurs modules:

- `app/services/browser_extraction.py` - Fonctions principales d'extraction web avec le wrapper Albert
- `app/services/web_classification_browser.py` - Classification et analyse avec browser-use
- `app/commands/web_commands/web_search.py` - Commandes utilisateur modifiées

## Fonctionnement technique

1. Adaptation de browser-use à Albert:
   - La classe `AlbertAgentWrapper` adapte l'interface d'Albert pour être compatible avec browser-use
   - Cette classe intercepte les appels destinés à OpenAI et les redirige vers Albert API

2. Lorsqu'un utilisateur demande l'exploration d'un lien, Albert:
   - Crée un agent browser-use avec notre wrapper personnalisé
   - Lance une instance de navigateur Chromium en arrière-plan
   - Extrait le contenu principal de la page
   - Utilise l'API Albert pour générer un résumé de ce contenu
   - Met en cache le résultat pour les futures requêtes

3. Pour l'ajout de liens:
   - L'agent extrait les métadonnées (titre, contenu principal)
   - Albert analyse et classifie le contenu en catégories
   - Les informations sont stockées dans la base de données de liens

## Dépannage

### Erreurs courantes

- **Erreur de connexion**: Vérifiez votre connexion internet et assurez-vous que l'URL est correcte.
- **Erreur Playwright**: Assurez-vous que Playwright est correctement installé avec `playwright install --with-deps chromium`.
- **Erreur API Albert**: Vérifiez que votre clé API Albert est valide dans le fichier `.env`.
- **Erreur "Missing X server or $DISPLAY"**: Ce problème devrait être résolu par la configuration de la variable d'environnement `PLAYWRIGHT_HEADLESS=true` dans le fichier `.env`.

### Logs

Les logs détaillés sont disponibles dans le dossier de logs standard d'Albert et peuvent aider à diagnostiquer les problèmes.

## Limites connues

- Certains sites web peuvent bloquer l'accès aux robots ou utiliser des techniques anti-scraping.
- Les sites nécessitant une authentification ne sont pas pris en charge.
- La performance peut varier selon la complexité des pages web et la qualité de la connexion internet.

## Configuration spécifique

Le navigateur est configuré pour fonctionner en mode headless (sans interface graphique) via une variable d'environnement, ce qui permet son fonctionnement dans des environnements sans serveur X (comme Docker). Si vous rencontrez des problèmes liés au mode headless, vérifiez que la variable `PLAYWRIGHT_HEADLESS=true` est bien définie dans votre fichier `.env`. 