# mcp/ — Serveur MCP Colaig

## Statut : IMPLÉMENTÉ (Phase 4 + sessions persistées)

Colaig s'expose comme un **serveur MCP (Model Context Protocol)** streamable HTTP, permettant à tout client MCP (Claude Desktop, Cursor, agent LLM autonome…) d'interagir avec le pipeline RAG et les agents.

**Une session MCP est une conversation Colaig à part entière.** L'historique est persisté dans `{workspace}/.colaig/conversations/{conversation_id}.json`, identique aux conversations Tchap/Matrix. La mémoire sémantique per-user (`user_memory`) fonctionne aussi pour les clients MCP.

Voir [docs/MCP_SESSIONS.md](../../docs/MCP_SESSIONS.md) pour la documentation complète.

## Architecture

```python
from colaig.mcp import ColaigMCPServer

mcp_server = ColaigMCPServer(
    resolver=resolver,
    retriever=retriever,
    indexer=indexer,
    storage=storage,
    config=config,
    generator=generator,              # Phase 1 fallback
    analyser=analyser,                # Phase 2 (optionnel)
    orchestrator=orchestrator,        # Phase 2 (optionnel)
    synthesiser=synthesiser,          # Phase 2 (optionnel)
    conversation_memory=conv_memory,  # Persistance historique (Phase 5)
    user_memory=user_memory,          # Mémoire sémantique per-user (Phase 5)
)

# Montage sur FastAPI
app.mount("/mcp", mcp_server.http_app(path="/mcp"))
```

## Primitives MCP enregistrées

### Tools (10+)

| Tool | Description | Paramètres |
|------|-------------|------------|
| `colaig_ask` | Pipeline complet (analyse → recherche → réponse) + historique persisté | `question`, `workspace_id?`, `conversation_id?`, `user_id?` |
| `colaig_search` | Recherche documentaire RAG seule | `query`, `k?`, `score_threshold?` |
| `colaig_list_workspaces` | Liste les workspaces configurés | — |
| `colaig_reindex` | Relance l'indexation des documents | `workspace_id?` |
| `colaig_get_config` | Retourne la configuration active (storage, messaging, llm, workspaces) | — |
| `colaig_test_backend` | Teste des credentials sans sauvegarder | `type`, `backend`, `credentials` (JSON) |
| `colaig_set_backend` | Configure un backend (test + sauvegarde runtime.yml) | `type`, `backend`, `credentials` (JSON) |
| `colaig_onboard` | Onboarding interactif via élicitations MCP (formulaires natifs) | `ctx: Context` |
| `colaig_upload_file` | Upload un fichier dans un workspace | `workspace_id`, `filename`, `content_base64`, `path?` |
| `colaig_list_documents` | Liste les documents d'un workspace | `workspace_id`, `limit?` |
| `colaig_get_document` | Détails IA d'un document (résumé, catégorie, entités) | `workspace_id`, `path` |
| `colaig_create_workspace` | Crée un nouveau workspace | `name`, `description?`, `storage_path?`, ... |
| `colaig_update_workspace` | Met à jour un workspace | `workspace_id`, `name?`, ... |
| `colaig_link_conversation` | Lie une conversation à un workspace | `conversation_id`, `workspace_id` |
| `colaig_create_task` | Crée une tâche autonome planifiée (**auth requise**) | `name`, `workspace_id`, `query`, `schedule_type`, `schedule_value`, `delivery_type`, `delivery_target` |
| `colaig_list_tasks` | Liste les tâches planifiées de l'utilisateur (**auth requise**) | — |
| `colaig_delete_task` | Supprime une tâche par task_id (**auth requise**) | `task_id` |
| `colaig_run_task_now` | Déclenche immédiatement une tâche (**auth requise**) | `task_id` |

`colaig_ask` utilise le pipeline Phase 2 (agents) si les 3 agents sont configurés, sinon Phase 1 (generator seul).

`colaig_onboard` utilise les élicitations MCP pour afficher des formulaires natifs dans Claude Desktop/Cursor. Fallback texte si le client ne supporte pas les élicitations.

### Persistance des sessions `colaig_ask`

```
1. colaig_link_conversation("rh", "claude-desktop-alice-rh")
   → lie la session au workspace /espace-rh/

2. colaig_ask("Quelle est la procédure de congé maladie ?",
              conversation_id="claude-desktop-alice-rh",
              user_id="claude-desktop-alice")
   → charge /espace-rh/.colaig/conversations/claude-desktop-alice-rh.json
   → pipeline complet avec historique
   → sauvegarde le tour après réponse

3. colaig_ask("Et les congés paternité ?",
              conversation_id="claude-desktop-alice-rh",  # même ID
              user_id="claude-desktop-alice")
   → historique rechargé — réponse contextualisée
```

Le `conversation_id` est la clé de session : un agent LLM qui passe le même ID entre appels maintient une conversation continue, stockée dans le workspace comme n'importe quelle conversation Tchap.

### Resources (4+)

| URI | Description |
|-----|-------------|
| `colaig://workspaces` | Liste JSON des workspaces (id, name) |
| `colaig://workspace/{id}/config` | Config JSON d'un workspace (id, name, description, rag_enabled, tone, language, tools_enabled) |
| `colaig://workspace/{id}/documents` | DocumentIndex d'un workspace (si COLAIG_DOCUMENT_INDEX_ENABLED) |
| `colaig://onboarding/status` | État de configuration actuel + ce qui manque + prochaine action |
| `colaig://onboarding/backends` | Backends disponibles par type avec champs requis et exemples |

Les resources par workspace sont enregistrées dynamiquement à l'initialisation.

### Prompts (2)

| Prompt | Description | Paramètres |
|--------|-------------|------------|
| `workspace_assistant` | Prompt contextuel pour un assistant workspace | `workspace_id?`, `question?` |
| `onboarding_guide` | Contextualise le LLM pour guider l'utilisateur en onboarding | — |

## Dépendance

```toml
# pyproject.toml
"mcp[cli]>=1.8.0"
```

Utilise `FastMCP` du SDK Python MCP officiel (`mcp.server.fastmcp.FastMCP`).

## Transport

Le serveur utilise le transport **Streamable HTTP** (`mcp.streamable_http_app()`), montable directement sur FastAPI. Le endpoint est disponible sur `/mcp`.

## Authentification par token (auth/)

Les sessions MCP authentifiées sont traitées **exactement comme des DM Tchap** du point de vue du `.colaig` : même workspace personnel, même historique, même mémoire sémantique. Le canal (matrix vs mcp) n'est que du métadonnée.

Token Bearer dans les headers HTTP → `MCPTokenMiddleware` → `TokenContext` dans `ContextVar` → lu par `colaig_ask`.

Voir `colaig/auth/CLAUDE.md` pour le détail complet.

Tools token disponibles :
| Tool | Description |
|------|-------------|
| `colaig_create_token` | Crée un token + génère la config MCP dans le workspace personnel |
| `colaig_list_tokens` | Liste les tokens (sans secrets) |
| `colaig_revoke_token` | Révoque un token par nom |

## Tâches autonomes — Mode C (à implémenter)

Les tools de gestion de tâches planifiées **nécessitent un token valide** (`get_current_token()` non None). Ils opèrent sur le workspace personnel de l'utilisateur authentifié.

```
[Création]  colaig_create_task() → vérifie ACL user→workspace cible → stocke /{slug}/.colaig/tasks/{id}.json
[Exécution] run_task_scheduler_loop() → lit user_id du fichier → check_workspace_access() → run_workspace_task()
[Livraison] messaging.send(conversation_id, result) OU storage.upload(path, result)
```

**ACL workspaces cibles** : identique à `colaig_ask` — `user_id in workspace.user_ids` ou `workspace.public`. Vérifiée à la création ET à l'exécution.

**Schedule types** :
- `"cron"` + expression cron (`"0 8 * * 1"` = lundi 8h)
- `"interval"` + durée (`"7d"`, `"24h"`, `"30m"`)

**Delivery types** :
- `"messaging"` + `conversation_id` → `messaging.send()` (DM Tchap de l'user)
- `"document"` + `workspace_id` + `path` → `storage.upload()`

Voir `colaig/agents/CLAUDE.md#mode-c` et `memory/background_tasks.md` pour le design complet.

## Configuration

Activé via variable d'environnement :

```bash
COLAIG_MCP_ENABLED=true    # Active le serveur MCP
COLAIG_AGENTS_ENABLED=true # Active le pipeline agents (Phase 2)
COLAIG_BASE_URL=https://colaig.org.fr  # URL publique (pour les configs MCP générées)
COLAIG_MCP_AUTH_ENABLED=false          # false = pass-through / true = 401 sans token
```

Si `MCP_ENABLED=true` mais `AGENTS_ENABLED=false`, le serveur MCP fonctionne en Phase 1 (generator seul pour `colaig_ask`).

## Point d'attention FastMCP

Les resources dynamiques par workspace utilisent un pattern de closure (méthode `_register_workspace_resource`) pour capturer les données du workspace. FastMCP 1.13+ valide que les paramètres de la fonction correspondent aux paramètres du template URI — les fonctions des resources dynamiques doivent donc être sans paramètre, avec les données capturées par closure.
