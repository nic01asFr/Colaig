# Configuration du mode headless pour browser-use

Ce document explique comment le mode headless a été configuré pour Playwright et browser-use dans l'application Albert-Tchap.

## Contexte du problème

Browser-use utilise Playwright pour contrôler un navigateur web (Chromium). Pour fonctionner correctement dans des environnements sans interface graphique (comme les serveurs ou les conteneurs Docker), le navigateur doit être lancé en mode "headless" (sans interface graphique).

## Solutions implémentées

Nous avons mis en place deux mécanismes pour garantir que le mode headless fonctionne correctement:

### 1. Configuration dans le fichier .env

La variable d'environnement `PLAYWRIGHT_HEADLESS=true` est définie dans le fichier `.env`. Cette variable est utilisée par Playwright pour déterminer si le navigateur doit être lancé en mode headless.

```
PLAYWRIGHT_HEADLESS=true
```

### 2. Définition explicite des variables d'environnement dans le code

Dans `app/services/browser_extraction.py`, nous définissons explicitement les variables d'environnement au moment de l'initialisation de Playwright:

```python
# Définir les variables d'environnement critiques pour le mode headless
os.environ["PLAYWRIGHT_HEADLESS"] = "true"
os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
```

### 3. Initialisation asynchrone

Pour résoudre des problèmes d'initialisation, la fonction `ensure_playwright_installed()` a été modifiée pour être asynchrone:

```python
async def ensure_playwright_installed():
    # ...
```

Cela permet d'intégrer l'initialisation de Playwright dans le flux asynchrone de l'application, évitant ainsi l'erreur:
```
Impossible d'installer Playwright: object NoneType can't be used in 'await' expression
```

Les appels à cette fonction ont été mis à jour dans tous les fichiers pour utiliser `await`.

## Fonctionnement avec browser-use

Pour la version 0.1.41 de browser-use, la configuration headless est gérée exclusivement par variables d'environnement. Le constructeur de la classe Agent ne prend pas de paramètre `headless`. Voici comment browser-use utilise les variables d'environnement:

1. La bibliothèque lit la variable d'environnement `PLAYWRIGHT_HEADLESS`
2. Si cette variable est définie à "true", le navigateur est lancé en mode headless
3. Cette configuration est appliquée lors de l'initialisation de la session Playwright

## Dépannage

Si vous rencontrez toujours des problèmes liés au mode headless:

1. Vérifiez que le fichier `.env` contient bien la ligne `PLAYWRIGHT_HEADLESS=true`
2. Vérifiez que la variable d'environnement est correctement définie au démarrage de l'application:
   ```python
   print(os.environ.get("PLAYWRIGHT_HEADLESS"))  # Devrait afficher "true"
   ```
3. Assurez-vous que les dépendances système de Playwright sont correctement installées:
   ```
   playwright install-deps
   ```
4. Pour les environnements Windows, il peut être nécessaire d'installer des composants supplémentaires:
   ```
   playwright install msedge
   ```
5. Dans les conteneurs Docker, assurez-vous que les variables d'environnement sont bien passées au conteneur 