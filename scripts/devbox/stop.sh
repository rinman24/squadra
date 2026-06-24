#!/usr/bin/env bash
# stop.sh — stop the squadra dev container (docker compose down).
#
# Container-scoped: this is `compose down` for the squadra stack ONLY. It does NOT, and
# must NOT, deallocate the devbox VM — VM lifecycle stays with app's scripts/devbox
# (migrate-squadra plan, decision #2). Named volumes (squadra_claude_home auth/memory)
# and the repo bind mount persist; `-v` is deliberately never passed.
#
# Usage: scripts/devbox/stop.sh [--dry-run] [--yes]
#   --dry-run  print the docker compose command without running it
#   --yes      skip the confirmation prompt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/devbox/lib.sh
source "${SCRIPT_DIR}/lib.sh"

export SQUADRA_DRY_RUN=0
export SQUADRA_ASSUME_YES=0

usage() {
  cat <<'EOF'
stop.sh — stop the squadra dev container (docker compose down).

Stops + removes the squadra container only. Does NOT deallocate the VM, and does NOT
remove named volumes (squadra_claude_home persists). For VM lifecycle use app's
scripts/devbox.

Usage: scripts/devbox/stop.sh [--dry-run] [--yes]
  --dry-run  print the docker compose command without running it
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

log "Stopping the squadra stack (compose down — the VM is NOT touched)."
confirm "compose down the squadra stack? (the repo bind mount + squadra_claude_home volume persist)" ||
  die "Aborted; nothing stopped."

compose down

if [[ "${SQUADRA_DRY_RUN}" != "1" ]]; then
  audit_log "down project=${SQUADRA_PROJECT_NAME}"
  log "squadra stopped. Bring it back with: scripts/devbox/up.sh"
fi
