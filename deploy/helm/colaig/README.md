# Chart Helm Colaig — déploiement Onyxia / SSP Cloud

Déploie Colaig en un pod : storage S3/MinIO, LLM OpenAI-compatible, webchat. Zéro base de données.

## Déploiement rapide (SSP Cloud)

Profil SSP Cloud (LLM Open WebUI + S3 MinIO). Le LLM SSP Cloud expose une API
OpenAI-compatible :

> **Mesuré le 22/08/2026 — ne pas revenir à `/openai`.**
>
> Les clients LLM construisent eux-mêmes `{base}/v1/chat/completions`. Avec
> `--set llm.apiUrl=https://llm.lab.sspcloud.fr/openai`, l'appel devient
> `/openai/v1/chat/completions` et le serveur répond **403 — « Direct API passthrough is
> disabled »**. Le déploiement démarre puis échoue au premier appel LLM.
>
> La base correcte est **`https://llm.lab.sspcloud.fr/api`** : `/api/v1/chat/completions`
> répond 200. Elle sert aussi un modèle de plus que `/openai` (7 contre 6 — `/openai`
> n'expose pas `qwen3-cursor`).
>
> Ne jamais suffixer la base par `/v1` : cela produirait `/v1/v1/`.



```bash
helm install colaig deploy/helm/colaig \
  --set llm.backend=openai \
  --set llm.apiUrl=https://llm.lab.sspcloud.fr/api \
  --set llm.apiKey=<TOKEN_SSPCLOUD_LLM> \
  --set llm.localEmbeddings=true \
  --set storage.s3.endpointUrl=<ENDPOINT_MINIO> \
  --set storage.s3.accessKey=<AWS_ACCESS_KEY_ID> \
  --set storage.s3.secretKey=<AWS_SECRET_ACCESS_KEY> \
  --set storage.s3.sessionToken=<AWS_SESSION_TOKEN> \
  --set storage.s3.bucket=<BUCKET>
```

Sur Onyxia, le formulaire de lancement est généré depuis `values.schema.json`.

## Embeddings

- Si le LLM expose un endpoint `/v1/embeddings`, renseigner `llm.modelEmbed`.
- Sinon `llm.localEmbeddings=true` calcule les embeddings en local (bge-m3) dans le pod.

## Vérification

```bash
helm lint deploy/helm/colaig
helm template colaig deploy/helm/colaig | kubectl apply --dry-run=client -f -
```

## Clé LLM — deux sources, dans cet ordre

### 1. Explicite (prioritaire)

```
--set llm.apiKey=<TOKEN>
```

Un choix de l'opérateur. Rien ne le remplace — c'est ce qui permet de déployer hors
d'Onyxia.

### 2. La passerelle IA d'Onyxia (repli automatique)

Si `llm.apiKey` est vide, le chart prend `ai.activeProvider.apiKey`, **rempli par Onyxia
au lancement**. La configuration publique de l'instance le dit :

> « Vos identifiants AI Gateway sont injectés de façon sécurisée dans votre
> environnement à chaque démarrage du service. »
> « Votre session OIDC vous donne un accès transparent à la passerelle IA. »

Le canal est `x-onyxia.overwriteDefaultWith` dans `values.schema.json` — Onyxia remplit
les défauts du formulaire depuis le contexte de l'utilisateur :

| valeur du chart | rempli depuis |
|---|---|
| `ai.enabled` | `{{ai.enabled}}` |
| `ai.activeProvider.apiBase` | `{{ai.activeProvider.apiBase}}` |
| `ai.activeProvider.apiKey` | `{{ai.activeProvider.apiKey}}` |
| `ai.activeProvider.selectedModel` | `{{ai.activeProvider.selectedModel}}` |

Le modèle se choisit dans une liste, elle-même remplie par
`overwriteListEnumWith: {{ai.activeProvider.models}}` — pas de nom à taper à la main.

**Aucun droit particulier n'est requis.** Le pod ne lit rien dans le namespace : les
valeurs lui sont poussées au démarrage. Le rôle `edit` n'est pas nécessaire pour cela.

> Une première version de ce lot faisait explorer les secrets du namespace au pod. Ce
> n'est pas le mécanisme de SSPCloud, cela demandait un droit inutile, et cela ouvrait
> une exfiltration — un secret voisin pris pour une clé LLM et envoyé à un tiers. Le
> code a été retiré au profit de ces quatre lignes de schéma.
