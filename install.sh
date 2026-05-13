#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$1"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
fail()  { printf "${RED}[FAIL]${NC}  %s\n" "$1"; exit 1; }

IS_ROOT=false
[ "$(id -u)" -eq 0 ] && IS_ROOT=true

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")

# --- Install Docker if missing ---
install_docker() {
    if ! $IS_ROOT; then
        fail "Docker not found. Re-run with sudo to install it: sudo sh install.sh"
    fi
    info "Installing Docker..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl gnupg lsb-release >/dev/null
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg \
            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
          https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
          $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
          > /etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q dnf-plugins-core
        dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
        dnf install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q yum-utils
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
    else
        fail "Unsupported package manager. Install Docker manually: https://docs.docker.com/engine/install/"
    fi

    systemctl enable docker
    systemctl start docker
    ok "Docker installed and started"
}

# --- Check Docker ---
if command -v docker >/dev/null 2>&1; then
    ok "Docker already installed"
else
    install_docker
fi

# --- Ensure Docker is running ---
if ! docker info >/dev/null 2>&1; then
    if $IS_ROOT; then
        info "Starting Docker daemon..."
        systemctl start docker
    else
        fail "Docker daemon not running. Start it with: sudo systemctl start docker"
    fi
fi

# --- Check docker compose ---
if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose plugin available"
elif command -v docker-compose >/dev/null 2>&1; then
    ok "docker-compose standalone available"
    # Alias for the rest of the script
    docker() {
        if [ "$1" = "compose" ]; then
            shift
            command docker-compose "$@"
        else
            command docker "$@"
        fi
    }
else
    fail "Docker Compose not found. Install it: https://docs.docker.com/compose/install/"
fi

# --- Add user to docker group (only when root) ---
if $IS_ROOT; then
    if id -nG "$REAL_USER" | grep -qw docker; then
        ok "$REAL_USER already in docker group"
    else
        info "Adding $REAL_USER to docker group..."
        usermod -aG docker "$REAL_USER"
        ok "$REAL_USER added to docker group (re-login for group to take effect)"
    fi
fi

# --- Start the stack ---
info "Starting monitoring stack..."
cd "$SCRIPT_DIR"
docker compose up -d --quiet-pull 2>/dev/null || docker compose up -d

ok "Monitoring stack running"

# --- Configure Claude Code env vars ---
OTEL_BLOCK="
# Claude Code OTel monitoring
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317"

MARKER="CLAUDE_CODE_ENABLE_TELEMETRY"

add_to_shell_rc() {
    rc_file="$1"
    if [ -f "$rc_file" ] && grep -q "$MARKER" "$rc_file"; then
        ok "Env vars already in $rc_file"
    else
        printf '%s\n' "$OTEL_BLOCK" >> "$rc_file"
        if $IS_ROOT; then
            chown "$REAL_USER":"$(id -gn "$REAL_USER")" "$rc_file"
        fi
        ok "Env vars added to $rc_file"
    fi
}

# Detect shell and add to appropriate rc file
if [ -f "$REAL_HOME/.zshrc" ]; then
    add_to_shell_rc "$REAL_HOME/.zshrc"
elif [ -f "$REAL_HOME/.bashrc" ]; then
    add_to_shell_rc "$REAL_HOME/.bashrc"
else
    add_to_shell_rc "$REAL_HOME/.bashrc"
fi

# Also add to .profile for login shells
if [ -f "$REAL_HOME/.profile" ]; then
    add_to_shell_rc "$REAL_HOME/.profile"
fi

# --- Wait for services ---
info "Waiting for services to be ready..."
TRIES=0
MAX_TRIES=30
while [ $TRIES -lt $MAX_TRIES ]; do
    if curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 1
done

if [ $TRIES -lt $MAX_TRIES ]; then
    ok "Grafana ready"
else
    warn "Grafana not responding yet (may still be starting)"
fi

# --- Summary ---
printf "\n"
printf "${GREEN}========================================${NC}\n"
printf "${GREEN}  Claude Code Monitoring - Ready!${NC}\n"
printf "${GREEN}========================================${NC}\n"
printf "\n"
printf "  Grafana:    ${CYAN}http://localhost:3000${NC}\n"
printf "  Prometheus: ${CYAN}http://localhost:14703${NC}\n"
printf "  Dashboard:  ${CYAN}http://localhost:3000/dashboards${NC}\n"
printf "\n"
printf "  Login: admin / admin (or anonymous viewer)\n"
printf "\n"
printf "  ${YELLOW}Next steps:${NC}\n"
printf "  1. Open a new terminal (or run: source ~/.zshrc)\n"
printf "  2. Start Claude Code - telemetry flows automatically\n"
printf "  3. Open Grafana to see metrics\n"
printf "\n"
