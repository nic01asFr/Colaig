FROM python:3.11-slim

WORKDIR /code
COPY . .

# Installer les dépendances système nécessaires pour Playwright avec des outils de débogage
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libglib2.0-dev libnss3 libnss3-dev libnspr4 \
    libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libexpat1 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libgbm1 libxcb1 libxkbcommon0 libpango-1.0-0 libcairo2 \
    libasound2 libatspi2.0-0 xvfb fonts-liberation xauth \
    procps htop iputils-ping curl vim \
    && rm -rf /var/lib/apt/lists/*

# Installer wheel explicitement avant les autres dépendances
RUN pip install --upgrade pip wheel setuptools --timeout 1000 && \
    pip install --timeout 1000 --retries 5 -e .

# Variables d'environnement pour Playwright en mode headless
ENV PLAYWRIGHT_HEADLESS=true \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=0 \
    DISPLAY=:99

# Installer et configurer Playwright avec les variables d'environnement pour headless
RUN python -m playwright install chromium

# Configuration spécifique pour browser-use
RUN mkdir -p /code/.browser-use && \
    echo '{\n  "playwright": {\n    "browser": "chromium",\n    "launchOptions": {\n      "headless": true,\n      "args": ["--no-sandbox"]\n    }\n  }\n}' > /code/.browser-use/config.json

# Créer un script de démarrage qui configure correctement Xvfb et lance l'application
RUN echo '#!/bin/bash\n\
# Nettoyer les anciens locks de Xvfb\n\
rm -f /tmp/.X*-lock\n\
rm -f /tmp/.X11-unix/X*\n\
\n\
# Démarrer Xvfb en arrière-plan\n\
Xvfb :99 -screen 0 1280x960x24 -ac &\n\
XVFB_PID=$!\n\
\n\
# Attendre que Xvfb soit prêt\n\
sleep 2\n\
\n\
# Vérifier que Xvfb est bien démarré\n\
if ! ps -p $XVFB_PID > /dev/null; then\n\
    echo "Erreur: Xvfb n'\''a pas pu démarrer correctement"\n\
    exit 1\n\
fi\n\
\n\
echo "Xvfb démarré avec succès sur DISPLAY=:99"\n\
\n\
# Exécuter l'\''application Python avec DISPLAY défini\n\
cd /code && DISPLAY=:99 python -m app "$@"\n\
\n\
# Capturer le code de retour de l'\''application\n\
APP_EXIT_CODE=$?\n\
\n\
# Arrêter Xvfb\n\
kill $XVFB_PID\n\
\n\
# Nettoyer les locks après arrêt\n\
rm -f /tmp/.X*-lock\n\
rm -f /tmp/.X11-unix/X*\n\
\n\
# Retourner le code de sortie de l'\''application\n\
exit $APP_EXIT_CODE' > /code/start.sh && \
    chmod +x /code/start.sh

WORKDIR /code
ENV PYTHONPATH=/code

# Utiliser le script de démarrage
CMD ["/code/start.sh"]