#!/usr/bin/env bash
# up.sh — bring the flotilla dev container up on the devbox host.
#
# Container-scoped: builds the image, starts the single `flotilla` service, and creates
# the in-repo `.venv` (uv sync) inside it. It NEVER touches the VM — that lifecycle stays
# with app's scripts/devbox/* (migrate-flotilla plan, decision #2). The daily driver is
# VS Code "Reopen in Container"; this script is the host-side equivalent for terminal use.
#
# Usage: scripts/devbox/up.sh [--dry-run] [--yes]
#   --dry-run  print the docker compose commands without executing them
#   --yes      skip the confirmation prompt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/devbox/lib.sh
source "${SCRIPT_DIR}/lib.sh"

export FLOTILLA_DRY_RUN=0
export FLOTILLA_ASSUME_YES=0

usage() {
  cat <<'EOF'
up.sh — bring the flotilla dev container up on the devbox host.

Builds the image, starts the `flotilla` service, and runs `uv sync` inside it. Never
touches the VM (use app's scripts/devbox for VM lifecycle).

Usage: scripts/devbox/up.sh [--dry-run] [--yes]
  --dry-run  print the docker compose commands without executing them
  --yes      skip the confirmation prompt
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) FLOTILLA_DRY_RUN=1 ;;
    --yes | -y) FLOTILLA_ASSUME_YES=1 ;;
    -h | --help) usage 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

require_docker
ensure_repo

log "Bringing up the flotilla stack (project ${FLOTILLA_PROJECT_NAME}) from ${FLOTILLA_REPO_DIR}."
confirm "Build + start the flotilla dev container on this host?" ||
  die "Aborted; nothing started."

compose up -d --build

# First-run / idempotent venv sync inside the container. The in-repo `.venv` lives in the
# bind mount (not the image), so it is created here against the locked deps.
log "Syncing the project venv inside the container (uv sync --frozen) ..."
compose exec -T "${FLOTILLA_SERVICE}" bash -lc 'uv sync --frozen'

compose ps

if [[ "${FLOTILLA_DRY_RUN}" != "1" ]]; then
  audit_log "up project=${FLOTILLA_PROJECT_NAME} dir=${FLOTILLA_REPO_DIR}"
  log "flotilla is up. Attach with VS Code \"Reopen in Container\", or:
  docker compose -p ${FLOTILLA_PROJECT_NAME} -f ${FLOTILLA_COMPOSE_PATH} exec ${FLOTILLA_SERVICE} bash"
fi
