# Albert API Documentation

## Configuration des Embeddings

Le service supporte deux fournisseurs d'embeddings :

### Albert API (par défaut)
- Modèle : `AgentPublic/e5-small-v2`
- Dimension : 384
- Configuration via `ALBERT_MODEL_EMBEDDING=AgentPublic/e5-small-v2`

### Mistral API
- Modèle : `mistral-embed`
- Dimension : 1024
- Configuration :
  ```env
  MISTRAL_API_URL=https://api.mistral.ai
  MISTRAL_API_TOKEN=votre_token_mistral
  ALBERT_MODEL_EMBEDDING=mistral-embed
  ```

Pour changer de fournisseur, il suffit de modifier la variable `ALBERT_MODEL_EMBEDDING` dans le fichier `.env`.