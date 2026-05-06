#!/usr/bin/env bash
# ============================================================
# Finn Enterprise — One-command deployment script
# ============================================================
# Usage:
#   ./deploy-enterprise.sh              # API + Nginx (default)
#   ./deploy-enterprise.sh --ollama     # API + Nginx + Ollama
#   ./deploy-enterprise.sh --down       # Tear down all services
#   ./deploy-enterprise.sh --status     # Show running services
#   ./deploy-enterprise.sh --logs       # Tail logs
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.enterprise.yml"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_TEMPLATE="$SCRIPT_DIR/.env.enterprise"
CERT_SCRIPT="$SCRIPT_DIR/nginx/generate-self-signed-cert.sh"
CERTS_DIR="$SCRIPT_DIR/certs"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Prerequisite checks ─────────────────────────────────────
check_prerequisites() {
    local missing=0

    if ! command -v docker &>/dev/null; then
        error "Docker is not installed. See https://docs.docker.com/engine/install/"
        missing=1
    fi

    if ! docker compose version &>/dev/null 2>&1; then
        error "Docker Compose V2 is required. Update Docker or install the compose plugin."
        missing=1
    fi

    if [ $missing -ne 0 ]; then
        exit 1
    fi

    info "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+') detected"
}

# ── Environment setup ───────────────────────────────────────
setup_env() {
    if [ ! -f "$ENV_FILE" ]; then
        info "Creating .env from enterprise template..."
        cp "$ENV_TEMPLATE" "$ENV_FILE"
        warn "Review and customize .env before production use."
    else
        info ".env already exists — using existing configuration."
    fi
}

# ── TLS certificates ────────────────────────────────────────
setup_tls() {
    if [ -f "$SCRIPT_DIR/nginx/ssl/server.crt" ] && [ -f "$SCRIPT_DIR/nginx/ssl/server.key" ]; then
        info "TLS certificates found."
        return
    fi

    warn "No TLS certificates found. Generating self-signed certificate..."
    warn "For production, replace with certificates from your enterprise CA."
    bash "$CERT_SCRIPT"
}

# ── Corporate CA certs ──────────────────────────────────────
setup_corporate_certs() {
    mkdir -p "$CERTS_DIR"
    if ! ls "$CERTS_DIR"/*.crt 1>/dev/null 2>&1; then
        info "No corporate CA certificates in certs/ — skipping."
        info "To add: place .crt files in $CERTS_DIR/ and rebuild."
    else
        local count
        count=$(ls -1 "$CERTS_DIR"/*.crt 2>/dev/null | wc -l)
        info "Found $count corporate CA certificate(s) in certs/"
    fi
}

# ── Deploy ───────────────────────────────────────────────────
deploy() {
    local profiles=()

    if [[ "${1:-}" == "--ollama" ]]; then
        profiles=(--profile ollama)
        info "Ollama profile enabled — local AI models will be available."
    fi

    info "Building and starting services..."
    docker compose -f "$COMPOSE_FILE" "${profiles[@]}" up -d --build

    echo ""
    info "============================================"
    info " Finn Enterprise is starting up!"
    info "============================================"
    echo ""
    info "  HTTPS:  https://localhost:${NGINX_HTTPS_PORT:-443}"
    info "  HTTP:   http://localhost:${NGINX_HTTP_PORT:-80} (redirects to HTTPS)"
    echo ""
    info "  First startup may take 1-2 minutes while the"
    info "  embedding model is downloaded (~90 MB)."
    echo ""
    info "  Monitor progress:  docker compose -f $COMPOSE_FILE logs -f"
    info "  Check status:      ./deploy-enterprise.sh --status"
    echo ""

    if [[ "${1:-}" == "--ollama" ]]; then
        echo ""
        info "  Ollama is available at http://finn-ollama:11434"
        info "  (internal network only — not exposed to host)"
        info "  Pull a model:  docker exec finn-ollama ollama pull llama3.2"
        echo ""
    fi
}

# ── Tear down ────────────────────────────────────────────────
teardown() {
    info "Stopping all services..."
    docker compose -f "$COMPOSE_FILE" --profile ollama down
    info "Services stopped. Volumes are preserved."
    info "To remove volumes: docker compose -f $COMPOSE_FILE --profile ollama down -v"
}

# ── Status ───────────────────────────────────────────────────
show_status() {
    docker compose -f "$COMPOSE_FILE" --profile ollama ps
}

# ── Logs ─────────────────────────────────────────────────────
show_logs() {
    docker compose -f "$COMPOSE_FILE" --profile ollama logs -f --tail=100
}

# ── Main ─────────────────────────────────────────────────────
main() {
    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║   Finn Enterprise Deployment     ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""

    case "${1:-}" in
        --down)
            teardown
            ;;
        --status)
            show_status
            ;;
        --logs)
            show_logs
            ;;
        *)
            check_prerequisites
            setup_env
            setup_tls
            setup_corporate_certs
            deploy "${1:-}"
            ;;
    esac
}

main "$@"
