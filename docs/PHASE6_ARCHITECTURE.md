# COLAIG — Architecture Phase 6 : Trame Vivante, Behaviors, Vector-First

> Document de conception exploratoire — février 2026
> Complète [docs/ARCHITECTURE.md](ARCHITECTURE.md) et les vademecums existants.

---

## Vue d'ensemble : ce qui change

Phase 6 enrichit le pipeline existant de cinq concepts interdépendants :

1. **Trame vivante** — état sémantique d'une conversation, persisté avec elle, traversant le pipeline à chaque requête
2. **Profil déclaratif workspace** — `identity.yaml` + `behaviors/*.yaml` dans `.colaig/profile/`
3. **PreExecutionCard** — configuration complète du pipeline construite **avant** l'Analyser
4. **Lazy loading skills + ToolContext déclaratif** — filtrage pre-LLM sur skills et tools
5. **Document Map guidée** — métadonnées enrichies selon la spécialisation du workspace

Ces cinq concepts s'articulent autour d'une interface transversale critique : **Embedding × Index × Cache × Rerank × Storage × Agents**.

---

## I. Structure `.colaig/` redesignée

### Avant (actuelle)

```
{workspace}/.colaig/
├── config.yaml
├── prompts/
│   ├── analyser.md
│   ├── orchestrator.md
│   └── synthesiser.md
├── skills/
│   └── *.md
├── indexes/
│   ├── chunks.faiss
│   ├── chunks_meta.pkl
│   ├── documents.faiss         # DocumentIndex
│   └── registry.json           # DocumentIndex registry
└── conversations/
    └── {conv_id}.json
```

### Après (Phase 6)

```
{workspace}/.colaig/
│
├── config.yaml                               # Config workspace (existant, inchangé)
│
├── profile/                                  # NOUVEAU — Profil déclaratif
│   ├── identity.yaml                         # Domaine, ton, vocabulaire, taxonomie document map
│   └── behaviors/                            # Behaviors déclaratifs
│       ├── draft_mode.yaml
│       ├── expert_mode.yaml
│       └── summary_mode.yaml
│
├── prompts/                                  # Existant (inchangé)
│   ├── analyser.md
│   ├── orchestrator.md
│   └── synthesiser.md
│
├── skills/                                   # Existant (inchangé — fichiers Markdown)
│   └── *.md
│
├── indexes/                                  # Enrichi
│   ├── chunks.faiss                          # Index chunks (existant)
│   ├── chunks_meta.pkl                       # Métadonnées chunks (existant)
│   ├── documents.faiss                       # DocumentIndex résumés (existant)
│   ├── registry.json                         # DocumentIndex registry (existant)
│   ├── skills.faiss                          # NOUVEAU — Index lazy loading skills
│   ├── skills_meta.json                      # NOUVEAU — {position → skill_name}
│   ├── behaviors.faiss                       # NOUVEAU — Index activation behaviors
│   └── behaviors_meta.json                   # NOUVEAU — {position → behavior_name}
│
└── conversations/                            # Enrichi
    ├── {conv_id}.json                        # Historique messages (existant)
    └── {conv_id}_trame.json                  # NOUVEAU — Trame vivante de la conversation
```

**Principes :**
- `profile/` regroupe tout ce qui caractérise le workspace de manière déclarative (sans code). C'est le centre de personnalisation de l'instance.
- `skills.faiss` + `behaviors.faiss` sont des index légers (souvent < 50 vecteurs). Ils sont chargés en mémoire au démarrage et rechargés si leurs fichiers sources changent.
- `{conv_id}_trame.json` est **co-localisé** avec `{conv_id}.json` — ils forment une unité : l'historique des messages + l'état sémantique du workflow conversationnel.

---

## II. Trame Vivante

### Concept dual

La trame vivante a deux dimensions insécables :

**Dimension conversation** : persistée avec la conversation dans `{conv_id}_trame.json`. Survit entre les requêtes. C'est la mémoire de l'état macro du dialogue — dans quelle phase du workflow est-on, quels documents ont déjà été trouvés, quelles décisions ont été prises.

**Dimension pipeline** : lue au début de chaque requête et mise à jour après le pipeline. Chaque agent la consulte selon son rôle. Après la réponse, le `TrameManager` persiste les mises à jour dans le storage.

### Schéma de données (`models.py`)

```python
@dataclass
class WorkflowStep:
    """Étape du workflow conversationnel (niveau macro)."""
    step_id: str           # Identifiant sémantique : "initial_question", "docs_found", "draft_validated"
    description: str       # Description lisible pour l'utilisateur
    completed: bool = False
    turn_index: int = -1   # Index du tour où l'étape s'est réalisée (-1 = pas encore)
    summary: str = ""      # Résumé de ce qui s'est passé à cette étape


@dataclass
class ContextAnchor:
    """Élément établi dans la conversation — ne pas re-retriever."""
    anchor_type: str       # "document" | "decision" | "constraint" | "entity"
    ref: str               # Chemin fichier, ou texte de la décision/contrainte
    description: str = ""  # Description complémentaire
    turn_index: int = 0    # Turn où l'ancre a été établie


@dataclass
class ConversationTrame:
    """Trame vivante — état sémantique du workflow par conversation.

    Stocké dans {workspace}/.colaig/conversations/{conv_id}_trame.json
    Chargé en début de pipeline, mis à jour après pipeline, sauvegardé dans storage.
    """
    conv_id: str
    workspace_id: str

    # Phase macro de la conversation (distinct de PipelinePhase qui est interne au pipeline)
    # Représente l'état du dialogue avec l'utilisateur, pas l'état du pipeline agentique
    conversation_phase: str = "discovery"
    # Phases possibles : discovery | analysis | drafting | review | concluded

    # Behavior actif pour cette conversation (résolu en pré-pipeline, stable entre turns)
    active_behavior: str | None = None  # Nom du behavior YAML, ou None

    # Étapes du workflow conversationnel (méta-niveau, pas les étapes d'exécution)
    workflow_steps: list[WorkflowStep] = field(default_factory=list)

    # Anchors — éléments connus, pour éviter de re-retriever ou réexpliquer
    context_anchors: list[ContextAnchor] = field(default_factory=list)

    # Statistiques
    turn_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
```

### Cycle de vie dans le pipeline (par requête)

```
MESSAGE REÇU (handlers.py)
│
├─ [LOAD] TrameManager.load(conv_id, storage, workspace)
│     → Télécharge {conv_id}_trame.json depuis storage
│     → Si absent : initialise une ConversationTrame vide (discovery, no anchors)
│     → Expose : conversation_phase, active_behavior, context_anchors
│
├─ [PRE-PIPELINE] PreExecutionBuilder.build(message, trame, workspace, ...)
│     → Résout behavior actif via FAISS (ou reprend trame.active_behavior si déjà établi)
│     → Lazy-load skills via FAISS skills.faiss → top-k pertinents
│     → Filtre tools via ToolContext déclaratif + behavior overrides
│     → Construit PreExecutionCard
│
├─ [ANALYSER] Reçoit message + PreExecutionCard
│     → Prompt inclut : conversation_phase, anchors (pour contextualiser l'analyse)
│     → Sa sortie (Intent) peut inclure : suggested_next_phase, new_anchors à créer
│     → Ses directives vers l'Orchestrateur tiennent compte des anchors connus
│
├─ [ORCHESTRATEUR] Reçoit Intent + PreExecutionCard
│     → Utilise context_anchors : si un doc est déjà ancré, ne pas le re-retriever
│     → Tools filtrés dans PreExecutionCard = liste déjà optimisée (moins de bruit LLM)
│     → Découvre de nouveaux documents → signale comme anchors potentiels dans ExecutionPlan
│
├─ [SYNTHÉTISEUR] Reçoit ExecutionPlan + PreExecutionCard
│     → Adapte format/ton selon conversation_phase (discovery = pédagogique, drafting = formel)
│     → Sait qu'un anchor == élément déjà connu → ne pas réexpliquer ce qui est établi
│
├─ [UPDATE] TrameManager.update(trame, intent, plan, response)
│     → Met à jour conversation_phase si Intent a suggéré next_phase
│     → Ajoute context_anchors nouvelles (docs trouvés, décisions exprimées)
│     → Incrémente turn_count
│     → Upload {conv_id}_trame.json dans storage (co-localisé avec {conv_id}.json)
│
└─ RÉPONSE ENVOYÉE
```

### Rôle des agents dans la trame

| Agent | Lit la trame | Enrichit la trame (via signaux) |
|-------|-------------|--------------------------------|
| Analyser | `conversation_phase` (adapter l'analyse selon la phase) ; `context_anchors` (éviter de re-proposer le même chemin) | Suggère `next_phase` + nouvelles anchors dans `Intent` |
| Orchestrateur | `context_anchors` (skip re-retrieval docs déjà connus) ; `active_behavior` (tools prioritaires) | Identifie docs trouvés → signale comme anchors dans `ExecutionPlan` |
| Synthétiseur | `conversation_phase` (format adapté) ; `context_anchors` (ne pas réexpliquer l'établi) | Détecte décisions/contraintes exprimées → signale dans `GeneratedResponse.metadata` |
| **TrameManager** | — | **Seul composant qui écrit la trame** — après pipeline complet |

**Principe d'isolation** : les agents ne modifient **jamais** la trame directement. Ils signalent ce qu'ils ont découvert dans leurs outputs. Le `TrameManager` est le seul à écrire. Cela évite les états incohérents si un agent échoue en cours d'exécution.

---

## III. Profil Déclaratif Workspace

### identity.yaml — Spécialisation du workspace

```yaml
# {workspace}/.colaig/profile/identity.yaml
name: "Colaig RH — DINUM"
domain: "Ressources Humaines"
sub_domain: "Administration Publique Française"
language: "fr"
tone: "formel"

vocabulary:
  terms:
    - RIFSEEP
    - PPCR
    - CAP
    - mutation
    - avancement de grade
    - évaluation professionnelle
  abbreviations:
    CAP: "Commission Administrative Paritaire"
    RIFSEEP: "Régime Indemnitaire tenant compte des Fonctions, Sujétions, Expertise et Engagement"
    PPCR: "Parcours Professionnels, Carrières et Rémunérations"

document_map:
  # Taxonomie utilisée pour ai_category dans DocumentRecord
  categorization_taxonomy:
    - circulaire
    - note_de_service
    - rapport_annuel
    - guide_pratique
    - formulaire
    - fiche_thematique
    - compte_rendu

  # Entités à extraire spécifiquement pour ce domaine
  entity_types_to_extract:
    - references_reglementaires   # "Décret n°2014-513", "Article L911-2 CGFiP"
    - dates_importantes            # Dates d'application, échéances, convocations
    - grades_corps                 # "attaché principal", "directeur", "corps des..."
    - structures_mentionnees       # "DRH", "Bureau B2", "CSAM", "CAP"

  # Prompt injecté dans Albert lors de l'analyse documentaire
  metadata_enrichment_instructions: |
    Ce document appartient à un workspace RH d'une administration française.
    Le vocabulaire métier inclut : RIFSEEP, PPCR, CAP, mutation, avancement.
    Pour la catégorisation, utilise strictement la taxonomie fournie.
    Extrais particulièrement : références réglementaires exactes, dates d'application,
    grades et corps mentionnés, structures administratives impliquées.
```

### behaviors/*.yaml — Comportements déclaratifs

```yaml
# {workspace}/.colaig/profile/behaviors/draft_mode.yaml
name: draft_mode
description: "Mode rédaction active — l'utilisateur compose ou demande un document"

activation:
  # Ces phrases sont embeddings → stockées dans behaviors.faiss
  # Activation si similarité cosinus > min_similarity
  semantic_triggers:
    - "rédige un document"
    - "écris moi une note"
    - "crée un brouillon de"
    - "formule en style administratif"
    - "aide-moi à rédiger"
    - "je dois écrire"
  # Filtre dur sur IntentType (en plus de la similarité)
  intent_types:
    - "action"
    - "summary"
  min_similarity: 0.75

agents:
  synthesiser:
    temperature: 0.7        # Plus créatif pour rédiger
    format: "markdown"
    max_tokens: 2000
  analyser:
    tools_priority:
      - "rag_search"
      - "storage_fetch"

# Skills prioritaires (chargés en plus du lazy loading)
skills_boost:
  - "redaction"
  - "style_administratif"

tools:
  disable:
    - "calculator"
    - "date_tool"

output:
  tone: "formel"
  include_sources: false       # Pas de "[source.pdf]" dans un brouillon
  include_confidence: false
```

### Construction de l'index behaviors.faiss

```
Pour chaque behavior YAML dans .colaig/profile/behaviors/ :
  1. Lire semantic_triggers (liste de phrases)
  2. Embed chaque trigger via Albert /embeddings
  3. Ajouter tous les vecteurs à behaviors.faiss
  4. Dans behaviors_meta.json : position → {"behavior_name": "draft_mode", "trigger": "rédige un document"}
```

Un behavior avec 6 triggers produit 6 vecteurs dans `behaviors.faiss`, tous mappant vers le même `behavior_name`. La recherche FAISS `top-3` sur le message → le behavior avec le meilleur score moyen est activé si score > `min_similarity`.

---

## IV. PreExecutionCard — Unification pré-pipeline

### Principe

La `PreExecutionCard` est construite **avant** que l'Analyser ne tourne. Elle remplace la construction ad-hoc des configurations agents. C'est le **point de convergence** de :
- La trame vivante (phase actuelle, anchors)
- Le behavior actif (si applicable)
- Les skills sélectionnés (lazy loading)
- Les tools disponibles (filtrage déclaratif)
- Le profil workspace (pour les agents)

### Schéma (`models.py`)

```python
@dataclass
class PreExecutionCard:
    """Configuration du pipeline avant orchestration.

    Créée par PreExecutionBuilder dans agents/pre_execution.py.
    Passée à Analyser, Orchestrateur, Synthétiseur via AgentContext.
    """
    # État conversation (depuis trame vivante)
    workspace_id: str | None
    conversation_phase: str | None
    context_anchors: list[ContextAnchor]

    # Behavior résolu (via FAISS + trame)
    active_behavior_name: str | None
    active_behavior_config: BehaviorConfig | None
    active_behavior_score: float = 0.0

    # Tools filtrés (ToolContext déclaratif + behavior.tools.disable)
    available_tools: list[ToolDefinition]

    # Skills sélectionnés (lazy loading via skills.faiss)
    selected_skills: list[dict]          # [{"name": ..., "content": ...}]
    selected_skills_scores: list[float]

    # Overrides agents (depuis behavior.agents.*)
    agent_overrides: dict[str, dict]     # {"synthesiser": {"temperature": 0.7}}

    # Profil workspace (depuis identity.yaml)
    workspace_profile: WorkspaceProfile | None
```

### ToolContext déclaratif — Ajouts sur `ToolDefinition`

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    category: str = ""

    # NOUVEAU — Conditions de disponibilité (évaluées sans LLM, en pré-pipeline)
    requires_workspace: bool = False
    allowed_intent_types: list[str] = field(default_factory=list)   # [] = tous les intents
    excluded_phases: list[str] = field(default_factory=list)        # ex: ["concluded"]
    requires_capabilities: list[str] = field(default_factory=list)  # ex: ["storage_write"]
```

Application sur les tools existants :

| Tool | `requires_workspace` | `allowed_intent_types` | `excluded_phases` |
|------|---------------------|----------------------|-------------------|
| `search_documents` | True | `["question","search","summary","action"]` | `["concluded"]` |
| `fetch_document` | True | `[]` (tous) | `[]` |
| `list_documents` | True | `["search","action"]` | `["concluded"]` |
| `summarize_text` | False | `["summary","question"]` | `[]` |
| `search_document_index` | True | `["question","search"]` | `[]` |

La logique de filtrage est purement booléenne (pas de LLM) :
```python
def filter_tools(
    tools: list[ToolDefinition],
    workspace: WorkspaceConfig | None,
    trame: ConversationTrame | None,
    behavior: BehaviorConfig | None,
    intent_type: str | None = None,
) -> list[ToolDefinition]:
    result = []
    for tool in tools:
        # 1. Workspace requis
        if tool.requires_workspace and not workspace:
            continue
        # 2. Intent type (si intent déjà connu au moment du filtrage)
        if intent_type and tool.allowed_intent_types:
            if intent_type not in tool.allowed_intent_types:
                continue
        # 3. Phase exclue
        if trame and trame.conversation_phase in tool.excluded_phases:
            continue
        # 4. Override behavior (disable list)
        if behavior and tool.name in behavior.tools.disable:
            continue
        result.append(tool)
    return result
```

### Lazy loading skills via skills.faiss

**Construction** (lors de la création/modification d'un fichier `skills/*.md`) :

```python
# colaig/rag/skill_indexer.py
async def build_skills_index(storage, albert, workspace_path):
    skills_dir = f"{workspace_path}/.colaig/skills/"
    files = await storage.list_files(skills_dir)
    embeddings, meta = [], []
    for f in files:
        if not f.name.endswith(".md"):
            continue
        content = await storage.download(f.path)
        text = content.decode()
        # Description = 200 premiers chars ou section "## Description" si présente
        description = _extract_description(text)
        emb = await albert.embed(description)
        embeddings.append(emb)
        meta.append({"name": f.name.removesuffix(".md"), "path": f.path})

    store = FaissStore(dimension=len(embeddings[0]))
    # Utiliser DocumentChunk minimal comme métadonnée ou dict JSON
    store.add_raw(np.array(embeddings))
    # Sauvegarder
    faiss_bytes = store.to_bytes()
    await storage.upload(f"{workspace_path}/.colaig/indexes/skills.faiss", faiss_bytes)
    await storage.upload(
        f"{workspace_path}/.colaig/indexes/skills_meta.json",
        json.dumps(meta).encode()
    )
```

**Usage** (en pré-pipeline, dans `PreExecutionBuilder`) :

```python
# 1. Réutilise le même embedding que celui calculé pour le message
query_emb = await albert.embed(message.body)   # 1 appel Albert — réutilisé pour tout

# 2. Recherche FAISS sur l'index skills (index léger, pas de reranking)
if skills_faiss:
    results = skills_faiss.search_raw(query_emb, k=3)
    selected_names = [skills_meta[r.chunk_index]["name"] for r in results]
    selected_scores = [r.score for r in results]
else:
    # Fallback : charger tous (comportement actuel)
    selected_names = [f.name.removesuffix(".md") for f in await storage.list_files(skills_dir)]
    selected_scores = [1.0] * len(selected_names)

# 3. Skills_boost depuis behavior (priorité forcée)
if behavior:
    for boosted_name in behavior.skills_boost:
        if boosted_name not in selected_names:
            selected_names.insert(0, boosted_name)
            selected_scores.insert(0, 1.0)

# 4. Charger les contenus
selected_skills = []
for name in selected_names[:5]:   # Cap à 5 skills max
    try:
        content = await storage.download(f"{workspace_path}/.colaig/skills/{name}.md")
        selected_skills.append({"name": name, "content": content.decode()})
    except Exception:
        pass
```

**Gain** : Un workspace avec 15 skills (15 × ~500 tokens = 7500 tokens injectés en bloc) → après lazy loading : 3 skills = ~1500 tokens. Économie de ~6000 tokens/requête sur le prompt du Synthétiseur.

---

## V. Interface Embedding × Index × Cache × Rerank × Storage × Agents

C'est l'interface transversale critique que tout le pipeline traverse. Elle doit être comprise comme une **triple interface** entre trois familles de systèmes :

- **Producers** : modules qui calculent ou stockent (Albert, FaissStore, Storage)
- **Intermediates** : modules qui transforment et routent (Indexer, Retriever, PreExecutionBuilder, TrameManager)
- **Consumers** : modules qui décident (Analyser, Orchestrateur, Synthétiseur)

### Catalogue des index FAISS d'un workspace

| Index | Fichier | Vecteur représente | Dim | Taille typique | Mise à jour | Consommé par |
|-------|---------|-------------------|-----|----------------|-------------|--------------|
| Chunks | `chunks.faiss` | Chunk de document (800 tokens) | 1024 | 1k–50k vecteurs | `rag/indexer.py` (scan périodique) | `rag/retriever.py` — RAG search |
| Documents | `documents.faiss` | Résumé AI d'un document entier | 1024 | 10–5k vecteurs | `rag/document_index.py` | Tool `search_document_index` |
| Skills | `skills.faiss` | Description d'un skill | 1024 | 5–50 vecteurs | `rag/skill_indexer.py` (on change) | `PreExecutionBuilder` |
| Behaviors | `behaviors.faiss` | Trigger phrase d'un behavior | 1024 | 10–100 vecteurs | `rag/behavior_indexer.py` (on change) | `PreExecutionBuilder` |

**Propriété clé** : tous partagent la même dimension (1024 pour bge-m3). `FaissStore` peut être réutilisé pour tous. Seule la nature des métadonnées change : `DocumentChunk` pour chunks/documents, dict JSON simple pour skills/behaviors.

### Stratégie de cache

| Objet | Layer cache | Durée | Invalidation |
|-------|------------|-------|-------------|
| Index FAISS chunks (en mémoire) | Process memory | Durée process | Re-indexation → `faiss_store.reload()` |
| Index FAISS documents | Process memory | Durée process | Idem |
| Index skills.faiss | Process memory | Durée process | Rechargé si etag skills/ change |
| Index behaviors.faiss | Process memory | Durée process | Rechargé si etag profile/behaviors/ change |
| Registry DocumentIndex | Process memory (dict) | TTL = 300s | Scan périodique |
| WorkspaceConfig | `storage/cache.py` TTL | 60s | Expiration TTL |
| WorkspaceProfile (identity.yaml) | Process memory | Durée process | Restart ou reload manuel |
| BehaviorConfig (YAML parsés) | Process memory dict | Durée process | Restart |
| ConversationTrame | Process memory (dict conv_id → trame) | TTL 30 min | Mise à jour ou expiration |
| **Embeddings requêtes** | **ABSENT → À AJOUTER** | LRU 5 min | Hash texte (clé = sha256(text)[:16]) |

**Gap critique identifié : cache embedding requêtes**

Chaque requête fait 1 appel Albert `/embeddings` pour le message utilisateur. Ce vecteur est utilisé pour `behaviors.faiss`, `skills.faiss`, et le RAG — puis oublié. Solution :

```python
class EmbeddingCache:
    """LRU cache pour les embeddings calculés. TTL court (requêtes rarement identiques)."""
    def __init__(self, max_size: int = 200, ttl_seconds: int = 300):
        self._cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def get(self, text: str) -> list[float] | None:
        key = self._key(text)
        if key in self._cache:
            emb, ts = self._cache[key]
            if time.time() - ts < self._ttl:
                self._cache.move_to_end(key)  # LRU
                return emb
        return None

    def put(self, text: str, embedding: list[float]) -> None:
        key = self._key(text)
        self._cache[key] = (embedding, time.time())
        self._cache.move_to_end(key)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
```

Gain typique : faible pour les messages libres (rarement identiques), moyen pour les `query_reformulated` (plus stables dans un domaine métier précis).

### Reranking — quand et comment

| Index | Reranking | Justification |
|-------|-----------|--------------|
| `chunks.faiss` | **Oui** — Albert reranker API | Index grand, chunks courts parfois ambigus, qualité critique pour la réponse finale |
| `documents.faiss` | Optionnel | Index plus petit, résumés plus riches → similarité cosinus souvent suffisante |
| `skills.faiss` | **Non** | Index très petit (< 50 vecteurs), top-3 cosinus suffit |
| `behaviors.faiss` | **Non** | Index minuscule + seuil de similarité = filtre suffisant |

Le reranker Albert est réservé au chemin critique (chunks → réponse finale). Le surcoût latence (~200ms) est justifié par le gain de qualité sur les résultats RAG. Il ne s'applique pas aux index de service (skills, behaviors).

### Flux de données complet — une requête annotée

```
MESSAGE REÇU : "Quelle est la procédure de mutation pour les attachés ?"
│
│ ◈ EMBED (Albert /embeddings)  [1 appel]
│   → query_vector [1024 floats]
│   → Mise en cache EmbeddingCache (TTL 5 min)
│
├─ [PRE-PIPELINE]
│   ├─ behaviors.faiss.search(query_vector, k=3)
│   │   → Résultat : score 0.71 < seuil 0.75 → aucun behavior activé
│   │
│   ├─ skills.faiss.search(query_vector, k=2)
│   │   → ["procedures_admin", "vocabulaire_rh"] (score 0.82, 0.78)
│   │   → Download 2 fichiers .md depuis storage [2 appels storage]
│   │
│   ├─ filter_tools(workspace=RH, phase="discovery", behavior=None, intent=None)
│   │   → 5 tools disponibles : search_documents, fetch_document,
│   │     list_documents, summarize_text, search_document_index
│   │
│   └─ PreExecutionCard construite
│
├─ [ANALYSER]  ◈ CHAT (Albert /chat)  [1 appel]
│   Input : message + PreExecutionCard (phase, anchors, skills injectés)
│   Output : Intent(
│     intent_type=QUESTION,
│     query_reformulated="procédure administrative mutation attachés administration publique",
│     needs_rag=True,
│     orchestrator_directives=Directives(resources=["procedures/"], strategy="precise")
│   )
│
│ ◈ EMBED (Albert /embeddings)  [1 appel — query reformulée]
│   → query_ref_vector [1024 floats]
│   → Vérifier EmbeddingCache d'abord
│
├─ [ORCHESTRATEUR — boucle agentique]
│   │
│   ├─ Iter 1 : tool_call "search_documents"
│   │   ├─ chunks.faiss.search(query_ref_vector, k=20)  [mémoire — rapide]
│   │   └─ albert_reranker(chunks[:20]) → top 5  ◈ RERANK (Albert /rerank)  [1 appel]
│   │
│   ├─ CHAT  ◈  [1 appel]  → Iter 2 : tool_call "search_document_index"
│   │   └─ documents.faiss.search(query_ref_vector, k=3)  [mémoire — rapide]
│   │
│   └─ CHAT  ◈  [1 appel]  → finish_reason="stop" → ExecutionPlan complet
│       ExecutionPlan contient SearchResult[5] + DocumentIndexResult[3]
│
└─ [SYNTHÉTISEUR]  ◈ CHAT (Albert /chat)  [1 appel]
    Input : ExecutionPlan + PreExecutionCard
    Skills injectés : procedures_admin + vocabulaire_rh (depuis PreExecutionCard)
    Tone : formel (depuis workspace.tone — aucun behavior actif)
    Output : GeneratedResponse avec sources citées

POST-PIPELINE :
→ TrameManager.update(trame, intent, plan, response)
  → Nouvel anchor : document("procedures/mutation_fonctionnaires.pdf", turn=1)
  → conversation_phase reste "discovery" (Intent n'a pas suggéré next_phase)
  → Sauvegarde {conv_id}_trame.json dans storage  [1 appel storage]
```

**Compte d'appels Albert pour cette requête** :
| Étape | Appel | Évitable |
|-------|-------|----------|
| Embed message | 1 × `/embeddings` | Oui (cache EmbeddingCache) |
| Embed query reformulée | 1 × `/embeddings` | Oui (cache, si même query) |
| Analyser | 1 × `/chat` | Non |
| Orchestrateur iter 1 | 1 × `/chat` | Non |
| Rerank chunks | 1 × `/rerank` | Non (si reranking activé) |
| Orchestrateur iter 2 | 1 × `/chat` | Non |
| Orchestrateur iter 3 (stop) | 1 × `/chat` | Non |
| Synthétiseur | 1 × `/chat` | Non |
| **Total** | **8 appels** | 0-2 économisés avec cache |

**Avec trame vivante (turn 2 de la même conversation)** : si `mutation_fonctionnaires.pdf` est déjà ancré → l'Analyser peut dire "document déjà connu, ne pas re-retriever" → Orchestrateur saute `search_documents` → **7 appels**. L'économie s'accumule sur les conversations longues (phase `drafting`, phase `review`).

---

## VI. Document Map influencée par la spécialisation workspace

### Principe

Lors de l'analyse IA d'un document dans `rag/document_index.py`, le prompt envoyé à Albert pour extraire `ai_category`, `ai_entities`, `ai_keywords` est **enrichi par le profil workspace**. Le même document indexé dans deux workspaces différents produit des `DocumentRecord.ai_*` différents.

### Chaîne complète

```
identity.yaml (vocabulaire + taxonomie + instructions)
    ↓ chargé par WorkspaceProfileLoader
WorkspaceProfile (dataclass en mémoire)
    ↓ injecté dans
document_index.py → prompt enrichi pour Albert
    ↓ produit
DocumentRecord.ai_category (taxonomie workspace)
DocumentRecord.ai_entities (entités du domaine)
DocumentRecord.ai_keywords (vocabulaire métier)
    ↓ persistés dans
registry.json + documents.faiss (avec métadonnées enrichies)
    ↓ utilisés par
Retriever → search hybride : similarité cosinus + matching entités métier
Synthétiseur → réponses avec vocabulaire du domaine
PreExecutionCard → WorkspaceProfile disponible pour contextualiser les agents
```

### Comparaison sans/avec profil (même document)

**Sans profil (générique)** :
```json
{
  "ai_category": "document_administratif",
  "ai_entities": ["DINUM", "2024"],
  "ai_keywords": ["prime", "fonctionnaires", "décret"]
}
```

**Avec profil RH** :
```json
{
  "ai_category": "circulaire",
  "ai_entities": [
    "Décret n°2014-513 du 20 mai 2014",
    "CAP du 15/03/2024",
    "corps des attachés d'administration centrale"
  ],
  "ai_keywords": ["RIFSEEP", "PPCR", "avancement de grade", "CAP"]
}
```

La taxonomie du workspace transforme `"document_administratif"` générique en `"circulaire"` précis. Le vocabulaire oriente l'extraction de mots-clés vers les termes métier. Les `entity_types_to_extract` orientent l'extraction vers les référentiels réglementaires du domaine.

### Implémentation dans document_index.py

```python
async def _analyze_document(
    self,
    content: str,
    workspace_profile: WorkspaceProfile | None,
) -> dict:
    """Analyse IA d'un document — enrichie par le profil workspace si disponible."""

    base_instructions = (
        "Analyse ce document. Retourne JSON avec :\n"
        "- summary: résumé 2-3 phrases\n"
        "- category: catégorie du document\n"
        "- entities: liste d'entités nommées clés\n"
        "- keywords: liste de mots-clés métier\n"
        "- language: langue détectée (fr/en)\n"
        "- doc_type: type de document"
    )

    if workspace_profile and workspace_profile.document_map:
        dm = workspace_profile.document_map
        vocab_terms = ", ".join(workspace_profile.vocabulary.terms[:20])
        taxonomy = ", ".join(dm.categorization_taxonomy)
        entities_types = ", ".join(dm.entity_types_to_extract)
        enrichment = (
            f"Contexte workspace : {workspace_profile.domain}"
            f" — {workspace_profile.sub_domain}\n"
            f"Taxonomie catégories : [{taxonomy}]\n"
            f"Types d'entités à extraire : [{entities_types}]\n"
            f"Vocabulaire métier du domaine : {vocab_terms}\n"
        )
        if dm.metadata_enrichment_instructions:
            enrichment += f"\nInstructions supplémentaires : {dm.metadata_enrichment_instructions}\n"
        instructions = enrichment + "\n" + base_instructions
    else:
        instructions = base_instructions

    response = await self._albert.chat([
        {"role": "system", "content": instructions},
        {"role": "user", "content": content[:4000]},
    ])
    return _parse_analysis_json(response)
```

---

## VII. Vector-First — Carte des substitutions

### Où passer de textuel/structurel → vectoriel

| Opération | État actuel | Phase 6 | Mécanisme |
|-----------|------------|---------|-----------|
| Sélection skills | Charge ALL `*.md` en bloc | `skills.faiss` → top-3 | Embed query → search cosinus |
| Activation behavior | Hardcodé (ton workspace statique) | `behaviors.faiss` → score > seuil | Embed message → search cosinus |
| Recherche chunks (RAG) | FAISS ✓ | FAISS ✓ — inchangé | — |
| Recherche résumés docs | FAISS ✓ | FAISS ✓ — inchangé | — |
| Résolution workspace | String matching `room_id` | String matching ✓ | Pas de gain sémantique ici |
| Détection phase conversation | Absent | Intent + heuristiques (Phase 6) ; FAISS optionnel (Phase 7) | — |
| Enrichissement doc map | Prompt générique | Prompt guidé par vocabulaire profil | Reste textuel mais guidé |

### Règle de décision vector-first

**Utiliser FAISS quand** : la question est *"quel élément ressemble le plus à X ?"* (ambiguïté sémantique, lookup flou, matching dans un espace continu).

**Rester textuel/booléen quand** : la question est *"cet élément satisfait-il condition Y ?"* (logique stricte, identifiants, conditions booléennes).

| Opération | Règle | Décision |
|-----------|-------|---------|
| "Quel skill est pertinent ?" | Ressemblance sémantique | → FAISS |
| "Quel behavior correspond ?" | Ressemblance sémantique | → FAISS |
| "Ce tool est-il disponible en mode chatbot ?" | Condition booléenne | → `requires_workspace: False` |
| "Ce tool est-il exclu en phase concluded ?" | Condition booléenne | → liste `excluded_phases` |
| "Ce conv_id appartient à quel workspace ?" | Identifiant strict | → dict mapping |
| "Quels documents sont pertinents ?" | Similarité sémantique | → FAISS chunks |

---

## VIII. Modules, dataclasses et protocols — architecture finale

### Nouveaux modules

| Module | Fichier | Responsabilité |
|--------|---------|---------------|
| `TrameManager` | `colaig/agents/trame_manager.py` | Load/update/save `ConversationTrame` via StorageProtocol |
| `ProfileService` | `colaig/agents/profile_service.py` | Charge `identity.yaml` + `behaviors/*.yaml`, détecte behavior actif |
| `PreExecutionBuilder` | `colaig/agents/pre_execution.py` | Construit `PreExecutionCard` + exécute plan de retrieval multi-source |
| `ProgressReporter` | `colaig/messaging/progress.py` | Envoie messages intermédiaires formatés pour le canal |
| `TaskExecutor` | `colaig/tasks/executor.py` | Lance pipelines async non-bloquants, queue par conversation |
| `SkillIndexer` | `colaig/rag/skill_indexer.py` | Indexe `skills/*.md` → `skills.faiss + skills_meta.json` |
| `BehaviorIndexer` | `colaig/rag/behavior_indexer.py` | Indexe triggers behaviors → `behaviors.faiss + behaviors_meta.json` |

### Nouveaux protocols (`protocols.py`)

```python
class TrameManagerProtocol(Protocol):
    async def load(self, conv_id: str, storage, ws_path: str) -> ConversationTrame: ...
    async def update(self, trame, intent, plan, response) -> ConversationTrame: ...
    async def save(self, trame: ConversationTrame, storage, ws_path: str) -> None: ...

class PreExecutionBuilderProtocol(Protocol):
    async def build(self, msg, trame, workspace, profile, embeddings) -> PreExecutionCard: ...
    async def execute_retrieval(self, search_dirs, retriever, doc_index, memory) -> dict: ...

class TaskExecutorProtocol(Protocol):
    async def submit(self, coro, task_id: str, conv_id: str,
                     on_complete, on_error=None) -> TaskHandle: ...
    async def cancel(self, task_id: str) -> None: ...
```

### Nouvelles dataclasses dans `models.py`

```python
# --- Trame vivante (inchangées depuis conception) ---
@dataclass
class WorkflowStep: ...    # step_id, description, completed, turn_index, summary

@dataclass
class ContextAnchor: ...   # anchor_type, ref, description, turn_index

@dataclass
class ConversationTrame: ... # conv_id, workspace_id, conversation_phase, active_behavior,
                              # workflow_steps, context_anchors, turn_count

# --- Profil workspace (inchangés depuis conception) ---
@dataclass
class VocabularyConfig: ...
@dataclass
class DocumentMapConfig: ...
@dataclass
class WorkspaceProfile: ...
@dataclass
class BehaviorActivationConfig: ...
@dataclass
class BehaviorAgentConfig: ...
@dataclass
class BehaviorToolsConfig: ...
@dataclass
class BehaviorConfig: ...

# --- NOUVEAUX Phase 6 finale ---

@dataclass
class SearchDirectives:
    """Plan de récupération multi-source — sortie Analyser.
    Remplace AgentDirectives(target_agent="orchestrator")."""
    # Requêtes sémantiques par source (liste vide = skip)
    chunk_queries:    list[str] = field(default_factory=list)  # → chunks.faiss
    document_queries: list[str] = field(default_factory=list)  # → documents.faiss
    skill_queries:    list[str] = field(default_factory=list)  # → skills.faiss
    history_queries:  list[str] = field(default_factory=list)  # → ConversationMemory
    # Filtres structurés auto-détectés depuis l'intent
    context_filters: dict = field(default_factory=dict)
    # {"ai_category": "circulaire", "date_range": "2023+", "exclude_paths": [...]}
    # Objectif et critères de complétude pour Agent 2
    objective: str = ""
    completeness_criteria: list[str] = field(default_factory=list)


@dataclass
class CompletionSignal:
    """Verdict Agent 3 (mode juge) — pilote la sortie de boucle Agent 2.
    Retourné par Synthesiser.assess(), appelé comme tool assess_completion."""
    sufficient: bool
    confidence: float = 0.0
    missing_elements: list[str] = field(default_factory=list)
    suggested_directions: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ChannelFormat:
    """Contraintes de rendu du canal cible — résolu avant le pipeline."""
    supports_html: bool = False
    supports_markdown: bool = True
    max_length: int = 0              # 0 = illimité
    supports_streaming: bool = False # True si le canal supporte les mises à jour progressives
    reply_style: str = "conversational"  # conversational | structured | json


@dataclass
class TaskHandle:
    """Handle d'une tâche async soumise au TaskExecutor."""
    task_id: str
    conversation_id: str
    status: str = "pending"          # pending | running | done | error | cancelled
    created_at: datetime = field(default_factory=datetime.utcnow)


# --- PreExecutionCard — enrichie ---
@dataclass
class PreExecutionCard:
    # État conversation (depuis trame)
    workspace_id: str | None
    conversation_phase: str | None
    context_anchors: list[ContextAnchor]
    # Behavior actif
    active_behavior_name: str | None
    active_behavior_config: BehaviorConfig | None
    active_behavior_score: float = 0.0
    # Skills lazy-loaded
    selected_skills: list[dict] = field(default_factory=list)
    selected_skills_scores: list[float] = field(default_factory=list)
    # Tools filtrés (déclaratif + behavior.tools.disable)
    available_tools: list[ToolDefinition] = field(default_factory=list)
    # Overrides agents depuis behavior.agents.*
    agent_overrides: dict[str, dict] = field(default_factory=dict)
    # Profil workspace
    workspace_profile: WorkspaceProfile | None = None
    # Contexte fixe transmis directement à Agent 1 (config + identity + behavior)
    fixed_context: dict = field(default_factory=dict)
    # Embedding du message — calculé 1 fois, réutilisé behaviors + skills + RAG
    message_embedding: list[float] | None = None
```

### Modifications sur existants

**`models.py` — `Intent`** :
```python
# search_directives remplace orchestrator_directives
search_directives: Optional[SearchDirectives] = None     # NOUVEAU
synthesiser_directives: Optional[AgentDirectives] = None # inchangé
# Fast path — court-circuit pipeline complet
is_direct: bool = False                                  # NOUVEAU
direct_response: Optional[str] = None                   # NOUVEAU
```

**`models.py` — `AgentContext`** :
```python
pre_exec: Optional[PreExecutionCard] = None              # NOUVEAU
retrieval_results: dict = field(default_factory=dict)    # NOUVEAU
# {"chunks": [SearchResult...], "docs": [...], "skills": [...], "history": [...]}
```

**`models.py` — `ToolDefinition`** : ajouter 4 champs de filtrage déclaratif
(`requires_workspace`, `allowed_intent_types`, `excluded_phases`, `requires_capabilities`).

**`agents/context_builder.py`** : `build_agent_context()` accepte `pre_exec` optionnel ;
si présent, utilise `pre_exec.selected_skills` au lieu de `_load_workspace_skills()`.

---

## IX. Pipeline Phase 6 — Architecture finale

### Flux complet annoté

```
MESSAGE REÇU
│
├── [handlers.py — retour immédiat, non-bloquant]
│   channel_format = resolve_channel(msg.platform, msg.conversation_type)
│   reporter       = ProgressReporter(messaging, conv_id, channel_format)
│
│   task = await task_executor.submit(
│       coro      = _run_pipeline(msg, context, reporter),
│       task_id   = generate_id(),
│       conv_id   = msg.conversation_id,
│       on_complete = lambda r: messaging.send(conv_id, r.text),
│       on_error    = lambda e: messaging.send(conv_id, ERROR_MESSAGE),
│   )
│   ← retour immédiat — handler libéré, prêt pour le message suivant
│
└── [_run_pipeline() — tâche background, queue par conversation]
    │
    ├── [PreExecutionBuilder.build()]          ← avant tout LLM
    │   embedding  = embed(msg.body)           ← 1 appel Albert, réutilisé partout
    │   trame      = TrameManager.load()       ← phase + anchors
    │   behavior   = behaviors.faiss.search()  ← actif + score
    │   skills     = skills.faiss.search()     ← top-k lazy
    │   tools      = filter_tools(behavior, trame, intent=None)
    │   fixed_ctx  = {config, identity, behavior_config}
    │   → PreExecutionCard
    │
    ├── AGENT 1 — Mistral  (1 appel LLM léger)
    │   → reporter.report("Analyse de votre demande...")
    │   Input  : message + PreExecutionCard.fixed_context
    │   Output : Intent + SearchDirectives
    │              chunk_queries, document_queries, skill_queries, history_queries
    │              context_filters (auto-détectés : dates, catégories, anchors exclus)
    │              objective, completeness_criteria
    │
    │   Si intent.is_direct → messaging.send(direct_response) → FIN (fast path)
    │
    ├── [PreExecutionBuilder.execute_retrieval(search_dirs)]
    │   Fixe    : PreExecutionCard.fixed_context  (déjà dans AgentContext)
    │   Séman.  : chunks.faiss     ← chunk_queries + context_filters
    │             documents.faiss  ← document_queries + context_filters
    │             skills.faiss     ← skill_queries (lazy top-k)
    │             ConversationMemory ← history_queries
    │   → AgentContext.retrieval_results = {chunks, docs, skills, history}
    │
    ├── AGENT 2 — gpt-oss  (boucle LLM + tools)
    │   → reporter.report("Recherche en cours...")
    │   Input         : AgentContext enrichi (retrieval_results) + SearchDirectives
    │   active_filters = search_dirs.context_filters.copy()
    │   ┌──────────────────────────────────────────────────────────────┐
    │   │  BOUCLE (max N itérations — COLAIG_ORCHESTRATOR_MAX_ITER)   │
    │   │   reporter.report(f"Consultation de {doc_name}...")         │
    │   │   execute_tool(tool_call, filters=active_filters)           │
    │   │   accumulate → ExecutionPlan.search_results + tool_results  │
    │   │   active_filters = _refine_filters(active_filters, result)  │
    │   │   call assess_completion (→ Agent 3 mode juge, 1 appel léger)│
    │   │     sufficient=True  + confidence > seuil → BREAK           │
    │   │     sufficient=False → integrate directions → continue       │
    │   └──────────────────────────────────────────────────────────────┘
    │   → ExecutionPlan
    │
    └── AGENT 3 — medium  (1 appel LLM)
        → reporter.report("Rédaction de la réponse...")
        Si channel_format.supports_streaming:
            synthesise_stream() → tokens → messaging.update_message() progressive
        Sinon:
            synthesise() → GeneratedResponse complet → on_complete(response)
        → TrameManager.update() + save()
```

### Non-blocage — TaskExecutor avec queue par conversation

Messages d'une même conversation → traitement **séquentiel** (cohérence trame).
Messages de conversations différentes → traitement **parallèle** (concurrence cross-conv).

```python
class TaskExecutor:
    def __init__(self, max_concurrent: int = 20) -> None:
        self._conv_queues: dict[str, asyncio.Queue] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def submit(self, coro, task_id, conv_id, on_complete, on_error=None) -> TaskHandle:
        handle = TaskHandle(task_id=task_id, conversation_id=conv_id)
        if conv_id not in self._conv_queues:
            self._conv_queues[conv_id] = asyncio.Queue()
            asyncio.create_task(self._process_queue(conv_id))
        await self._conv_queues[conv_id].put((coro, handle, on_complete, on_error))
        return handle  # ← retour immédiat

    async def _process_queue(self, conv_id: str) -> None:
        while True:
            coro, handle, on_complete, on_error = await self._conv_queues[conv_id].get()
            async with self._semaphore:
                try:
                    result = await coro
                    if on_complete: await on_complete(result)
                except Exception as exc:
                    if on_error: await on_error(exc)
```

### Streaming

**Agent 3 — deux méthodes** :

```python
class Synthesiser:
    async def synthesise(self, plan, context, channel_format) -> GeneratedResponse:
        # 1 appel albert.chat() — retour complet

    async def synthesise_stream(self, plan, context) -> AsyncIterator[str]:
        # albert.chat_stream() — tokens au fil de l'eau
        async for token in self._albert.chat_stream(messages, ...):
            yield token

    async def assess(self, accumulated, objective, criteria, iteration) -> CompletionSignal:
        # 1 appel LLM léger (ALBERT_MODEL_LIGHT), prompt court, sortie JSON
        # Appelé depuis assess_completion tool dans la boucle Agent 2
```

**Dispatching par canal** (`ChannelFormat.supports_streaming`) :

| Backend | Streaming | Mécanisme |
|---------|-----------|-----------|
| MCP | Oui — natif SSE | FastMCP yield depuis `synthesise_stream()` |
| Matrix/Tchap | Oui — edit progressive | Send msg vide → edit toutes les N tokens |
| Telegram | Non — buffer | `synthesise()` complet → send |
| WebChat | Oui — WebSocket | `synthesise_stream()` → WS push |

**`resolve_channel()`** (fonction pure dans `messaging/progress.py`) :

```python
def resolve_channel(platform: str, conv_type: ConversationType) -> ChannelFormat:
    match platform:
        case "matrix": return ChannelFormat(
            supports_html=True, supports_markdown=True,
            supports_streaming=True, reply_style="conversational")
        case "telegram": return ChannelFormat(
            supports_html=False, supports_markdown=True,
            max_length=4096, supports_streaming=False)
        case "mcp": return ChannelFormat(
            supports_html=False, supports_markdown=False,
            supports_streaming=True, reply_style="json")
        case _: return ChannelFormat()  # défaut conservateur
```

### Sélection des modèles

```python
# config.py
ALBERT_MODEL_LIGHT  = os.getenv("ALBERT_MODEL_LIGHT",  "mistralai/Ministral-3-8B-Instruct-2512")
ALBERT_MODEL_CHAT   = os.getenv("ALBERT_MODEL_CHAT",   "openai/gpt-oss-120b")
ALBERT_MODEL_MEDIUM = os.getenv("ALBERT_MODEL_MEDIUM", "mistralai/Mistral-Small-3.2-24B-Instruct-2506")

# Règle de sélection (main.py)
def select_model(role: str, has_tools: bool) -> str:
    if has_tools:
        return config.ALBERT_MODEL_CHAT  # tools → lourd pour tous
    return {
        "analyser":    config.ALBERT_MODEL_LIGHT,   # formule les requêtes
        "orchestrator": config.ALBERT_MODEL_CHAT,   # raisonne + boucle
        "synthesiser": config.ALBERT_MODEL_MEDIUM,  # rédige
        "assess":      config.ALBERT_MODEL_LIGHT,   # juge de complétude
    }[role]
```

### assess_completion — dialogue Agent 2 ↔ Agent 3

`assess_completion` est enregistré comme tool dans le `ToolRegistry` de l'Orchestrator.
Agent 2 l'appelle explicitement quand il pense avoir assez — pas d'appel automatique.

```python
# context_builder.py — ajout dans build_tool_registry()
ASSESS_COMPLETION_DEFINITION = ToolDefinition(
    name="assess_completion",
    description=(
        "Évalue si le contenu accumulé est suffisant pour répondre à l'objectif. "
        "Appelle cet outil quand tu penses avoir collecté assez d'information."
    ),
    parameters=[
        ToolParameter(name="summary",  type="string",
                      description="Résumé de ce qui a été trouvé"),
        ToolParameter(name="missing",  type="string",
                      description="Ce qui semble encore manquer, ou '' si rien"),
    ],
    requires_workspace=False,
    allowed_intent_types=[],   # disponible pour tous les intents
    excluded_phases=[],
)
# Handler : appelle synthesiser.assess() → retourne CompletionSignal.__dict__
```

---

## X. Contraintes et points de vigilance

### Zero Database

Tous les nouveaux artefacts sont des fichiers dans le storage :
- `skills.faiss`, `behaviors.faiss` → `{ws}/.colaig/indexes/`
- `{conv_id}_trame.json` → `{ws}/.colaig/conversations/`
- `identity.yaml`, `behaviors/*.yaml` → `{ws}/.colaig/profile/`

Si `storage_readonly=True` : trame en mémoire uniquement (non persistée).
Index skills/behaviors non reconstruits (lecture seule).

### Cohérence trame

`TrameManager` est le **seul** composant qui écrit la trame. Les agents signalent dans leurs outputs, ils n'écrivent pas. Si un agent échoue en cours de boucle, la trame reste dans son état précédent (toujours cohérent).

### Compatibilité backward

- `profile/identity.yaml` absent → `WorkspaceProfile=None` → comportement Phase 5 inchangé
- `skills.faiss` absent → fallback chargement en bloc (comportement Phase 5)
- `{conv_id}_trame.json` absent → `ConversationTrame` vide initialisée (discovery, no anchors)
- `task_executor=None` dans `handlers.py` → mode synchrone (tous les 671 tests Phase 5 passent)
- `reporter=None` dans les agents → pas de messages intermédiaires
- `synthesiser=None` dans `orchestrator` → pas d'`assess_completion` (mode Phase 5)

### Cohérence index skills/behaviors

Deux stratégies de reconstruction (non exclusives) :
1. **À la demande** : `indexer.py` détecte changements dans `skills/` et `profile/behaviors/`
2. **Lazy** : si index absent → `PreExecutionBuilder` tombe en fallback (charge tous les skills, aucun behavior) — résultat correct, juste non-optimisé

---

## XI. Étapes d'implémentation

Règle de non-régression : chaque étape est backward-compatible. Nouveaux paramètres
optionnels (`reporter=None`, `task_executor=None`, `pre_exec=None`). Les 671 tests
existants restent verts à chaque étape.

| # | Contenu | Fichiers | Impact tests |
|---|---------|----------|--------------|
| **1** | Socle données | `models.py` `protocols.py` | 0 cassé, +unitaires modèles |
| **2** | Canal + progression | `messaging/progress.py` (nouveau) | +unitaires ProgressReporter |
| **3** | TaskExecutor | `tasks/__init__.py` `tasks/executor.py` (nouveaux) | +tests async |
| **4** | Infrastructure contextuelle | `agents/trame_manager.py` `agents/profile_service.py` `agents/pre_execution.py` (nouveaux) | +tests par module |
| **5** | Agent 1 enrichi | `agents/analyser.py` | tests Analyser mis à jour |
| **6** | Agent 3 dual mode + streaming | `agents/synthesiser.py` `agents/context_builder.py` | +tests assess() + stream |
| **7** | Agent 2 + intégration finale | `agents/orchestrator.py` `messaging/handlers.py` `config.py` `main.py` `mcp/server.py` | tests pipeline complet |

---

---

## XII. Connecteurs MCP — Colaig comme client MCP

### Concept

En plus d'exposer un serveur MCP, Colaig peut consommer des serveurs MCP externes.
Chaque workspace peut déclarer des **connecteurs MCP** vers des serveurs tiers (bases
documentaires, APIs métier, services gouvernementaux). Ces serveurs enrichissent
le pipeline sans nécessiter de code spécifique côté Colaig.

```
workspace/.colaig/config.yaml          ← MCPConnectorConfig déclarés ici
  ↓
MCPClientPool (integrations/mcp_client.py)
  ↓
Deux usages parallèles :
  1. Resources → MCPResourceIndexer → chunks.faiss du workspace
  2. Tools     → ToolRegistry de l'Orchestrateur
```

### MCPConnectorConfig (models.py)

```python
@dataclass
class MCPConnectorConfig:
    name: str                     # Identifiant unique (ex: "docs-juridiques")
    url: str                      # URL endpoint MCP
    transport: str = "http"       # "http" | "sse"
    auth_token: str = ""          # Bearer token si requis
    index_resources: bool = True  # Indexer les resources dans chunks.faiss
    expose_tools: bool = True     # Enregistrer les tools dans ToolRegistry
    enabled: bool = True
```

Déclaré dans `WorkspaceConfig.mcp_connectors` (liste vide = pas de connecteurs).

### Flux d'intégration

#### Resources MCP → FAISS

```
MCPClientPool.connect(connector)
  ↓ GET /resources
  Liste de resources {uri, name, description, mimeType}
  ↓
MCPResourceIndexer (rag/indexer.py ou dédié)
  Pour chaque resource :
    content = GET /resources/read?uri={uri}
    chunks = chunker.chunk_document(content, source_path=uri)
    embeddings = albert.embed_batch([c.text for c in chunks])
    faiss_store.add(embeddings, chunks)
    DocumentRecord.path = f"mcp://{connector.name}/{uri}"
  ↓
chunks.faiss du workspace (namespace préfixé "mcp://")
```

Les chunks MCP sont **indistinguables des chunks storage** pour l'Agent 2 : la recherche
RAG les inclut automatiquement. `DocumentRecord.path = "mcp://..."` permet au
Synthétiseur de formater les sources correctement.

#### Tools MCP → ToolRegistry

```
MCPClientPool.connect(connector)
  ↓ GET /tools
  Liste de tools {name, description, inputSchema}
  ↓
Pour chaque tool :
  ToolDefinition(
      name = f"{connector.name}__{tool.name}",   # namespacing
      description = tool.description,
      parameters = [from inputSchema],
      category = "mcp_external",
      requires_workspace = False,
  )
  ↓ registré dans build_tool_registry()
```

L'Agent 2 appelle le tool via `MCPClientPool.call_tool(connector_name, tool_name, args)`.
Le ToolRegistry route vers `MCPClientPool` selon le préfixe `{connector_name}__`.

### Architecture MCPClientPool

```python
# integrations/mcp_client.py

class MCPClientPool:
    """Gère les connexions aux serveurs MCP externes (Phase 6)."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client
        self._connections: dict[str, MCPConnectorConfig] = {}

    async def connect_workspace(self, workspace: WorkspaceConfig) -> None:
        """Connecte tous les connecteurs activés d'un workspace."""
        for connector in workspace.mcp_connectors:
            if connector.enabled:
                self._connections[connector.name] = connector

    async def list_resources(self, connector_name: str) -> list[dict]:
        """GET /resources → liste des resources disponibles."""
        ...

    async def read_resource(self, connector_name: str, uri: str) -> str:
        """GET /resources/read → contenu d'une resource."""
        ...

    async def list_tools(self, connector_name: str) -> list[dict]:
        """GET /tools → liste des tools disponibles."""
        ...

    async def call_tool(
        self,
        connector_name: str,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """POST /tools/call → résultat de l'exécution."""
        ...
```

### Synchronisation et indexation

**Déclencheurs de ré-indexation** :
1. Démarrage du workspace (si `index_resources=True`)
2. Notification MCP `resource/changed` (si le serveur supporte les subscriptions)
3. Scan périodique configurable (via `document_index_refresh_interval`)

**Namespacing** :
- Resources : `mcp://{connector.name}/{resource.uri}` dans `DocumentRecord.path`
- Tools : `{connector.name}__{tool.name}` dans `ToolDefinition.name`

Ce namespacing garantit l'absence de collision entre resources/tools locaux et MCP.

### Compatibilité backward

- `WorkspaceConfig.mcp_connectors` est `[]` par défaut → aucun impact sur workspaces existants
- `MCPClientPool` est optionnel dans `build_tool_registry()` et `handlers.py`
- Sans connecteurs MCP : `MCPClientPool` n'est pas instancié (zéro overhead)
- Implémentation `integrations/mcp_client.py` constitue l'étape 8 du plan d'implémentation

---

*Document finalisé le 2026-02-28.
Sections I–VII : conception Phase 6 exploratoire (validée).
Sections VIII–XI : architecture finale retenue pour implémentation.
Section XII : connecteurs MCP (Colaig comme client MCP) — ajout 2026-02-28.*
