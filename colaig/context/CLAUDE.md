# context/ — Le Cerveau de Colaig

## Propriétaire : Agent CERVEAU

## Rôle

Le Context Resolver est le composant le plus important de Colaig.
Pour chaque message reçu, il détermine :
- **Qui** parle (user_id → domaine, nom)
- **D'où** (room_id → type de salon, workspace associé)
- **Quel mode** (assistant / chatbot / personnel)
- **Quel comportement** adopter (system prompt, ton, outils)

## resolver.py — Algorithme de résolution

```
Message reçu (IncomingMessage)
    │
    ▼
1. Chercher conversation_id dans le cache des mappings → workspace
    │
    ├── TROUVÉ → mode = ASSISTANT
    │
    └── PAS TROUVÉ → Refresh workspaces depuis storage
         │
         ├── TROUVÉ (conversation_id dans ws.conversations)
         │   → Mettre en cache, mode = ASSISTANT
         │
         └── PAS TROUVÉ
              │
              ├── DM → find_workspace_for_user() (user_ids)
              │   ├── TROUVÉ → mode = ASSISTANT (workspace existant)
              │   └── PAS TROUVÉ → get_or_create_personal_workspace()
              │       → mode = PERSONAL, workspace personnel /.colaig/personal/{safe_id}/
              │
              ├── Salon PUBLIC → mode = CHATBOT
              │   (workspace par défaut, explique Colaig)
              │
              └── Salon PRIVÉ non mappé → mode = CHATBOT
```

### Cache des mappings
- Dict en mémoire : `{conversation_id: WorkspaceConfig}`
- TTL configurable (default 60s)
- `register_workspace()` pour enregistrer immédiatement sans attendre le TTL

## workspace.py — Gestion des workspaces

### Fonctions principales
```python
async def load_workspace(storage, path) -> WorkspaceConfig
async def list_workspaces(storage) -> list[WorkspaceConfig]
async def create_workspace(storage, storage_path, name, ...) -> WorkspaceConfig
async def get_or_create_personal_workspace(storage, user_id) -> WorkspaceConfig
async def add_conversation_to_workspace(storage, workspace_path, conversation_id)
async def update_workspace_config(storage, workspace_path, **fields)
def find_workspace_for_conversation(workspaces, conversation_id)
def find_workspace_for_user(workspaces, user_id)
def create_default_workspace() -> WorkspaceConfig  # mode chatbot uniquement
```

### Champs proactifs dans WorkspaceConfig
Nouveaux champs opt-in (lus depuis `config.yaml`, défaut = désactivé) :
```yaml
proactive_notifications: true     # Active les notifications de changements docs
notification_channels:            # Canaux cibles (vide = toutes les conversations)
  - "!roomid:server"
```
Utilisés par `run_indexation_loop` (main.py) via `notifier.format_notification()`.
Voir `colaig/rag/CLAUDE.md` section `notifier.py` pour le détail.

### Workspace personnel (mode DM)
```python
# Chemin : /{safe_user_id}/  (dossier de premier niveau dans le storage, comme tout workspace)
# user_ids: [user_id], rag_enabled: False
# Idempotent — charge si existant, crée sinon
ws = await get_or_create_personal_workspace(storage, "@alice:tchap.fr")
# → /alice_tchap_fr/ avec scaffold .colaig/ complet
```

### Structure workspace sur storage
```
{workspace_path}/
├── (documents métier)
└── .colaig/
    ├── config.yaml          # WorkspaceConfig sérialisé
    ├── indexes/             # Index FAISS RAG (fichiers binaires)
    ├── conversations/       # Historiques {conversation_id}.json
    └── users/               # Mémoire sémantique per-user
        └── {safe_user_id}/
            ├── memory.faiss
            ├── memory.pkl
            └── profile.json
```

## user_memory.py — Mémoire sémantique per-user

Trois rythmes d'opération — tous workspace-bound :

```python
# Rythme 1 — Lecture (appelé depuis PreExecutionBuilder.build())
facts = await user_memory.read(user_id, workspace_path, query_embedding, k=5)
# → list[MemoryFact] pertinents — [] si aucun ou erreur

# Rythme 2 — Extraction fire-and-forget (après envoi réponse)
user_memory.schedule_extract(user_id, workspace_path, user_msg, assistant_msg, conv_id)
# → asyncio.create_task() — ne bloque jamais

# Rythme 3 — Consolidation background (~1h par user actif)
await user_memory.consolidate(user_id, workspace_path)
# → REFINE_PROFILE (synthèse LLM → profile.json) + rebuild index
```

### Clé de registry : `"user::{workspace_path}::{safe_user_id}"`
### `load_profile()` → `Optional[UserProfile]` (None si absent)

## layers.py — Construction des 5 couches

```python
def build_context(workspace, message, mode, history=None) -> WorkspaceContext:
    # Couche 1 — Comportement : system_prompt, ton, expertise
    # Couche 2 — Capacités : tools disponibles
    # Couche 3 — Conversation : derniers N messages
    # Couche 4 — Connaissances : (rempli plus tard par RAG)
    # Couche 5 — Profil : user_domain extrait du user_id Matrix
```

### Historique de conversation
- Fichiers JSON sur storage : `{workspace_path}/.colaig/conversations/{conversation_id}.json`
- Charger les N derniers messages (configurable, default 10)
- Si le fichier n'existe pas → historique vide (pas d'erreur)

## Points d'attention
- Le resolver est **rapide** (<100ms) — tout est en cache ou en mémoire
- `get_or_create_personal_workspace()` fait I/O storage → appel async dans resolver
- Graceful degradation : si storage down, fallback sur `create_default_workspace()` en mémoire
- Les erreurs de config.yaml invalide → logger + ignorer ce workspace
- En mode DM, workspace personnel est toujours un vrai workspace sur storage — pas de workspace "virtuel"
