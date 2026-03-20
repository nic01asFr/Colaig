#!/usr/bin/env bash
# =============================================================
# Colaig Platform - Script de lancement Docker local
# Usage: ./scripts/run-platform.sh [build|start|stop|logs|status]
# =============================================================
set -euo pipefail

COMPOSE_FILE="docker-compose.platform.yml"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"

# --- Helpers -------------------------------------------------
info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[1;31m[ERR]\033[0m   $*" >&2; }

check_docker() {
    if ! command -v docker &>/dev/null; then
        err "Docker n'est pas installé. Installez-le : https://docs.docker.com/get-docker/"
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        info "Docker daemon ne tourne pas, tentative de démarrage..."
        sudo dockerd &>/dev/null &
        sleep 3
        if ! docker info &>/dev/null 2>&1; then
            err "Impossible de démarrer le daemon Docker."
            exit 1
        fi
    fi
    ok "Docker est opérationnel"
}

ensure_env() {
    if [ ! -f "$ENV_FILE" ]; then
        info "Création du fichier .env avec des valeurs par défaut..."
        SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -base64 48)
        cat > "$ENV_FILE" <<EOF
# --- Colaig Platform - Configuration locale ---
# Généré automatiquement par run-platform.sh

# Session & Admin
PLATFORM_SESSION_SECRET=${SESSION_SECRET}
PLATFORM_ADMIN_PASSWORD=admin123
PLATFORM_OPERATOR_EMAILS=admin@example.gouv.fr
PLATFORM_DB_PATH=/code/data/platform.db
PLATFORM_PORT=8080

# ProConnect désactivé pour le dev local
PROCONNECT_ENABLED=false
PROCONNECT_CLIENT_ID=
PROCONNECT_CLIENT_SECRET=
PROCONNECT_REDIRECT_URI=
PROCONNECT_ISSUER=https://auth.proconnect.gouv.fr/api/v2

# Albert API (renseigner un vrai token pour tester les bots)
ALBERT_API_URL=https://api.albert.etalab.gouv.fr/v1
ALBERT_API_TOKEN=
ALBERT_MODEL=meta-llama/Llama-3.1-8B-Instruct
ALBERT_MODEL_EMBEDDING=BAAI/bge-m3

# Logging
LOGLEVEL=DEBUG
CONSOLE_LOGGING=true
EOF
        ok "Fichier .env créé. Éditez-le si nécessaire : $ENV_FILE"
    else
        ok "Fichier .env existant détecté"
    fi
}

do_build() {
    info "Construction de l'image Docker..."
    docker compose -f "$COMPOSE_FILE" build --no-cache
    ok "Image construite avec succès"
}

do_start() {
    info "Démarrage de la plateforme Colaig..."
    docker compose -f "$COMPOSE_FILE" up -d
    ok "Plateforme démarrée !"
    echo ""
    info "Interface web : http://localhost:${PLATFORM_PORT:-8080}"
    info "Mot de passe admin : $(grep PLATFORM_ADMIN_PASSWORD "$ENV_FILE" | cut -d= -f2)"
    echo ""
    info "Commandes utiles :"
    echo "  Logs    : $0 logs"
    echo "  Status  : $0 status"
    echo "  Arrêt   : $0 stop"
}

do_stop() {
    info "Arrêt de la plateforme..."
    docker compose -f "$COMPOSE_FILE" down
    ok "Plateforme arrêtée"
}

do_logs() {
    docker compose -f "$COMPOSE_FILE" logs -f --tail=100
}

do_status() {
    docker compose -f "$COMPOSE_FILE" ps
    echo ""
    # Health check
    PORT="${PLATFORM_PORT:-8080}"
    if curl -sf "http://localhost:$PORT/health" &>/dev/null; then
        ok "Plateforme accessible sur http://localhost:$PORT"
    else
        err "Plateforme non accessible sur le port $PORT"
    fi
}

# --- Main ----------------------------------------------------
ACTION="${1:-start}"

check_docker
ensure_env

case "$ACTION" in
    build)  do_build ;;
    start)  do_build; do_start ;;
    stop)   do_stop ;;
    logs)   do_logs ;;
    status) do_status ;;
    restart) do_stop; do_build; do_start ;;
    *)
        echo "Usage: $0 [build|start|stop|logs|status|restart]"
        exit 1
        ;;
esac
