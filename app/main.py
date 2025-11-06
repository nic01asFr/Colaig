"""
Application principale FastAPI pour WebExtract Service.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.endpoints import extract
from app.api.models import HealthResponse
from app.core.browser.playwright_manager import ensure_playwright_installed, is_playwright_available

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application."""
    # Startup
    logger.info("Démarrage de WebExtract Service...")

    # Initialiser Playwright
    playwright_ok = await ensure_playwright_installed()
    if playwright_ok:
        logger.info("Playwright initialisé avec succès")
    else:
        logger.warning("Playwright non disponible - extraction limitée")

    yield

    # Shutdown
    logger.info("Arrêt de WebExtract Service...")


# Créer l'application FastAPI
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="""
Service autonome d'extraction de contenu web avec support multi-LLM.

## Fonctionnalités

- **Extraction Multi-Stratégies** : Direct Playwright, Agent IA, HTTP fallback
- **Support Multi-LLM** : OpenAI, Anthropic (Claude), Albert
- **Détection Automatique** : Identifie le type de contenu (article, produit, etc.)
- **Prompts Optimisés** : Templates spécialisés par type de contenu

## Utilisation

Envoyez une requête POST à `/api/v1/extract` avec :
- `url` : URL à extraire
- `extraction_type` : Type de contenu (optionnel, détection auto)
- `llm_config` : Configuration du LLM (optionnel, utilise config par défaut)
- `options` : Options d'extraction (timeout, headless, etc.)

## Documentation

- Documentation interactive : `/docs`
- Schéma OpenAPI : `/openapi.json`
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # À restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclure les routers
app.include_router(
    extract.router,
    prefix="/api/v1",
    tags=["extraction"]
)


@app.get("/", response_class=JSONResponse)
async def root():
    """Endpoint racine."""
    return {
        "service": "WebExtract Service",
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse, tags=["monitoring"])
async def health_check():
    """Endpoint de santé du service."""
    return HealthResponse(
        status="healthy",
        version=settings.api_version,
        playwright_available=is_playwright_available()
    )


@app.get("/ready", response_class=JSONResponse, tags=["monitoring"])
async def readiness_check():
    """Endpoint de readiness pour Kubernetes."""
    playwright_ready = is_playwright_available()

    if playwright_ready:
        return {"status": "ready", "playwright": "available"}
    else:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "playwright": "unavailable"}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower()
    )
