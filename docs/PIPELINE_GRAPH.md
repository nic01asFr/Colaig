# Colaig v3 — Pipeline Graph & Arbre des Configurations

Document de référence généré le 2026-02-28.
Décrit exhaustivement l'arbre des possibilités du pipeline, le graph relationnel des composants,
la matrice des configurations et la stratégie de couverture de tests.

---

## 1. Graph Relationnel des Composants

### 1.1 Dépendances Requises (toujours présentes)

```
AlbertClient ──────────────────────────────────────────────────┐
    │                                                          │
    ├──> EmbeddingService ──> FaissStore ──> Retriever         │
    │                              │                          │
    │                              └──> Indexer <── Chunker   │
    │                                        │                │
    └──> Generator                    StorageProtocol ◄───────┤
                                             │                │
ContextResolver ◄────────────────────────────┘                │
    │                                                         │
    └──> WorkspaceConfig (.colaig/config.yaml)                │
                                                              │
MessageHandler ◄──────────────────────────────────────────────┘
    │   requires: messaging + resolver + retriever + generator + storage
    │   optional: analyser + orchestrator + synthesiser + conversation_memory
    │             tool_registry + workspace_stores + albert_client(audio)
    │
MessagingProtocol (matrix | telegram)
```

### 1.2 Dépendances Conditionnelles (selon config)

```
agents_enabled=True ──────────────────────────────────────────────────┐
                                                                       │
    Synthesiser ◄── albert(medium) ◄── ALBERT_MODEL_MEDIUM             │
        │                                                              │
    Analyser ◄── albert(light) ◄── ALBERT_MODEL_LIGHT [phase6]        │
        │    ◄── tool_registry [use_tool_calling]                     │
        │                                                              │
    Orchestrator ◄── albert(chat) ◄── ALBERT_MODEL_CHAT [phase6]      │
        │       ◄── tool_registry [is_agentic]                        │
        │       ◄── workspace_stores [isolation]                      │
        │       ◄── reporter [ProgressReporter, phase6]               │
        │                                                              │
    ToolRegistry ──────────────────────────────────────────────────────┤
        │   base: search_documents, fetch_document,                   │
        │         list_documents, summarize_text                      │
        │   + document_index_enabled: search/list/get_document_index  │
        │   + agents_phase6_enabled: assess_completion                │
        │          └──> Synthesiser.assess()                          │
        │                                                              │
    ConversationMemory ◄── EmbeddingService + StorageProtocol         │
                                                                       │
document_index_enabled=True                                            │
    DocumentIndex ◄── EmbeddingService + AlbertClient + Storage       │
                                                                       │
mcp_enabled=True                                                       │
    ColaigMCPServer ◄── resolver + retriever + indexer + storage      │
                    ◄── analyser + orchestrator + synthesiser [agents] │
```

---

## 2. Arbre des Possibilités (Decision Tree)

```
handle_message(IncomingMessage)
│
├── [HAS AUDIO & empty body]
│   ├── albert_client present?
│   │   ├── YES → transcribe(audio) → inject → body.strip()?
│   │   │         ├── non-empty → continue
│   │   │         └── empty → send("Je n'arrive pas à traiter...") → RETURN
│   │   └── NO → skip transcription (body remains empty)
│   │             [continues with empty body → likely chatbot/fallback]
│
├── resolver.resolve(message) [pre-resolution pour mode detection]
│   ├── EXCEPTION → skip to pipeline directement
│   └── OK → context.mode ?
│       ├── CHATBOT
│       │   ├── body matches _CMD_CREATE → create workspace → link → RETURN
│       │   ├── body matches _CMD_LINK → link conversation → RETURN
│       │   └── not is_reply → send onboarding → RETURN
│       │       [is_reply → continue to pipeline]
│       └── ASSISTANT | PERSONAL → continue to pipeline
│
├── is_phase2? [all([analyser, orchestrator, synthesiser])]
│   │
│   ├── FALSE → _handle_phase1()
│   │   ├── resolver.resolve() → WorkspaceContext
│   │   ├── context.workspace.rag_enabled?
│   │   │   ├── YES → retriever.retrieve()
│   │   │   │   ├── workspace_stores[ws.workspace_id] present? → use ws_store
│   │   │   │   └── no ws_store → use default store
│   │   │   └── NO → search_results = []
│   │   ├── generator.generate(query, context, search_results)
│   │   │   └── 1 appel Albert (ALBERT_MODEL_CHAT)
│   │   └── save_history (via storage)
│   │
│   └── TRUE → _handle_phase2()
│       ├── [conversation_memory present AND workspace.storage_path]
│       │   └── conversation_memory.load_relevant_history() → context.conversation_history
│       │
│       ├── THINKING: analyser.analyse(message, context)
│       │   ├── body matches GREETING_PATTERNS → shortcut
│       │   │   └── Intent(GREETING, is_direct=True, direct_response="Bonjour!")
│       │   ├── use_tool_calling?
│       │   │   ├── YES → albert.chat_with_tools(ANALYSE_INTENT_TOOL)
│       │   │   └── NO → albert.chat() → parse JSON from text
│       │   └── Intent {
│       │         intent_type, query_reformulated, entities,
│       │         needs_rag, needs_tools, confidence,
│       │         orchestrator_directives, synthesiser_directives,
│       │         search_directives [Phase 6],
│       │         is_direct, direct_response,
│       │         suggested_next_phase, new_anchors
│       │       }
│       │
│       ├── RETRIEVING: orchestrator.execute(intent, context)
│       │   ├── is_agentic? [albert AND tool_registry present]
│       │   │   ├── FALSE → _execute_deterministic()
│       │   │   │   ├── _plan_steps():
│       │   │   │   │   ├── GREETING → steps = []
│       │   │   │   │   ├── needs_rag → rag_search step
│       │   │   │   │   ├── resources_to_target → storage_fetch step
│       │   │   │   │   └── needs_tools → mcp_tool placeholder step
│       │   │   │   └── execute each step sequentially
│       │   │   │
│       │   │   └── TRUE → _execute_agentic()
│       │   │       ├── pre_exec.retrieval_results → inject chunks in plan
│       │   │       ├── tool filtering:
│       │   │       │   ├── pre_exec.available_tools present → use pre_exec tools
│       │   │       │   └── else → use agent_ctx.available_tools
│       │   │       ├── tool_schemas empty? OR intent=GREETING → skip loop
│       │   │       └── LOOP (max_iterations):
│       │   │           ├── last iteration → tool_choice="none"
│       │   │           ├── albert.chat_with_tools() → ChatCompletionResult
│       │   │           ├── has_tool_calls?
│       │   │           │   ├── NO → set orchestrator_reasoning → BREAK
│       │   │           │   └── YES → execute each tool:
│       │   │           │       ├── tool=search_documents → plan.tool_results (RAG format)
│       │   │           │       ├── tool=assess_completion → synthesiser.assess()
│       │   │           │       └── other tools → plan.tool_results
│       │   │           └── feed tool results back to messages
│       │   │
│       │   └── ExecutionPlan {
│       │         intent, steps, search_results, tool_results,
│       │         orchestrator_reasoning, context_card
│       │       }
│       │
│       ├── SYNTHESIZING: synthesiser.synthesise(plan, context, history)
│       │   └── build_messages() → albert.chat() → 1 appel Albert
│       │
│       ├── COMPLETE: messaging.send(response.text)
│       │
│       └── save history:
│           ├── conversation_memory present → save_turn()
│           └── else → save_history() via storage
```

---

## 3. Matrice des Configurations

| # | agents_enabled | phase6_enabled | document_index | analyser_use_tool_calling | Orchestrator mode | assess_completion | Tests |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---|
| C1 | ✗ | ✗ | ✗ | ✗ | — | ✗ | Phase 1 minimal |
| C2 | ✗ | ✗ | ✓ | ✗ | — | ✗ | Phase 1 + DocIndex scan |
| C3 | ✓ | ✗ | ✗ | ✗ | Deterministic | ✗ | Phase 2 déterministe |
| C4 | ✓ | ✗ | ✗ | ✓ | Agentic | ✗ | Phase 5 agentic |
| C5 | ✓ | ✗ | ✓ | ✓ | Agentic | ✗ | Phase 5 + DocIndex |
| C6 | ✓ | ✓ | ✗ | ✗ | Agentic | ✓ | Phase 6 complet |
| C7 | ✓ | ✓ | ✓ | ✗ | Agentic | ✓ | Phase 6 Full |

### Configurations Workspace

| # | rag_enabled | tools_enabled | storage_readonly | workspace_stores | Context mode |
|---|:-:|---|:-:|:-:|:-:|
| W1 | ✓ | tous | ✗ | ✗ | ASSISTANT |
| W2 | ✗ | [] | ✗ | ✗ | ASSISTANT (no RAG) |
| W3 | ✓ | sous-ensemble | ✗ | ✓ | ASSISTANT (isolated) |
| W4 | ✓ | tous | ✓ | ✗ | ASSISTANT (readonly) |
| W5 | — | — | — | — | CHATBOT (no workspace) |
| W6 | — | — | — | — | PERSONAL (DM) |

---

## 4. Nœuds Critiques et Invariants

### Invariants du pipeline (vérifiables par tests)

1. **is_phase2** = `all([analyser is not None, orchestrator is not None, synthesiser is not None])`
   - Si l'un des 3 est None → Phase 1 automatiquement

2. **is_agentic** = `orchestrator._albert is not None AND orchestrator._tool_registry is not None`
   - Si albert ou tool_registry absent → déterministe

3. **Tool registry composition** :
   - `search_documents` ∈ tools TOUJOURS (si Retriever fourni)
   - `assess_completion` ∈ tools SSI `synthesiser is not None AND agents_phase6_enabled=True`
   - `search_document_index` ∈ tools SSI `document_index is not None`

4. **Tool filtering** :
   - `available_tools = role_tools ∩ workspace.tools_enabled` (si workspace.tools_enabled non vide)
   - Priorité Phase 6 : `pre_exec.available_tools` override `agent_ctx.available_tools`

5. **GREETING shortcut** :
   - Analyser : `is_direct=True` + `direct_response` set
   - Orchestrateur agentic : `intent.intent_type == GREETING` → skip loop entier
   - NOTE : `intent.is_direct` n'est PAS vérifié dans handlers.py — l'orchestrateur et le synthétiseur
     sont toujours appelés même si `is_direct=True`. Fast-path non implémenté côté handlers.

6. **Audio transcription** :
   - Condition : `message.attachments non vide AND message.body.strip() == "" AND albert_client`
   - Pas de transcription si body déjà non vide

7. **CHATBOT mode** :
   - Condition `not is_reply` → onboarding envoyé et RETURN (pas de pipeline)
   - `is_reply=True` → le message continue vers le pipeline

8. **Conversation memory** :
   - Activée si : `self._conversation_memory AND context.workspace AND context.workspace.storage_path`
   - Mode CHATBOT (workspace=None) → toujours `save_history()` via storage

---

## 5. ToolRegistry — Inventaire Complet

| Tool | Catégorie | Toujours présent | Requis | Filtré par |
|---|---|:-:|---|---|
| `search_documents` | rag | ✓ | Retriever | workspace.tools_enabled |
| `fetch_document` | storage | ✓ | Storage + Workspace | workspace.tools_enabled |
| `list_documents` | storage | ✓ | Storage + Workspace | workspace.tools_enabled |
| `summarize_text` | llm | ✓ | AlbertClient | workspace.tools_enabled |
| `search_document_index` | document_index | ✗ | DocumentIndex | workspace.tools_enabled |
| `list_document_index` | document_index | ✗ | DocumentIndex | workspace.tools_enabled |
| `get_document_metadata` | document_index | ✗ | DocumentIndex | workspace.tools_enabled |
| `assess_completion` | meta | ✗ | Synthesiser + phase6 | workspace.tools_enabled |

---

## 6. Couplages Forts / Points de Fragilité

| Point de fragilité | Risque | Mitigation |
|---|---|---|
| `is_direct` non géré dans handlers.py | Salutations passent par orchestrateur+synthétiseur inutilement | À implémenter dans handlers.py (Phase 7?) |
| Orchestrateur agentique requiert `albert_client` | Si albert indisponible → mode déterministe silencieux | Log présent mais pas d'alarme |
| `assess_completion` requiert `synthesiser` construit AVANT `build_tool_registry` | Ordre d'init dans main.py critique | Commentaire dans main.py, testé |
| `workspace_stores` non rempli au premier message | RAG utilise store global (partagé) | Acceptable, `initial_indexation` le peuple |
| `conversation_memory.load_relevant_history` timeout | Historique non chargé → réponse sans contexte | `except → logger.warning` (dégradé gracieux) |
| `CHATBOT` + `is_reply=True` → pipeline exécuté sans workspace | Synthesiser avec `context.workspace=None` | Testé dans test_handlers_phase2 |

---

## 7. Stratégie de Tests par Configuration

### Fichiers de tests existants

| Fichier | Couvre |
|---|---|
| `test_handlers.py` | Phase 1 : ASSISTANT/CHATBOT/PERSONAL, RAG enabled/disabled, error handling |
| `test_handlers_phase2.py` | Phase 2 : THINKING/RETRIEVING/SYNTHESIZING/COMPLETE, phase callbacks |
| `test_analyser.py` | Analyser: JSON parsing, tool calling, Phase 6 SearchDirectives |
| `test_orchestrator.py` | Orchestrator: agentic/déterministe, tool execution, Phase 6 |
| `test_synthesiser.py` | Synthesiser: synthesise(), assess(), synthesise_stream() |
| `test_context_builder.py` | build_tool_registry(): compositions, assess_completion |
| `test_phase5_integration.py` | End-to-end: Analyser→Orchestrator→Synthesiser avec tool calling |
| `test_phase4_integration.py` | End-to-end: pipeline déterministe |

### Nouveau fichier : test_pipeline_configurations.py

Couvre les **combinaisons** inter-composants non testées :
- Matrice complète `is_phase2 × is_agentic × phase6_enabled`
- `is_phase2` partiel (1 ou 2 agents seulement → retombe sur Phase 1)
- Workspace features interactions (`rag_enabled × workspace_stores × tools_enabled`)
- Audio transcription avec/sans albert_client
- CHATBOT : commandes create/link et onboarding conditions
- ConversationMemory : load/save selon workspace.storage_path
- ToolRegistry : combinaisons document_index + assess_completion

---

## 8. Variables d'Environnement — Arbre d'Activation

```
COLAIG_AGENTS_ENABLED=true
    └── COLAIG_AGENTS_PHASE6_ENABLED=true
        ├── ALBERT_MODEL_LIGHT=... → Analyser model
        ├── ALBERT_MODEL_MEDIUM=... → Synthesiser model
        └── assess_completion tool registered (Synthesiser → ToolRegistry)

COLAIG_DOCUMENT_INDEX_ENABLED=true
    └── search/list/get_document_index tools registered

COLAIG_ANALYSER_USE_TOOL_CALLING=true
    └── Analyser uses chat_with_tools (not chat)

COLAIG_MCP_ENABLED=true
    └── ColaigMCPServer mounted on /mcp

STORAGE_BACKEND=local|webdav|bigfolder|s3|msgraph
    └── Different StorageProtocol implementation

MESSAGING_BACKEND=matrix|telegram
    └── Different MessagingProtocol implementation
```
