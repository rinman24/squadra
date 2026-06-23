#!/usr/bin/env bash
# fleet-host-activate.sh — idempotent fleet-host activation (ADR-0002 §11).
#
# Installs flotilla into the VM venv from the pinned commit and renders/installs
# the systemd units. Safe to run repeatedly and safe to run before the Key Vault
# `get` grant exists: it needs the PAT (from Key Vault, via the VM managed
# identity) only to clone the PRIVATE flotilla repo, so if Key Vault is not yet
# reachable it logs and exits 0 — re-run it once the grant lands.
#
# It deliberately does NOT enable the timer and does NOT clone gswa-backend:
#   - fleet activation is a separate, deliberate `systemctl enable --now
#     flotilla.timer` after the on-host smoke passes (decision 16);
#   - `flotilla fleet-tick` clones FLEET_HOME on the first tick.
#
# Reads /opt/flotilla/fleet-host.env (laid down by cloud-init) for FLEET_KEY_VAULT,
# FLEET_HOME, FLEET_APP_REPO_URL, FLOTILLA_REPO_URL, FLEET_VENV, FLEET_USER,
# FLEET_PARENT_SCOPE_IDS; and /opt/flotilla/PINNED_COMMIT for the flotilla pin.

set -euo pipefail

ENV_FILE=${FLEET_HOST_ENV_FILE:-/opt/flotilla/fleet-host.env}
PIN_FILE=${FLEET_PINNED_COMMIT_FILE:-/opt/flotilla/PINNED_COMMIT}

log() { echo "fleet-host-activate: $*"; }

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
: "${FLEET_KEY_VAULT:?set FLEET_KEY_VAULT in $ENV_FILE}"
: "${FLEET_HOME:?set FLEET_HOME in $ENV_FILE}"
: "${FLOTILLA_REPO_URL:?set FLOTILLA_REPO_URL in $ENV_FILE}"
VENV=${FLEET_VENV:-/opt/flotilla/venv}
PIN=$(tr -d '[:space:]' < "$PIN_FILE" 2>/dev/null || true)

# Authenticate as the VM managed identity and read the PAT from Key Vault. If
# either fails (grant not yet in place), this is not fatal — exit 0, re-run later.
if ! az login --identity --only-show-errors --output none 2>/dev/null; then
  log "managed-identity login failed; Key Vault grant not ready? re-run after it lands."
  exit 0
fi
PAT=$(az keyvault secret show --vault-name "$FLEET_KEY_VAULT" --name fleet-ado-pat \
        --query value --output tsv 2>/dev/null || true)
if [ -z "$PAT" ]; then
  log "could not read fleet-ado-pat from $FLEET_KEY_VAULT; re-run after the get grant lands."
  exit 0
fi

# pip-install flotilla from the pinned commit over HTTPS+PAT. The PAT reaches git
# only through an env-var credential helper, never argv or disk (memory
# git-push-https-pat-not-ssh); AZURE_DEVOPS_EXT_PAT is exported only for this
# process and the units never see it.
#
# --force-reinstall is load-bearing, not cosmetic: flotilla's version is a static
# 0.1.0, so a plain --upgrade is a no-op across commits — pip sees 0.1.0 already
# satisfied and SKIPS rebuilding from the new pin, silently leaving stale code in
# the venv. --force-reinstall makes a cutover actually rebuild from the pinned
# commit. flotilla has no third-party runtime deps, so only flotilla is reinstalled.
export AZURE_DEVOPS_EXT_PAT="$PAT"
SPEC="git+${FLOTILLA_REPO_URL}"
[ -n "$PIN" ] && [ "$PIN" != "REPLACE_WITH_FLOTILLA_CUTOVER_COMMIT" ] && SPEC="${SPEC}@${PIN}"
log "installing ${SPEC} into ${VENV}"
GIT_CONFIG_COUNT=2 \
GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= \
GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1='!f() { echo username=pat; echo "password=$AZURE_DEVOPS_EXT_PAT"; }; f' \
  "$VENV/bin/pip" install --upgrade --force-reinstall "$SPEC"
unset AZURE_DEVOPS_EXT_PAT

# Render + install the systemd units (timer NOT enabled).
sudo "$VENV/bin/flotilla" install-units \
  --key-vault "$FLEET_KEY_VAULT" \
  --fleet-home "$FLEET_HOME" \
  --venv-bin "$VENV/bin" \
  --app-repo-url "${FLEET_APP_REPO_URL:-}" \
  --parent-scope-ids "${FLEET_PARENT_SCOPE_IDS:-}"
sudo systemctl daemon-reload

log "done. Smoke a dry-run, then activate: sudo systemctl enable --now flotilla.timer"
