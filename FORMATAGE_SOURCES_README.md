# Formatage des Sources - Documentation

## Vue d'ensemble

Ce document décrit le système de formatage des sources pour la commande `!chercher` et `!synthese`, qui permet d'afficher les documents sources avec des liens de partage OCS Nextcloud adaptés à l'utilisateur ou des URLs WebDAV en fallback.

## Fonctionnalités principales

### 1. Affichage des sources
- **Format simple** : Utilisation de puces simples (`-`) au lieu d'emojis pour une meilleure lisibilité
- **Liens cliquables** : Intégration avec l'API OCS de Nextcloud pour créer des liens de partage adaptés
- **Informations contextuelles** : Affichage des pages et sections pertinentes
- **Regroupement intelligent** : Consolidation des sources par document unique

### 2. Gestion des liens de partage OCS avancée

#### Stratégie de partage intelligente
Le système utilise une stratégie à deux niveaux pour maximiser l'accessibilité :

1. **Partage direct avec l'utilisateur** (shareType: 0)
   - Création d'un partage personnalisé pour l'utilisateur qui fait la demande
   - Extraction automatique du nom d'utilisateur depuis l'ID Matrix (`@user:domain.fr` → `user`)
   - Permissions en lecture seule pour la sécurité
   - Expiration configurable (défaut: 7 jours)

2. **Fallback vers lien public** (shareType: 3)
   - Si le partage direct échoue, création d'un lien public
   - Expiration courte par défaut (2 jours) pour la sécurité
   - Protection par mot de passe optionnelle

#### API OCS Nextcloud
Le système utilise l'API OCS officielle de Nextcloud :
- **Endpoint** : `{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares`
- **Méthode** : POST avec `OCS-APIRequest: true`
- **Formats supportés** : JSON avec validation complète des réponses

#### Validation et gestion d'erreurs

## 🚨 Problèmes de liens WebDAV vs OCS

### Problème identifié
Actuellement, les liens générés par Colaig utilisent souvent un **fallback WebDAV** au lieu des vrais liens de partage OCS. 

**Format WebDAV actuel (problématique)** :
```
https://bnum.din.gouv.fr/mdrive/remote.php/dav/files/nicolas.laval-developpement-durable.gouv.fr1/Colaig-Articles.pdf?download=1
```

**Format OCS correct attendu** :
```
https://bnum.din.gouv.fr/mdrive/s/{token}     # Lien public
https://bnum.din.gouv.fr/mdrive/f/{path}      # Partage direct
```

### Causes possibles
1. **API OCS désactivée** sur le serveur Nextcloud
2. **Problèmes d'authentification** avec l'API OCS
3. **Permissions de partage** insuffisantes
4. **Configuration incorrecte** des paramètres OCS

## 🔧 Outils de diagnostic

### Commande `!diagnostic_ocs`
Cette commande permet aux administrateurs de diagnostiquer les problèmes de configuration OCS :

```
!diagnostic_ocs
```

**Fonctionnalités** :
- ✅ Vérification de la configuration OCS du serveur
- ✅ Test de création de liens publics
- ✅ Test de création de partages utilisateur
- ✅ Rapport détaillé avec recommandations
- ✅ Validation optionnelle d'un lien existant

**Exemple d'utilisation** :
```
!diagnostic_ocs https://example.com/remote.php/dav/files/user/doc.pdf?download=1
```

### Commande `!test_link_ocs`
Cette commande permet de tester et valider un lien spécifique :

```
!test_link_ocs <lien_à_tester>
```

**Fonctionnalités** :
- 🔍 Détection du type de lien (OCS vs WebDAV)
- ✅ Validation du format
- ⚠️ Détection des fallbacks WebDAV
- 💡 Recommandations spécifiques

**Exemple** :
```
!test_link_ocs https://bnum.din.gouv.fr/mdrive/remote.php/dav/files/user/doc.pdf?download=1
```

### Service OCSLinkValidator
Le service `OCSLinkValidator` fournit les fonctionnalités de diagnostic :

```python
from app.services.ocs_link_validator import OCSLinkValidator

# Initialisation
validator = OCSLinkValidator(base_url, username, password)

# Validation de la configuration
config_results = await validator.validate_ocs_configuration()

# Test de création de partages
share_results = await validator.test_share_creation()

# Validation d'un lien existant
link_validation = await validator.validate_existing_link(url)

# Rapport complet
report = await validator.generate_diagnostic_report()
```

## 🛠️ Corrections apportées

### 1. Amélioration de create_share_link()
- ✅ Utilisation correcte de l'URL retournée par l'API OCS
- ✅ Gestion améliorée des erreurs avec logs détaillés
- ✅ Validation des réponses OCS avant utilisation
- ✅ Fallback intelligent vers liens publics

### 2. Diagnostic et monitoring
- ✅ Service de validation OCS complet
- ✅ Commandes d'administration pour le diagnostic
- ✅ Rapports détaillés avec recommandations
- ✅ Tests automatisés de la configuration

### 3. Documentation technique
- ✅ Guide de dépannage des problèmes OCS
- ✅ Documentation des formats de liens corrects
- ✅ Procédures de diagnostic pour les administrateurs

## 📋 Procédure de dépannage

### Étape 1: Diagnostic initial
```
!diagnostic_ocs
```

### Étape 2: Analyse des résultats
- Si **Configuration OCS invalide** → Vérifier la configuration serveur
- Si **Partage direct échoue** → Vérifier les permissions utilisateur  
- Si **Liens publics échouent** → Vérifier l'activation du partage public

### Étape 3: Test d'un lien spécifique
```
!test_link_ocs <lien_problématique>
```

### Étape 4: Actions correctives
Selon les résultats :
- **Activer l'API OCS** sur le serveur Nextcloud
- **Configurer les permissions** de partage
- **Vérifier l'authentification** des comptes de service
- **Mettre à jour la configuration** Colaig

## 📝 Bonnes pratiques

### Pour les administrateurs
1. Lancez `!diagnostic_ocs` régulièrement pour vérifier la santé du système
2. Testez les liens générés avec `!test_link_ocs`
3. Surveillez les logs pour détecter les problèmes de partage
4. Assurez-vous que l'API OCS est activée sur Nextcloud

### Pour les développeurs
1. Utilisez le service `OCSLinkValidator` pour les tests automatisés
2. Implémentez des vérifications de santé dans les CI/CD
3. Loggez les erreurs OCS avec suffisamment de détails
4. Prévoyez toujours un fallback WebDAV fonctionnel

## 🔗 Liens utiles

- [Documentation API OCS Nextcloud](https://docs.nextcloud.com/server/latest/developer_manual/client_apis/OCS/ocs-share-api.html)
- [Configuration du partage Nextcloud](https://docs.nextcloud.com/server/latest/admin_manual/configuration_files/file_sharing_configuration.html)
- [Dépannage API OCS](https://help.nextcloud.com/c/support/ocs-api/97) 