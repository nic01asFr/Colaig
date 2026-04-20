# Colaig — Image Docker all-in-one
# Zero Database : pas de PostgreSQL, Redis, Qdrant, ou autre service
# Tout est sur WebDAV (Nextcloud/Bnum)

FROM python:3.11-slim

WORKDIR /app

# Dépendances système pour matrix-nio, extraction de texte et décodage audio Opus
RUN apt-get update && apt-get install -y --no-install-recommends \
    libolm-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY colaig/ colaig/
COPY config/ config/
COPY tests/ tests/
COPY pyproject.toml .

# Dev dependencies (tests)
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov

# Répertoire de données (cache local uniquement)
RUN mkdir -p /app/data/faiss_cache /app/data/logs

# Variables d'environnement par défaut
ENV COLAIG_LOG_LEVEL=INFO
ENV COLAIG_DATA_DIR=/app/data

# Port interface admin
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# Démarrage
CMD ["python", "-m", "colaig.main"]
