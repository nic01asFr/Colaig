# Déploiement de COLAIG sur SSPCloud (Onyxia)

Ce guide décrit le déploiement de COLAIG sur le datalab
[SSPCloud](https://datalab.sspcloud.fr) (Onyxia / Kubernetes), en utilisant le
**LLM SSPCloud** (`https://llm.lab.sspcloud.fr` — Open WebUI + Ollama) comme
backend d'inférence.

## Principe

COLAIG est packagé en **chart Helm** (`charts/colaig/`) déployé comme service
Onyxia. Le service tourne dans un pod et communique en HTTPS sortant avec :

- **Tchap** (homeserver Matrix) — le bot
- **WebDAV / BigFolder** — le stockage documentaire (reste externe)
- **LLM SSPCloud** — l'inférence (chat + tool-calling) et les embeddings

La **clé d'API LLM est récupérée automatiquement** depuis le compte SSPCloud de
l'utilisateur, sans saisie manuelle.

## Récupération automatique de la clé LLM

Onyxia persiste la configuration « AI Assistant » de l'utilisateur (*Mon compte
→ AI Assistant*) dans un **Secret Kubernetes** du namespace, nommé
`<service>-secretassistant`, clé `config.json` :

```json
{ "api_keys": { "OPENAI_API_KEY": "sk-..." } }
```

Au démarrage, [`app/platform/sspcloud.py`](../../../app/platform/sspcloud.py) lit
ce Secret via l'API Kubernetes (token du ServiceAccount du pod) et exporte la clé
en `LLM_API_KEY`. Cela requiert `kubernetes.role: edit` (RoleBinding vers le
ClusterRole `edit`, fourni par le chart).

Ordre de priorité de la clé :

1. `llm.apiKey` / `LLM_API_KEY` explicite (override)
2. `albert.apiToken` / `ALBERT_API_TOKEN` (mode Albert)
3. Auto-découverte SSPCloud (`*-secretassistant`)

## Découplage du backend LLM

La résolution des endpoints est centralisée dans `Config` (voir
[`app/config.py`](../../../app/config.py)) :

| Variable | Rôle | Défaut SSPCloud |
|---|---|---|
| `LLM_BASE_URL` | Base API chat (OpenAI-compatible, **sans** `/v1` forcé) | `https://llm.lab.sspcloud.fr/api` |
| `LLM_MODEL` | Modèle de chat | `qwen3-6-35b-moe` |
| `LLM_API_KEY` | Clé chat | auto (`secretassistant`) |
| `EMBEDDINGS_BASE_URL` | Base API embeddings (vide = même que chat) | — |
| `EMBEDDINGS_MODEL` | Modèle embeddings | `BAAI/bge-m3` |
| `EMBEDDINGS_API_KEY` | Clé embeddings (vide = clé chat) | auto |

Si tous les champs `LLM_*`/`EMBEDDINGS_*` sont vides, le comportement
historique basé sur `ALBERT_API_URL`/`ALBERT_API_TOKEN` est conservé
(rétro-compatible).

> Endpoints OpenAI-compatibles d'Open WebUI : `/api`, `/openai`, `/ollama/v1`.
> Le modèle `qwen3-6-35b-moe` supporte le **function-calling natif** requis par
> la boucle agent. À défaut, `LLMTransport` bascule sur le parsing texte.

## Déploiement via le catalogue Onyxia

1. Publier le chart (CI `colaig-helm-publish.yml` → `helm-repo/index.yaml`) et
   l'image (CI `colaig-sspcloud-build.yml` → `ghcr.io/<owner>/colaig-platform`).
2. Ajouter le dépôt Helm dans la configuration de région Onyxia, ou installer
   manuellement (voir ci-dessous).
3. Lancer le service : Onyxia injecte automatiquement `oidc.username`, les creds
   S3, et le hostname d'ingress.
4. Renseigner dans le formulaire : identifiant/mot de passe du bot Tchap, URL et
   identifiants WebDAV, mot de passe admin de la plateforme. Le **modèle LLM**
   et la **base URL** ont des valeurs par défaut SSPCloud.

## Installation manuelle (test)

```bash
helm install colaig charts/colaig \
  --set matrix.botUsername="colaig.bot" \
  --set matrix.botPassword="<mdp>" \
  --set matrix.homeServer="https://matrix.agent.tchap.gouv.fr" \
  --set webdav.url="https://<bigfolder>/dav" \
  --set webdav.username="<user>" \
  --set webdav.password="<mdp>" \
  --set webdav.defaultWorkspace="<workspace>" \
  --set platform.adminPassword="<admin>"
# La clé LLM est récupérée automatiquement (kubernetes.role=edit requis).
```

Accès : l'UI admin de la plateforme est exposée via l'ingress Onyxia (port 8080,
health `/health`).

## Prérequis opérationnels

- **Joignabilité Tchap depuis le pod** : le bot maintient un sync Matrix sortant
  vers le homeserver Tchap. Valider que le homeserver est joignable depuis un pod
  du datalab (`curl https://<homeserver>/_matrix/client/versions`). C'est le
  point bloquant n°1 ; sans cela le bot ne se connecte pas.
- **Compte bot Tchap** dédié (identifiant + mot de passe ou access token).
- **Clé AI Assistant** renseignée dans le compte SSPCloud de l'utilisateur.
- **`kubernetes.role: edit`** pour l'auto-découverte de la clé LLM.

## Stockage

- Le **WebDAV/BigFolder reste externe** : aucune migration vers S3 n'est requise.
  Le pod y accède en HTTPS sortant.
- Un **PVC** (`persistence`, 5Gi par défaut) monté sur `/home/onyxia/work`
  persiste la base SQLite de la plateforme (`platform.db`) et les sessions.
- Les creds **S3/MinIO** sont injectés (`AWS_*`) pour un usage futur éventuel.

## Limites connues

- Le LLM SSPCloud est un service **Open WebUI/Ollama** orienté usage interactif
  du datalab : adapté à un usage **labo/POC/démo**, à dimensionner pour de la
  production.
- L'endpoint embeddings d'Open WebUI peut différer selon la configuration ; en
  cas de souci, pointer `EMBEDDINGS_BASE_URL` vers un fournisseur dédié.
