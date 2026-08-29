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

## Clé LLM — deux façons de la fournir

### 1. Explicite (toujours prioritaire)

```
--set llm.apiKey=<TOKEN_SSPCLOUD_LLM>
```

La clé part dans un `Secret`, monté en `LLM_API_KEY`. C'est un choix de l'opérateur, et
rien ne le remplace.

### 2. Auto-découverte dans l'espace Onyxia (L3.6)

Si `llm.apiKey` est vide, Colaig cherche la clé **dans les secrets du namespace** au
démarrage — celle que l'utilisateur a renseignée pour son espace.

**Prérequis : le pod doit porter le rôle `edit`** sur le namespace. Sans lui, l'API
Kubernetes rend `403`, et Colaig le dit dans ses journaux au lieu d'échouer sans un mot :

```
aucune clé LLM découverte — secrets is forbidden — le pod n'a pas le rôle `edit`
```

**La sélection est volontairement étroite.** Le rôle `edit` donne la lecture de *tous*
les secrets du namespace — mots de passe de bases, jetons S3. Prendre « le premier
secret ressemblant à une clé » enverrait un jour un identifiant de service voisin à un
endpoint LLM tiers. Seuls les secrets **dont le nom désigne le LLM** sont lus :

```
sspcloud-llm · llm-api-key · colaig-llm · openai-api-key
```

Si votre espace la range sous un autre nom :

```
--set env.COLAIG_SSPCLOUD_SECRETS=mon-secret
```

Colaig journalise **ce qu'il a vu et où** — des noms de secrets, jamais des valeurs —
pour qu'un premier déploiement soit diagnosticable.

> Cette liste de noms n'a pas été vérifiée contre un espace Onyxia réel : c'est un point
> de départ. Le journal du premier démarrage dit quels secrets existent réellement.
