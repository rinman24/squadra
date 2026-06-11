#!/usr/bin/env bash
# fleet-tick.sh — the cron/ticker entry point for the fleet supervisor
# one entry, one lock, every 3 minutes. Serialization
# lives in the supervisor itself (non-blocking flock on supervisor.lock), so an
# overlapping fire exits cleanly.
#
# This script ships as flotilla package data and is driven by `flotilla tick`
# (which resolves it via importlib.resources and sets FLEET_PYTHON). Run one
# tick by hand: `flotilla tick`. Activation is manual/opt-in — see the flotilla
# README.
#
# Env knobs (all optional):
#   FLEET_HOME    repo flotilla operates on (default: cwd)
#   FLEET_ROOT    fleet state dir, holds supervisor.log (default $FLEET_HOME/.claude/fleet)
#   FLEET_PYTHON  interpreter that has flotilla installed (default: python3)

set -u

FLEET_HOME=${FLEET_HOME:-$(pwd)}
FLEET_ROOT=${FLEET_ROOT:-$FLEET_HOME/.claude/fleet}
PYTHON=${FLEET_PYTHON:-python3}

mkdir -p "$FLEET_ROOT"
{
  echo "=== fleet tick $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  "$PYTHON" -m flotilla.supervisor "$@"
} >>"$FLEET_ROOT/supervisor.log" 2>&1
