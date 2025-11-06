# Dockerfile pour WebExtract Service
FROM python:3.11-slim

LABEL maintainer="WebExtract Team"
LABEL description="Service autonome d'extraction de contenu web"

# Installer les dépendances système nécessaires pour Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Dépendances Playwright/Chromium
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    # X virtual framebuffer pour mode headless
    xvfb \
    # Outils de debug (optionnel)
    curl \
    && rm -rf /var/lib/apt/lists/*

# Créer un utilisateur non-root
RUN useradd -m -u 1000 webextract && \
    mkdir -p /app && \
    chown -R webextract:webextract /app

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de dépendances
COPY --chown=webextract:webextract pyproject.toml ./

# Installer les dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Installer Playwright et les navigateurs
RUN python -m playwright install chromium && \
    python -m playwright install-deps chromium

# Copier le code de l'application
COPY --chown=webextract:webextract app ./app

# Passer à l'utilisateur non-root
USER webextract

# Variables d'environnement par défaut
ENV PLAYWRIGHT_HEADLESS=true \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

# Exposer le port de l'API
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Commande de démarrage avec xvfb pour le mode headless
CMD ["xvfb-run", "-a", "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
