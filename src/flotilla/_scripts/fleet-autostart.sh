#!/usr/bin/env bash
# fleet-autostart.sh — opt-in autostart of the fleet supervisor ticker on
# container (re)start.
#
# A dev image that ships no cron and no systemd, whose main process is
# `sleep infinity`, has nothing to launch a scheduler on boot. Wire this script
# into the container's compose `command` (`… ; exec sleep infinity`) so it runs
# on every start. It is the autostart counterpart of the manual ticker
# (`flotilla start`) — same mechanism, just started for you on boot.
#
# This script ships as flotilla package data and resolves its sibling
# fleet-tick.sh from the same _scripts directory (not a path relative to the
# repo flotilla operates on).
#
# OPT-IN BY INVARIANT: a no-op unless FLEET_AUTOSTART is truthy. Installing this
# tooling changes nothing at runtime until a developer opts in (e.g. from a
# gitignored env file, alongside FLEET_EPIC_IDS scoping).
#
# IDEMPOTENT: re-running (or a container restart) is safe — it starts the
# detached `fleet-ticker` tmux session only if it is not already running. The
# ticker inherits this process's environment, so PATH, the PAT, FLEET_PYTHON,
# and the FLEET_* knobs all carry through to each supervisor tick.
#
# Env knobs (all optional):
#   FLEET_AUTOSTART              on/off switch; truthy (1/true/yes/on) = start
#   FLEET_HOME                   repo flotilla operates on (default: cwd)
#   FLEET_TICK_INTERVAL_SECONDS  seconds between ticks (default 180 = the */3 cadence)
#   FLEET_TICKER_SESSION         tmux session name for the loop (default fleet-ticker)

set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
FLEET_HOME=${FLEET_HOME:-$(pwd)}
INTERVAL=${FLEET_TICK_INTERVAL_SECONDS:-180}
SESSION=${FLEET_TICKER_SESSION:-fleet-ticker}
TICK="$SCRIPT_DIR/fleet-tick.sh"

log() { echo "fleet-autostart: $*"; }

# Opt-in gate — anything but an explicit truthy value is a no-op.
case ${FLEET_AUTOSTART:-} in
  1 | [Tt]rue | [Yy]es | [Oo]n) ;;
  *)
    log "FLEET_AUTOSTART not truthy (\"${FLEET_AUTOSTART:-}\") — autostart disabled (opt-in); nothing started."
    exit 0
    ;;
esac

if ! command -v tmux >/dev/null 2>&1; then
  log "tmux not found; cannot start the ticker (container continues)." >&2
  exit 0
fi

if [ ! -f "$TICK" ]; then
  log "tick script not found at $TICK; nothing started." >&2
  exit 0
fi

# Already ticking? Leave it alone (idempotent across restarts / re-runs).
if tmux has-session -t "$SESSION" 2>/dev/null; then
  log "ticker session '$SESSION' already running — nothing to do."
  exit 0
fi

# The loop is only the timer; each fire is a fresh supervisor process under the
# supervisor's own flock, so crash-only semantics are preserved.
tmux new-session -d -s "$SESSION" "while :; do bash '$TICK'; sleep $INTERVAL; done"
log "started ticker '$SESSION' (every ${INTERVAL}s); attach with: tmux attach -t $SESSION"
