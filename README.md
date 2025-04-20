<!--
SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>

SPDX-License-Identifier: MIT
-->

# Albert-Tchap

Assistant pour Tchap basé sur Albert et le modèle Llama

## Installation

### Prérequis
- Python 3.11+
- [Poetry](https://python-poetry.org/)

### Étapes d'installation

1. Cloner le dépôt
```bash
git clone https://github.com/votre-organisation/albert-tchap.git
cd albert-tchap
```

2. Installation des dépendances
```bash
poetry install
```

3. Installation des navigateurs pour Playwright
```bash
poetry run python scripts/install_playwright.py
```

4. Configuration
Copiez le fichier `.env.example` vers `.env` et modifiez les valeurs selon votre environnement
```bash
cp .env.example .env
```

## Utilisation

### Démarrer l'application
```bash
poetry run python -m albert_tchap.main
```

### Commandes disponibles
- Recherche web: `poetry run python -m albert_tchap.commands.web_search`
- Classification de contenu: `poetry run python -m albert_tchap.web.classification`

## Résolution des problèmes courants

### Conflit de dépendances
Si vous rencontrez des conflits de dépendances lors du build Docker, notamment avec browser-use, vérifiez que vos versions sont compatibles:

```
# Versions minimales requises pour browser-use 0.1.41
faiss-cpu>=1.10.0
httpx>=0.27.2
langchain-openai>=0.3.11
pydantic>=2.10.4,<2.11.0
openai>=1.68.2,<2.0.0
```

Pour mettre à jour ces dépendances dans votre environnement:
```bash
pip install --upgrade faiss-cpu httpx langchain-openai "pydantic>=2.10.4,<2.11.0" "openai>=1.68.2,<2.0.0"
```

Dans un environnement Docker, reconstruisez l'image après avoir mis à jour les dépendances dans pyproject.toml.

### Problèmes avec Playwright
Si les navigateurs ne s'installent pas correctement:
```bash
poetry run playwright install --with-deps chromium
```

## Développement

Pour exécuter les tests:
```bash
poetry run pytest
```

# Albert Tchap

*[English version below](#english-version)*

| <a href="https://github.com/etalab-ia/albert"><b>Albert API sur GitHub</b></a> | <a href="https://huggingface.co/AgentPublic"><b>Modèles Albert sur HuggingFace</b></a> |

## Description du projet

Bot pour [Tchap, l'application de messagerie de l'administration française](https://tchap.beta.gouv.fr/).
Ce bot utilise [Albert](https://github.com/etalab-ia/albert), l'agent conversationnel (*large language models*, LLM) de l'administration française, pour répondre à des questions sur [Tchap, l'application de messagerie de l'administration française](https://tchap.beta.gouv.fr/).

Le projet est un POC (Proof of Concept - preuve de concept) pour montrer comment un bot peut être utilisé pour répondre à des questions sur Tchap en utilisant Albert.
Il s'agit d'un travail WIP (Work In Progress - en cours de développement) et n'est pas (encore) destiné à être utilisé en production.

Le projet est un fork de [tchap_bot](https://code.peren.fr/open-source/tchapbot) qui est un bot Matrix pour Tchap, conçu par le [Pôle d'Expertise de la Régulation Numérique](https://www.peren.gouv.fr/). La partie bibliothèque (`matrix_bot`) est fortement inspirée de https://github.com/imbev/simplematrixbotlib.

Contient :
- `app/.` : la codebase pour le Tchap bot Albert
- `app/matrix_bot` : une bibliothèque qui encapsule [matrix-nio](https://github.com/matrix-nio/matrix-nio) faire des bots Matrix


### Installation locale

Le projet utilise un fichier de dépendances et de config `pyproject.toml` et non un fichier `requirements.txt`. Il est donc nécessaire d'utiliser `pip` en version 19.0 ou supérieure, ou bien avec un package manager comme `pdm`, `pip-tools`, `uv`, `rye`, `hatch` etc. (mais pas `poetry` qui n'utilise pas le standard `pyproject.toml`).

```bash
# Récupération du code avec Git
git clone ${GITHUB_URL}

# Création d'un environnement virtuel Python
python3 -m venv .venv

# Activation de l'environnement virtuel Python
source .venv/bin/activate

# Installation des dépendances
pip install .
```


### Configuration

Créez le fichier d'environnement `app/.env` avec les informations de connexion (ou fournissez-les en variables d'environnement). Vous pouvez vous inspirer du fichier `app/.env.example` qui est initialisé avec les valeurs par défaut :
```bash
cp app/.env.example app/.env
```

L'ensemble des variables d'environements disponibles est documenté dans le fichier suivant : [app/config.py](./app/config.py)


### Lancer le bot

Pour lancer le bot executez :
```bash
python app
```

#### NOTE 1

Cette commande stoppera surement si vous ne la lancez pas en mode sudo car
elle installe par défault le data/store et le data/session.txt à la racine "/".
Vous pouvez lancer l'application pour qu'elle crée ces fichiers dans le dossier du projet directement avec la commande :

```