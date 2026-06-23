# shellcheck shell=bash
# lib.sh — shared helpers for the flotilla dev-container host scripts.
#
# Sourced (not executed) by up.sh / stop.sh / rebuild.sh. Sourcing this file also loads
# config.sh (tracked defaults) and config.local.sh (per-developer, gitignored, OPTIONAL).
#
# Conventions the helpers honour, set by the calling script from its flags:
#   FLOTILLA_DRY_RUN=1     run_or_echo / compose print commands instead of running them
#   FLOTILLA_ASSUME_YES=1  confirm() returns success without prompting

# --- logging -----------------------------------------------------------------------
log() { printf '%s\n' "[flotilla] $*" >&2; }
die() {
  printf '%s\n' "[flotilla] error: $*" >&2
  exit 1
}

# --- config loading ----------------------------------------------------------------
_FL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/devbox/config.sh
source "${_FL_LIB_DIR}/config.sh"

# config.local.sh is OPTIONAL — these scripts need no secret/subscription. Source it
# only if present (unlike app's devbox, which requires it for the Azure subscription).
if [[ -f "${_FL_LIB_DIR}/config.local.sh" ]]; then
  # shellcheck source=/dev/null
  source "${_FL_LIB_DIR}/config.local.sh"
fi

# Absolute compose-file path, derived AFTER both configs load so overrides apply. Using
# an absolute -f lets the scripts run from any cwd; docker compose still resolves the
# build context relative to the compose file's own directory (i.e. the repo root).
FLOTILLA_COMPOSE_PATH="${FLOTILLA_REPO_DIR}/${FLOTILLA_COMPOSE_FILE}"

# --- audit -------------------------------------------------------------------------
# Append a timestamped breadcrumb to the gitignored audit log.
audit_log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"${FLOTILLA_AUDIT_LOG}"
}

# --- dry-run / confirm gates -------------------------------------------------------
# Run argv as a command, or print it (prefixed with +) when FLOTILLA_DRY_RUN=1.
run_or_echo() {
  if [[ "${FLOTILLA_DRY_RUN:-0}" == "1" ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

# y/N prompt; returns success on yes. Bypassed (success) when FLOTILLA_ASSUME_YES=1, or
# under FLOTILLA_DRY_RUN where nothing is executed so there is nothing to gate.
confirm() {
  if [[ "${FLOTILLA_DRY_RUN:-0}" == "1" ]]; then
    log "(dry-run) would prompt: ${1:-Proceed?} [assuming yes]"
    return 0
  fi
  if [[ "${FLOTILLA_ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  local reply
  read -r -p "[flotilla] ${1:-Proceed?} [y/N] " reply
  [[ "${reply}" =~ ^[Yy]$ ]]
}

# --- docker preflight --------------------------------------------------------------
# These scripts run on the devbox HOST, where Docker Engine + the compose plugin
# are installed (by app's scripts/devbox provisioning). Fail fast with the fix if not.
require_docker() {
  # A dry run only prints the plan, so it must work without the daemon present.
  if [[ "${FLOTILLA_DRY_RUN:-0}" == "1" ]]; then
    log "(dry-run) skipping docker preflight"
    return 0
  fi
  command -v docker >/dev/null 2>&1 ||
    die "docker not found on PATH.
  These scripts run on the devbox HOST (not inside a container). Bring the VM up
  and install Docker via app's scripts/devbox/up.sh first."
  docker compose version >/dev/null 2>&1 ||
    die "the 'docker compose' plugin is not available (got the legacy docker-compose?)."
}

# --- compose wrapper ---------------------------------------------------------------
# Pin -p (project) and -f (compose file) on every invocation so the scripts and VS Code
# share one stack. Honours dry-run via run_or_echo.
compose() {
  run_or_echo docker compose -p "${FLOTILLA_PROJECT_NAME}" -f "${FLOTILLA_COMPOSE_PATH}" "$@"
}

# --- repo presence -----------------------------------------------------------------
# clone-if-absent: ensure FLOTILLA_REPO_DIR is a checkout. When run straight from a
# checkout (the default), this is a no-op. The clone uses the host's ambient git auth;
# for the ADO HTTPS origin that means a PAT-backed credential helper or cached creds.
ensure_repo() {
  # `.git` is a directory in a normal clone but a FILE in a worktree/submodule checkout,
  # so test existence, not -d.
  if [[ -e "${FLOTILLA_REPO_DIR}/.git" ]]; then
    return 0
  fi
  log "No checkout at ${FLOTILLA_REPO_DIR}; cloning ${FLOTILLA_REPO_URL} ..."
  run_or_echo git clone "${FLOTILLA_REPO_URL}" "${FLOTILLA_REPO_DIR}" ||
    die "clone failed. Check FLOTILLA_REPO_URL and that the host has git credentials
  for it (ADO HTTPS needs a PAT). See docs/dev-container.md."
}
