#!/usr/bin/env bash
# rebuild.sh — rebuild the squadra dev-container image and recreate the container.
#
# The daily "I changed the Dockerfile / dependencies and want the running stack rebuilt"
# verb. Runs locally on the host (no tunnel/ssh), rebuilds the single squadra image,
# recreates the container, and re-syncs the venv. The
# repo bind mount and the squadra_claude_home volume survive a recreate, so no
# re-bootstrap is needed. Container-scoped: the VM is never touched.
#
# Usage: scripts/devbox/rebuild.sh [--no-cache] [--force-recreate] [--yes] [--dry-run]
#   --no-cache        rebuild the image from scratch (`build --no-cache` then `up -d`)
#   --force-recreate  recreate the container even if the image is unchanged
#   --yes / -y        skip the confirmation prompt
#   --dry-run         print the docker compose plan without executing it
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/devbox/lib.sh
source "${SCRIPT_DIR}/lib.sh"

export SQUADRA_DRY_RUN=0
export SQUADRA_ASSUME_YES=0
no_cache=0
recreate_flag=""

usage() {
  cat <<'EOF'
rebuild.sh — rebuild the squadra dev-container image and recreate the container.

Rebuilds the single squadra image and recreates the container; the repo bind mount and
squadra_claude_home volume persist. Runs locally on the host (no tunnel). The VM is
never touched.

Usage: scripts/devbox/rebuild.sh [--no-cache] [--force-recreate] [--yes] [--dry-run]
  --no-cache        rebuild the image from scratch (`build --no-cache` then `up -d`)
  --force-recreate  recreate the container even if the image is unchanged
  --yes / -y        skip the confirmation prompt
  --dry-run         print the docker compose plan without executing it
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache) no_cache=1 ;;
    --force-recreate) recreate_flag="--force-recreate" ;;
    --yes | -y) SQUADRA_ASSUME_YES=1 ;;
    --dry-run) SQUADRA_DRY_RUN=1 ;;
    -h | --help) usage 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# Human-readable label for the active variant, for the log line + audit breadcrumb.
variant="default"
if [[ "${no_cache}" == "1" && -n "${recreate_flag}" ]]; then
  variant="no-cache+force-recreate"
elif [[ "${no_cache}" == "1" ]]; then
  variant="no-cache"
elif [[ -n "${recreate_flag}" ]]; then
  variant="force-recreate"
fi

require_docker
ensure_repo

log "Rebuilding the squadra stack (variant: ${variant})."
confirm "Rebuild + recreate the squadra dev container? This drops any attached VS Code /
  tmux session (the repo bind mount + squadra_claude_home volume persist)." ||
  die "Aborted; nothing rebuilt."

# `--no-cache` is a build-time flag, so it splits into a `build` then a plain `up -d`
# (`up --build` does not accept it). Otherwise a single `up -d --build` suffices.
if [[ "${no_cache}" == "1" ]]; then
  compose build --no-cache
  compose up -d ${recreate_flag:+"${recreate_flag}"}
else
  compose up -d --build ${recreate_flag:+"${recreate_flag}"}
fi

# Deps may have changed — re-sync the in-repo venv against the locked deps.
log "Re-syncing the project venv inside the container (uv sync --frozen) ..."
compose exec -T "${SQUADRA_SERVICE}" bash -lc 'uv sync --frozen'

compose ps

if [[ "${SQUADRA_DRY_RUN}" != "1" ]]; then
  audit_log "rebuild project=${SQUADRA_PROJECT_NAME} variant=${variant}"
fi
