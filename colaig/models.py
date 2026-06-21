"""
Colaig — Modèles de données partagés

TOUTES les dataclasses utilisées par plus d'un module sont ici.
Les modules ne créent PAS leurs propres dataclasses pour les données partagées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# =============================================================================
# ENUMS
# =============================================================================

class ConversationType(str, Enum):
    """Type de conversation (provider-agnostic)."""
    DM = "dm"                    # Message direct (2 personnes)
    PRIVATE = "private"          # Conversation privée (invités seulement)
    PUBLIC = "public"            # Conversation publique (rejoignable)
    CHANNEL = "channel"          # Canal (Slack-style)
    UNKNOWN = "unknown"


# Alias de rétrocompatibilité
RoomType = ConversationType


class ContextMode(str, Enum):
    """Mode de fonctionnement résolu par le Context Resolver."""
    ASSISTANT = "assistant"      # Workspace trouvé → réponses contextuelles
    CHATBOT = "chatbot"          # Pas de workspace → mode FAQ/explication
    PERSONAL = "personal"        # DM → vue transversale multi-workspaces


class PipelinePhase(str, Enum):
    """Phase du pipeline multi-agent (Phase 2+)."""
    THINKING = "thinking"           # Analyseur en cours
    RETRIEVING = "retrieving"       # Recherche documentaire
    EXECUTING = "executing"         # Exécution de tools/steps
    SYNTHESIZING = "synthesizing"   # Synthétiseur en cours
    COMPLETE = "complete"           # Pipeline terminé


# =============================================================================
# TRAME VIVANTE — État sémantique de la conversation (Phase 6)
# =============================================================================

@dataclass
class WorkflowStep:
    """Étape du workflow conversationnel (niveau macro).

    Représente l'avancement du dialogue avec l'utilisateur,
    pas les étapes internes du pipeline agentique.
    """
    step_id: str           # Identifiant sémantique : "initial_question", "docs_found"
    description: str       # Description lisible pour l'utilisateur
    completed: bool = False
    turn_index: int = -1   # Index du tour où l'étape s'est réalisée (-1 = pas encore)
    summary: str = ""      # Résumé de ce qui s'est passé à cette étape


@dataclass
class ContextAnchor:
    """Élément établi dans la conversation — ne pas re-retriever.

    Permet aux agents de savoir ce qui est déjà connu pour éviter
    les re-recherches et les ré-explications inutiles.
    """
    anchor_type: str       # "document" | "decision" | "constraint" | "entity"
    ref: str               # Chemin fichier, ou texte de la décision/contrainte
    description: str = ""  # Description complémentaire
    turn_index: int = 0    # Tour où l'ancre a été établie
    established_at: datetime | None = None  # Horodatage UTC de création


@dataclass
class ConversationTrame:
    """Trame vivante — état sémantique du workflow par conversation.

    Stocké dans {workspace}/.colaig/conversations/{conv_id}_trame.json.
    Chargé en début de pipeline, mis à jour après pipeline par TrameManager.
    TrameManager est le seul composant qui écrit la trame (isolation des agents).
    """
    conv_id: str
    workspace_id: str

    # Phase macro de la conversation (distinct de PipelinePhase interne au pipeline)
    # Phases possibles : discovery | analysis | drafting | review | concluded
    conversation_phase: str = "discovery"

    # Behavior actif pour cette conversation (stable entre turns)
    active_behavior: str | None = None

    # Étapes du workflow conversationnel (méta-niveau, pas les étapes d'exécution)
    workflow_steps: list[WorkflowStep] = field(default_factory=list)

    # Anchors — éléments connus, pour éviter de re-retriever ou réexpliquer
    context_anchors: list[ContextAnchor] = field(default_factory=list)

    # Statistiques
    turn_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# =============================================================================
# MESSAGES
# =============================================================================

@dataclass
class Attachment:
    """Pièce jointe à un message."""
    filename: str
    content_type: str = ""
    size: int = 0
    url: str = ""
    content: bytes | None = None


@dataclass
class IncomingMessage:
    """Message reçu depuis n'importe quel canal de communication.

    Champs génériques, indépendants du provider.
    Créé par messaging/matrix.py (ou slack.py, webchat.py), consommé par messaging/handlers.py.
    """
    user_id: str                 # Identifiant utilisateur (format libre)
    conversation_id: str         # Identifiant conversation (room_id Matrix, channel Slack, etc.)
    body: str                    # Contenu texte du message
    conversation_type: ConversationType = ConversationType.UNKNOWN
    message_id: str = ""         # Identifiant unique du message
    timestamp: datetime = field(default_factory=datetime.utcnow)
    display_name: str = ""       # Nom affiché de l'auteur
    is_reply: bool = False       # Est-ce une réponse à un message
    reply_to: str = ""           # ID du message auquel on répond (thread)
    platform: str = ""           # Plateforme source (matrix, slack, webchat, mcp)
    attachments: list[Attachment] = field(default_factory=list)

    # Métadonnées spécifiques au provider (pour debug/logging)
    raw_metadata: dict | None = None

    # Alias de rétrocompatibilité (propriétés calculées)
    @property
    def room_id(self) -> str:
        return self.conversation_id

    @property
    def room_type(self) -> ConversationType:
        return self.conversation_type

    @property
    def event_id(self) -> str:
        return self.message_id

    @property
    def reply_to_event_id(self) -> str:
        return self.reply_to


# =============================================================================
# STORAGE / FICHIERS
# =============================================================================

@dataclass
class StorageWebhookEvent:
    """Événement webhook provenant d'un backend de stockage (provider-agnostic).

    Produit par StorageProtocol.handle_webhook_event() après validation de
    la signature. Consommé par la route POST /webhooks/storage pour déclencher
    les actions Colaig appropriées (discovery, reindex…).
    """
    # Type sémantique — valeurs normalisées quel que soit le provider
    event_type: str              # "collaboration_added" | "file_created"
                                 # | "file_modified" | "file_deleted"
    folder_path: str = ""        # Chemin Colaig du dossier concerné (si connu)
    folder_id: str = ""          # ID natif du dossier (ex: Box folder ID)
    file_path: str = ""          # Chemin Colaig du fichier (si file event)
    file_id: str = ""            # ID natif du fichier
    raw: dict = None             # Payload brut (pour debug)

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}


@dataclass
class StorageFile:
    """Fichier ou répertoire dans le backend de stockage (provider-agnostic).

    Remplace WebDAVFile. Même structure, nom générique.
    Créé par les implémentations StorageProtocol.
    """
    path: str                    # /espace-projet/documents/guide.pdf
    name: str                    # guide.pdf
    is_directory: bool = False
    size: int = 0                # Taille en bytes
    etag: str = ""               # Pour détection de changements
    last_modified: datetime | None = None
    content_type: str = ""       # application/pdf, text/plain, etc.


# Alias de rétrocompatibilité
WebDAVFile = StorageFile


@dataclass
class UserProfile:
    """Profil consolidé d'un utilisateur (produit par UserMemory.consolidate()).

    Stocké dans {workspace}/.colaig/users/{safe_user_id}/profile.json.
    Reconstruit périodiquement par la boucle de consolidation background.
    """
    role: str = ""
    expertise_areas: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    active_projects: list[str] = field(default_factory=list)
    communication_style: str = ""
    # Métadonnées
    consolidated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict) -> UserProfile:
        """Construit un UserProfile depuis un dict JSON (tolérant aux champs manquants)."""
        return cls(
            role=str(data.get("role", "")),
            expertise_areas=[str(x) for x in data.get("expertise_areas", []) if x],
            preferences=[str(x) for x in data.get("preferences", []) if x],
            constraints=[str(x) for x in data.get("constraints", []) if x],
            active_projects=[str(x) for x in data.get("active_projects", []) if x],
            communication_style=str(data.get("communication_style", "")),
            consolidated_at=datetime.utcnow(),
        )

    def is_empty(self) -> bool:
        """True si le profil ne contient aucune information utile."""
        return not any([
            self.role, self.expertise_areas, self.preferences,
            self.constraints, self.active_projects, self.communication_style,
        ])


@dataclass
class MCPConnectorConfig:
    """Serveur MCP externe déclaré par un workspace (Phase 6).

    Permet à Colaig d'agir comme client MCP vers des serveurs externes.
    Les resources sont indexées dans chunks.faiss du workspace.
    Les tools sont enregistrés dans le ToolRegistry de l'Orchestrateur.
    Déclaré dans WorkspaceConfig.mcp_connectors.
    """
    name: str                           # Identifiant unique (ex: "docs-juridiques")
    url: str                            # URL endpoint MCP
    transport: str = "http"             # "http" | "sse"
    auth_token: str = ""                # Bearer token si authentification requise
    index_resources: bool = True        # Indexer les resources MCP dans chunks.faiss
    expose_tools: bool = True           # Enregistrer les tools MCP dans ToolRegistry
    enabled: bool = True
    # Sécurité — filtrage des tools par politique
    tool_policy: str = "all"            # "all" | "read_only" | "explicit"
    allowed_tools: list[str] = field(default_factory=list)  # si tool_policy="explicit"
    # Sécurité — filtrage des URLs navigables (SSRF protection)
    allowed_domains: list[str] = field(default_factory=list)  # whitelist glob (ex: "*.gouv.fr")
    blocked_ip_ranges: list[str] = field(default_factory=lambda: [
        "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
        "192.168.0.0/16", "127.0.0.0/8", "0.0.0.0/8",
    ])
    # Session — isolation multi-utilisateur (ex: X-Session-Id)
    session_scope: str = "none"         # "none" | "conversation" | "user"
    session_header: str = "X-Session-Id"  # nom du header pour le session_id
    # Rate limiting
    max_calls_per_minute: int = 0       # 0 = pas de limite
    # Troncature des résultats (protection prompt injection via contenu web)
    max_result_length: int = 10000      # chars max par résultat tool


# =============================================================================
# WORKSPACE / CONFIGURATION
# =============================================================================

@dataclass
class StorageEvent:
    """Événement storage provider-agnostic (CloudEvents-aligned).

    Produit par check_updates() (polling) ou watch() (events natifs).
    Consommé par Indexer.handle_event() — même interface quelle que soit la source.

    Types normalisés :
        "file.created"   — nouveau fichier détecté
        "file.modified"  — fichier existant modifié (etag changé)
        "file.deleted"   — fichier supprimé du storage
    """
    type: str                                        # "file.created" | "file.modified" | "file.deleted"
    path: str                                        # Colaig path
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)     # etag, size, content_type, etc.


@dataclass
class UpdateSummary:
    """Résultat détaillé de check_updates() — rétrocompatible avec les comparaisons int.

    Exemples :
        if update > 0: ...        # équivalent à update.count > 0
        assert update == 1        # compare update.count
        update.changed_paths      # liste des chemins nouveaux/modifiés
        update.removed_paths      # ensemble des chemins supprimés
    """
    count: int = 0
    changed_paths: list[str] = field(default_factory=list)
    removed_paths: set[str] = field(default_factory=set)
    events: list[StorageEvent] = field(default_factory=list)  # événements détaillés

    def __bool__(self) -> bool:
        return self.count > 0 or bool(self.removed_paths)

    def __int__(self) -> int:
        return self.count

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.count == other
        if isinstance(other, UpdateSummary):
            return self.count == other.count
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.count > other
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.count >= other
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.count < other
        return NotImplemented

    def __hash__(self) -> int:  # requis car __eq__ est défini
        return hash(self.count)


@dataclass
class WorkspaceConfig:
    """Configuration d'un espace de travail Colaig.

    Chargé depuis .colaig/config.yaml par context/workspace.py.
    """
    workspace_id: str            # identifiant unique (slugifié)
    name: str                    # Nom affichable
    storage_path: str            # Chemin racine dans le backend de stockage
    description: str = ""

    # Mapping conversations (provider-agnostic)
    conversations: list[str] = field(default_factory=list)  # Liste de conversation_ids associés
    user_ids: list[str] = field(default_factory=list)        # Liste de user_ids pour DMs personnels
    owners: list[str] = field(default_factory=list)          # user_ids admins de CE workspace (config réflexive scopée)

    # Comportement (Couche 1)
    system_prompt: str = ""
    tone: str = "professional"   # professional, casual, formal, technical
    expertise_level: str = "general"  # general, expert, beginner
    language: str = "fr"

    # RAG (Couche 4)
    rag_enabled: bool = True
    similarity_threshold: float = 0.3
    max_results: int = 5
    priority_documents: list[str] = field(default_factory=list)

    # Capacités (Couche 2)
    tools_enabled: list[str] = field(default_factory=list)

    # Index
    index_path: str = ""         # Chemin relatif vers /.colaig/indexes/

    # Accès storage
    storage_readonly: bool = False  # True si Colaig n'a que des droits de lecture

    # Connecteurs MCP externes (Phase 6) — serveurs MCP consommés par ce workspace
    mcp_connectors: list[MCPConnectorConfig] = field(default_factory=list)

    # Notifications proactives (Mode A/B) — opt-in par workspace
    proactive_notifications: bool = False
    # Canaux de notification : si vide, utilise toutes les conversations du workspace
    notification_channels: list[str] = field(default_factory=list)

    # Accès MCP — workspace public (accessible sans auth MCP)
    # True → accessible aux sessions non authentifiées (ex: workspace d'accueil)
    # False (défaut) → requiert un token avec appartenance user_ids
    public: bool = False

    # Fédération — visibilité et identité du workspace
    # "private"    → visible uniquement sur cette instance (défaut)
    # "federation" → visible aux instances partageant le même storage (auto-discovery)
    # "public"     → visible à toutes les instances (inter-fédération via mcp_connectors)
    visibility: str = "private"
    # True si ce bot est le coordinateur principal dans un salon multi-bots
    primary: bool = False
    # Thèmes/domaines pour l'auto-discovery SRV futur (ex: ["rh", "juridique"])
    topics: list[str] = field(default_factory=list)

    # Alias de rétrocompatibilité
    @property
    def webdav_path(self) -> str:
        return self.storage_path

    @property
    def tchap_rooms(self) -> list[str]:
        return self.conversations


@dataclass
class WorkspaceContext:
    """Contexte résolu complet pour un message.

    Créé par context/resolver.py + context/layers.py.
    Consommé par rag/generator.py et messaging/handlers.py.
    """
    workspace: WorkspaceConfig | None
    mode: ContextMode

    # Couche 1 — Comportement
    system_prompt: str = ""

    # Couche 2 — Capacités
    available_tools: list[str] = field(default_factory=list)

    # Couche 3 — Conversation
    conversation_history: list[dict] = field(default_factory=list)

    # Couche 5 — Profil
    user_id: str = ""            # Identifiant utilisateur (pour ACL outils inter-workspace)
    user_display_name: str = ""
    user_domain: str = ""        # Domaine extrait du user_id

    # Phase 6 — Trame vivante (injectée depuis TrameManager avant le pipeline)
    conversation_phase: str | None = None
    context_anchors: list = field(default_factory=list)


# =============================================================================
# PROFIL WORKSPACE — Comportements déclaratifs (Phase 6)
# =============================================================================

@dataclass
class VocabularyConfig:
    """Vocabulaire spécifique au domaine du workspace."""
    terms: list[str] = field(default_factory=list)
    abbreviations: dict[str, str] = field(default_factory=dict)


@dataclass
class DocumentMapConfig:
    """Configuration de la carte documentaire (taxonomie + extraction d'entités)."""
    categorization_taxonomy: list[str] = field(default_factory=list)
    entity_types_to_extract: list[str] = field(default_factory=list)
    metadata_enrichment_instructions: str = ""


@dataclass
class WorkspaceProfile:
    """Profil déclaratif d'un workspace — chargé depuis identity.yaml.

    Décrit le domaine, le vocabulaire métier et la taxonomie documentaire.
    Utilisé par l'Indexer (enrichissement DocumentRecord) et par le pipeline
    pour adapter le ton et le vocabulaire des réponses.
    """
    name: str = ""
    domain: str = ""
    sub_domain: str = ""
    language: str = "fr"
    tone: str = "formel"
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    document_map: DocumentMapConfig = field(default_factory=DocumentMapConfig)


@dataclass
class BehaviorActivationConfig:
    """Conditions d'activation d'un behavior (similarité embedding + intent type)."""
    semantic_triggers: list[str] = field(default_factory=list)
    intent_types: list[str] = field(default_factory=list)
    min_similarity: float = 0.75


@dataclass
class BehaviorAgentConfig:
    """Overrides agent pour un behavior actif."""
    temperature: float | None = None
    format: str = ""
    max_tokens: int | None = None
    tools_priority: list[str] = field(default_factory=list)


@dataclass
class BehaviorToolsConfig:
    """Configuration tools pour un behavior actif."""
    disable: list[str] = field(default_factory=list)


@dataclass
class BehaviorConfig:
    """Behavior déclaratif — chargé depuis behaviors/*.yaml.

    Modifie le comportement du pipeline (temperature, format, skills, tools)
    selon le mode courant détecté par similarité sémantique sur behaviors.faiss.
    Activé via PreExecutionBuilder.
    """
    name: str
    description: str = ""
    activation: BehaviorActivationConfig = field(default_factory=BehaviorActivationConfig)
    agents: dict[str, BehaviorAgentConfig] = field(default_factory=dict)
    skills_boost: list[str] = field(default_factory=list)
    tools: BehaviorToolsConfig = field(default_factory=BehaviorToolsConfig)
    # {"tone": "formel", "include_sources": False, "include_confidence": False}
    output: dict = field(default_factory=dict)


# =============================================================================
# RAG / DOCUMENTS
# =============================================================================

@dataclass
class DocumentChunk:
    """Chunk de document indexé.

    Créé par rag/chunker.py, stocké dans rag/faiss_store.py.
    """
    text: str                    # Contenu textuel du chunk
    source_path: str             # Chemin du document source dans le storage
    source_name: str = ""        # Nom du fichier source
    page: int = 0                # Numéro de page (si applicable)
    section: str = ""            # Titre de section (si applicable)
    position: int = 0            # Position dans le document (0-based)
    doc_type: str = "text"       # pdf, docx, md, txt, odt
    source_hash: str = ""        # Hash du document source (pour invalidation)
    indexed_at: datetime = field(default_factory=datetime.utcnow)
    contextual_prefix: str = ""  # Préfixe contextuel généré par LLM (Contextual Retrieval)

    # Métadonnées additionnelles
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """Résultat d'une recherche RAG.

    Créé par rag/retriever.py, consommé par rag/generator.py.
    """
    chunk: DocumentChunk         # Le chunk trouvé
    score: float                 # Score de similarité (0→1, plus élevé = mieux)
    rank: int = 0                # Position dans les résultats (0-based)
    embedding: list = field(default_factory=list)  # Vecteur FAISS reconstruit — utilisé par MMR pour vraie cosine


@dataclass
class GeneratedResponse:
    """Réponse générée par le LLM.

    Créé par rag/generator.py ou agents/synthesiser.py, consommé par messaging/handlers.py.
    """
    text: str                    # Texte de la réponse (Markdown)
    sources: list[str] = field(default_factory=list)  # Chemins des docs sources
    confidence: float = 0.0      # Score de confiance moyen des résultats RAG
    model_used: str = ""         # Modèle Albert utilisé
    tokens_used: int = 0         # Tokens consommés
    generation_time_ms: int = 0  # Temps de génération en ms
    context_card: ContextCard | None = None  # Carte de contexte (Phase 2+)


# =============================================================================
# PIPELINE AGENTS — Analyseur → Orchestrateur → Synthétiseur
# =============================================================================

@dataclass
class AgentDirectives:
    """Directives ciblées produites par l'Analyseur pour un agent spécifique.

    L'Analyseur produit des directives distinctes pour l'Orchestrateur et le
    Synthétiseur, permettant à chaque agent de cibler son comportement.
    """
    target_agent: str              # "orchestrator" | "synthesiser"
    instructions: str = ""         # Consignes textuelles
    resources_to_target: list[str] = field(default_factory=list)
    tools_to_use: list[str] = field(default_factory=list)
    search_strategy: str = ""      # Pour orchestrateur : "broad", "precise", "multi-step"
    response_format: str = ""      # Pour synthétiseur : "list", "paragraph", "table", "step-by-step"
    response_tone: str = ""        # Pour synthétiseur : override du ton workspace
    focus_points: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    """Contexte propre à un agent, construit depuis le workspace.

    Chaque agent du pipeline reçoit un AgentContext construit par
    agents/context_builder.py. Contient le prompt (default + override),
    les tools/resources filtrés selon le rôle, et les directives reçues
    de l'analyseur (pour orchestrateur et synthétiseur).
    """
    agent_role: str                # "analyser" | "orchestrator" | "synthesiser"
    system_prompt: str = ""        # Prompt par défaut + override workspace
    workspace_config: WorkspaceConfig | None = None
    available_tools: list[str] = field(default_factory=list)
    available_resources: list[str] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)  # Skills chargés depuis .colaig/skills/
    directives: AgentDirectives | None = None      # Directives reçues de l'analyseur
    metadata: dict = field(default_factory=dict)
    # Phase 6
    pre_exec: PreExecutionCard | None = None       # PreExecutionCard (Phase 6)
    retrieval_results: dict = field(default_factory=dict)
    # {"chunks": [SearchResult...], "docs": [...], "skills": [...], "history": [...]}


@dataclass
class ContextCard:
    """Carte de contexte transportée à travers le pipeline.

    Inspirée du pattern BigLocalApps. Chaque tool/agent enrichit la ContextCard
    avec le mode, les tools disponibles, les resources utilisées, etc.
    Retournée au client MCP avec chaque réponse.
    """
    mode: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    available_tools: list[str] = field(default_factory=list)
    suggested_resources: list[str] = field(default_factory=list)
    workflow_hint: str = ""
    sources_used: list[str] = field(default_factory=list)
    pipeline_phases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)


class IntentType(str, Enum):
    """Type d'intention détectée par l'Analyseur."""
    QUESTION = "question"           # Question sur les documents
    ACTION = "action"               # Demande d'action (créer, modifier, envoyer)
    SEARCH = "search"               # Recherche documentaire pure
    SUMMARY = "summary"             # Demande de synthèse/résumé
    CONFIGURATION = "configuration" # Configuration de Colaig
    GREETING = "greeting"           # Salutation / small talk
    CLARIFICATION = "clarification" # Besoin de clarification
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """Intention analysée d'un message utilisateur.

    Produit par agents/analyser.py, consommé par agents/orchestrator.py.
    Contient des directives ciblées pour chaque agent en aval.
    """
    intent_type: IntentType
    query_reformulated: str = ""   # Question reformulée pour la recherche RAG
    entities: dict = field(default_factory=dict)  # Entités extraites (dates, noms, etc.)
    needs_rag: bool = True         # L'orchestrateur doit-il chercher dans les docs ?
    needs_tools: bool = False      # L'orchestrateur doit-il utiliser des outils MCP ?
    confidence: float = 0.0        # Confiance de la détection (0→1)
    raw_analysis: str = ""         # Analyse brute du LLM (debug)
    orchestrator_directives: AgentDirectives | None = None  # Directives pour l'orchestrateur (backward compat)
    synthesiser_directives: AgentDirectives | None = None   # Directives pour le synthétiseur
    # Phase 6 — remplace orchestrator_directives
    search_directives: SearchDirectives | None = None       # Plan de récupération multi-source
    # Fast path — court-circuit pipeline complet (salutations, réponses directes)
    is_direct: bool = False
    direct_response: str | None = None
    # Signaux pour TrameManager (suggérés par Agent 1, écrits par TrameManager seulement)
    suggested_next_phase: str | None = None
    new_anchors: list[ContextAnchor] = field(default_factory=list)


@dataclass
class ExecutionStep:
    """Une étape du plan d'exécution.

    Produit par agents/orchestrator.py.
    """
    step_type: str                 # "rag_search", "mcp_tool", "llm_call", "storage_fetch"
    description: str = ""
    params: dict = field(default_factory=dict)     # Paramètres de l'étape
    result: dict | None = None  # Résultat après exécution
    status: str = "pending"        # pending, running, done, error
    error: str = ""


@dataclass
class ExecutionPlan:
    """Plan d'exécution produit par l'Orchestrateur.

    Séquence d'étapes à exécuter pour répondre à l'intention.
    """
    intent: Intent
    steps: list[ExecutionStep] = field(default_factory=list)
    search_results: list = field(default_factory=list)  # List[SearchResult] accumulés
    tool_results: list[dict] = field(default_factory=list)  # Résultats d'outils MCP
    execution_time_ms: int = 0
    context_card: ContextCard | None = None  # Carte de contexte (Phase 2+)
    orchestrator_reasoning: str = ""  # Raisonnement final de l'orchestrateur (mode agentique)


# =============================================================================
# PIPELINE PHASE 6 — Retrieval multi-source, completion, canal, tâches
# =============================================================================

@dataclass
class SearchDirectives:
    """Plan de récupération multi-source — produit par l'Analyser (Phase 6).

    Remplace AgentDirectives(target_agent="orchestrator").
    Décrit comment PreExecutionBuilder.execute_retrieval() doit chercher.
    Utilisé aussi par Agent 2 pour prioriser et filtrer dans sa boucle itérative.
    """
    # Requêtes sémantiques par source (liste vide = source ignorée)
    chunk_queries: list[str] = field(default_factory=list)      # → chunks.faiss
    document_queries: list[str] = field(default_factory=list)   # → documents.faiss
    skill_queries: list[str] = field(default_factory=list)      # → skills.faiss
    history_queries: list[str] = field(default_factory=list)    # → ConversationMemory
    # Filtres structurés auto-détectés depuis l'intent
    context_filters: dict = field(default_factory=dict)
    # Ex: {"ai_category": "circulaire", "date_range": "2023+", "exclude_paths": [...]}
    # Objectif et critères de complétude pour Agent 2 + assess_completion
    objective: str = ""
    completeness_criteria: list[str] = field(default_factory=list)


@dataclass
class CompletionSignal:
    """Verdict Agent 3 (mode juge) — pilote la sortie de boucle Agent 2.

    Retourné par Synthesiser.assess(), appelé comme tool assess_completion
    depuis la boucle agentique de l'Orchestrateur.
    """
    sufficient: bool
    confidence: float = 0.0
    missing_elements: list[str] = field(default_factory=list)
    suggested_directions: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ChannelFormat:
    """Contraintes de rendu du canal cible — résolu avant le pipeline.

    Produit par messaging.progress.resolve_channel().
    Détermine si le pipeline utilise streaming, HTML, longueur max, etc.
    """
    supports_html: bool = False
    supports_markdown: bool = True
    supports_tables: bool = False        # True si le canal supporte les tableaux (HTML/Markdown)
    max_length: int = 0                  # 0 = illimité
    supports_streaming: bool = False     # True si le canal supporte les mises à jour progressives
    reply_style: str = "conversational"  # "conversational" | "structured" | "json"


@dataclass
class TaskHandle:
    """Handle d'une tâche async soumise au TaskExecutor.

    Retourné immédiatement par TaskExecutor.submit() sans attendre
    l'exécution de la tâche (non-bloquant).
    """
    task_id: str
    conversation_id: str
    status: str = "pending"              # "pending" | "running" | "done" | "error" | "cancelled"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PreExecutionCard:
    """Configuration du pipeline construite avant l'Analyser (Phase 6).

    Créée par agents/pre_execution.py (PreExecutionBuilder.build()).
    Passée à Analyser, Orchestrateur, Synthétiseur via AgentContext.pre_exec.
    Centralise : état trame, behavior actif, skills lazy-loaded, tools filtrés.
    """
    # État conversation (depuis trame vivante)
    workspace_id: str | None
    conversation_phase: str | None
    context_anchors: list[ContextAnchor] = field(default_factory=list)

    # Behavior actif (résolu via behaviors.faiss + trame.active_behavior)
    active_behavior_name: str | None = None
    active_behavior_config: BehaviorConfig | None = None
    active_behavior_score: float = 0.0

    # Skills lazy-loaded (via skills.faiss, top-k pertinents au message)
    selected_skills: list[dict] = field(default_factory=list)        # [{"name":..,"content":..}]
    selected_skills_scores: list[float] = field(default_factory=list)

    # Tools disponibles (filtrage déclaratif + behavior.tools.disable)
    available_tools: list[ToolDefinition] = field(default_factory=list)

    # Overrides agents depuis behavior.agents.* (ex: {"synthesiser": {"temperature": 0.7}})
    agent_overrides: dict[str, dict] = field(default_factory=dict)

    # Profil workspace (depuis identity.yaml)
    workspace_profile: WorkspaceProfile | None = None

    # Contexte fixe transmis directement à Agent 1 (config + identity + behavior)
    fixed_context: dict = field(default_factory=dict)

    # Embedding du message — calculé 1 fois, réutilisé pour behaviors + skills FAISS
    # NE PAS utiliser pour les requêtes RAG (embed séparé dans execute_retrieval())
    message_embedding: list[float] | None = None


# =============================================================================
# DOCUMENT INDEX — Métadonnées de fichiers décentralisées (par workspace)
# =============================================================================

class DocumentStatus(str, Enum):
    """Statut d'un document dans le DocumentIndex."""
    PENDING = "pending"        # Détecté, pas encore analysé
    ANALYZING = "analyzing"    # Analyse IA en cours
    ANALYZED = "analyzed"      # Analyse complète (résumé, catégorie, entités)
    ERROR = "error"            # Erreur lors de l'analyse


@dataclass
class DocumentRecord:
    """Métadonnées complètes d'un fichier dans le DocumentIndex.

    Stocké dans registry.json et dans les métadonnées FAISS documents.
    Créé et maintenu par rag/document_index.py.
    """
    # Identité du fichier
    path: str                                    # Chemin dans le storage
    name: str = ""                               # Nom du fichier
    size: int = 0                                # Taille en bytes
    checksum: str = ""                           # SHA256 du contenu
    mime_type: str = ""                          # application/pdf, text/plain, etc.
    last_modified: datetime | None = None
    etag: str = ""                               # Etag du storage (détection de changements)

    # Statut de traitement
    status: DocumentStatus = DocumentStatus.PENDING
    indexed_at: datetime | None = None        # Date de première indexation
    analyzed_at: datetime | None = None       # Date de dernière analyse IA
    error_message: str = ""                      # Message d'erreur si status=ERROR

    # Métadonnées IA (remplies par l'analyse Albert)
    ai_summary: str = ""                         # Résumé du document (2-3 phrases)
    ai_category: str = ""                        # Catégorie (procédure, rapport, guide...)
    # v3.1 : dict au lieu de list[str] — pivot du matching ClassificationEngine
    # Clés : "supplier", "customer", "amount", "date", + entités custom du profil workspace
    ai_entities: dict = field(default_factory=dict)
    ai_keywords: list[str] = field(default_factory=list)   # Mots-clés métier
    ai_language: str = ""                        # Langue détectée (fr, en...)
    ai_doc_type: str = ""                        # Type (formulaire, guide, compte-rendu...)

    # Classification (rag/classifier.py)
    # virtual_path = chemin organisé suggéré par règle ou LLM (métadonnée de navigation, pas de déplacement physique)
    virtual_path: str = ""
    virtual_filename: str = ""                   # Nom normalisé suggéré
    rule_applied: str = ""                       # Nom règle matchée, "ai_suggestion", ou ""
    classification_confidence: float = 0.0       # 1.0 règle exacte, 0.7 suggestion IA, 0.0 aucune

    # Relation avec chunks.faiss
    chunk_count: int = 0                         # Nombre de chunks dans chunks.faiss
    faiss_doc_id: int = -1                       # Position dans documents.faiss (-1 si absent)

    def to_dict(self) -> dict:
        """Sérialise en dict JSON-compatible."""
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "checksum": self.checksum,
            "mime_type": self.mime_type,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "etag": self.etag,
            "status": self.status.value,
            "indexed_at": self.indexed_at.isoformat() if self.indexed_at else None,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
            "error_message": self.error_message,
            "ai_summary": self.ai_summary,
            "ai_category": self.ai_category,
            "ai_entities": self.ai_entities,
            "ai_keywords": self.ai_keywords,
            "ai_language": self.ai_language,
            "ai_doc_type": self.ai_doc_type,
            "virtual_path": self.virtual_path,
            "virtual_filename": self.virtual_filename,
            "rule_applied": self.rule_applied,
            "classification_confidence": self.classification_confidence,
            "chunk_count": self.chunk_count,
            "faiss_doc_id": self.faiss_doc_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DocumentRecord:
        """Désérialise depuis un dict JSON.

        Migration backward-compat :
            ai_entities anciennement list[str] → converti en dict vide
            (les anciens registry.json chargent sans erreur, re-analysés au prochain cycle)
        """
        raw_entities = data.get("ai_entities", {})
        if isinstance(raw_entities, list):
            # Migration : anciens enregistrements avec ai_entities comme liste
            ai_entities: dict = {}
        else:
            ai_entities = raw_entities or {}

        return cls(
            path=data["path"],
            name=data.get("name", ""),
            size=data.get("size", 0),
            checksum=data.get("checksum", ""),
            mime_type=data.get("mime_type", ""),
            last_modified=datetime.fromisoformat(data["last_modified"])
                if data.get("last_modified") else None,
            etag=data.get("etag", ""),
            status=DocumentStatus(data.get("status", "pending")),
            indexed_at=datetime.fromisoformat(data["indexed_at"])
                if data.get("indexed_at") else None,
            analyzed_at=datetime.fromisoformat(data["analyzed_at"])
                if data.get("analyzed_at") else None,
            error_message=data.get("error_message", ""),
            ai_summary=data.get("ai_summary", ""),
            ai_category=data.get("ai_category", ""),
            ai_entities=ai_entities,
            ai_keywords=data.get("ai_keywords", []),
            ai_language=data.get("ai_language", ""),
            ai_doc_type=data.get("ai_doc_type", ""),
            virtual_path=data.get("virtual_path", ""),
            virtual_filename=data.get("virtual_filename", ""),
            rule_applied=data.get("rule_applied", ""),
            classification_confidence=data.get("classification_confidence", 0.0),
            chunk_count=data.get("chunk_count", 0),
            faiss_doc_id=data.get("faiss_doc_id", -1),
        )


@dataclass
class DocumentIndexSearchResult:
    """Résultat d'une recherche dans le DocumentIndex.

    Créé par rag/document_index.py, consommé par les tools agents.
    """
    record: DocumentRecord
    score: float = 0.0    # Score de similarité vectorielle (0→1)
    rank: int = 0         # Position dans les résultats (0-based)


# =============================================================================
# TOOL CALLING — ToolDefinition, ToolCall, ToolResult, ChatCompletionResult
# =============================================================================

@dataclass
class ToolParameter:
    """Paramètre d'un outil appelable par l'Orchestrateur."""
    name: str
    type: str                      # "string" | "integer" | "number" | "boolean" | "array"
    description: str = ""
    required: bool = False
    enum: list[str] = field(default_factory=list)  # Valeurs autorisées


@dataclass
class ToolDefinition:
    """Définition complète d'un outil disponible pour l'Orchestrateur.

    Produit le schéma JSON OpenAI-compatible (format tools/function calling).
    Créé dans agents/tools/*, enregistré dans agents/tool_registry.py.
    """
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    category: str = ""             # "rag" | "storage" | "llm"
    # Phase 6 — Conditions de disponibilité (évaluées sans LLM en pré-pipeline)
    requires_workspace: bool = False
    allowed_intent_types: list[str] = field(default_factory=list)   # [] = tous les intents
    excluded_phases: list[str] = field(default_factory=list)        # ex: ["concluded"]
    requires_capabilities: list[str] = field(default_factory=list)  # ex: ["storage_write"]

    def to_openai_schema(self) -> dict:
        """Retourne le schéma OpenAI function calling."""
        properties: dict = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


@dataclass
class ToolCall:
    """Appel d'outil demandé par le LLM dans la boucle agentique."""
    tool_name: str
    arguments: dict = field(default_factory=dict)
    call_id: str = ""              # OpenAI-style tool_call id


@dataclass
class ToolResult:
    """Résultat de l'exécution d'un appel d'outil."""
    tool_name: str
    call_id: str = ""
    result: str = ""               # Résultat sérialisé en JSON ou texte
    success: bool = True
    error: str = ""


@dataclass
class ChatCompletionResult:
    """Retour de AlbertClient.chat_with_tools.

    Le LLM retourne soit du texte (content), soit des tool_calls, jamais les deux.
    """
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""        # "stop" | "tool_calls"

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# =============================================================================
# CONFIGURATION CLIENT (multi-tenant)
# =============================================================================

@dataclass
class ClientConfig:
    """Configuration d'un client/tenant Colaig.

    Décrit une paire storage + messaging pour un client spécifique.
    Chargé depuis config/clients.yml (un bloc par client).

    Le LLM est partagé par défaut (hérite de ColaigConfig) ; la section
    `llm` du YAML permet un override par client (ex: clé OpenAI propre).

    Toutes les valeurs sont optionnelles sauf `client_id` — les champs
    inutilisés (dépendants du backend choisi) restent vides.
    """
    client_id: str = ""

    # --- Storage ---
    storage_backend: str = "local"      # webdav | local | s3 | bigfolder | msgraph
    # WebDAV
    storage_webdav_url: str = ""
    storage_webdav_username: str = ""
    storage_webdav_password: str = ""
    # Local filesystem
    storage_local_path: str = ""
    # S3 / MinIO
    storage_s3_endpoint: str = ""
    storage_s3_access_key: str = ""
    storage_s3_secret_key: str = ""
    storage_s3_bucket: str = ""
    storage_s3_prefix: str = ""
    storage_s3_region: str = ""
    storage_s3_session_token: str = ""
    # Bigfolder / Archivist
    storage_bigfolder_url: str = ""
    storage_bigfolder_service_token: str = ""
    storage_bigfolder_root: str = "/"
    # Microsoft Graph (OneDrive / SharePoint)
    storage_msgraph_tenant_id: str = ""
    storage_msgraph_client_id: str = ""
    storage_msgraph_client_secret: str = ""
    storage_msgraph_drive_user_id: str = ""
    storage_msgraph_drive_id: str = ""
    storage_msgraph_root_path: str = ""
    # Google Drive (Service Account JWT)
    storage_gdrive_service_account_json: str = ""
    storage_gdrive_root_folder_id: str = "root"
    storage_gdrive_impersonate_user: str = ""
    # Box (JWT Server Authentication)
    storage_box_config_file: str = ""
    storage_box_root_folder_id: str = "0"
    storage_box_user_id: str = ""

    # --- Messaging ---
    messaging_backend: str = "matrix"   # matrix | telegram
    # Matrix / Tchap
    messaging_matrix_homeserver: str = ""
    messaging_matrix_username: str = ""
    messaging_matrix_password: str = ""
    # Telegram
    messaging_telegram_token: str = ""
    messaging_telegram_webhook: str = ""

    # --- MCP Auth override (optionnel — hérite "token" si absent) ---
    mcp_auth_mode: str = ""         # "token" | "oidc" — vide = hérite du défaut
    mcp_oidc_issuer: str = ""
    mcp_oidc_audience: str = ""
    mcp_oidc_jwks_uri: str = ""

    # --- LLM override (optionnel — hérite de ColaigConfig si absent) ---
    llm_backend: str = ""       # vide = hérite de ColaigConfig
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model_chat: str = ""
    llm_model_embed: str = ""
    # Azure spécifique
    llm_azure_resource: str = ""
    llm_azure_deployment_chat: str = "gpt-4o"
    llm_azure_deployment_embed: str = "text-embedding-3-small"
    llm_azure_api_version: str = "2024-02-01"

    @classmethod
    def from_dict(cls, data: dict) -> ClientConfig:
        """Construit un ClientConfig depuis un dict YAML (structure imbriquée)."""
        cc = cls()
        cc.client_id = str(data.get("id", ""))

        s = data.get("storage", {})
        cc.storage_backend = s.get("backend", "local")
        cc.storage_webdav_url = s.get("url", "")
        cc.storage_webdav_username = s.get("username", "")
        cc.storage_webdav_password = s.get("password", "")
        cc.storage_local_path = s.get("path", "")
        cc.storage_s3_endpoint = s.get("endpoint_url", "")
        cc.storage_s3_access_key = s.get("access_key", "")
        cc.storage_s3_secret_key = s.get("secret_key", "")
        cc.storage_s3_bucket = s.get("bucket", "")
        cc.storage_s3_prefix = s.get("prefix", "")
        cc.storage_s3_region = s.get("region", "")
        cc.storage_s3_session_token = s.get("session_token", "")
        cc.storage_bigfolder_url = s.get("api_url", "")
        cc.storage_bigfolder_service_token = s.get("service_token", "") or s.get("api_key", "")
        cc.storage_bigfolder_root = s.get("root", "/")
        cc.storage_msgraph_tenant_id = s.get("tenant_id", "")
        cc.storage_msgraph_client_id = s.get("client_id", "")
        cc.storage_msgraph_client_secret = s.get("client_secret", "")
        cc.storage_msgraph_drive_user_id = s.get("drive_user_id", "")
        cc.storage_msgraph_drive_id = s.get("drive_id", "")
        cc.storage_msgraph_root_path = s.get("root_path", "")
        cc.storage_gdrive_service_account_json = s.get("service_account_json", "")
        cc.storage_gdrive_root_folder_id = s.get("root_folder_id", "root")
        cc.storage_gdrive_impersonate_user = s.get("impersonate_user", "")
        cc.storage_box_config_file = s.get("box_config_file", "") or s.get("config_file", "")
        cc.storage_box_root_folder_id = s.get("root_folder_id", "0")
        cc.storage_box_user_id = s.get("user_id", "")

        m = data.get("messaging", {})
        cc.messaging_backend = m.get("backend", "matrix")
        cc.messaging_matrix_homeserver = m.get("homeserver", "")
        cc.messaging_matrix_username = m.get("username", "")
        cc.messaging_matrix_password = m.get("password", "")
        cc.messaging_telegram_token = m.get("bot_token", "")
        cc.messaging_telegram_webhook = m.get("webhook_url", "")

        ll = data.get("llm", {})
        cc.llm_backend = ll.get("backend", "")
        cc.llm_api_url = ll.get("api_url", "")
        cc.llm_api_key = ll.get("api_key", "")
        cc.llm_model_chat = ll.get("model_chat", "")
        cc.llm_model_embed = ll.get("model_embed", "")
        cc.llm_azure_resource = ll.get("azure_resource", "")
        cc.llm_azure_deployment_chat = ll.get("azure_deployment_chat", "gpt-4o")
        cc.llm_azure_deployment_embed = ll.get("azure_deployment_embed", "text-embedding-3-small")
        cc.llm_azure_api_version = ll.get("azure_api_version", "2024-02-01")

        ma = data.get("mcp_auth", {})
        cc.mcp_auth_mode = ma.get("mode", "")
        cc.mcp_oidc_issuer = ma.get("oidc_issuer", "")
        cc.mcp_oidc_audience = ma.get("oidc_audience", "")
        cc.mcp_oidc_jwks_uri = ma.get("oidc_jwks_uri", "")

        return cc


# =============================================================================
# POLITIQUE PLATEFORME
# =============================================================================

@dataclass
class PlatformPolicy:
    """Contraintes imposées par l'opérateur plateforme sur toutes les instances.

    Chargée depuis la section `platform_policy` de clients.yml.
    Validée au démarrage pour chaque client déclaré — violation = erreur fatale.

    Listes vides = aucune contrainte (tout autorisé).
    """
    # Backends storage autorisés (ex: ["webdav", "bigfolder", "msgraph"])
    # Vide → tout backend autorisé
    allowed_storage_backends: list = field(default_factory=list)

    # URL LLM autorisées (ex: ["https://albert-api.etalab.gouv.fr"])
    # Comparaison préfixe — vide → tout endpoint autorisé
    allowed_llm_endpoints: list = field(default_factory=list)

    # Modes auth MCP autorisés (ex: ["token", "oidc"])
    # Vide → tout mode autorisé
    allowed_mcp_auth_modes: list = field(default_factory=list)

    # Si True : 401 si pas de token (pas de chatbot public)
    enforce_mcp_auth: bool = False


# =============================================================================
# CONFIGURATION GLOBALE
# =============================================================================

@dataclass
class ColaigConfig:
    """Configuration globale de l'instance Colaig.

    Chargée par config.py depuis env + YAML + defaults.
    """
    # === Backends (choix du provider) ===
    storage_backend: str = "webdav"     # webdav | bigfolder | local | s3 | msgraph | box
    messaging_backend: str = "matrix"   # matrix | slack | webchat

    # === Messaging : Matrix/Tchap ===
    matrix_homeserver: str = ""
    matrix_username: str = ""
    matrix_password: str = ""

    # === LLM : Albert API (défaut souverain) ===
    albert_api_url: str = "https://albert-api.etalab.gouv.fr"
    albert_api_key: str = ""
    albert_model_chat: str = "openai/gpt-oss-120b"
    albert_model_embed: str = "BAAI/bge-m3"
    albert_model_rerank: str = "BAAI/bge-reranker-v2-m3"
    albert_model_ocr: str = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    albert_model_audio: str = "openai/whisper-large-v3"
    # Délai (secondes) entre pages OCR vision — limite le débit background
    # Défaut 3s → ~20 req/min max, laisse de la place aux appels utilisateur
    ocr_page_delay_s: float = 3.0

    # === Capability chains (chaînes de fallback par capacité — optionnel) ===
    # Format : "provider:model,provider:model" (ordre de priorité)
    # Providers connus : albert, mistral, openai, groq, together, ollama
    # Si vide → comportement Albert par défaut (rétrocompatible)
    cap_chat: str = ""    # COLAIG_CAP_CHAT   ex: "albert:gpt-oss-120b,mistral:mistral-large"
    cap_embed: str = ""   # COLAIG_CAP_EMBED  ex: "albert:BAAI/bge-m3"
    cap_ocr: str = ""     # COLAIG_CAP_OCR    ex: "mistral:mistral-small,albert:mistral-small-albert"
    cap_rerank: str = ""  # COLAIG_CAP_RERANK (future — Albert uniquement pour l'instant)
    cap_audio: str = ""   # COLAIG_CAP_AUDIO  (future — Albert uniquement pour l'instant)

    # === LLM : Backend générique (OpenAI, Azure, Ollama, Mistral...) ===
    # LLM_BACKEND=albert → AlbertClient (défaut, rétrocompat)
    # LLM_BACKEND=openai → OpenAIClient (OpenAI, Mistral, Groq, Together AI...)
    # LLM_BACKEND=azure  → AzureClient (Azure OpenAI Service)
    # LLM_BACKEND=ollama → OllamaClient (Ollama local)
    llm_backend: str = "albert"
    llm_api_url: str = ""       # Overrides albert_api_url si llm_backend != albert
    llm_api_key: str = ""       # Overrides albert_api_key si llm_backend != albert
    llm_model_chat: str = ""    # Overrides albert_model_chat si llm_backend != albert
    llm_model_embed: str = ""   # Overrides albert_model_embed si llm_backend != albert

    # Azure OpenAI — champs spécifiques (ignorés si llm_backend != azure)
    llm_azure_resource: str = ""
    llm_azure_deployment_chat: str = "gpt-4o"
    llm_azure_deployment_embed: str = "text-embedding-3-small"
    llm_azure_api_version: str = "2024-02-01"

    # === Storage : WebDAV ===
    webdav_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""

    # === Storage : Bigfolder ===
    bigfolder_api_url: str = ""
    bigfolder_service_token: str = ""  # Service token Archivist (format: svc_xxxxx)
    bigfolder_root: str = "/"          # Chemin racine BigFolder mappé à "/" Colaig

    # === Storage : Local ===
    local_storage_path: str = ""

    # === Storage : S3 / MinIO ===
    s3_endpoint_url: str = ""       # Vide = AWS S3 standard ; sinon MinIO/Scaleway/OVH
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_prefix: str = ""             # Préfixe chemin dans le bucket (ex: "colaig/")
    s3_region: str = "us-east-1"
    s3_session_token: str = ""      # Token STS temporaire (ex: SSP Cloud / MinIO STS)

    # === Storage : Microsoft Graph (OneDrive / SharePoint) ===
    msgraph_tenant_id: str = ""     # ID tenant Azure AD
    msgraph_client_id: str = ""     # App registration client ID
    msgraph_client_secret: str = "" # App registration client secret
    msgraph_drive_user_id: str = "" # UPN ou object-id de l'utilisateur cible (ex: colaig@org.com)
    msgraph_drive_id: str = ""      # Drive ID spécifique (vide = drive par défaut de l'utilisateur)
    msgraph_root_path: str = ""     # Chemin racine dans le drive (vide = racine)

    # === Storage : Box (JWT Server Authentication) ===
    box_config_file: str = ""       # Chemin vers le JSON Box (prioritaire sur les vars individuelles)
    box_client_id: str = ""         # Client ID de l'application Box
    box_client_secret: str = ""     # Client secret de l'application Box
    box_enterprise_id: str = ""     # Enterprise ID (Admin Console → Enterprise Settings)
    box_public_key_id: str = ""     # ID de la clé publique JWT enregistrée dans Box
    box_private_key: str = ""       # Clé privée RSA PEM (avec sauts de ligne)
    box_passphrase: str = ""        # Passphrase de la clé privée RSA
    box_root_folder_id: str = "0"   # ID du dossier racine ("0" = racine compte service)
    box_user_id: str = ""           # Si renseigné : agir comme cet utilisateur Box
    box_webhook_primary_key: str = ""    # Clé HMAC-SHA256 principale (webhooks.primaryKey du JSON)
    box_webhook_secondary_key: str = ""  # Clé secondaire optionnelle (rotation)

    # === Storage : Google Drive (Service Account JWT) ===
    gdrive_service_account_json: str = ""  # Contenu JSON service account (ou chemin si commence par "/")
    gdrive_root_folder_id: str = "root"    # ID dossier racine Drive ("root" = racine du drive)
    gdrive_impersonate_user: str = ""      # Email à impersoner (domain-wide delegation, optionnel)

    # === Messaging : Telegram ===
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""  # Vide = mode polling (getUpdates)

    # === Application ===
    log_level: str = "INFO"
    data_dir: str = "/app/data"
    web_port: int = 8000

    # RAG defaults
    default_similarity_threshold: float = 0.3
    default_max_results: int = 5
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Polling
    index_refresh_interval_seconds: int = 300  # 5 minutes
    workspace_cache_ttl_seconds: int = 60      # 1 minute

    # === Phase 2 : Pipeline agents + MCP ===
    agents_enabled: bool = False   # Activer le pipeline multi-agent (Phase 2)
    mcp_enabled: bool = False      # Activer le serveur MCP
    analyser_temperature: float = 0.1   # Température LLM pour l'analyseur
    synthesiser_temperature: float = 0.3  # Température LLM pour le synthétiseur

    # === Phase 5 : Agents réels + tool calling + mémoire ===
    orchestrator_max_iterations: int = 5     # Nombre max d'itérations boucle agentique
    orchestrator_temperature: float = 0.1    # Température LLM pour l'orchestrateur agentique
    conversation_memory_max_stored: int = 100   # Nb max de messages stockés par conversation
    conversation_memory_max_retrieved: int = 10 # Nb max de messages récupérés par requête
    analyser_use_tool_calling: bool = False  # Activer le tool calling pour l'analyseur

    # === DocumentIndex (service de métadonnées décentralisé) ===
    document_index_enabled: bool = False         # Activer le service DocumentIndex
    document_index_max_pending: int = 10         # Max docs à analyser par cycle
    document_index_refresh_interval: int = 600   # Interval scan en secondes (10 min)
    document_index_cache_ttl: int = 300          # TTL cache index en mémoire (secondes)
    document_index_ai_analysis: bool = True      # Activer l'analyse IA (résumé, catégorie...)

    # === Auto-discovery de workspaces ===
    auto_discover_enabled: bool = False   # opt-in, désactivé par défaut
    auto_discover_interval: int = 600     # secondes entre deux scans (10 min)

    # === Phase 6 : Trame vivante + Profil + TaskExecutor + Modèles multi-tiers ===
    albert_model_light: str = "mistralai/Ministral-3-8B-Instruct-2512"            # Agent 1 (Analyser) + assess
    albert_model_medium: str = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"  # Agent 3 (Synthesiser)
    task_executor_max_concurrent: int = 20   # Max tâches background simultanées
    task_executor_queue_ttl: int = 1800      # TTL queues inactives en secondes (30 min)
    agents_phase6_enabled: bool = False      # Activer le pipeline Phase 6 complet

    # === RAG hybride : BM25 + RRF + Contextual Chunking + HyDE ===
    hybrid_search_enabled: bool = False      # Activer BM25 + Reciprocal Rank Fusion
    contextual_chunking_enabled: bool = False  # Activer la contextualisation LLM des chunks
    contextual_model: str = ""               # Modèle LLM pour la contextualisation (vide = albert_model_light)
    hyde_enabled: bool = False               # Activer HyDE (Hypothetical Document Embeddings)
    hyde_query_weight: float = 0.5           # Poids embedding HyDE dans la combinaison (0→1)
    rrf_k_constant: int = 60                 # Constante k du Reciprocal Rank Fusion
    local_embeddings: bool = False           # Fallback embeddings local (SentenceTransformer)

    # === Mode C : Tâches autonomes planifiées ===
    tasks_enabled: bool = False              # Activer le planificateur de tâches autonomes
    task_max_concurrent: int = 3             # Max sessions simultanées
    task_scheduler_interval: int = 60        # Secondes entre deux scans (polling)
    task_max_runs_kept: int = 10             # Max archives runs/{ts}/ par tâche
    task_session_timeout: int = 1800         # Timeout session en secondes (30 min)
    task_max_error_count: int = 3            # Désactivation auto après N échecs consécutifs

    # === Authentification MCP par token ===
    base_url: str = ""                  # URL publique de l'instance (ex: https://colaig.org.fr)
                                        # Utilisée pour générer les configs MCP clients
    mcp_auth_enabled: bool = False      # Activer l'auth token sur /mcp (false = pass-through)
    admin_user_ids: list = field(default_factory=list)
                                        # user_ids avec rôle admin (ex: ["@alice:tchap.fr"])
                                        # Miroir de COLAIG_ADMIN_USER_IDS — utilisé pour
                                        # l'affichage dans colaig_get_config uniquement.
                                        # TokenManager lit l'env directement (sans couplage).

    # === Workspace par défaut (chatbot / non authentifié) ===
    default_workspace_id: str = ""      # Si renseigné, remplace _default pour les sessions
                                        # sans workspace (chatbot mode + MCP non authentifié)
                                        # ex: "accueil" → workspace d'accueil avec RAG

    # === OIDC SSO (auth MCP enterprise) ===
    # Activer via COLAIG_MCP_AUTH_MODE=oidc + les 3 variables ci-dessous.
    # Requis : pip install 'colaig[oidc]'  (PyJWT[crypto])
    mcp_auth_mode: str = "token"        # "token" (défaut) | "oidc"
    oidc_issuer: str = ""               # URL issuer IdP. Ex: https://sso.org.fr/realms/org
    oidc_audience: str = ""             # Audience JWT.   Ex: colaig-instance-rh
    oidc_jwks_uri: str = ""             # URL endpoint JWKS de l'IdP
