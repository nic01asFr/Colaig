# 🌐 Commandes Web - Guide de Démarrage Rapide

> Système de recherche local intelligent avec actualisation automatique des sources

## 🚀 Démarrage Rapide

### 1. Ajouter votre premier lien
```
!ajouter_lien https://beta.gouv.fr/startups/tchap.html
```

### 2. Effectuer une recherche
```
!recherche_web Qu'est-ce que Tchap ?
```

### 3. Explorer un lien temporairement
```
!explorer_lien https://www.service-public.fr
```

### 4. Voir vos liens indexés
```
!liste_liens
```

## 📋 Commandes Disponibles

| Commande | Description | Exemple |
|----------|-------------|---------|
| `!recherche_web [question]` | Recherche intelligente avec actualisation auto | `!recherche_web transformation digitale` |
| `!ajouter_lien [URL] [titre?] [catégorie?]` | Indexe un site web complet | `!ajouter_lien https://example.com "Titre" tech` |
| `!explorer_lien [URL] [--ocr?]` | Analyse temporaire d'une URL | `!explorer_lien https://example.com --ocr` |
| `!liste_liens [catégorie?]` | Affiche les liens par catégorie | `!liste_liens administration` |

## ✨ Fonctionnalités Clés

### 🔄 Actualisation Automatique
- Les sources obsolètes sont automatiquement actualisées lors des recherches
- Seuils adaptatifs selon le type de contenu (actualités: 1j, blogs: 3j, docs: 7j)

### 🎯 Recherche Orientée "Site"
- Les résultats sont présentés par site web, pas par fragments
- Score composite basé sur la pertinence globale du site

### 📊 Indexation Intelligente
- Extraction automatique du contenu textuel
- Génération de résumés avec Albert
- Vectorisation pour la recherche sémantique

### 🏷️ Organisation par Catégories
- `administration` : Sites gouvernementaux
- `technique` : Documentation technique
- `documentation` : Guides et manuels
- `general` : Contenu généraliste

## 🔧 Configuration Requise

```bash
# Variables d'environnement
ALBERT_API_URL=https://albert.api.etalab.gouv.fr
ALBERT_API_TOKEN=your_token_here
ALBERT_MODEL_EMBEDDING=text-embedding-ada-002

WEBDAV_URL=https://webdav.example.com
WEBDAV_USERNAME=username
WEBDAV_PASSWORD=password
WEBDAV_ROOT_PATH=/colaig
```

## 📈 Exemple d'Utilisation Complète

```bash
# 1. Indexer quelques sources de référence
!ajouter_lien https://beta.gouv.fr/startups/tchap.html "Tchap" administration
!ajouter_lien https://www.service-public.fr "Service Public" administration
!ajouter_lien https://doc.tchap.gouv.fr "Doc Tchap" documentation

# 2. Effectuer des recherches
!recherche_web Comment fonctionne Tchap ?
!recherche_web Quels sont les services publics numériques ?

# 3. Explorer de nouvelles sources
!explorer_lien https://numerique.gouv.fr --ocr

# 4. Gérer vos liens
!liste_liens administration
!liste_liens
```

## 🎯 Réponse Type

```
🔍 Recherche avec données actualisées : Comment fonctionne Tchap ?

Tchap est une messagerie instantanée sécurisée développée spécifiquement 
pour les agents publics français. Elle permet des échanges sécurisés...

📚 Sources consultées (2) :
1. **Tchap — beta.gouv.fr** - https://beta.gouv.fr/startups/tchap.html (sim: 0.85, actualisée)
2. **Documentation Tchap** - https://doc.tchap.gouv.fr (sim: 0.72, récente)

🔄 Actualisation effectuée :
- 1 source(s) mise(s) à jour pour cette recherche
- Données garanties fraîches au moment de la réponse
```

## 🛠️ Dépannage Rapide

**❌ Aucun résultat trouvé**
- Vérifiez avec `!liste_liens` que des sources sont indexées
- Reformulez votre question avec des termes différents

**⚠️ Erreur d'extraction**
- Vérifiez que l'URL est accessible
- Certains sites bloquent l'extraction automatique

**🐌 Performance lente**
- L'actualisation peut prendre du temps (normal)
- Limitez le nombre de sources indexées

## 📚 Documentation Complète

- **[Documentation Détaillée](./docs/Colaig_docs/modules/web_commands.md)** - Guide complet avec architecture et exemples avancés
- **[Documentation des Commandes](./docs/commandes.md)** - Vue d'ensemble du système de commandes

## 🚀 Prochaines Étapes

1. **Indexez 3-5 sites de référence** sur vos sujets d'intérêt
2. **Organisez par catégories** pour une meilleure navigation
3. **Utilisez la recherche régulièrement** - le système s'améliore avec l'usage
4. **Explorez les options OCR** pour l'analyse d'éléments visuels

---

> 💡 **Conseil** : Commencez petit avec quelques sources de qualité, puis étendez progressivement votre base de connaissances locale. 