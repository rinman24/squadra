#!/bin/sh
# Hermetic regression tests for runner-wrap.sh (the fleet pane command).
#
# Runs entirely against a temp fleet root with a stubbed `claude` binary — no
# network, no ADO, no tmux. The status CLI is the real one (flotilla is
# installed in the test interpreter), so the wrapper↔CLI seam is exercised for
# real. Entry point: the pytest wrapper tests/test_runner_wrap.py, which pins
# FLEET_PYTHON to the interpreter running the suite.

set -u

PYTHON=${FLEET_PYTHON:-python3}
# Resolve the packaged runner-wrap.sh exactly the way the supervisor does — via
# importlib.resources, NOT a repo-relative path.
WRAP=$("$PYTHON" -c 'import flotilla._resources as r; print(r.resolve_script("runner-wrap.sh"))') || {
  echo "could not resolve packaged runner-wrap.sh (is flotilla installed in $PYTHON?)" >&2
  exit 70
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

fails=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
assert() { desc=$1; shift; if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi; }
refute() { desc=$1; shift; if "$@" >/dev/null 2>&1; then fail "$desc"; else pass "$desc"; fi; }

# Shared env for every wrapper invocation. Heartbeat every second so the
# liveness test stays fast; fleet root + home isolated under $TMP. The wrapper
# cds into FLEET_HOME and derives the worktree path under it.
FLEET_HOME=$TMP/home
mkdir -p "$FLEET_HOME"
FLEET_HEARTBEAT_INTERVAL_SECONDS=1
FLEET_PYTHON=$PYTHON
export FLEET_HOME FLEET_HEARTBEAT_INTERVAL_SECONDS FLEET_PYTHON
unset TMUX_PANE

status_field() { # <fleet-root> <issue-id> <field>
  STATUS_FILE="$1/$2/status.json" "$PYTHON" -c \
    'import json, os; v = json.load(open(os.environ["STATUS_FILE"]))[os.environ["FIELD"]]; print("" if v is None else v)' \
    2>/dev/null
}
field() { # <fleet-root> <issue-id> <field>
  FIELD=$3 status_field "$1" "$2"
}

mkdir -p "$TMP/bin"

# Stub: a healthy agent — records its argv, parks awaiting-pr-approval, exits 0.
cat >"$TMP/bin/claude-park" <<EOF
#!/bin/sh
printf '%s\n' "\$@" > "$TMP/claude-args"
"$PYTHON" -m flotilla.status update \
  --issue-id 41 --fleet-root "\$FLEET_ROOT" \
  --phase parked --parked-state awaiting-pr-approval \
  --pr-url https://pr/41 --add-worker task-1
exit 0
EOF

# Stub: an agent that dies mid-work without parking.
cat >"$TMP/bin/claude-die" <<'EOF'
#!/bin/sh
exit 7
EOF

# Stub: an agent that works long enough for heartbeats to land, then parks.
cat >"$TMP/bin/claude-slow-park" <<EOF
#!/bin/sh
sleep 3
"$PYTHON" -m flotilla.status update \
  --issue-id 41 --fleet-root "\$FLEET_ROOT" \
  --phase parked --parked-state qa-ready
exit 0
EOF

# Stub: an agent that parks needs-decision but exits non-zero afterwards.
cat >"$TMP/bin/claude-park-then-die" <<EOF
#!/bin/sh
"$PYTHON" -m flotilla.status update \
  --issue-id 41 --fleet-root "\$FLEET_ROOT" \
  --phase parked --parked-state needs-decision
exit 3
EOF

chmod +x "$TMP/bin"/*

# --- argument validation ------------------------------------------------------

FLEET_ROOT="$TMP/argcheck" sh "$WRAP" >/dev/null 2>&1
assert "args: no args exits 64" [ "$?" -eq 64 ]
FLEET_ROOT="$TMP/argcheck" sh "$WRAP" not-a-number feat/x >/dev/null 2>&1
assert "args: non-numeric issue-id exits 64" [ "$?" -eq 64 ]
FLEET_ROOT="$TMP/argcheck" sh "$WRAP" 41 feat/x bogus >/dev/null 2>&1
assert "args: non-numeric attempt exits 64" [ "$?" -eq 64 ]

# --- healthy run: park + sidecars + prompt ------------------------------------

ROOT1="$TMP/fleet1"
FLEET_ROOT=$ROOT1 FLEET_CLAUDE_CMD="$TMP/bin/claude-park" \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "park: wrapper exits 0" [ "$?" -eq 0 ]
assert "park: phase is parked" [ "$(field "$ROOT1" 41 phase)" = "parked" ]
assert "park: parked_state preserved" \
  [ "$(field "$ROOT1" 41 parked_state)" = "awaiting-pr-approval" ]
assert "park: pr_url recorded" [ "$(field "$ROOT1" 41 pr_url)" = "https://pr/41" ]
assert "park: roster recorded" grep -q 'task-1' "$ROOT1/41/status.json"
assert "park: branch seeded" [ "$(field "$ROOT1" 41 branch)" = "feat/slice-41-x" ]
assert "park: worktree derived with /->+" \
  [ "$(field "$ROOT1" 41 worktree)" = "$FLEET_HOME/.claude/worktrees/feat+slice-41-x" ]
assert "park: runner.pid written" grep -qE '^[0-9]+$' "$ROOT1/41/runner.pid"
refute "park: no pane-id without TMUX_PANE" [ -f "$ROOT1/41/pane-id" ]
assert "park: runner.log written" grep -q 'runner-41-a1' "$ROOT1/41/runner.log"
assert "park: prompt names the skill and inputs" \
  grep -q '/afk-slice-runner issue-id=41 branch=feat/slice-41-x attempt=1' "$TMP/claude-args"
assert "park: permissions skipped for headless run" \
  grep -q -- '--dangerously-skip-permissions' "$TMP/claude-args"
CONSTANTS_MODEL=$("$PYTHON" -c 'from flotilla.constants import FLEET_MODEL; print(FLEET_MODEL)')
CONSTANTS_EFFORT=$("$PYTHON" -c 'from flotilla.constants import FLEET_EFFORT; print(FLEET_EFFORT)')
assert "park: --model flag passed" grep -qx -- '--model' "$TMP/claude-args"
assert "park: model defaults to constants.py" grep -qx -- "$CONSTANTS_MODEL" "$TMP/claude-args"
assert "park: --effort flag passed" grep -qx -- '--effort' "$TMP/claude-args"
assert "park: effort defaults to constants.py" grep -qx -- "$CONSTANTS_EFFORT" "$TMP/claude-args"
HB_PID=$(cat "$ROOT1/41/heartbeat.pid")
refute "park: heartbeat loop killed on exit" kill -0 "$HB_PID"

# --- pane-id sidecar ------------------------------------------------------------

ROOT2="$TMP/fleet2"
FLEET_ROOT=$ROOT2 FLEET_CLAUDE_CMD="$TMP/bin/claude-park" TMUX_PANE='%7' \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "pane: pane-id recorded from TMUX_PANE" [ "$(cat "$ROOT2/41/pane-id")" = "%7" ]

# --- unexpected death: backstop ---------------------------------------------------

ROOT3="$TMP/fleet3"
FLEET_ROOT=$ROOT3 FLEET_CLAUDE_CMD="$TMP/bin/claude-die" \
  bash "$WRAP" 41 feat/slice-41-x 2 >/dev/null 2>&1
assert "die: non-zero exit propagated" [ "$?" -eq 7 ]
assert "die: backstop parks the slice" [ "$(field "$ROOT3" 41 phase)" = "parked" ]
assert "die: parked_state is failed" [ "$(field "$ROOT3" 41 parked_state)" = "failed" ]
assert "die: last_error records the rc" \
  grep -q 'runner exited unexpectedly (rc=7' "$ROOT3/41/status.json"
assert "die: attempt seeded from argv" [ "$(field "$ROOT3" 41 attempt)" = "2" ]

# --- parked-but-nonzero: parked state is preserved -------------------------------

ROOT4="$TMP/fleet4"
FLEET_ROOT=$ROOT4 FLEET_CLAUDE_CMD="$TMP/bin/claude-park-then-die" \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "parked-rc: non-zero exit propagated" [ "$?" -eq 3 ]
assert "parked-rc: agent's parked_state kept" \
  [ "$(field "$ROOT4" 41 parked_state)" = "needs-decision" ]

# --- liveness: heartbeat advances while the agent works ---------------------------

ROOT5="$TMP/fleet5"
FLEET_ROOT=$ROOT5 FLEET_CLAUDE_CMD="$TMP/bin/claude-slow-park" \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "liveness: wrapper exits 0" [ "$?" -eq 0 ]
STARTED=$(field "$ROOT5" 41 started_at)
LAST_HB=$(field "$ROOT5" 41 last_heartbeat)
assert "liveness: heartbeat advanced past started_at" [ "$LAST_HB" != "$STARTED" ]

# --- heartbeat default: unset var falls back to constants.py ----------------------

ROOT6="$TMP/fleet6"
CONSTANTS_DEFAULT=$(FLEET_HEARTBEAT_INTERVAL_SECONDS= "$PYTHON" -c \
  'from flotilla.constants import HEARTBEAT_INTERVAL_SECONDS; print(HEARTBEAT_INTERVAL_SECONDS)')
FLEET_ROOT=$ROOT6 FLEET_CLAUDE_CMD="$TMP/bin/claude-park" FLEET_HEARTBEAT_INTERVAL_SECONDS= \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "interval-default: wrapper exits 0 with var unset" [ "$?" -eq 0 ]
assert "interval-default: slice still parks" [ "$(field "$ROOT6" 41 phase)" = "parked" ]
assert "interval-default: interval resolved from constants.py" \
  grep -q "heartbeat ${CONSTANTS_DEFAULT}s" "$ROOT6/41/runner.log"

# --- model/effort: env overrides win over the constants.py default ----------------

ROOT7="$TMP/fleet7"
FLEET_ROOT=$ROOT7 FLEET_CLAUDE_CMD="$TMP/bin/claude-park" \
  FLEET_MODEL=claude-sonnet-4-6 FLEET_EFFORT=medium \
  bash "$WRAP" 41 feat/slice-41-x 1 >/dev/null 2>&1
assert "model-override: wrapper exits 0" [ "$?" -eq 0 ]
assert "model-override: --model uses the env value" \
  grep -qx -- 'claude-sonnet-4-6' "$TMP/claude-args"
assert "model-override: --effort uses the env value" \
  grep -qx -- 'medium' "$TMP/claude-args"

# --- summary -----------------------------------------------------------------

if [ "$fails" -eq 0 ]; then
  echo "all runner-wrap tests passed"
  exit 0
fi
echo "$fails runner-wrap test(s) FAILED"
exit 1
