# Déploiement d'Albert Tchap avec Docker Compose

Ce document explique comment déployer Albert Tchap et ses services associés en utilisant Docker Compose.

## Prérequis

- Docker et Docker Compose installés sur votre machine
- Variables d'environnement configurées (fichier `.env`)

## Configuration

1. Copiez le fichier `.env.example` en `.env` :

```bash
cp .env.example .env
```

2. Modifiez le fichier `.env` avec vos propres paramètres :
   - Identifiants Matrix/Tchap
   - Token API Albert
   - Informations de connexion WebDAV

## Démarrage

Pour démarrer l'ensemble des services :

```bash
docker-compose up --build -d
```

L'option `--build` permet de reconstruire les images si nécessaire, et `-d` exécute les conteneurs en arrière-plan.

## Vérification

Pour vérifier que les services fonctionnent correctement :

```bash
docker-compose ps
```

Pour consulter les logs d'Albert Tchap :

```bash
docker-compose logs -f albert-tchap
```

## Arrêt

Pour arrêter tous les services :

```bash
docker-compose down
```

Pour arrêter les services et supprimer les volumes (attention : cela supprimera toutes les données persistantes) :

```bash
docker-compose down -v
```

## Configuration WebDAV

Par défaut, un serveur WebDAV local est démarré sur le port 8888. Dans un environnement de production, vous devriez plutôt utiliser votre propre serveur WebDAV et ajuster les variables d'environnement en conséquence.

Pour configurer votre propre serveur WebDAV, modifiez les variables suivantes dans le fichier `.env` :

```
WEBDAV_URL=https://votre_serveur_webdav.gouv.fr
WEBDAV_USERNAME=votre_utilisateur_webdav
WEBDAV_PASSWORD=votre_mot_de_passe_webdav
WEBDAV_ROOT_PATH=/documents
```

## Volumes persistants

Les volumes suivants sont utilisés pour la persistance des données :

- `albert-data` : Données générales d'Albert Tchap
- `albert-config` : Fichiers de configuration
- `albert-cache` : Cache pour améliorer les performances

## Environnement de production

Pour un déploiement en production, il est recommandé de :

1. Configurer un serveur WebDAV externe sécurisé au lieu d'utiliser celui fourni
2. Utiliser des secrets Docker pour les informations sensibles
3. Configurer un reverse proxy (comme Traefik ou Nginx) pour sécuriser l'accès
4. Mettre en place une surveillance des logs et des performances 