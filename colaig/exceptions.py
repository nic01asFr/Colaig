"""
Colaig — Hiérarchie d'exceptions

Chaque module utilise ses propres exceptions héritant de ColaigError.
Cela permet aux handlers de capturer finement les erreurs et de
fournir des messages user-friendly adaptés.
"""


class ColaigError(Exception):
    """Exception de base pour toutes les erreurs Colaig."""
    pass


class ConfigError(ColaigError):
    """Configuration invalide ou incomplete (detectee au demarrage)."""
    pass


# --- Storage (provider-agnostic) ---

class StorageError(ColaigError):
    """Erreur lors d'une opération sur le backend de stockage."""
    pass

class StorageFileNotFoundError(StorageError):
    """Fichier ou dossier introuvable dans le storage (404)."""
    pass

class StorageAuthError(StorageError):
    """Erreur d'authentification storage (401/403)."""
    pass

# Alias de rétrocompatibilité
WebDAVError = StorageError
WebDAVNotFoundError = StorageFileNotFoundError
WebDAVAuthError = StorageAuthError


# --- LLM (provider-agnostic) ---

class LLMError(ColaigError):
    """Erreur lors d'un appel à un backend LLM (Albert, OpenAI, Azure, Ollama...)."""
    pass

class QuotaExceededError(LLMError):
    """Quota d'usage LLM par tenant dépassé (limite journalière requêtes/tokens)."""
    pass

class LLMRateLimitError(LLMError):
    """Rate limit atteint sur le backend LLM (429).

    retry_after : délai (secondes) suggéré par l'API via header Retry-After.
    None si le header est absent.
    """
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after

class LLMUnavailableError(LLMError):
    """Backend LLM indisponible (503/timeout)."""
    pass

# Aliases rétrocompatibilité Albert
AlbertError = LLMError
AlbertRateLimitError = LLMRateLimitError
AlbertUnavailableError = LLMUnavailableError


# --- Messaging (provider-agnostic) ---

class MessagingError(ColaigError):
    """Erreur de communication sur le canal de messagerie."""
    pass

# Alias de rétrocompatibilité
MatrixError = MessagingError


# --- RAG ---

class DocumentIndexError(ColaigError):
    """Erreur liée au service DocumentIndex (métadonnées de fichiers)."""
    pass

class DocumentAnalysisError(DocumentIndexError):
    """Erreur lors de l'analyse IA d'un document (Albert API)."""
    pass

class DocumentIndexNotFoundError(DocumentIndexError):
    """Registry DocumentIndex introuvable pour ce workspace."""
    pass

class FaissIndexError(ColaigError):
    """Erreur liée à l'index FAISS."""
    pass

class IndexNotFoundError(FaissIndexError):
    """Index FAISS introuvable pour ce workspace."""
    pass

class ChunkingError(ColaigError):
    """Erreur lors du chunking d'un document."""
    pass

class EmbeddingError(ColaigError):
    """Erreur lors du calcul d'embeddings."""
    pass


# --- Contexte ---

class ContextResolutionError(ColaigError):
    """Erreur lors de la résolution du contexte."""
    pass

class WorkspaceConfigError(ColaigError):
    """Configuration de workspace invalide ou corrompue."""
    pass


# --- Génération ---

class GenerationError(ColaigError):
    """Erreur lors de la génération de réponse."""
    pass


# --- Pipeline Agents (Phase 2+) ---

class AnalysisError(ColaigError):
    """Erreur lors de l'analyse d'intention (Analyseur)."""
    pass

class OrchestrationError(ColaigError):
    """Erreur lors de l'orchestration (Orchestrateur)."""
    pass

class ToolExecutionError(ColaigError):
    """Erreur lors de l'exécution d'un outil dans l'Orchestrateur agentique."""
    pass

class SynthesisError(ColaigError):
    """Erreur lors de la synthèse de réponse (Synthétiseur)."""
    pass


# --- MCP ---

class MCPError(ColaigError):
    """Erreur liée au serveur MCP."""
    pass


# --- Workspace (accès et existence) ---

class WorkspaceAccessDenied(ColaigError):
    """Levée quand un utilisateur tente d'accéder à un workspace sans droits."""

    def __init__(self, message_or_user_id: str = "", workspace_id: str = "") -> None:
        """Constructeur flexible — supporte deux signatures :

        Signature simple (acl.py) :
            WorkspaceAccessDenied("message d'erreur")

        Signature legacy (workspace_delegate.py) :
            WorkspaceAccessDenied(user_id="@alice:tchap.fr", workspace_id="rh")
        """
        if workspace_id:
            # Signature legacy : (user_id, workspace_id)
            super().__init__(
                f"Accès refusé : {message_or_user_id!r} n'a pas accès au workspace {workspace_id!r}"
            )
            self.user_id: str = message_or_user_id
            self.workspace_id: str = workspace_id
        else:
            super().__init__(message_or_user_id)
            self.user_id = ""
            self.workspace_id = ""


class WorkspaceNotFound(ColaigError):
    """Levée quand le workspace cible n'existe pas."""

    def __init__(self, workspace_id: str = "") -> None:
        msg = f"Workspace introuvable : {workspace_id!r}" if workspace_id else "Workspace introuvable"
        super().__init__(msg)
        self.workspace_id = workspace_id
