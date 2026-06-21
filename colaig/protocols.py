"""
Colaig — Contrats d'interface (Protocol classes)

CE FICHIER EST LE CONTRAT ENTRE TOUS LES MODULES.
Chaque agent Claude Code implémente les Protocols qui lui sont assignés.
Chaque agent consomme UNIQUEMENT via les Protocols, jamais via les implémentations.

L'injection de dépendances concrètes se fait dans main.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

# =============================================================================
# INTÉGRATIONS — Clients externes
# =============================================================================

@runtime_checkable
class StorageProtocol(Protocol):
    """Interface abstraite pour tout backend de stockage.

    Remplace WebDAVClientProtocol. Le code métier de Colaig (RAG, indexer,
    resolver, workspace) utilise UNIQUEMENT cette interface.

    L'implémentation concrète (WebDAV, Bigfolder, Local, S3) est injectée
    dans main.py selon la configuration STORAGE_BACKEND.

    Exemple :
        files = await storage.list_files("/espace-projet/documents/")
        content = await storage.download("/espace-projet/documents/guide.pdf")
        await storage.upload("/espace-projet/.colaig/indexes/docs.faiss", index_bytes)
    """

    async def list_files(self, path: str, recursive: bool = False) -> list:
        """Liste les fichiers à un chemin. Retourne List[StorageFile]."""
        ...

    async def download(self, path: str) -> bytes:
        """Télécharge le contenu d'un fichier."""
        ...

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        """Télécharge seulement si etag a changé. None si inchangé."""
        ...

    async def upload(self, path: str, content: bytes) -> None:
        """Upload un fichier (crée ou écrase)."""
        ...

    async def mkdir(self, path: str) -> None:
        """Crée un répertoire (et parents si nécessaire)."""
        ...

    async def exists(self, path: str) -> bool:
        """Vérifie si un chemin existe."""
        ...

    async def get_etag(self, path: str) -> str | None:
        """Retourne l'etag d'un fichier (None si inexistant)."""
        ...

    async def delete(self, path: str) -> None:
        """Supprime un fichier ou répertoire."""
        ...


# Alias de rétrocompatibilité (sera retiré après migration complète)
WebDAVClientProtocol = StorageProtocol


@runtime_checkable
class AlbertClientProtocol(Protocol):
    """Client Albert API — LLM souverain.

    Responsable des appels au LLM (chat) et au service d'embeddings.
    Implémenté par : colaig/integrations/albert.py

    Exemple :
        response = await albert.chat([
            {"role": "system", "content": "Tu es Colaig..."},
            {"role": "user", "content": "Quelle procédure pour..."}
        ])
        vector = await albert.embed("texte à vectoriser")
    """

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> str:
        """Appel chat completions. Retourne le texte de la réponse.

        Args:
            priority: "user" (défaut) ou "background". Les appels "background"
                (OCR, indexation) acquièrent un sémaphore limité (N-1 slots) pour
                toujours laisser au moins 1 slot libre aux requêtes utilisateur.
        """
        ...

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        priority: str = "user",
    ) -> AsyncIterator[str]:
        """Appel chat completions en streaming."""
        ...

    async def embed(self, text: str) -> list[float]:
        """Génère l'embedding d'un texte unique."""
        ...

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Génère les embeddings d'une liste de textes par batch."""
        ...

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        tool_choice: str = "auto",
        priority: str = "user",
    ) -> object:
        """Chat completions avec tool calling (format OpenAI). Retourne ChatCompletionResult."""
        ...


# Alias générique — tous les backends LLM implémentent ce contrat
LLMClientProtocol = AlbertClientProtocol


@runtime_checkable
class MessagingProtocol(Protocol):
    """Interface abstraite pour tout canal de communication.

    Remplace MatrixBotProtocol. Le code métier de Colaig (handlers)
    utilise UNIQUEMENT cette interface.

    L'implémentation concrète (Matrix/Tchap, Slack, WebChat) est injectée
    dans main.py selon la configuration MESSAGING_BACKEND.

    Exemple :
        messaging = MatrixMessaging(config)
        messaging.on_message(my_handler)
        await messaging.connect()
        await messaging.run()
    """

    async def connect(self) -> None:
        """Connexion au service de messagerie."""
        ...

    async def run(self) -> None:
        """Boucle d'écoute infinie des messages entrants."""
        ...

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        """Envoie un message dans une conversation.

        Args:
            conversation_id: Identifiant de la conversation (room_id Matrix, channel Slack, etc.)
            text: Texte brut du message.
            formatted: Version HTML/Markdown formatée (optionnel).
            is_status: Si True, message de statut/notice — rendu moins prominent si le backend
                       le supporte (ex: m.notice dans Matrix/Tchap).
        """
        ...

    async def send_typing(self, conversation_id: str, typing: bool = True, timeout: int = 10000) -> None:
        """Envoie/arrête l'indicateur de frappe."""
        ...

    def on_message(self, callback) -> None:
        """Enregistre un callback appelé pour chaque message reçu.

        Le callback reçoit un IncomingMessage.
        """
        ...


# Alias de rétrocompatibilité (sera retiré après migration complète)
MatrixBotProtocol = MessagingProtocol


# =============================================================================
# RAG — Pipeline de recherche documentaire
# =============================================================================

@runtime_checkable
class EmbeddingServiceProtocol(Protocol):
    """Service de calcul d'embeddings.

    Abstrait la source d'embeddings (Albert API, SentenceTransformer local, etc.)
    Implémenté par : colaig/rag/embeddings.py
    """

    async def embed_text(self, text: str) -> list[float]:
        """Embedding d'un texte unique. Retourne vecteur normalisé L2."""
        ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embedding batch. Retourne vecteurs normalisés L2."""
        ...

    @property
    def dimension(self) -> int:
        """Dimension des vecteurs produits."""
        ...


@runtime_checkable
class ChunkerProtocol(Protocol):
    """Service de découpage de documents en chunks.

    Implémenté par : colaig/rag/chunker.py
    """

    def chunk_document(
        self,
        content: str,
        source_path: str,
        doc_type: str = "text",
        metadata: dict | None = None,
    ) -> list:
        """Découpe un document en chunks. Retourne List[DocumentChunk]."""
        ...


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Gestion d'index vectoriel FAISS.

    Responsable du stockage, recherche, et persistance des vecteurs.
    Implémenté par : colaig/rag/faiss_store.py
    """

    def add(self, embeddings: list[list[float]], metadata: list) -> None:
        """Ajoute des vecteurs avec leurs métadonnées (List[DocumentChunk])."""
        ...

    def search(self, query_embedding: list[float], k: int = 5) -> list:
        """Recherche les k vecteurs les plus similaires. Retourne List[SearchResult]."""
        ...

    def save(self, directory: str) -> None:
        """Sérialise index + métadonnées sur disque (2 fichiers : .faiss + .pkl)."""
        ...

    def load(self, directory: str) -> None:
        """Désérialise index + métadonnées depuis disque."""
        ...

    def delete_by_source(self, source_path: str) -> int:
        """Supprime tous les vecteurs d'un document source. Retourne le nombre supprimé."""
        ...

    @property
    def count(self) -> int:
        """Nombre de vecteurs dans l'index."""
        ...


@runtime_checkable
class RetrieverProtocol(Protocol):
    """Service de recherche documentaire hybride.

    Combine recherche vectorielle + reranking + boost métier.
    Implémenté par : colaig/rag/retriever.py
    """

    async def retrieve(
        self,
        query: str,
        k: int = 5,
        score_threshold: float = 0.3,
    ) -> list:
        """Recherche les documents les plus pertinents. Retourne List[SearchResult]."""
        ...

    def set_store(self, store: VectorStoreProtocol) -> None:
        """Configure le VectorStore à utiliser (changement de workspace)."""
        ...


# =============================================================================
# DOCUMENT INDEX — Métadonnées de fichiers décentralisées
# =============================================================================

@runtime_checkable
class DocumentIndexProtocol(Protocol):
    """Service de métadonnées de fichiers décentralisé.

    Un index par workspace, stocké dans {workspace}/.colaig/indexes/documents/.
    Remplace une table pgvector centralisée par des fichiers FAISS + JSON portables.
    Implémenté par : colaig/rag/document_index.py

    Exemple :
        results = await doc_index.search("procédures RH", workspace_path="/espace-rh/")
        docs = await doc_index.list_documents("/espace-rh/", filters={"ai_category": "guide"})
        record = await doc_index.get_document("/espace-rh/", "/espace-rh/guide.pdf")
    """

    async def scan_workspace(self, workspace_path: str) -> int:
        """Scanne le workspace et enregistre les fichiers nouveaux/modifiés.

        Compare les etags du storage avec le registry interne.
        Les nouveaux fichiers et ceux avec etag modifié passent à status=PENDING.

        Returns:
            Nombre de fichiers nouveaux ou modifiés détectés.
        """
        ...

    async def analyze_pending(
        self,
        workspace_path: str,
        max_docs: int = 10,
    ) -> int:
        """Analyse IA des documents en statut PENDING.

        Pour chaque document : download → extract_text → Albert → embedding → FAISS.
        Après analyse réussie : status=ANALYZED.

        Args:
            max_docs: Nombre maximum de documents à analyser par appel.

        Returns:
            Nombre de documents analysés avec succès.
        """
        ...

    async def search(
        self,
        query: str,
        workspace_path: str,
        k: int = 5,
        filters: dict | None = None,
    ) -> list:
        """Recherche sémantique + filtres structurés. Retourne List[DocumentIndexSearchResult].

        Args:
            query: Texte de recherche (embedding calculé pour la similarité).
            workspace_path: Workspace cible.
            k: Nombre de résultats maximum.
            filters: Filtres structurés optionnels, ex: {"ai_category": "guide", "status": "analyzed"}.
        """
        ...

    async def list_documents(
        self,
        workspace_path: str,
        filters: dict | None = None,
        limit: int = 50,
    ) -> list:
        """Liste les documents avec filtres optionnels. Retourne List[DocumentRecord].

        Filtres supportés : status, ai_category, ai_language, name_contains, path_contains.
        Tri par défaut : analyzed_at décroissant.
        """
        ...

    async def get_document(
        self,
        workspace_path: str,
        doc_path: str,
    ) -> object | None:
        """Récupère le DocumentRecord d'un fichier par son path exact.

        Returns:
            DocumentRecord ou None si le document n'est pas dans le registry.
        """
        ...

    async def save(self, workspace_path: str) -> None:
        """Persiste index.faiss + metadata.pkl + registry.json sur le storage."""
        ...

    async def load(self, workspace_path: str) -> bool:
        """Charge l'index depuis le storage.

        Returns:
            True si l'index a été chargé, False si inexistant.
        """
        ...


# =============================================================================
# CONTEXTE — Résolution et construction
# =============================================================================

@runtime_checkable
class ContextResolverProtocol(Protocol):
    """Résolveur de contexte — le cerveau de Colaig.

    Pour chaque message, identifie le workspace et construit le contexte.
    Implémenté par : colaig/context/resolver.py
    """

    async def resolve(self, message) -> object:
        """Résout le contexte pour un message. Retourne WorkspaceContext."""
        ...

    async def refresh_cache(self) -> None:
        """Force le rechargement du cache de mapping conversation→workspace."""
        ...


# =============================================================================
# GÉNÉRATION — Pipeline simple Phase 1
# =============================================================================

@runtime_checkable
class GeneratorProtocol(Protocol):
    """Service de génération de réponses (Phase 1).

    Pipeline simple : contexte + résultats RAG → prompt → Albert → réponse.
    Implémenté par : colaig/rag/generator.py

    Sera complété en Phase 2 par le pipeline multi-agent
    (AnalyserProtocol → OrchestratorProtocol → SynthesiserProtocol)
    dont les contrats sont définis ci-dessous.
    """

    async def generate(
        self,
        query: str,
        context: object,  # WorkspaceContext
        search_results: list,  # List[SearchResult]
        conversation_history: list | None = None,
    ) -> object:
        """Génère une réponse. Retourne GeneratedResponse."""
        ...


# =============================================================================
# PIPELINE AGENTS — Phase 2+ (Analyseur → Orchestrateur → Synthétiseur)
# Contrats préparés, non utilisés en Phase 1.
# =============================================================================

@runtime_checkable
class AnalyserProtocol(Protocol):
    """Agent Analyseur — comprend l'intention du message.

    Premier maillon du pipeline. Reçoit le message brut + contexte,
    produit une Intent structurée (type, entités, reformulation).
    Implémenté par : colaig/agents/analyser.py
    """

    async def analyse(
        self,
        message: object,       # IncomingMessage
        context: object,       # WorkspaceContext
    ) -> object:
        """Analyse un message. Retourne Intent."""
        ...


@runtime_checkable
class OrchestratorProtocol(Protocol):
    """Agent Orchestrateur — planifie et exécute les étapes.

    Deuxième maillon. Reçoit l'Intent + contexte, construit un plan
    d'exécution, puis exécute chaque étape et collecte les résultats.
    Implémenté par : colaig/agents/orchestrator.py
    """

    async def execute(
        self,
        intent: object,        # Intent
        context: object,       # WorkspaceContext
    ) -> object:
        """Planifie et exécute. Retourne ExecutionPlan."""
        ...


@runtime_checkable
class SynthesiserProtocol(Protocol):
    """Agent Synthétiseur — formate la réponse finale.

    Dernier maillon. Reçoit le plan exécuté, construit le prompt final,
    appelle le LLM, et formate la réponse avec sources.
    Implémenté par : colaig/agents/synthesiser.py
    """

    async def synthesise(
        self,
        plan: object,          # ExecutionPlan
        context: object,       # WorkspaceContext
        conversation_history: list | None = None,
    ) -> object:
        """Synthétise les résultats. Retourne GeneratedResponse."""
        ...


# =============================================================================
# PIPELINE PHASE 6 — Trame, PreExecution, TaskExecutor, ProgressReporter
# =============================================================================

@runtime_checkable
class TrameManagerProtocol(Protocol):
    """Gestion de la trame vivante par conversation.

    Seul composant autorisé à écrire la trame.
    Les agents signalent via leurs outputs ; TrameManager applique les mises à jour.
    Implémenté par : colaig/agents/trame_manager.py
    """

    async def load(
        self,
        conv_id: str,
        workspace_path: str,
    ) -> object:
        """Charge la trame d'une conversation. Retourne ConversationTrame.

        Si absente : retourne une ConversationTrame vide initialisée (discovery, no anchors).
        """
        ...

    async def update(
        self,
        trame: object,    # ConversationTrame
        intent: object,   # Intent
        plan: object,     # ExecutionPlan
        response: object, # GeneratedResponse
    ) -> object:
        """Met à jour la trame avec les résultats du pipeline. Retourne ConversationTrame.

        Met à jour : conversation_phase, context_anchors, turn_count, updated_at.
        """
        ...

    async def save(
        self,
        trame: object,         # ConversationTrame
        workspace_path: str,
    ) -> None:
        """Persiste la trame dans le storage via StorageProtocol.

        No-op si storage_readonly=True (trame en mémoire uniquement).
        """
        ...


@runtime_checkable
class PreExecutionBuilderProtocol(Protocol):
    """Constructeur de la PreExecutionCard avant le pipeline.

    Construit la configuration complète du pipeline (behavior, skills, tools, embedding)
    avant que l'Analyser ne tourne.
    Implémenté par : colaig/agents/pre_execution.py
    """

    async def build(
        self,
        message: object,           # IncomingMessage
        trame: object,             # ConversationTrame
        workspace_context: object, # WorkspaceContext
    ) -> object:
        """Construit la PreExecutionCard avant le pipeline. Retourne PreExecutionCard.

        Opérations :
        - embed(message.body) → 1 appel Albert réutilisé pour behaviors + skills FAISS
        - behaviors.faiss.search() → behavior actif
        - skills.faiss.search() → top-k skills pertinents
        - filter_tools() → tools disponibles (déclaratif + behavior.tools.disable)
        - fixed_context = {config, identity, behavior_config}
        """
        ...

    async def execute_retrieval(
        self,
        search_directives: object, # SearchDirectives
        pre_exec: object,          # PreExecutionCard
        workspace_context: object, # WorkspaceContext
    ) -> dict:
        """Exécute le plan de récupération multi-source. Retourne dict de résultats.

        Retourne : {"chunks": [...], "docs": [...], "skills": [...], "history": [...]}
        Chaque clé correspond à une source dans SearchDirectives.
        """
        ...


@runtime_checkable
class TaskExecutorProtocol(Protocol):
    """Exécuteur de tâches async non-bloquant (Phase 6).

    Messages d'une même conversation → traitement séquentiel (cohérence trame).
    Messages de conversations différentes → traitement parallèle.
    Implémenté par : colaig/tasks/executor.py
    """

    async def submit(
        self,
        coro: object,          # coroutine à exécuter
        task_id: str,
        conv_id: str,
        on_complete: object,   # callback(result) → None
        on_error: object,      # callback(exception) → None, optionnel
    ) -> object:
        """Soumet une tâche pour exécution. Retourne TaskHandle (non-bloquant).

        La coroutine est placée dans la queue de la conversation et exécutée
        dès que les tâches précédentes de cette conversation sont terminées.
        """
        ...


@runtime_checkable
class ProgressReporterProtocol(Protocol):
    """Émetteur de messages intermédiaires pendant le pipeline (Phase 6).

    Envoie des messages formatés selon le canal (ChannelFormat) pour informer
    l'utilisateur de l'avancement : analyse, recherche, rédaction.
    Implémenté par : colaig/messaging/progress.py
    """

    async def report(self, phase: str, message: str) -> None:
        """Envoie un message intermédiaire formaté pour le canal.

        Args:
            phase: Phase du pipeline ("thinking", "retrieving", "synthesizing")
            message: Message lisible pour l'utilisateur.
        """
        ...

    async def report_tool_use(self, tool_name: str, detail: str = "") -> None:
        """Notifie l'utilisation d'un outil dans la boucle agentique.

        Args:
            tool_name: Nom de l'outil appelé.
            detail: Détail optionnel (ex: nom du document consulté).
        """
        ...
