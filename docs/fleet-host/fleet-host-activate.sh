#!/usr/bin/env bash
# fleet-host-activate.sh — idempotent fleet-host activation (ADR-0002 §11).
#
# Installs squadra into the VM venv from public PyPI at the pinned version and
# renders/installs the systemd units. squadra is published on PyPI (MIT) with no
# third-party runtime deps, so this step is CREDENTIAL-FREE: it reads nothing from
# Key Vault and needs no GitHub/ADO PAT. The ADO PAT + ANTHROPIC_API_KEY are fetched
# at TICK time by `squadra.secrets` (unchanged) for the app-backend clone + board ops.
#
# It deliberately does NOT enable the timer and does NOT clone app-backend:
#   - fleet activation is a separate, deliberate `systemctl enable --now
#     squadra.timer` after the on-host smoke passes (decision 16);
#   - `squadra fleet-tick` clones FLEET_HOME on the first tick.
#
# Reads /opt/squadra/fleet-host.env (laid down by cloud-init) for FLEET_KEY_VAULT,
# FLEET_HOME, FLEET_APP_REPO_URL, FLEET_VENV, FLEET_USER, FLEET_PARENT_SCOPE_IDS, and
# the optional SQUADRA_VERSION pin; and /opt/squadra/PINNED_VERSION (the default for
# SQUADRA_VERSION) for the squadra version pin.

set -euo pipefail

ENV_FILE=${FLEET_HOST_ENV_FILE:-/opt/squadra/fleet-host.env}
PIN_FILE=${FLEET_PINNED_VERSION_FILE:-/opt/squadra/PINNED_VERSION}

log() { echo "fleet-host-activate: $*"; }

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
: "${FLEET_KEY_VAULT:?set FLEET_KEY_VAULT in $ENV_FILE}"
: "${FLEET_HOME:?set FLEET_HOME in $ENV_FILE}"
VENV=${FLEET_VENV:-/opt/squadra/venv}
# Azure DevOps org/project the board adapter resolves from `az devops configure`
# defaults (board.py::_configured_default). Overridable via the env file; default
# to your-org / example-project (matches FLEET_APP_REPO_URL — the app-backend app repo
# the fleet operates on, and the ADO board).
ADO_ORG=${FLEET_ADO_ORG:-https://dev.azure.com/your-org}
ADO_PROJECT=${FLEET_ADO_PROJECT:-example-project}
# Version pin: SQUADRA_VERSION from the env file, else the PINNED_VERSION file.
SQUADRA_VERSION=${SQUADRA_VERSION:-$(tr -d '[:space:]' < "$PIN_FILE" 2>/dev/null || true)}
: "${SQUADRA_VERSION:?set SQUADRA_VERSION in $ENV_FILE or write $PIN_FILE}"

# pip-install squadra from public PyPI at the pinned version. No credential is
# needed: squadra is an MIT package with no third-party runtime deps.
#
# --force-reinstall is load-bearing, not cosmetic: it makes a version cutover
# actually rebuild from PyPI rather than leaving stale code in the venv. Because
# squadra has no third-party runtime deps, only squadra is reinstalled.
log "installing squadra==${SQUADRA_VERSION} from PyPI into ${VENV}"
"$VENV/bin/pip" install --upgrade --force-reinstall "squadra==${SQUADRA_VERSION}"

# Render + install the systemd units (timer NOT enabled).
sudo "$VENV/bin/squadra" install-units \
  --key-vault "$FLEET_KEY_VAULT" \
  --fleet-home "$FLEET_HOME" \
  --venv-bin "$VENV/bin" \
  --app-repo-url "${FLEET_APP_REPO_URL:-}" \
  --parent-scope-ids "${FLEET_PARENT_SCOPE_IDS:-}"
sudo systemctl daemon-reload

# Set the az devops org/project defaults the board adapter reads
# (board.py::_configured_default). These live per-user under ~/.azure, so set them
# for BOTH the service user — this script already runs as azureuser — and root,
# under which the on-host goss smoke runs the dry-run tick (docs/fleet-host/SMOKE.md).
log "configuring az devops defaults: organization=${ADO_ORG} project=${ADO_PROJECT}"
az devops configure --defaults "organization=${ADO_ORG}" "project=${ADO_PROJECT}"
sudo az devops configure --defaults "organization=${ADO_ORG}" "project=${ADO_PROJECT}"

log "done. Smoke a dry-run, then activate: sudo systemctl enable --now squadra.timer"
