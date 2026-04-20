# auth/ — Authentification MCP

## Statut : IMPLÉMENTÉ

## Deux modes d'authentification

| Mode | Config | Usage |
|------|--------|-------|
| `token` (défaut) | `COLAIG_MCP_AUTH_MODE=token` | Tokens statiques par user, self-hosted, perso |
| `oidc` | `COLAIG_MCP_AUTH_MODE=oidc` + 3 vars OIDC | SSO enterprise (Azure AD, Keycloak...) |

Les deux modes sont mutuellement exclusifs par instance. Le mode est sélectionné au démarrage dans `main.py` — le reste du pipeline (`TokenContext` → workspace resolver → tools) est identique.

## Principe

Un token MCP est une **session utilisateur**, traitée exactement comme un DM Tchap/Matrix du point de vue du `.colaig`. Le token résout vers un `user_id` → `IncomingMessage(conversation_type=DM)` → `ContextMode.PERSONAL` → workspace personnel, mémoire, `ask_workspace`. Aucune différence de traitement entre Tchap et MCP du point de vue du pipeline.

**Provider-agnostic** — même `StorageProtocol` que le reste de Colaig. Aucune DB, aucun index central.

## Format du token

```
colaig_{safe_slug}_{secret_64hex}
ex: colaig_alice_tchap_fr_a3f8b2c9d1e4f5...64chars
```

- `safe_slug` : dérivé de `user_id` via la même logique que `workspace.py:get_or_create_personal_workspace()`
- `secret_64hex` : 64 chars hex = 256 bits, toujours la même longueur → extraction par la droite

**Résolution O(1) sans index central :**
1. Parser le token → extraire `safe_slug` et vérifier le format
2. Dériver le chemin du workspace personnel : `/.colaig/personal/{safe_slug}/`
3. Charger `/.colaig/personal/{safe_slug}/.colaig/tokens/{sha256(token)}.json`
4. Vérifier l'expiration → retourner `TokenContext(user_id, scope, name)`

## Stockage (dans le workspace personnel de l'utilisateur)

```
/{safe_slug}/
└── .colaig/
    ├── tokens/
    │   └── {sha256(raw_token)}.json      # données du token
    └── mcp-configs/
        └── colaig-{safe_name}.json        # config MCP prête à copier
```

Token JSON :
```json
{
  "user_id": "@alice:tchap.fr",
  "role": "user",
  "scope": "*",
  "name": "Claude Desktop",
  "created_at": "2026-03-12T...",
  "expires_at": null
}
```

Config MCP auto-générée :
```json
{
  "mcpServers": {
    "colaig-claude-desktop": {
      "type": "http",
      "url": "https://colaig.org.fr/mcp",
      "headers": { "Authorization": "Bearer colaig_alice_tchap_fr_..." }
    }
  }
}
```

## Scope → ContextMode

| Token scope | workspace_id | ConversationType | ContextMode | ask_workspace |
|------------|-------------|-----------------|-------------|--------------|
| `"*"` | non fourni | `DM` | `PERSONAL` | ✅ disponible |
| `"*"` | fourni | `PRIVATE` | `ASSISTANT` | ❌ |
| `"espace-rh"` | forcé à `"espace-rh"` | `PRIVATE` | `ASSISTANT` | ❌ |

## Rôle admin

### Concept

Le rôle `admin` donne accès aux tools MCP d'administration de l'instance :
`colaig_set_backend`, `colaig_create_workspace`, `colaig_get_config`, `colaig_onboard`.

Le rôle est déterminé **côté serveur** à la création du token, via `COLAIG_ADMIN_USER_IDS`.
Un utilisateur ne peut jamais s'auto-promouvoir — le paramètre `role` du client est ignoré.

```bash
COLAIG_ADMIN_USER_IDS=@alice:tchap.fr,@bob:agent.gouv.fr
```

### Backward compat

Les tokens créés avant l'introduction du rôle (sans champ `"role"` dans le JSON) sont
résolus avec `role="user"` par défaut dans `TokenManager.resolve()`.

### Utilisation dans les tools MCP

```python
from colaig.auth.tokens import get_current_token, require_admin

# En tête de chaque tool admin-gated :
if err := require_admin(get_current_token()):
    return err  # JSON {"error": "..."} — même format que les autres guards
```

`require_admin()` retourne :
- `None` si le token est présent et `role == "admin"` → accès autorisé
- `json.dumps({"error": "Authentification requise"})` si token absent
- `json.dumps({"error": "Droits administrateur requis"})` si token user

## Fichiers

### tokens.py

```python
@dataclass
class TokenContext:
    user_id: str
    scope: str = "*"
    name: str = ""
    role: str = "user"  # "user" | "admin" — déterminé par COLAIG_ADMIN_USER_IDS

# ContextVar async — lisible depuis n'importe quel MCP tool
def get_current_token() -> Optional[TokenContext]
def set_current_token(ctx: Optional[TokenContext]) -> None
def require_admin(token_ctx) -> Optional[str]  # None = OK, str = erreur JSON

class TokenManager:
    def __init__(self, storage, base_url: str = "")
    def _is_admin(user_id: str) -> bool          # lit COLAIG_ADMIN_USER_IDS
    async def create(user_id, name, scope="*", expires_at=None) -> str  # raw token
    async def resolve(raw_token) -> Optional[TokenContext]              # O(1)
    async def revoke(user_id, token_name) -> bool
    async def list_tokens(user_id) -> list[dict]                        # sans secrets
```

### oidc_validator.py

```python
class OIDCValidationError(Exception):
    """Token OIDC invalide, expiré ou non vérifiable."""

class OIDCValidator:
    def __init__(self, issuer: str, audience: str, jwks_uri: str)
    async def validate(raw_token: str) -> TokenContext
    # Raises OIDCValidationError si invalide/expiré/kid inconnu
```

- Charge les clés publiques JWKS depuis l'IdP et les **cache 1h** en mémoire
- Re-fetch automatique si kid inconnu (rotation de clé détectée)
- Graceful degradation si IdP down : cache existant conservé
- Algorithms supportés : RS256, RS384, RS512, ES256, ES384, ES512
- `user_id` = `email` → `preferred_username` → `sub` (ordre de priorité)
- `role` toujours `"user"` — jamais depuis le token client
- Dépendance : `pip install 'colaig[oidc]'` (PyJWT[crypto])

### middleware.py

```python
class MCPTokenMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, app, token_manager,
        mcp_path="/mcp",
        auth_required=False,        # conservé pour compatibilité
        oidc_validator=None,        # OIDCValidator si mode=oidc, None sinon
    )
    # Si oidc_validator fourni → validation OIDC
    # Sinon → validation token statique via TokenManager
```

## Intégration MCP server (server.py)

`colaig_ask` lit `get_current_token()` en tête d'exécution :
- Token présent → override `user_id`, enforce scope, `ConversationType.DM` si scope=`"*"` sans workspace
- Token absent → comportement existant (user_id param, PRIVATE)

3 tools token ajoutés :
- `colaig_create_token(name, scope, expires_in_days)` → raw token + chemin config MCP
- `colaig_list_tokens()` → liste masquée
- `colaig_revoke_token(name)` → suppression fichier

Ces tools nécessitent un token valide (ils lisent `get_current_token()`).

## Configuration

```bash
# ── Mode token (défaut) ────────────────────────────────────────────────────
COLAIG_BASE_URL=https://colaig.org.fr   # URL publique pour les configs MCP générées
COLAIG_MCP_AUTH_ENABLED=false           # false = pass-through (backward compat)
                                         # true  = 401 si pas de token
COLAIG_ADMIN_USER_IDS=@alice:tchap.fr,@bob:agent.gouv.fr
                                         # user_ids avec rôle admin (virgule-séparé)

# ── Mode OIDC enterprise ───────────────────────────────────────────────────
COLAIG_MCP_AUTH_MODE=oidc               # "token" (défaut) | "oidc"
COLAIG_OIDC_ISSUER=https://sso.org.fr/realms/org
COLAIG_OIDC_AUDIENCE=colaig-instance-rh
COLAIG_OIDC_JWKS_URI=https://sso.org.fr/realms/org/protocol/openid-connect/certs
```

`TokenManager` est instancié uniquement si `COLAIG_MCP_ENABLED=true`.
Le middleware est ajouté uniquement si `COLAIG_MCP_ENABLED=true`.

## Points d'attention

- `_personal_ws_slug()` doit rester synchronisé avec `workspace.py:get_or_create_personal_workspace()`
- Le `sha256(raw_token)` est le nom du fichier token — jamais le token brut en clair sur le storage
- `ContextVar` : thread-safe et async-safe — chaque requête HTTP a son propre contexte
- Révocation immédiate : supprimer le fichier JSON suffit, pas de cache côté serveur
