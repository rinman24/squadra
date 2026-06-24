#!/usr/bin/env bash
# up.sh — bring the squadra dev container up on the gswa-devbox host.
#
# Container-scoped: builds the image, starts the single `squadra` service, and creates
# the in-repo `.venv` (uv sync) inside it. It NEVER touches the VM — that lifecycle stays
# with gswa's scripts/devbox/* (migrate-squadra plan, decision #2). The daily driver is
# VS Code "Reopen in Container"; this script is the host-side equivalent for terminal use.
#
# Usage: scripts/devbox/up.sh [--dry-run] [--yes]
#   --dry-run  print the docker compose commands without executing them
#   --yes      skip the confirmation prompt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/devbox/lib.sh
source "${SCRIPT_DIR}/lib.sh"

export SQUADRA_DRY_RUN=0
export SQUADRA_ASSUME_YES=0

usage() {
  cat <<'EOF'
up.sh — bring the squadra dev container up on the gswa-devbox host.

Builds the image, starts the `squadra` service, and runs `uv sync` inside it. Never
touches the VM (use gswa's scripts/devbox for VM lifecycle).

Usage: scripts/devbox/up.sh [--dry-run] [--yes]
  --dry-run  print the docker compose commands without executing them
  --yes      skip the confirmation prompt
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) SQUADRA_DRY_RUN=1 ;;
    --yes | -y) SQUADRA_ASSUME_YES=1 ;;
    -h | --help) usage 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

require_docker
ensure_repo

log "Bringing up the squadra stack (project ${SQUADRA_PROJECT_NAME}) from ${SQUADRA_REPO_DIR}."
confirm "Build + start the squadra dev container on this host?" ||
  die "Aborted; nothing started."

compose up -d --build

# First-run / idempotent venv sync inside the container. The in-repo `.venv` lives in the
# bind mount (not the image), so it is created here against the locked deps.
log "Syncing the project venv inside the container (uv sync --frozen) ..."
compose exec -T "${SQUADRA_SERVICE}" bash -lc 'uv sync --frozen'

compose ps

if [[ "${SQUADRA_DRY_RUN}" != "1" ]]; then
  audit_log "up project=${SQUADRA_PROJECT_NAME} dir=${SQUADRA_REPO_DIR}"
  log "squadra is up. Attach with VS Code \"Reopen in Container\", or:
  docker compose -p ${SQUADRA_PROJECT_NAME} -f ${SQUADRA_COMPOSE_PATH} exec ${SQUADRA_SERVICE} bash"
fi
