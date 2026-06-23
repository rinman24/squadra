# shellcheck shell=bash
# config.sh — tracked, non-secret defaults for the flotilla dev-container host scripts.
#
# Sourced by lib.sh; NOT executable on its own. Every value is `${VAR:-default}` so any
# constant can be overridden from the environment (or from config.local.sh, which is
# sourced after this file) without editing tracked code.
#
# These scripts are CONTAINER-SCOPED. They drive `docker compose` for the flotilla stack
# on the devbox HOST and nothing else: they never create, start, or deallocate the
# VM. VM lifecycle stays owned by app's scripts/devbox/* (migrate-flotilla plan,
# decision #2). There is no Azure CLI dependency here and no per-developer secret, so
# config.local.sh is OPTIONAL — the defaults below work out of the box.

# --- repo + compose stack ----------------------------------------------------------
# Clone source for the clone-if-absent path. ADO today; Phase 2 repoints this to GitHub.
FLOTILLA_REPO_URL="${FLOTILLA_REPO_URL:-https://dev.azure.com/your-org/example-project/_git/flotilla}"
# The checkout the stack bind-mounts. Defaults to the repo root that contains THIS
# script, so up/down/rebuild work straight from a checkout; override to point the stack
# at a clone elsewhere on the host.
FLOTILLA_REPO_DIR="${FLOTILLA_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Compose file, relative to FLOTILLA_REPO_DIR.
FLOTILLA_COMPOSE_FILE="${FLOTILLA_COMPOSE_FILE:-.devcontainer/docker-compose.yml}"
# Compose project name — MUST equal `name:` in .devcontainer/docker-compose.yml and the
# project name VS Code uses, or the scripts and "Reopen in Container" spawn two stacks.
FLOTILLA_PROJECT_NAME="${FLOTILLA_PROJECT_NAME:-flotilla}"
# The single service in the compose file.
FLOTILLA_SERVICE="${FLOTILLA_SERVICE:-flotilla}"

# --- audit -------------------------------------------------------------------------
# Gitignored local breadcrumb trail (the authoritative record is `docker events` /
# the daemon log). Lives next to the scripts.
FLOTILLA_AUDIT_LOG="${FLOTILLA_AUDIT_LOG:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.flotilla-devbox.log}"
