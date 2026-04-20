# platform/ — Gestion de configuration et provisioning

## Statut : IMPLÉMENTÉ

## Fichiers

### config_store.py — ConfigStore

Persistance de la configuration runtime d'une instance unique dans `config/runtime.yml`.
Écrit par `colaig_set_backend` (MCP tool). Lu par `config.py` au démarrage avec priorité sur `.env`.

```python
class ConfigStore:
    def load() -> dict
    def get(section: str) -> dict          # section = "storage" | "messaging" | "llm"
    def get_all() -> dict
    def set(section: str, values: dict)    # crée le fichier si absent
    def apply_to_env(section=None)         # applique dans os.environ
```

Sections : `storage`, `messaging`, `llm`.
Mapping YAML → env : `_STORAGE_ENV_MAP`, `_MESSAGING_ENV_MAP`, `_LLM_ENV_MAP`.

### provisioner.py — ClientProvisioner

Gestion programmatique des entrées client dans `clients.yml` et génération de packages self-hosted.

```python
class ClientProvisioner:
    def __init__(clients_yml_path: str | Path)

    # Lecture
    def load() -> dict
    def get_client(client_id: str) -> Optional[dict]
    def list_clients() -> list[str]

    # Écriture
    def upsert(client_id, storage, messaging, llm=None, mcp_auth=None) -> str  # "created" | "updated"
    def delete(client_id) -> bool

    # Package self-hosted
    def build_selfhosted_package(
        client_id: str,
        base_url: str = "",
        albert_api_url: str = "https://albert-api.etalab.gouv.fr",
        albert_api_key: str = "",
        albert_model_chat: str = "openai/gpt-oss-120b",
        albert_model_embed: str = "BAAI/bge-m3",
    ) -> bytes  # ZIP contenant docker-compose.yml + .env
```

## Format clients.yml

```yaml
platform_policy:                     # optionnel — contraintes opérateur
  allowed_storage_backends: [webdav, bigfolder]
  allowed_llm_endpoints:
    - https://albert-api.etalab.gouv.fr
  allowed_mcp_auth_modes: [token, oidc]
  enforce_mcp_auth: false

clients:
  - id: org-rh
    storage:
      backend: webdav
      url: https://nextcloud.org.fr/remote.php/dav/files/colaig/
      username: colaig-rh
      password: secret
    messaging:
      backend: matrix
      homeserver: https://matrix.agent.tchap.gouv.fr
      username: "@colaig-rh:agent.tchap.gouv.fr"
      password: secret
    # llm: absent → hérite de la config globale (ALBERT_*)
    mcp_auth:                          # optionnel
      mode: oidc
      oidc_issuer: https://sso.org.fr/realms/org
      oidc_audience: colaig-instance-rh
      oidc_jwks_uri: https://sso.org.fr/realms/org/protocol/openid-connect/certs
```

## Package self-hosted

`build_selfhosted_package()` retourne un ZIP contenant :

- **`docker-compose.yml`** — service `colaig/colaig:latest`, port 8000, volumes `./data` + `./secrets`
- **`.env`** — pré-rempli depuis la config du client (`STORAGE_BACKEND`, `WEBDAV_URL`, `MATRIX_*`, `ALBERT_*`, `COLAIG_MCP_AUTH_MODE`, etc.)

Les credentials LLM (`ALBERT_API_KEY`) sont copiés depuis les variables d'environnement de l'instance plateforme au moment de la génération.

## Routes HTTP

Exposées par `create_app(clients_yml_path=...)` dans `web/routes.py`.
Auth : `Authorization: Bearer <COLAIG_PLATFORM_API_KEY>`.

| Méthode | Route | Description |
|---------|-------|-------------|
| `POST` | `/api/platform/provision` | Provision client (test backends optionnel) |
| `DELETE` | `/api/platform/provision/{client_id}` | Supprimer client |
| `GET` | `/api/platform/provision` | Lister IDs client |
| `GET` | `/api/platform/provision/{client_id}/package` | Télécharger ZIP self-hosted |

### Provision — corps de requête

```json
{
  "client_id": "org-rh",
  "storage": {"backend": "webdav", "url": "...", "username": "...", "password": "..."},
  "messaging": {"backend": "matrix", "homeserver": "...", "username": "...", "password": "..."},
  "llm": null,
  "mcp_auth": {"mode": "oidc", "oidc_issuer": "...", "oidc_audience": "...", "oidc_jwks_uri": "..."},
  "test_backends": true
}
```

Si `test_backends=true`, les backends sont testés via `_test_storage`/`_test_messaging` (mêmes helpers que `colaig_set_backend`). En cas d'échec → HTTP 422, rien n'est persisté.

## Points d'attention

- `ClientProvisioner` ne redémarre pas Colaig — `restart_required: true` dans la réponse HTTP
- La `platform_policy` existante dans `clients.yml` est préservée lors des upserts
- `COLAIG_PLATFORM_API_KEY` absent → accès ouvert (dev / self-hosted sans auth)
- `ConfigStore` (instance unique) et `ClientProvisioner` (multi-client) sont deux mécanismes distincts et indépendants
- La `PlatformPolicy` est validée au démarrage pour chaque client via `validate_client_config_against_policy()` — violation = erreur fatale
