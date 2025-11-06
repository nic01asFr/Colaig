"""
Modèles de données pour l'API WebExtract.
"""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, HttpUrl


class LLMConfig(BaseModel):
    """Configuration du LLM à utiliser pour l'extraction."""

    provider: Literal["openai", "anthropic", "albert"] = Field(
        default="openai",
        description="Provider LLM à utiliser"
    )
    api_key: str = Field(
        description="Clé API pour le provider LLM"
    )
    model: str = Field(
        default="gpt-4o",
        description="Nom du modèle à utiliser"
    )
    base_url: Optional[str] = Field(
        default=None,
        description="URL de base pour l'API (optionnel)"
    )


class ExtractionOptions(BaseModel):
    """Options pour l'extraction."""

    use_agent: bool = Field(
        default=True,
        description="Utiliser l'agent IA ou extraction directe seulement"
    )
    timeout: int = Field(
        default=45,
        ge=5,
        le=300,
        description="Timeout en secondes pour l'extraction"
    )
    headless: bool = Field(
        default=True,
        description="Utiliser le navigateur en mode headless"
    )
    enable_ocr: bool = Field(
        default=False,
        description="Activer l'extraction OCR pour les images"
    )


class ExtractRequest(BaseModel):
    """Requête d'extraction de contenu web."""

    url: str = Field(
        description="URL de la page à extraire"
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Prompt personnalisé pour l'extraction (optionnel)"
    )
    extraction_type: Literal["general", "article", "product", "repository", "documentation"] = Field(
        default="general",
        description="Type d'extraction à effectuer"
    )
    llm_config: Optional[LLMConfig] = Field(
        default=None,
        description="Configuration du LLM (optionnel, utilise la config par défaut si non fourni)"
    )
    options: Optional[ExtractionOptions] = Field(
        default=None,
        description="Options d'extraction (optionnel)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com/article",
                "prompt": "Extrait le contenu principal de cet article",
                "extraction_type": "article",
                "llm_config": {
                    "provider": "openai",
                    "api_key": "sk-...",
                    "model": "gpt-4o"
                },
                "options": {
                    "use_agent": True,
                    "timeout": 45,
                    "headless": True,
                    "enable_ocr": False
                }
            }
        }


class WebResult(BaseModel):
    """Résultat d'une extraction de contenu web."""

    success: bool = Field(
        description="Indique si l'extraction a réussi"
    )
    url: str = Field(
        description="URL de la page extraite"
    )
    content_type: Optional[str] = Field(
        default=None,
        description="Type de contenu détecté"
    )
    title: Optional[str] = Field(
        default=None,
        description="Titre de la page"
    )
    content: Optional[str] = Field(
        default=None,
        description="Contenu extrait"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles"
    )
    error: Optional[str] = Field(
        default=None,
        description="Message d'erreur si l'extraction a échoué"
    )

    @classmethod
    def from_error(cls, url: str, error_message: str) -> "WebResult":
        """Crée un résultat d'erreur."""
        return cls(
            success=False,
            url=url,
            error=error_message
        )

    @classmethod
    def from_success(
        cls,
        url: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> "WebResult":
        """Crée un résultat de succès."""
        return cls(
            success=True,
            url=url,
            content_type=content_type,
            title=title,
            content=content,
            metadata=metadata or {}
        )


class ExtractResponse(BaseModel):
    """Réponse de l'API d'extraction."""

    success: bool
    url: str
    content_type: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def from_web_result(cls, result: WebResult) -> "ExtractResponse":
        """Convertit un WebResult en ExtractResponse."""
        return cls(
            success=result.success,
            url=result.url,
            content_type=result.content_type,
            title=result.title,
            content=result.content,
            metadata=result.metadata,
            error=result.error
        )

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "url": "https://example.com/article",
                "content_type": "article",
                "title": "Titre de l'article",
                "content": "Contenu extrait de l'article...",
                "metadata": {
                    "extraction_method": "agent",
                    "extraction_duration_ms": 3500,
                    "content_length": 5000
                },
                "error": None
            }
        }


class HealthResponse(BaseModel):
    """Réponse du endpoint de santé."""

    status: str = Field(description="Statut du service")
    version: str = Field(description="Version du service")
    playwright_available: bool = Field(description="Playwright est-il disponible?")
