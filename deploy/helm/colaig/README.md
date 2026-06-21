# Chart Helm Colaig — déploiement Onyxia / SSP Cloud

Déploie Colaig en un pod : storage S3/MinIO, LLM OpenAI-compatible, webchat. Zéro base de données.

## Déploiement rapide (SSP Cloud)

Profil SSP Cloud (LLM Open WebUI + S3 MinIO). Le LLM SSP Cloud expose une API
OpenAI-compatible sous `/openai` :

```bash
helm install colaig deploy/helm/colaig \
  --set llm.backend=openai \
  --set llm.apiUrl=https://llm.lab.sspcloud.fr/openai \
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
