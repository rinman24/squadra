#!/usr/bin/env bash
# fleetctl.sh — on-demand start/stop/status control for the fleet supervisor
# ticker (ADR-0007). The imperative counterpart of the FLEET_AUTOSTART boot
# hook (fleet-autostart.sh): same mechanism — the detached `fleet-ticker` tmux
# session whose loop fires one supervisor tick every FLEET_TICK_INTERVAL_SECONDS
# — but driven by hand, right now, this session.
#
# This script ships as flotilla package data and is the body of the `flotilla`
# console command: `flotilla {start|stop|status|tick|log}` execs it. It resolves
# its sibling fleet-tick.sh from the same _scripts directory.
#
# Use FLEET_AUTOSTART for *standing* activation (bring the fleet up on every
# container boot); use this for *hands-on* up/down of the running ticker. The
# two compose: autostart calls the same start path.
#
#   flotilla start    start the ticker if not already running (idempotent)
#   flotilla stop     stop the ticker if running
#   flotilla status   report whether the ticker is running + tail the log
#   flotilla tick     run one supervisor tick in the foreground (extra args
#                     pass through to the supervisor, e.g. FLEET_MAX_RUNNERS=0
#                     flotilla tick for a read-only smoke tick)
#   flotilla log      tail the supervisor log (default last 40 lines); -f to
#                     follow live (tail -f), -n N for a custom line count
#
# Env knobs (shared with fleet-autostart.sh / fleet-tick.sh):
#   FLEET_HOME                   repo flotilla operates on (default: cwd)
#   FLEET_ROOT                   fleet state dir, holds supervisor.log
#                                (default $FLEET_HOME/.claude/fleet)
#   FLEET_TICK_INTERVAL_SECONDS  seconds between ticks (default 180 = the */3 cadence)
#   FLEET_TICKER_SESSION         tmux session name for the loop (default fleet-ticker)

set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
FLEET_HOME=${FLEET_HOME:-$(pwd)}
FLEET_ROOT=${FLEET_ROOT:-$FLEET_HOME/.claude/fleet}
INTERVAL=${FLEET_TICK_INTERVAL_SECONDS:-180}
SESSION=${FLEET_TICKER_SESSION:-fleet-ticker}
TICK="$SCRIPT_DIR/fleet-tick.sh"
LOG="$FLEET_ROOT/supervisor.log"

log() { echo "fleetctl: $*"; }

require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    log "tmux not found; cannot manage the ticker session." >&2
    exit 1
  fi
}

ticker_running() { tmux has-session -t "$SESSION" 2>/dev/null; }

cmd_start() {
  require_tmux
  if [ ! -f "$TICK" ]; then
    log "tick script not found at $TICK; nothing started." >&2
    exit 1
  fi
  if ticker_running; then
    log "ticker '$SESSION' already running — nothing to do."
    return 0
  fi
  # The loop is only the timer; each fire is a fresh supervisor process under
  # the supervisor's own flock, so crash-only semantics are preserved.
  tmux new-session -d -s "$SESSION" "while :; do bash '$TICK'; sleep $INTERVAL; done"
  log "started ticker '$SESSION' (every ${INTERVAL}s); attach with: tmux attach -t $SESSION"
}

cmd_stop() {
  require_tmux
  if ! ticker_running; then
    log "ticker '$SESSION' is not running — nothing to do."
    return 0
  fi
  tmux kill-session -t "$SESSION"
  log "stopped ticker '$SESSION' — no new ticks will fire."
  log "in-flight runners live in the separate 'fleet' tmux session and keep going; the board + status.json stay authoritative. 'tmux attach -t fleet' to watch them."
}

cmd_status() {
  if command -v tmux >/dev/null 2>&1 && ticker_running; then
    log "ticker '$SESSION' is RUNNING (tick every ${INTERVAL}s)."
  else
    log "ticker '$SESSION' is STOPPED."
  fi
  if [ -f "$LOG" ]; then
    echo "--- last 15 lines of $LOG ---"
    tail -n 15 "$LOG"
  else
    log "no supervisor log yet at $LOG (no tick has run)."
  fi
}

cmd_tick() {
  if [ ! -f "$TICK" ]; then
    log "tick script not found at $TICK." >&2
    exit 1
  fi
  exec bash "$TICK" "$@"
}

cmd_log() {
  local follow=0
  local lines=40
  while [ $# -gt 0 ]; do
    case $1 in
      -f|--follow) follow=1; shift ;;
      -n|--lines) lines=${2:?-n needs a line count}; shift 2 ;;
      -n*) lines=${1#-n}; shift ;;
      *) log "unknown argument to log: $1" >&2; usage ;;
    esac
  done
  if [ ! -f "$LOG" ]; then
    log "no supervisor log yet at $LOG (no tick has run)." >&2
    exit 1
  fi
  if [ "$follow" -eq 1 ]; then
    exec tail -n "$lines" -f "$LOG"
  fi
  tail -n "$lines" "$LOG"
}

usage() {
  cat >&2 <<EOF
usage: flotilla {start|stop|status|tick|log} [args...]

  start    start the detached '$SESSION' ticker if not already running
  stop     stop the '$SESSION' ticker if running
  status   report whether the ticker is running and tail the supervisor log
  tick     run one supervisor tick in the foreground (args pass through)
  log      tail the supervisor log; -f to follow live, -n N for N lines
EOF
  exit 2
}

case ${1:-} in
  start) shift; cmd_start "$@" ;;
  stop) shift; cmd_stop "$@" ;;
  status) shift; cmd_status "$@" ;;
  tick) shift; cmd_tick "$@" ;;
  log) shift; cmd_log "$@" ;;
  *) usage ;;
esac
