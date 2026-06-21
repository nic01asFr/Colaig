# agents/ — Pipeline Multi-Agent Contextualisé

## Propriétaire : Agent CERVEAU

## Statut : IMPLÉMENTÉ (Phase 4 + Phase 5)

Le pipeline multi-agent est opérationnel. Phase 5 introduit l'Orchestrateur agentique (boucle LLM + tool calling), la mémoire conversationnelle sémantique, et le registre d'outils.

## Architecture

```
Message + WorkspaceContext
    │
    ▼
┌───────────────────────────────────────────────────────────┐
│  ANALYSEUR (1 appel Albert, temp=0.1)                     │
│                                                           │
│  Contexte :                                               │
│  - prompt par défaut + override .colaig/prompts/analyser.md│
│  - historique conversation, profil utilisateur             │
│  - config workspace (description, langue, ton)            │
│  - descriptions des tools disponibles (si tool_registry)  │
│                                                           │
│  Mode par défaut : prompt JSON libre → parsing             │
│  Mode use_tool_calling=True : chat_with_tools avec         │
│    analyse_intent → JSON garanti sans parsing              │
│                                                           │
│  Produit :                                                │
│  - Intent (type, reformulation, entities, needs_rag)      │
│  - AgentDirectives pour l'Orchestrateur                   │
│    (resources à cibler, tools, stratégie de recherche)    │
│  - AgentDirectives pour le Synthétiseur                   │
│    (format, ton, structure, focus)                        │
└─────────────┬─────────────────────────────────────────────┘
              │
              ▼
┌───────────────────────────────────────────────────────────┐
│  ORCHESTRATEUR (boucle agentique OU coordination pure)    │
│                                                           │
│  Mode agentique (albert + tool_registry fournis) :        │
│  - Boucle LLM : chat_with_tools → tool_calls → exécution │
│  - Max iterations configurable (défaut 5)                  │
│  - Dernier tour : tool_choice="none" → réponse texte      │
│  - plan.orchestrator_reasoning = résumé final              │
│                                                           │
│  Mode déterministe (backward compat, sans albert) :       │
│  - 0 appel LLM — coordination pure basée sur directives   │
│  - Planification : rag_search, storage_fetch, mcp_tool    │
│                                                           │
│  Tools built-in (ToolRegistry) :                          │
│  - search_documents (retriever RAG)                       │
│  - fetch_document, list_documents (storage)               │
│  - summarize_text (Albert chat)                           │
│                                                           │
│  Produit :                                                │
│  - ExecutionPlan (steps, search_results, tool_results,    │
│    orchestrator_reasoning, context_card)                  │
└─────────────┬─────────────────────────────────────────────┘
              │
              ▼
┌───────────────────────────────────────────────────────────┐
│  SYNTHÉTISEUR (1 appel Albert, temp=0.3)                  │
│                                                           │
│  Contexte :                                               │
│  - directives de l'Analyseur (synthesiser_directives)     │
│  - résultats complets de l'Orchestrateur                  │
│  - orchestrator_reasoning (mode agentique)                │
│  - prompt par défaut + override .colaig/prompts/          │
│    synthesiser.md                                         │
│  - skills workspace (.colaig/skills/*.md)                 │
│  - config (langue, tone, expertise_level)                 │
│                                                           │
│  Produit :                                                │
│  - GeneratedResponse (text, sources, confidence,          │
│    context_card, generation_time_ms)                      │
│  - ContextCard enrichie avec phases complètes             │
└───────────────────────────────────────────────────────────┘
```

## Mémoire Conversationnelle (Phase 5)

```python
class ConversationMemory:
    """Mémoire sémantique — toujours inclure ALWAYS_INCLUDE_RECENT (3) derniers
    messages + top-K sémantiquement proches par cosine similarity."""

    async def load_relevant_history(
        workspace_path, conversation_id, current_query, max_messages=10
    ) -> list[dict]

    async def save_turn(
        workspace_path, conversation_id, user_message, assistant_response, existing_history
    ) -> list[dict]
```

- **Sans embedding service** → fallback last-N messages (comportement classique)
- **Avec embedding service** → récupération sémantique : ALWAYS_INCLUDE_RECENT=3 + top-K cosine
- **Stockage** : JSON dans `.colaig/conversations/{conversation_id}.json`
- **Max stocké** : configurable via `COLAIG_CONVERSATION_MEMORY_MAX_STORED` (défaut 100)

## Fichiers

### context_builder.py

```python
async def build_agent_context(
    storage, workspace: WorkspaceConfig, agent_role: str,
    directives: AgentDirectives = None,
) -> AgentContext

def build_tool_registry(
    retriever, storage, albert, workspace=None
) -> ToolRegistry
```

`build_tool_registry` construit un registre in-memory avec les 4 outils built-in.

### tool_registry.py — Registre d'outils

```python
class ToolRegistry:
    def register(definition: ToolDefinition, handler: Callable) -> None
    def get(name: str) -> Optional[tuple[ToolDefinition, Callable]]
    def list_openai_schemas() -> list[dict]
    def filter_by_names(names: list[str]) -> ToolRegistry
    async def execute(tool_call: ToolCall) -> ToolResult
```

### tools/ — Outils built-in

| Tool | Handler factory | Description |
|------|-----------------|-------------|
| `search_documents` | `create_search_handler(retriever)` | RAG sémantique |
| `fetch_document` | `create_fetch_handler(storage, workspace)` | Téléchargement fichier |
| `list_documents` | `create_list_handler(storage, workspace)` | Liste répertoire |
| `summarize_text` | `create_summarize_handler(albert)` | Résumé LLM |
| `ask_workspace` | `create_ask_workspace_handler(user_id, all_workspaces, retriever, ...)` | Délégation inter-workspace avec ACL |

### workspace_delegate.py — Délégation inter-workspace

Module partagé pour l'exécution de tâches dans un workspace cible.

**ACL** : `user_id in workspace.user_ids` — configuré par l'admin dans `config.yaml`.

```python
# Délégation légère — ask_workspace (RAG cross-workspace)
result = await run_rag_delegate(
    workspace_id="espace-rh",
    query="politique congés",
    user_id="@alice:tchap.fr",
    all_workspaces=all_workspaces,
    retriever=retriever,
    workspace_stores=workspace_stores,  # isolation
)
# → WorkspaceDelegateResult(chunks=[{text, source, score, ...}])

# Tâche complète — Mode C (pipeline analyser+orchestrateur+synthétiseur)
result = await run_workspace_task(
    workspace_id="espace-rh",
    query="résume les horaires de travail",
    user_id="@alice:tchap.fr",
    all_workspaces=all_workspaces,
    analyser=analyser, orchestrator=orchestrator, synthesiser=synthesiser,
    # ou: retriever=retriever, generator=generator  (Phase 1 fallback)
)
# → WorkspaceTaskResult(response="...", sources=["doc.pdf"], confidence=0.85)
```

**Exceptions** : `WorkspaceNotFound`, `WorkspaceAccessDenied`.

### Mode C — Session d'orchestration persistante avec plan dynamique

Le Mode C repose sur trois niveaux hiérarchiques stockés dans `.colaig/tasks/` du workspace personnel :

```
TaskDefinition        → "Quoi faire, avec quelles contraintes, quand, comment livrer"
    └── Session       → Instance persistante de l'orchestrateur, pilotée par le scheduler
          └── Plan    → Document vivant (plan.json) : objectif + étapes + statuts
                └── Subtask × N → Sous-agents run_workspace_task() pour chaque étape
```

**Principe d'autorisation** :
- Création : token MCP ou DM Tchap → `user_id` résolu, stocké dans `task.json`
- Exécution : scheduler lit `user_id` → `check_workspace_access()` à chaque tool call

**Structure de stockage** :
```
/{slug}/.colaig/
├── conversations/task_{task_id}_{ts}.json  ← historique LLM (ConversationMemory)
└── tasks/
    ├── {task_id}.json                      ← TaskDefinition + lifecycle
    └── {task_id}/
        ├── plan.json                       ← Plan dynamique — mis à jour à chaque step
        ├── session.json                    ← État runtime (conversation_id, step, heartbeat)
        └── subtasks/
            ├── {subtask_id}.json           ← Définition sous-tâche
            └── {subtask_id}/result.json    ← Résultat sous-agent
```

**Plan dynamique** — le LLM peut ajouter/modifier/réordonner les étapes en cours d'exécution :
```json
{ "steps": [
    { "step_id": "s1", "type": "subtask", "subtask_id": "sub_rh", "status": "done", "result_summary": "..." },
    { "step_id": "s2", "type": "subtask", "subtask_id": "sub_juridique", "status": "running" },
    { "step_id": "s3", "type": "synthesis", "status": "pending" },
    { "step_id": "s4", "type": "report",   "status": "pending" }
]}
```

**Tool registry étendu (background)** — en plus des tools interactifs :
| Tool | Description |
|------|-------------|
| `run_subtask` | Pipeline complet workspace cible → persiste sous-tâche + résultat + plan mis à jour |
| `update_plan` | Modifie le plan dynamique |
| `report_to_user` | `messaging.send(delivery.conversation_id, msg)` |
| `create_document` | `storage.upload(path, content)` + ACL |
| `create_background_task` | Crée une tâche *(PERSONAL mode seulement)* |
| `pause_and_ask_user` | Suspend, attend réponse user *(Phase 2)* |

**Session lifecycle** : `PENDING → RUNNING → DONE` | `↘ WAITING_FOR_USER → RUNNING` | `↘ FAILED (timeout)`

Concurrence : 1 session RUNNING max par user. Heartbeat dans `session.json`.

Voir `memory/background_tasks.md` pour le design complet.

**ask_workspace dans l'orchestrateur** :
- Injecté dynamiquement dans `_execute_agentic()` si `context.user_id` connu
- Remplace le placeholder du tool_registry par un handler user-spécifique
- `workspace_resolver.workspaces` toujours frais (liste vivante)
- `WorkspaceContext.user_id` peuplé depuis `message.user_id` dans `layers.build_context()`

### profile_service.py — Résolution comportementale

Charge et sélectionne les behaviors/skills sémantiquement proches d'un message utilisateur.

```python
class ProfileService:
    def __init__(
        self, storage,
        embeddings=None,         # Requis pour la résolution sémantique
        index_registry=None,     # FaissIndexRegistry — behaviors/skills en cache mémoire
    )

    async def resolve_behavior(message_embedding, workspace_path) -> Optional[str]
    # → texte du behavior le plus pertinent (ou None si score < seuil)

    async def resolve_skills(message_embedding, workspace_path) -> list[str]
    # → liste de textes skills pertinents
```

**Caching** : avec `index_registry`, `behaviors.faiss` et `skills.faiss` sont chargés une seule fois en mémoire via `FaissIndexRegistry.get_or_load(key, loader)` — utilisent `ColaigIndex.behaviors_key()` / `ColaigIndex.skills_key()`. Sans registry, chargement direct depuis storage à chaque appel.

---

### analyser.py — Agent Analyseur

```python
class Analyser:
    def __init__(
        self, albert, storage, temperature=0.1,
        use_tool_calling=False,   # Mode JSON garanti via chat_with_tools
        tool_registry=None,       # Pour descriptions dans le prompt
    )
    async def analyse(message: IncomingMessage, context: WorkspaceContext) -> Intent
```

### orchestrator.py — Agent Orchestrateur

```python
class Orchestrator:
    def __init__(
        self, storage, retriever,
        albert=None,              # Requis pour mode agentique
        tool_registry=None,       # Requis pour mode agentique
        max_iterations=5,         # Max tours de la boucle agentique
        temperature=0.1,
        workspace_resolver=None,  # ContextResolver — workspaces frais pour ask_workspace
        bm25_stores=None,         # dict[workspace_id, BM25Store] — pour ask_workspace hybrid
        on_step_complete=None,  # Callback async(step, plan)
    )
    @property
    def is_agentic(self) -> bool  # True si albert + tool_registry fournis

    async def execute(intent: Intent, context: WorkspaceContext) -> ExecutionPlan
```

### synthesiser.py — Agent Synthétiseur

```python
class Synthesiser:
    def __init__(self, albert, storage, model=None, temperature=0.3, max_tokens=2048)
    async def synthesise(
        plan: ExecutionPlan, context: WorkspaceContext,
        conversation_history=None
    ) -> GeneratedResponse
```

## Intégration dans handlers.py

```python
handler = MessageHandler(
    messaging, resolver, retriever, generator, storage,
    # Phase 2 :
    analyser=analyser,
    orchestrator=orchestrator,
    synthesiser=synthesiser,
    # Phase 5 (optionnel) :
    conversation_memory=conversation_memory,
    tool_registry=tool_registry,
    on_phase_change=callback,
)
```

## Structure workspace étendue (.colaig/)

```
.colaig/
├── config.yaml              # Config workspace
├── indexes/                 # Index FAISS
├── conversations/           # Historiques JSON (ConversationMemory)
├── prompts/                 # Consignes par agent (override user)
│   ├── analyser.md
│   ├── orchestrator.md
│   └── synthesiser.md
└── skills/                  # Connaissances métier
    ├── procedures.md
    └── glossaire.md
```

## Points d'attention

- **Mode agentique** : 2+ appels Albert par message (Analyseur + boucle Orchestrateur + Synthétiseur)
- **Mode déterministe** : 2 appels Albert (Analyseur + Synthétiseur) — `Orchestrator(storage, retriever)` sans albert
- **Backward compat** : Phase 1 / Phase 2 sans Phase 5 fonctionnent sans modification
- **Tool calling** : Albert API (gpt-oss-120b, Mistral) supporte nativement le format OpenAI `tools`
- **Mémoire sémantique** : dégradation gracieuse si pas d'embedding service → last-N classique

## Administration réflexive (tools/admin_tools.py)

L'orchestrateur agentique injecte des méta-outils si le contexte l'autorise
(`WorkspaceACL.can_manage(context, admin_user_ids, workspaces)` — DM admin/owner) :
`manage_workspace` (create/update), `link_conversation`, `set_workspace_prompt`,
`list_manageable_workspaces`, et `manage_workspace_owners` (admin global only).

- Garde fine par cible : `WorkspaceACL.can_manage_workspace(user_id, ws, admin_user_ids)`.
- `Orchestrator(admin_user_ids=...)` ; injection miroir de `ask_workspace`.
- Owners : `WorkspaceConfig.owners` (créateur = owner) ; modifiables seulement via
  `manage_workspace_owners` / `set_workspace_owners` (hors `_UPDATABLE` — anti-escalade).
Détails : docs/REFLEXIF_ET_OPS.md.
