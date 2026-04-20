# Colaig MCP — Sessions conversationnelles persistées

## Vue d'ensemble

Le serveur MCP de Colaig permet à un **client LLM** (Claude Desktop, Cursor, agent autonome…) d'interagir avec Colaig exactement comme un utilisateur humain le ferait sur Tchap ou Telegram.

Une session MCP est une **conversation Colaig à part entière** :

- L'historique est persisté dans `{workspace}/.colaig/conversations/{conversation_id}.json`
- La mémoire sémantique per-user (`user_memory`) s'alimente et se lit entre sessions
- Le pipeline complet (analyser → orchestrateur → synthétiseur) est utilisé, avec le contexte historique injecté

---

## Concept : `conversation_id` = clé de session

Le protocole MCP streamable HTTP est **stateless** — chaque appel `colaig_ask` est une requête HTTP indépendante. La continuité conversationnelle repose entièrement sur le `conversation_id` que le client passe de façon cohérente.

```
Appel 1 : colaig_ask(question="Bonjour",    conversation_id="session-abc")
           → crée   /espace-rh/.colaig/conversations/session-abc.json
           → [{role: user, ...}, {role: assistant, ...}]

Appel 2 : colaig_ask(question="Et pour X?", conversation_id="session-abc")
           → charge /espace-rh/.colaig/conversations/session-abc.json
           → historique injecté dans le pipeline agents
           → réponse contextualisée ("comme je l'ai mentionné…")
           → mise à jour du fichier JSON
```

Un `conversation_id` différent = une session différente, sans partage d'historique.

---

## Paramètres de `colaig_ask`

| Paramètre | Type | Défaut | Rôle |
|-----------|------|--------|------|
| `question` | str | — | Question à poser |
| `workspace_id` | str | `""` | ID du workspace cible (résolu auto si vide) |
| `conversation_id` | str | `"mcp-default"` | Clé d'historique persistant — à stabiliser par session |
| `user_id` | str | `"mcp-client"` | Identifiant du client LLM — utilisé par `user_memory` |

### Recommandations

**`conversation_id`** — choisir un ID stable et descriptif :
```
"claude-desktop-alice-projet-rh-2026"       # par projet + user + année
"agent-analyse-appels-offres-mars-2026"     # par tâche agent autonome
"cursor-bob-refactoring-auth"               # par contexte IDE
```

**`user_id`** — identifier le client appelant pour la mémoire per-user :
```
"claude-desktop-alice"    # Claude Desktop d'Alice
"cursor-bob"              # Cursor de Bob
"agent-orchestrateur-v2"  # Agent autonome
```

---

## Liaison session ↔ workspace

Un `conversation_id` doit être **lié à un workspace** pour que le RAG et l'historique fonctionnent. Deux façons :

### Option A — `workspace_id` explicite à chaque appel
```python
colaig_ask(
    question="...",
    workspace_id="rh",                    # résolution directe
    conversation_id="session-abc",
)
```

### Option B — Liaison persistante via `colaig_link_conversation`
```python
# Une seule fois (persist dans config.yaml du workspace)
colaig_link_conversation(
    workspace_id="rh",
    conversation_id="session-abc",
)

# Ensuite, workspace_id peut être omis
colaig_ask(question="...", conversation_id="session-abc")
```

L'Option B est préférable pour les agents autonomes : la liaison survit aux redémarrages de Colaig et ne nécessite pas de connaître le `workspace_id` à chaque appel.

---

## Stockage de l'historique

Le fichier `{workspace_path}/.colaig/conversations/{sanitized_id}.json` est au format standard Colaig :

```json
[
  {"role": "user",      "content": "Quelle est la procédure de congé maladie ?", "ts": "2026-03-12T15:30:00"},
  {"role": "assistant", "content": "Selon le guide RH (section 4.2)...",          "ts": "2026-03-12T15:30:02"},
  {"role": "user",      "content": "Et pour les congés paternité ?",              "ts": "2026-03-12T15:31:00"},
  {"role": "assistant", "content": "Pour les congés paternité...",                "ts": "2026-03-12T15:31:03"}
]
```

- Même format que les conversations Tchap/Matrix — **interopérable**
- Tronqué à `COLAIG_CONVERSATION_MEMORY_MAX_STORED` (défaut 100) messages
- Récupération sémantique si `conversation_memory` a un embedding service : les messages les plus pertinents à la question courante sont sélectionnés (pas juste les N derniers)

---

## Mémoire per-user (`user_memory`)

Si `user_memory` est injecté dans `ColaigMCPServer` (activé automatiquement si `COLAIG_AGENTS_ENABLED=true`), chaque échange MCP alimente la mémoire sémantique de l'utilisateur identifié par `user_id`.

```
Échange 1 (user_id="claude-desktop-alice") :
  Question : "Je travaille sur la réforme des marchés publics"
  → user_memory extrait le fait : "alice travaille sur marchés publics"
  → stocké dans /espace-rh/.colaig/users/claude-desktop-alice/memory.faiss

Échange suivant (même user_id, workspace quelconque) :
  → user_memory charge les faits pertinents
  → PreExecutionBuilder les injecte dans le contexte
  → réponse adaptée au profil de l'utilisateur
```

L'extraction est **fire-and-forget** — elle ne bloque pas la réponse.

---

## Flux complet

```
Client LLM
    │
    │ colaig_ask(question, conversation_id, user_id, workspace_id)
    ▼
ColaigMCPServer
    │
    ├─ resolver.resolve() → workspace context
    │
    ├─ conversation_memory.load_relevant_history()
    │   └─ {workspace}/.colaig/conversations/{conv_id}.json
    │   → context.conversation_history ← injecté
    │
    ├─ Pipeline agents (analyser → orchestrateur → synthétiseur)
    │   avec context.conversation_history disponible
    │
    ├─ conversation_memory.save_turn()
    │   → mise à jour {workspace}/.colaig/conversations/{conv_id}.json
    │
    ├─ user_memory.schedule_extract()  [fire-and-forget]
    │   → extraction faits → {workspace}/.colaig/users/{user_id}/memory.faiss
    │
    └─ JSON {"answer": ..., "sources": [...], "confidence": ...}
```

---

## Configuration

```bash
COLAIG_MCP_ENABLED=true       # Active le serveur MCP sur /mcp
COLAIG_AGENTS_ENABLED=true    # Active Phase 2 + conversation_memory + user_memory

# Paramètres mémoire conversationnelle
COLAIG_CONVERSATION_MEMORY_MAX_STORED=100    # Messages max stockés par session
COLAIG_CONVERSATION_MEMORY_MAX_RETRIEVED=10  # Messages max injectés par requête
```

Si `COLAIG_AGENTS_ENABLED=false`, `colaig_ask` fonctionne en Phase 1 (generator seul) mais **sans persistance d'historique** (`conversation_memory` est `None`).

---

## Cas d'usage : agent LLM autonome

Un agent LLM peut utiliser Colaig comme mémoire documentaire longue durée :

```python
# Initialisation de la session agent
await colaig_link_conversation(
    workspace_id="projet-infrastructure",
    conversation_id="agent-infra-sprint-42",
)

# Tour 1 — l'agent pose une question de fond
result = await colaig_ask(
    question="Quelles sont les contraintes techniques du déploiement cloud souverain ?",
    conversation_id="agent-infra-sprint-42",
    user_id="agent-infra-v3",
)

# Tour N — l'agent reprend le fil après d'autres traitements
result = await colaig_ask(
    question="En tenant compte de ces contraintes, est-ce que S3 compatible est envisageable ?",
    conversation_id="agent-infra-sprint-42",  # même ID = même contexte
    user_id="agent-infra-v3",
)
# → Colaig recharge l'historique complet du sprint 42
# → réponse cohérente avec les échanges précédents
```

Les échanges sont persistés sur le storage (Nextcloud, S3, filesystem…) du workspace — **auditables par l'équipe**, accessibles même si l'agent redémarre.
