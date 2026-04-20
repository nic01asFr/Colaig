# web/ — Interface Admin

## Propriétaire : Agent CERVEAU

## Stack
- FastAPI + Jinja2 + HTMX (pas de SPA React/Vue)
- CSS minimal (Pico CSS ou classless)

## Routes admin (create_app)

```
GET  /                  → Dashboard HTML (santé, stats)
GET  /workspaces        → Liste JSON des workspaces
GET  /workspaces/{id}   → Détail workspace JSON
POST /workspaces/{id}/reindex  → Forcer ré-indexation
GET  /workspaces/{id}/index-status → État de l'index
POST /workspaces        → Créer un workspace (scaffold .colaig/)
POST /workspaces/{id}/conversations → Lier une conversation
DELETE /workspaces/{id}/conversations/{cid} → Délier
PUT  /workspaces/{id}   → Mettre à jour config workspace
POST /webhooks/storage  → Webhook événements storage (re-index)
POST /ask               → Pipeline direct (test, si handler fourni)
GET  /health            → Health check JSON
GET  /metrics           → Métriques JSON
```

## Routes plateforme (actives si clients_yml_path fourni)

```
POST   /api/platform/provision                      → Créer/MAJ client dans clients.yml
DELETE /api/platform/provision/{client_id}          → Supprimer un client
GET    /api/platform/provision                      → Lister les IDs client
GET    /api/platform/provision/{client_id}/package  → Télécharger ZIP self-hosted
```

### Auth routes plateforme
`Authorization: Bearer <COLAIG_PLATFORM_API_KEY>`. Si la variable est absente → accès ouvert (dev/self-hosted).

### Activation
```python
app = create_app(
    ...,
    clients_yml_path="config/clients.yml",  # active les routes /api/platform/
)
```

## create_app() — signature complète

```python
def create_app(
    resolver=None,           # ContextResolverProtocol
    indexer=None,            # Indexer
    config=None,             # ColaigConfig
    mcp_server=None,         # ColaigMCPServer (monté sur /mcp)
    storage=None,            # StorageProtocol
    handler=None,            # MessageHandler (active POST /ask)
    workspace_indexers=None, # dict[workspace_id, Indexer]
    discovery_fn=None,       # async callable() — re-découverte workspaces
    clients_yml_path=None,   # str|Path — active routes /api/platform/provision
) -> FastAPI:
```

## Modèles de requête Pydantic

- `CreateWorkspaceRequest` — storage_path, name, workspace_id, description, conversations, system_prompt, tone, language, rag_enabled
- `UpdateWorkspaceRequest` — name, description, system_prompt, tone, language, rag_enabled, similarity_threshold, max_results
- `AskRequest` — message, conversation_id, user_id
- `ProvisionRequest` — client_id, storage (ProvisionStorageConfig), messaging (ProvisionMessagingConfig), llm?, mcp_auth?, test_backends=True
- `ProvisionStorageConfig` — backend + tous les champs par backend (webdav/local/s3/msgraph/bigfolder/gdrive)
- `ProvisionMessagingConfig` — backend + homeserver/username/password/bot_token/webhook_url
- `ProvisionLLMConfig` — backend + api_url/api_key/model_chat/model_embed/azure_*
- `ProvisionMCPAuthConfig` — mode, oidc_issuer, oidc_audience, oidc_jwks_uri
