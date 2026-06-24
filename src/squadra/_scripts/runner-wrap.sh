#!/usr/bin/env bash
# runner-wrap.sh — the fleet pane command.
#
# Thin deterministic shell around one slice runner. It seeds status.json, owns
# the heartbeat loop (liveness = "process alive", independent of whatever long
# tool call the agent is in), records pid/pane sidecars for the watchdog,
# invokes the headless Claude session on the configured runner skill, and backstops an
# unexpected exit by stamping parked_state=failed. The supervisor launches one
# of these per claimed slice into its own detached-tmux pane.
#
# This script ships as squadra package data; the supervisor resolves it via
# importlib.resources (squadra._resources.resolve_script) and passes the
# FLEET_* env below into the pane. FLEET_PYTHON must point at an interpreter
# that has squadra installed (the supervisor passes its own sys.executable).
#
# Usage:
#   runner-wrap.sh <issue-id> <branch> [attempt]
#
# Env knobs (all optional):
#   FLEET_HOME                        repo squadra operates on (default: cwd)
#   FLEET_ROOT                        status-file root (default $FLEET_HOME/.claude/fleet)
#   FLEET_HEARTBEAT_INTERVAL_SECONDS  heartbeat cadence (default: HEARTBEAT_INTERVAL_SECONDS
#                                     in squadra.constants)
#   FLEET_MODEL                       claude --model for the runner (default: FLEET_MODEL
#                                     in squadra.constants)
#   FLEET_EFFORT                      claude --effort for the runner (default: FLEET_EFFORT
#                                     in squadra.constants)
#   FLEET_RUNNER_SKILL                slice-runner skill name (default: DEFAULT_RUNNER_SKILL
#                                     in squadra.config)
#   FLEET_TDD_SKILL                   tdd skill passed to the runner (default: DEFAULT_TDD_SKILL
#                                     in squadra.config)
#   FLEET_QA_SKILL                    qa skill passed to the runner (default: DEFAULT_QA_SKILL
#                                     in squadra.config)
#   FLEET_PYTHON                      interpreter for the status CLI (default: python3)
#   FLEET_CLAUDE_CMD                  claude binary (stubbed in tests)

set -u

usage() {
  echo "usage: runner-wrap.sh <issue-id> <branch> [attempt]" >&2
  exit 64
}

[ "$#" -ge 2 ] || usage
ISSUE_ID=$1
BRANCH=$2
ATTEMPT=${3:-1}
case $ISSUE_ID in '' | *[!0-9]*) usage ;; esac
case $ATTEMPT in '' | *[!0-9]*) usage ;; esac

FLEET_HOME=${FLEET_HOME:-$(pwd)}
FLEET_ROOT=${FLEET_ROOT:-$FLEET_HOME/.claude/fleet}
PYTHON=${FLEET_PYTHON:-python3}
CLAUDE_CMD=${FLEET_CLAUDE_CMD:-claude}
INTERVAL=${FLEET_HEARTBEAT_INTERVAL_SECONDS:-}
MODEL=${FLEET_MODEL:-}
EFFORT=${FLEET_EFFORT:-}
RUNNER_SKILL=${FLEET_RUNNER_SKILL:-}
TDD_SKILL=${FLEET_TDD_SKILL:-}
QA_SKILL=${FLEET_QA_SKILL:-}
# Defaults are the single source of truth — INTERVAL/MODEL/EFFORT from
# squadra.constants, the skill names from squadra.config. Fetch them in one
# shot for any knob the environment did not supply. In the normal
# supervisor-driven path the launcher passes them all, so this never runs.
if [ -z "$INTERVAL" ] || [ -z "$MODEL" ] || [ -z "$EFFORT" ] || \
   [ -z "$RUNNER_SKILL" ] || [ -z "$TDD_SKILL" ] || [ -z "$QA_SKILL" ]; then
  defaults=$("$PYTHON" -c \
    'from squadra.constants import HEARTBEAT_INTERVAL_SECONDS as h, FLEET_MODEL as m, FLEET_EFFORT as e
from squadra.config import DEFAULT_RUNNER_SKILL as r, DEFAULT_TDD_SKILL as t, DEFAULT_QA_SKILL as q
print(h); print(m); print(e); print(r); print(t); print(q)') \
    || exit 70
  { read -r d_interval; read -r d_model; read -r d_effort
    read -r d_runner; read -r d_tdd; read -r d_qa; } <<EOF
$defaults
EOF
  INTERVAL=${INTERVAL:-$d_interval}
  MODEL=${MODEL:-$d_model}
  EFFORT=${EFFORT:-$d_effort}
  RUNNER_SKILL=${RUNNER_SKILL:-$d_runner}
  TDD_SKILL=${TDD_SKILL:-$d_tdd}
  QA_SKILL=${QA_SKILL:-$d_qa}
fi

SLICE_DIR=$FLEET_ROOT/$ISSUE_ID
mkdir -p "$SLICE_DIR"
# Everything below shows in the tmux pane AND lands in the slice log.
exec > >(tee -a "$SLICE_DIR/runner.log") 2>&1

RUNNER_ID="runner-${ISSUE_ID}-a${ATTEMPT}-$(date -u +%Y%m%dT%H%M%SZ)"
WORKTREE=$FLEET_HOME/.claude/worktrees/$(printf '%s' "$BRANCH" | tr '/' '+')

fleet_status() {
  "$PYTHON" -m squadra.status "$@" --fleet-root "$FLEET_ROOT"
}

fleet_status init --issue-id "$ISSUE_ID" --runner-id "$RUNNER_ID" \
  --branch "$BRANCH" --worktree "$WORKTREE" --attempt "$ATTEMPT" || exit 70

# Sidecars: the reap pass confirms a stale runner is genuinely dead via
# runner.pid before requeueing; pane-id lets it kill a wedged pane.
echo "$$" >"$SLICE_DIR/runner.pid"
if [ -n "${TMUX_PANE:-}" ]; then
  echo "$TMUX_PANE" >"$SLICE_DIR/pane-id"
fi

# The wrapper — not the agent — owns liveness: last_heartbeat advances every
# $INTERVAL seconds for as long as this process lives, and stops with it.
heartbeat_loop() {
  while :; do
    sleep "$INTERVAL"
    fleet_status heartbeat --issue-id "$ISSUE_ID" || true
  done
}
heartbeat_loop &
HB_PID=$!
echo "$HB_PID" >"$SLICE_DIR/heartbeat.pid"
# Reap the loop before exiting so its pid is truly gone (not a zombie) by the
# time anything inspects heartbeat.pid.
cleanup() {
  kill "$HB_PID" 2>/dev/null || true
  wait "$HB_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "fleet: runner $RUNNER_ID starting (issue #$ISSUE_ID, branch $BRANCH, attempt $ATTEMPT, model ${MODEL:-inherited}, effort ${EFFORT:-inherited}, heartbeat ${INTERVAL}s)"
cd "$FLEET_HOME" || exit 70
# Pin the compute tier explicitly so the runner never silently inherits an
# interactive session's default model/effort (squadra.constants is the source).
CLAUDE_ARGS=(-p "$RUNNER_SKILL issue-id=$ISSUE_ID branch=$BRANCH attempt=$ATTEMPT tdd-skill=$TDD_SKILL qa-skill=$QA_SKILL"
             --dangerously-skip-permissions)
[ -n "$MODEL" ] && CLAUDE_ARGS+=(--model "$MODEL")
[ -n "$EFFORT" ] && CLAUDE_ARGS+=(--effort "$EFFORT")
"$CLAUDE_CMD" "${CLAUDE_ARGS[@]}"
RC=$?

# Backstop: a healthy runner always exits parked (or done). Anything else is
# an unexpected death — record it so the watchdog/humans see why. A session
# that parked and then exited non-zero keeps its own parked_state.
PHASE=$(STATUS_FILE="$SLICE_DIR/status.json" "$PYTHON" -c \
  'import json, os; print(json.load(open(os.environ["STATUS_FILE"])).get("phase", ""))' \
  2>/dev/null) || PHASE=""
[ -n "$PHASE" ] || PHASE=unknown
if [ "$PHASE" != parked ] && [ "$PHASE" != done ]; then
  echo "fleet: runner exited without parking (rc=$RC, phase=$PHASE) — recording failure"
  fleet_status update --issue-id "$ISSUE_ID" --phase parked --parked-state failed \
    --last-error "runner exited unexpectedly (rc=$RC, phase=$PHASE)" || true
  [ "$RC" -ne 0 ] || RC=70
fi
echo "fleet: runner $RUNNER_ID exiting (rc=$RC, phase=$PHASE)"
exit "$RC"
