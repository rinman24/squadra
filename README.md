# flotilla

Deterministic supervisor + per-slice runner machinery for an **AFK, board-driven
Claude implementation fleet**. flotilla runs the implementation phase of an
engineering pipeline (`/tdd` → `/qa`) unattended across many vertical slices at
once, with Azure DevOps work-item state as the single source of truth at every
tier. Every fleet process is stateless or short-lived and reconstructs its view
from ADO on each run.

flotilla is the packaged, reusable extraction of the fleet originally built in
the `app` backend. The package ships the **deterministic machinery + its tests only** —
the agent-side skills (`/afk-slice-runner`, `/tdd`, `/qa`,
`/cleanup-merged-branches`) live in the consuming repo.

> flotilla operates *on* a target repository, identified by `FLEET_HOME` (the
> current working directory by default). The installed package is never the
> working repo; the shell glue it drives ships as package data and is resolved
> via `importlib.resources`.

## Install

flotilla is built with [uv](https://docs.astral.sh/uv/) + Hatchling (PEP 621,
src layout). It has **no third-party runtime dependencies** (pure standard
library); `tmux`, `git`, the Azure CLI (`az`), and the `claude` CLI must be
available on the host that runs the fleet.

```bash
# From a clone (development):
uv sync                      # create .venv, install flotilla + dev tools

# As a dependency of another project (consumer):
#   editable path dep while iterating, or a git+HTTPS pin once stable
uv pip install -e /path/to/flotilla
```

Three console scripts are installed:

| Command | Role |
|---|---|
| `flotilla {start\|stop\|status\|tick\|log}` | the hands-on ticker control (the `fleetctl` dispatcher) |
| `flotilla-supervisor` | one supervisor tick (`python -m flotilla.supervisor`) |
| `flotilla-status` | the per-slice status-file CLI (`python -m flotilla.status`) |

## Status file + heartbeat convention

Each in-flight slice has one status file — the *micro view* of its runner:

```
$FLEET_HOME/.claude/fleet/<issue-id>/status.json
```

The fleet root defaults under the target repo's `.claude/fleet`; on a
bind-mounted checkout it survives container restart. Exclude it from the target
repo's git (`**/.claude/fleet/`).

### Schema

| Field | Type | Meaning |
|---|---|---|
| `issue_id` | int | The slice's ADO Issue id |
| `runner_id` | str | Unique id of the runner attempt (e.g. `runner-41-a1-…`) |
| `branch` | str | Slice branch (`feat/slice-<id>-<kebab>`) |
| `worktree` | str | Absolute path of the slice's git worktree |
| `pr_url` | str \| null | The slice's PR once opened |
| `phase` | enum | `claiming` → `seams` → `tdd` → `qa` → `parked` → `done` |
| `parked_state` | enum \| null | `needs-decision`, `qa-ready`, `awaiting-pr-approval`, `failed` — non-null iff `phase` is `parked` |
| `worker_roster` | list[str] | Worker sub-agents the runner fanned out |
| `started_at` | str | ISO 8601 UTC, set at init |
| `last_heartbeat` | str | ISO 8601 UTC, stamped every heartbeat interval while the runner process is alive |
| `attempt` | int | 1-based attempt counter (transient failures retry up to 3) |
| `last_error` | str \| null | Last recorded failure diagnostic |

### Concurrency contract

Two writers touch the file: the runner wrapper's deterministic heartbeat loop
(liveness = *process alive*, independent of what the agent is doing) and the
agent updating `phase`/`parked_state`/`worker_roster` at transitions. Every
read-modify-write therefore happens under a sidecar `flock`
(`<issue-id>/.status.lock`) and lands atomically via tmp-file + rename. Readers
never see partial JSON; interleaved writers never lose fields.

### CLI

Shell callers (the runner wrapper, skills) use the status CLI:

```bash
flotilla-status init \
  --issue-id 41 --runner-id runner-41-a1 \
  --branch feat/slice-41-example --worktree "$FLEET_HOME/.claude/worktrees/feat+slice-41-example"

flotilla-status update --issue-id 41 --phase tdd
flotilla-status update --issue-id 41 \
  --phase parked --parked-state awaiting-pr-approval --pr-url <url>
flotilla-status update --issue-id 41 --phase tdd --parked-state none
flotilla-status heartbeat --issue-id 41
flotilla-status show --issue-id 41
```

`--fleet-root` overrides the location (used by tests; defaults to
`$FLEET_HOME/.claude/fleet`). `python -m flotilla.status …` is equivalent.

## Constants

All addendum constants live in `flotilla/constants.py` and are env-tunable
(read at process start — every fleet process is fresh per fire):

| Constant | Default | Env override |
|---|---|---|
| Fleet home (target repo) | current working directory | `FLEET_HOME` |
| Fleet root | `$FLEET_HOME/.claude/fleet` | `FLEET_ROOT` |
| Interpreter for the fleet | the supervisor's `sys.executable` (shell default `python3`) | `FLEET_PYTHON` |
| Max parallel runners | 2 | `FLEET_MAX_RUNNERS` |
| Heartbeat interval | 60s | `FLEET_HEARTBEAT_INTERVAL_SECONDS` |
| Staleness threshold | 600s | `FLEET_STALENESS_THRESHOLD_SECONDS` |
| Max attempts | 3 | `FLEET_MAX_ATTEMPTS` |
| Model | `claude-opus-4-8` | `FLEET_MODEL` |
| Reasoning effort | `high` | `FLEET_EFFORT` |

The compute tier (`FLEET_MODEL` / `FLEET_EFFORT`) is pinned in `constants.py` and
applied as explicit `claude --model … --effort …` flags to **every** model-backed
fleet call — the slice runner, the cleanup pass, and the auth probe (the probe
pins `--model` only, since it does no reasoning). This is deliberate: a headless
`claude -p` otherwise inherits whatever `model` an interactive session's
`settings.json` happens to pin, so the fleet's tier would be an ambient side
effect rather than a choice. Effort accepts the CLI levels
`low|medium|high|xhigh|max` (model-dependent). To run hotter or cheaper, export
`FLEET_MODEL` / `FLEET_EFFORT` — it flows through autostart → ticker → supervisor
→ runner panes by environment inheritance.

`FLEET_PYTHON` must point at an interpreter that has `flotilla` installed; the
supervisor injects its own `sys.executable` into each runner pane so the runner
reaches `flotilla.*` regardless of what `python3` resolves to on PATH.

Tag vocabulary (parked sub-states are **tags**, not states — the ADO Basic
process has only To Do / Doing / Done): `fleet:claimed`, `fleet:failed`,
`fleet:needs-decision`, `fleet:qa-ready`, `fleet:awaiting-pr-approval`.

Tuning path for the cap (addendum §1): raise as cores/headroom grow; back off on
CPU saturation or 429s.

## Slice runners

A runner is one short-lived, headless Claude session driving one slice. The
supervisor launches each into its own detached-tmux pane running the
deterministic wrapper (`flotilla/_scripts/runner-wrap.sh`, resolved from the
installed package and invoked as `runner-wrap.sh <issue-id> <branch> [attempt]`).

The wrapper owns everything that must not depend on an LLM:

- seeds `status.json` (`init`), records the `runner.pid` / `pane-id` /
  `heartbeat.pid` sidecars, and appends all output to `<issue-id>/runner.log`;
- runs the **heartbeat loop** — `last_heartbeat` advances every heartbeat
  interval for exactly as long as the wrapper process lives, so liveness means
  *process alive*, independent of how long the agent's current tool call runs;
- invokes the headless session: `claude -p "/afk-slice-runner issue-id=… branch=…
  attempt=…" --dangerously-skip-permissions --model "$FLEET_MODEL" --effort
  "$FLEET_EFFORT"`;
- **backstops** an unexpected death: a healthy runner always exits `parked` (or
  `done`); if the session exits in any other phase, the wrapper stamps
  `parked_state=failed` + `last_error` and propagates the non-zero exit.

The `afk-slice-runner` skill (in the consuming repo) is the agent side of the
contract: verify the claim, enter the slice worktree, write the shared seams
before any fan-out, execute `/tdd` then `/qa` **unchanged**, update
`phase`/`pr_url`/`worker_roster` at transitions, park with the matching ADO tag +
comment, exit. Parked states are never a hung session — they are queryable board
state plus the status file.

Runner wrapper env knobs: `FLEET_HOME`, `FLEET_ROOT`,
`FLEET_HEARTBEAT_INTERVAL_SECONDS`, `FLEET_MODEL`, `FLEET_EFFORT` (the supervisor
injects these into the pane env; when any is unset — e.g. a manual run — the
wrapper resolves the default from `flotilla.constants`, the single source of
truth), `FLEET_PYTHON`, `FLEET_CLAUDE_CMD` (stubbed in the hermetic tests).

## Supervisor

`flotilla/supervisor.py` is the deterministic, token-free tick: no LLM anywhere,
so it cannot hallucinate an ADO mutation, and it is unit-tested against in-memory
fakes. Each tick runs three ordered passes under one lock — **finalize → reap →
claim** — so cap accounting is fresh before anything new launches (addendum §5).

1. **Serialize** — take a non-blocking `flock` on `<fleet-root>/supervisor.lock`;
   a tick that cannot get the lock exits 0 without touching ADO.
2. **Count inflight** — Issues in `Doing` carrying `fleet:claimed`. A human's
   manually-moved `Doing` Issue is invisible to the fleet (no tag): never
   counted, never reaped.
3. **Claim** up to `FLEET_MAX_RUNNERS − inflight` *available* Issues, lowest id
   first. *Available* = `To Do`, no `fleet:*` tag, every Predecessor-linked Issue
   `Done`. Claim = `To Do → Doing` + tag `fleet:claimed` + a stamped comment,
   plus a local `claimed-at` marker for the watchdog. The branch is derived
   deterministically: `feat/slice-<id>-<kebab-of-title>` (suffix `-aN` on
   retries).
4. **Launch** — one `runner-wrap.sh <issue-id> <branch> <attempt>` per claimed
   slice, into its own pane of the detached `fleet` tmux session
   (`tmux attach -t fleet` is the live view). A failed launch rolls the claim
   back (tag removed, `Doing → To Do`, comment), so no slice is stranded.

Finalize and claim depend on a working `claude`; reap needs only `az` and `git`.
A tick with such work pending runs an **auth preflight** first: a throwaway
`claude -p 'reply READY' --dangerously-skip-permissions --model "$FLEET_MODEL"`
probe with a 120s hard timeout, passing only on exit 0 plus `READY` in stdout. On
a failed probe — dead auth, a transient API outage, and an unavailable model read
identically — the tick degrades to the reap pass only and retries next tick. Idle
and saturated ticks never pay for the probe.

**Finalize** retires slices that are truly done: Issue `Done` *and* a completed
PR for the slice branch. For each, it runs the consuming repo's
`/cleanup-merged-branches` skill headlessly for that branch, drops every `fleet:*`
tag, comments the PR link, and sets the status phase to `done`. A failed cleanup
is retried next tick.

**Reap (watchdog)** recovers from dead runners, with two independent guards
before it acts: *stale* (best liveness evidence older than the staleness
threshold) **and** *dead* (the `runner.pid` sidecar process no longer exists). A
stale-but-alive runner is always left alone. A **failed park**
(`parked_state=failed` with no parked tag) is positive failure evidence and skips
the staleness wait — reaped immediately once the pid is confirmed dead.
Deliberately parked runners are never reaped. Reaping archives the dead worktree
to `.claude/fleet/<issue-id>/archive/attempt-N/`, records the reap, drops
`fleet:claimed`, and moves `Doing → To Do`; the next claim retries with
attempt+1, and exhausted retries escalate to `fleet:failed` instead.

Scoping: `FLEET_EPIC_IDS` (comma-separated Epic ids, optional) restricts claiming
to slices under those Epics. Empty (the default) means every unblocked `To Do`
Issue in the project is eligible.

## Activation (manual, opt-in)

Nothing starts the fleet automatically. Scope claiming with `FLEET_EPIC_IDS`
before enabling. Two levers compose:

- **One tick by hand** (the safest first step): `flotilla tick` — logs to
  `$FLEET_ROOT/supervisor.log`. For a read-only smoke tick that mutates nothing,
  run `FLEET_MAX_RUNNERS=0 flotilla tick`: it still authenticates to ADO and runs
  the query/finalize/reap passes, but the claim pass launches nothing.
- **Start / stop / status / log on demand**:
  `flotilla {start|stop|status|log}`. `start` launches a detached `fleet-ticker`
  tmux session whose loop fires one supervisor tick every
  `FLEET_TICK_INTERVAL_SECONDS` (default 180), if not already running
  (idempotent); `stop` kills it (in-flight runners in the separate `fleet`
  session keep going); `status` reports whether the ticker is running and tails
  the supervisor log; `log` tails the log (`-f` to follow, `-n N` for N lines).
  Honors `FLEET_TICKER_SESSION` and `FLEET_TICK_INTERVAL_SECONDS`.
- **Autostart on container (re)start**: wire `flotilla/_scripts/fleet-autostart.sh`
  into the container's compose `command`. It is a no-op unless `FLEET_AUTOSTART`
  is truthy, and idempotently starts the `fleet-ticker` session on boot.
- **Cron** (optional, for images that ship one): see
  `flotilla/_scripts/fleet-cron.example` (`*/3 * * * *`).

Each fire is a fresh supervisor process under the same lock (the loop or cron is
only the timer, so crash-only semantics are preserved). In-flight slice state is
reconstructed from ADO plus the bind-mounted `.claude/fleet/` status files, so a
re-started ticker resumes cleanly.

Watch the fleet: `flotilla status` (is-it-running + recent log), `flotilla log
-f` (follow the supervisor log live), the board (`fleet:*` tags) is the macro
view, per-slice `status.json` is the micro view, `tmux attach -t fleet` is the
live pane view.

## Development

```bash
uv sync                        # env + deps
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run pyright                 # strict type check
uv run pytest                  # unit + hermetic shell tests
```

The shell glue (`runner-wrap.sh`, `fleet-tick.sh`, `fleet-autostart.sh`,
`fleetctl.sh`, `fleet-cron.example`) ships as package data under
`src/flotilla/_scripts/`. Anything that needs to invoke it — the supervisor's
tmux launcher, the `flotilla` dispatcher, the tick entry point — resolves it via
`flotilla._resources.resolve_script(...)` (`importlib.resources` + `chmod +x`),
never a path relative to `FLEET_HOME`.
