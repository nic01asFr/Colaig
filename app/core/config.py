"""
Configuration pour le service WebExtract.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration du service WebExtract."""

    # API Configuration
    api_title: str = "WebExtract Service"
    api_version: str = "1.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Default LLM Configuration
    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4o"
    default_llm_api_key: str = ""
    default_llm_base_url: Optional[str] = None

    # Provider-specific configurations
    # Albert
    albert_api_url: str = "https://albert.api.etalab.gouv.fr"
    albert_api_key: str = ""
    albert_model: str = "AgentPublic/llama3-instruct-8b"

    # OpenAI
    openai_api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Playwright Configuration
    playwright_headless: bool = True
    playwright_browser_pool_size: int = 3
    playwright_timeout: int = 45000  # milliseconds

    # Extraction Configuration
    default_extraction_timeout: int = 45  # seconds
    max_retries: int = 3
    enable_caching: bool = True
    cache_ttl_seconds: int = 3600

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Performance
    max_concurrent_extractions: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Instance globale de configuration
settings = Settings()
