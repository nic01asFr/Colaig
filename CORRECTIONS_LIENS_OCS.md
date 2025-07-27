# 🔧 Corrections des liens OCS - Résumé technique

## 📋 **Problème identifié**

Les liens de partage générés par Colaig utilisaient systématiquement le **fallback WebDAV** au lieu des vrais **liens de partage OCS** de Nextcloud.

**Format problématique (WebDAV)** :
```
https://bnum.din.gouv.fr/mdrive/remote.php/dav/files/nicolas.laval-developpement-durable.gouv.fr1/Colaig-Articles.pdf?download=1
```

**Format attendu (OCS)** :
```
https://bnum.din.gouv.fr/mdrive/s/{token}     # Lien public
https://bnum.din.gouv.fr/mdrive/f/{path}      # Partage direct
```

## 🛠️ **Corrections apportées**

### 1. **Amélioration de `build_document_link()` - `docquery_adapted.py`**

#### ✅ **Passage correct du `target_user`**
- **Avant** : `target_user=None` (toujours lien public)
- **Après** : `target_user=target_username` (partage direct si utilisateur disponible)

#### ✅ **Stratégie de partage intelligente**
```python
# Si on a un utilisateur cible, essayer un partage direct d'abord
if target_user:
    # Partage direct avec l'utilisateur (7 jours)
    share_link = await webdav_service.create_share_link(
        real_path,
        expiration_days=7,
        target_user=target_user
    )
else:
    # Lien public avec expiration courte (2 jours)
    share_link = await webdav_service.create_share_link(
        real_path,
        expiration_days=2,
        target_user=None
    )
```

#### ✅ **Diagnostic amélioré des échecs**
```python
if share_link:
    # Vérifier que le lien est bien au format OCS
    if "/s/" in share_link or "/f/" in share_link:
        logger.info(f"✅ Lien OCS créé avec succès: {share_link}")
        return share_link
    else:
        logger.warning(f"⚠️ Lien créé mais format non-OCS: {share_link}")
else:
    logger.warning(f"❌ Échec de création du lien OCS")
    logger.warning(f"Fallback vers WebDAV car create_share_link a retourné vide")
```

### 2. **Refactoring de `create_share_link()` - `webdav.py`**

#### ✅ **Extraction correcte de la base URL**
```python
# Extraire la base de l'URL Nextcloud (sans remote.php/dav/files/user)
base_url = self.base_url
if "/remote.php/dav/files/" in base_url:
    base_url = base_url.split("/remote.php/dav/files/")[0]
elif "/remote.php/webdav/" in base_url:
    base_url = base_url.split("/remote.php/webdav/")[0]

# S'assurer que la base URL n'a pas de slash final
base_url = base_url.rstrip('/')
```

#### ✅ **Test de connectivité OCS préalable**
```python
# Test de connectivité OCS avant de continuer
try:
    test_response = await self.http_client.get(
        f"{base_url}/ocs/v2.php/cloud/capabilities",
        headers={"OCS-APIRequest": "true"}
    )
    logger.info(f"[OCS_SHARE] Test connectivité OCS: status={test_response.status_code}")
    if test_response.status_code != 200:
        logger.warning(f"[OCS_SHARE] API OCS potentiellement inaccessible: {test_response.status_code}")
except Exception as test_error:
    logger.warning(f"[OCS_SHARE] Impossible de tester l'API OCS: {str(test_error)}")
```

#### ✅ **Headers HTTP complets**
```python
headers = {
    "OCS-APIRequest": "true",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json"
}
```

### 3. **Gestion améliorée des caractères spéciaux**

#### ✅ **Décodage/Encodage correct des chemins**
```python
# Décodage préalable pour éviter double-encodage
if "%" in normalized_path:
    original_path = normalized_path
    normalized_path = urllib.parse.unquote(normalized_path)
    logger.info(f"[OCS_SHARE] Décodage URL: '{original_path}' -> '{normalized_path}'")
```

#### ✅ **Encodage par segments pour les URLs WebDAV**
Pour le fallback WebDAV, l'encodage est maintenant fait segment par segment :
```python
# Encoder correctement le chemin pour les caractères spéciaux
path_parts = real_doc_path.split('/')
encoded_parts = []
for part in path_parts:
    # Encoder la partie tout en gardant les caractères sûrs
    encoded_part = urllib.parse.quote(part, safe='')
    encoded_parts.append(encoded_part)
encoded_path = '/'.join(encoded_parts)
```

#### ✅ **Échappement des caractères spéciaux pour Markdown**
Pour l'affichage dans Tchap, les caractères spéciaux sont correctement échappés :
```python
# Échapper les caractères spéciaux pour le markdown
safe_clean_name = clean_name.replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)')
```

### 4. **Nouveaux outils de diagnostic OCS**

#### ✅ **Commande `!diagnostic_ocs`**
- Effectue un diagnostic complet de la configuration OCS
- Vérifie la connectivité à l'API
- Test de création de liens de partage

#### ✅ **Commande `!test_link_ocs [chemin]`**
- Test la création d'un lien OCS pour un chemin spécifique
- Vérifie que le format de lien généré est correct

#### ✅ **Service `OCSLinkValidator`**
- Fournit des diagnostics détaillés sur les erreurs OCS
- Génère des rapports formatés pour l'aide au dépannage

## 🔍 **Vérification des corrections**

Pour vérifier que les corrections ont été efficaces :

1. Utilisez la commande `!diagnostic_ocs` pour un test complet
2. Vérifiez que les liens générés dans les commandes `!chercher` et `!synthese` sont bien au format :
   - `https://serveur/s/{token}` (lien public)
   - `https://serveur/f/{path}` (partage direct)
3. Testez avec des fichiers dont le nom contient des caractères spéciaux :
   - Espaces
   - Accents et caractères non-ASCII
   - Parenthèses et autres caractères spéciaux

## 🛠️ **Améliorations supplémentaires pour les caractères spéciaux**

### ✅ **Traitement amélioré des caractères spéciaux dans les URLs**

Les améliorations suivantes ont été apportées pour garantir le bon fonctionnement avec les caractères spéciaux dans les noms de fichiers :

1. **Décodage avant traitement** :
   - Les chemins sont d'abord décodés (via `urllib.parse.unquote()`) pour éviter un double encodage
   - Cette étape est essentielle car l'API OCS s'attend à des caractères décodés

2. **Encodage adapté pour WebDAV** :
   - Pour les URLs WebDAV fallback, encodage segment par segment
   - Préservation de la structure de chemin

3. **Nettoyage des liens OCS** :
   - Les liens OCS générés sont nettoyés pour éviter tout problème d'affichage
   - Validation explicite du format des liens (/s/ ou /f/)

4. **Formatage sécurisé pour Markdown dans Tchap** :
   - Échappement des caractères spéciaux pour le Markdown (`[]()`)
   - Traitement spécial pour les URLs contenant déjà des caractères encodés
   - Analyse et encodage segment par segment pour les URLs complexes

Ces améliorations garantissent que les liens fonctionnent correctement même avec des noms de fichiers contenant des caractères spéciaux comme :
- Espaces
- Accents et caractères non-ASCII (é, è, à, ü, etc.)
- Caractères spéciaux (parenthèses, crochets, etc.)
- Caractères réservés dans les URLs (?, &, etc.)

### 📋 **Exemple de traitement d'un chemin avec caractères spéciaux**

Pour un fichier nommé "Rapport d'évaluation (2023).pdf" :

1. **Chemin initial** : `Dossiers partagés/Rapport d'évaluation (2023).pdf`
2. **Pour API OCS** : Envoyé décodé comme `/Dossiers partagés/Rapport d'évaluation (2023).pdf`
3. **Pour fallback WebDAV** : Encodé comme `/Dossiers%20partag%C3%A9s/Rapport%20d%27%C3%A9valuation%20%282023%29.pdf`
4. **Affichage dans Tchap** : `[Rapport d'évaluation (2023).pdf](https://serveur/s/AbCdEfGh)` avec échappement correct des parenthèses 