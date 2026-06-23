# flotilla

Deterministic supervisor + per-slice runner machinery for an **AFK, board-driven
Claude implementation fleet**. flotilla runs the implementation phase of an
engineering pipeline (`/tdd` → `/qa`) unattended across many vertical slices at
once, with board work-item state as the single source of truth at every tier.
flotilla speaks a provider-neutral 3-bucket `Lifecycle` (queued / active / done);
a `BoardAccess` adapter translates that to a concrete board's native semantics at
the boundary. Azure DevOps (the `az` CLI) is the adapter that ships today; GitHub
and GitLab are tracked backlog adapters the same contract-test suite will
validate. Every fleet process is stateless or short-lived and reconstructs its
view from the board on each run.

flotilla is the packaged, reusable extraction of the fleet originally built in
the `gswa` backend (design: ADR-0007 and its 2026-06-10 addendum, which lives in
that repo; the provider-neutral seam is [ADR-0001](docs/adr/adr-0001-board-provider-seam.md)
and its [design note](docs/design/board-provider-seam.md)). The package ships the
**deterministic machinery + its tests + scaffolding** — the agent-side skills
(`/afk-slice-runner`, `/tdd`, `/qa`, `/cleanup-merged-branches`) are
consumer-owned and live in the consuming repo. flotilla *scaffolds* genericized
templates for the ones it drives (`flotilla init`), then invokes them only by
**skill name** through `claude`: scaffolding the template is not owning it, so the
runtime boundary is unchanged.

> flotilla operates *on* a target repository, identified by `FLEET_HOME` (the
> current working directory by default). The installed package is never the
> working repo; the shell glue it drives ships as package data and is resolved
> via `importlib.resources`.

## Install

flotilla is built with [uv](https://docs.astral.sh/uv/) + Hatchling (PEP 621,
src layout). It has **no third-party runtime dependencies** (pure standard
library); `tmux`, `git`, the board provider's CLI (the `az` CLI for the ADO
adapter that ships today), and the `claude` CLI must be available on the host that
runs the fleet.

```bash
# From a clone (development):
uv sync                      # create .venv, install flotilla + dev tools

# As a dependency of another project (consumer):
#   editable path dep while iterating, or a git+HTTPS pin once stable
uv pip install -e /path/to/flotilla
```

A single unified `flotilla` CLI is installed (the API / composition root):

| Command | Role |
|---|---|
| `flotilla init [--provider P] [--check]` | scaffold an annotated `flotilla.toml`; `--check` validates it against the live board |
| `flotilla tick [--dry-run]` | run one supervisor tick in-process |
| `flotilla {start\|stop\|status\|log}` | hands-on ticker control (shells to the packaged `fleetctl.sh`) |
| `flotilla slice {init\|update\|heartbeat\|show}` | the per-slice status-file ops (used by the runner wrapper) |

`python -m flotilla.supervisor` (one tick) and `python -m flotilla.status` (the
status-file CLI) remain as internal module entry points. The `flotilla-status`
console script is kept as a **deprecated alias** for `flotilla slice` until the
coupled gswa PR migrates off it.

## Configuration

flotilla reads a `flotilla.toml` in the target repo (the *what* — which board,
which states, which skills) and layers it under environment + flag overrides. The
precedence, lowest to highest:

```
built-in defaults  <  flotilla.toml  <  FLEET_* env  <  CLI flag
```

`flotilla.toml` describes the *target* (like a kubeconfig context) and defaults the
*how* wherever it can. Schema:

| Section / key | Default | Meaning |
|---|---|---|
| `[board].provider` | `ado` | `ado` \| `github` \| `gitlab`. Selects the `BoardAccess` adapter (registry in the CLI composition root). ADO ships today; GitHub/GitLab are tracked backlog adapters. |
| `[board].base_branch` | `main` | The branch a slice PR must complete against for finalize-eligibility. |
| `[board].tag_prefix` | `fleet:` | Configurable namespace for the fleet's tags; detection is prefix-based (`startswith`). The five suffixes are fixed (see [Tag vocabulary](#tag-vocabulary)). |
| `[board].parent_scope_ids` | `[]` (whole project) | Optional claim-scope filter — only slices under these parents are claimable. Supersedes the legacy `FLEET_EPIC_IDS` env, which is still honored. |
| `[board.states].queued` / `.active` / `.done` | — | Lists of the board's *native* state names mapped onto the three neutral `Lifecycle` buckets (many-native→one-neutral allowed). **REQUIRED** unless the provider is ADO-Basic, which defaults to `["To Do"]` / `["Doing"]` / `["Done"]`. GitHub/GitLab statuses are user-defined, so they must be declared. |
| `[pipeline].branch_template` | `feat/slice-{id}-{slug}` | Slice branch naming. flotilla owns the `-a{attempt}` retry suffix (fixed rule, not templated). |
| `[pipeline].worktree_dir` | `.claude/worktrees` | Where slice worktrees are created. |
| `[pipeline].runner_skill` | `/afk-slice-runner` | Skill the runner wrapper invokes per slice. |
| `[pipeline].tdd_skill` | `/tdd` | TDD skill name, threaded into the runner prompt. |
| `[pipeline].qa_skill` | `/qa` | QA skill name, threaded into the runner prompt. |
| `[pipeline].cleanup_skill` | `/cleanup-merged-branches` | Skill the finalize pass runs headlessly per merged branch. |

```toml
[board]
provider         = "ado"          # ado | github | gitlab   (REQUIRED)
base_branch      = "main"
tag_prefix       = "fleet:"
parent_scope_ids = [105]          # optional; empty = whole project (was FLEET_EPIC_IDS)

[board.states]                    # REQUIRED unless provider is ADO-Basic; many-native→one allowed
queued = ["To Do"]
active = ["Doing"]
done   = ["Done"]

[pipeline]
branch_template = "feat/slice-{id}-{slug}"
worktree_dir    = ".claude/worktrees"
runner_skill    = "/afk-slice-runner"
tdd_skill       = "/tdd"
qa_skill        = "/qa"
cleanup_skill   = "/cleanup-merged-branches"
```

Operational and secret knobs stay **env-only** with the defaults in
[Constants](#constants): `FLEET_MAX_RUNNERS`, the intervals
(`FLEET_TICK_INTERVAL_SECONDS`, `FLEET_HEARTBEAT_INTERVAL_SECONDS`,
`FLEET_STALENESS_THRESHOLD_SECONDS`), `FLEET_MAX_ATTEMPTS`,
`FLEET_MODEL`/`FLEET_EFFORT`, `FLEET_HOME`/`FLEET_ROOT`/`FLEET_PYTHON`, and the PAT.
These are not in `flotilla.toml`.

Safety is **validate-against-board, not mandatory typing.** `validate_config()`
resolves the configured state names, tag prefix, and base branch against the
*live* board — at startup of every tick and on `flotilla init --check` — and fails
loud on any mismatch (e.g. "configured active state 'Doing' not found among this
project's states"). A typo can't silently strand or mis-claim slices.

### Scaffolding (`flotilla init`)

`flotilla init [--provider ado]` makes adoption one command plus a few edits. It
emits:

- a complete, **annotated** `flotilla.toml` — every key written with its default
  and `provider` taken from `--provider`; and
- the genericized, **consumer-owned** runner-skill and cleanup-skill templates.

The skill templates are provider/repo-agnostic (a neutral lifecycle: claim-verify
→ worktree → seams → tdd → qa → park) with clearly-marked fill-in sections (e.g.
`## Gates`, shared-seam conventions) that work out of the box. flotilla copies
them out, then drives them only by skill name through `claude` — copying the
template is not owning it, so the "machinery + tests + scaffolding" runtime
boundary holds. `flotilla init --check` runs `validate_config()` against the live
board without writing anything.

> The unified `flotilla init` CLI surface lands with the PR2 core; the scaffolding
> engine itself ships on this branch.

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
| `issue_id` | int | The slice's board work-item id |
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
flotilla slice init \
  --issue-id 41 --runner-id runner-41-a1 \
  --branch feat/slice-41-example --worktree "$FLEET_HOME/.claude/worktrees/feat+slice-41-example"

flotilla slice update --issue-id 41 --phase tdd
flotilla slice update --issue-id 41 \
  --phase parked --parked-state awaiting-pr-approval --pr-url <url>
flotilla slice update --issue-id 41 --phase tdd --parked-state none
flotilla slice heartbeat --issue-id 41
flotilla slice show --issue-id 41
```

`--fleet-root` overrides the location (used by tests; defaults to
`$FLEET_HOME/.claude/fleet`). `python -m flotilla.status …` is equivalent, as is
the deprecated `flotilla-status …` alias.

## Constants

All addendum constants live in `flotilla/constants.py` and are env-tunable
(read at process start — every fleet process is fresh per fire):

| Constant | Default | Env override |
|---|---|---|
| Fleet home (target repo) | current working directory | `FLEET_HOME` |
| Fleet root | `$FLEET_HOME/.claude/fleet` | `FLEET_ROOT` |
| Interpreter for the fleet | the supervisor's `sys.executable` (shell default `python3`) | `FLEET_PYTHON` |
| Max parallel runners (the *claim budget* — 0 stops new claims only, it is not a safety lever) | 2 | `FLEET_MAX_RUNNERS` |
| Dry-run tick (plan + report, suppress every side effect) | off | `FLEET_DRY_RUN` |
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

<a id="tag-vocabulary"></a>
Tag vocabulary (parked sub-states are **tags**, not states — the ADO Basic
process has only To Do / Doing / Done). The five suffixes —
`claimed`, `failed`, `needs-decision`, `qa-ready`, `awaiting-pr-approval` — are
fixed canonical vocabulary, carried under a **configurable namespace prefix**
(default `fleet:`, set via `[board].tag_prefix` / `FLEET_TAG_PREFIX`), so the
shipping defaults read `fleet:claimed`, `fleet:failed`, `fleet:needs-decision`,
`fleet:qa-ready`, `fleet:awaiting-pr-approval`. Fleet-tag detection is
prefix-based (`startswith(prefix)`), not a hardcoded literal set.

The neutral comment the fleet attaches at each transition is emitted by core as a
structured event and rendered to the board's native markup at the adapter
boundary (ADO → HTML, GitHub → Markdown) — core itself emits no markup.

Tuning path for the cap (addendum §1): raise as cores/headroom grow; back off on
CPU saturation or 429s.

## Slice runners

A runner is one short-lived, headless Claude session driving one slice. The
supervisor launches each into its own per-slice ephemeral Docker compose project
via `SandboxAccess` (ADR-0002 §5): the compose `agent` service's command *is* the
deterministic wrapper (`flotilla/_scripts/runner-wrap.sh`, resolved from the
installed package and invoked as `runner-wrap.sh <issue-id> <branch> [attempt]`),
so container lifecycle == agent lifecycle and `docker inspect .State.ExitCode`
*is* the agent exit code.

The wrapper owns everything that must not depend on an LLM:

- seeds `status.json` (`init`), records the `runner.pid` / `pane-id` /
  `heartbeat.pid` sidecars, and appends all output to `<issue-id>/runner.log`;
- runs the **heartbeat loop** — `last_heartbeat` advances every heartbeat
  interval for exactly as long as the wrapper process lives, so liveness means
  *process alive*, independent of how long the agent's current tool call runs;
- invokes the headless session, threading the **configured** skill names into the
  prompt: `claude -p "<runner_skill> issue-id=… branch=… attempt=… tdd-skill=…
  qa-skill=…" --dangerously-skip-permissions --model "$FLEET_MODEL" --effort
  "$FLEET_EFFORT"`. Because the runner/tdd/qa skill names are config (not
  hardcoded), the runner skill no longer hardcodes `/tdd`,`/qa` — it runs whatever
  names it is handed;
- **backstops** an unexpected death: a healthy runner always exits `parked` (or
  `done`); if the session exits in any other phase, the wrapper stamps
  `parked_state=failed` + `last_error` and propagates the non-zero exit.

The `afk-slice-runner` skill (in the consuming repo) is the agent side of the
contract: verify the claim, enter the slice worktree, write the shared seams
before any fan-out, execute the configured tdd then qa skills **unchanged**,
update `phase`/`pr_url`/`worker_roster` at transitions, park with the matching
fleet tag + comment, exit. Parked states are never a hung session — they are queryable board
state plus the status file.

Runner wrapper env knobs: `FLEET_HOME`, `FLEET_ROOT`,
`FLEET_HEARTBEAT_INTERVAL_SECONDS`, `FLEET_MODEL`, `FLEET_EFFORT`,
`FLEET_RUNNER_SKILL`, `FLEET_TDD_SKILL`, `FLEET_QA_SKILL` (the supervisor injects
these into the pane env; when any is unset — e.g. a manual run — the wrapper
resolves the default from `flotilla.config` / `flotilla.constants`, the single
source of truth, the same fallback pattern as `FLEET_MODEL`/`FLEET_EFFORT`/the
interval), `FLEET_PYTHON`, `FLEET_CLAUDE_CMD` (stubbed in the hermetic tests).

## Supervisor

`flotilla/supervisor.py` is the deterministic, token-free tick: no LLM anywhere,
so it cannot hallucinate a board mutation, and it is unit-tested against in-memory
fakes (including a divergent GitHub-shaped fake, which catches any hardcoded
native-state leak and proves the core is provider-blind). It speaks the neutral
`Lifecycle` throughout; the `BoardAccess` adapter maps to native states at the
boundary. Each tick runs three ordered passes under one lock — **finalize → reap →
claim** — so cap accounting is fresh before anything new launches (addendum §5).
The native state names below (`To Do`/`Doing`/`Done`) are the **ADO-Basic
mapping** of the neutral `queued`/`active`/`done` `Lifecycle` buckets; under
another provider the adapter substitutes that board's configured names.

1. **Serialize** — take a non-blocking `flock` on `<fleet-root>/supervisor.lock`;
   a tick that cannot get the lock exits 0 without touching the board.
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
   slice, as the `agent`-service command of its own per-slice ephemeral Docker
   compose project via `SandboxAccess` (build + `compose up -d`, non-blocking;
   `docker compose logs` against the slice project is the live view). A failed
   launch rolls the claim back (tag removed, `Doing → To Do`, comment), so no
   slice is stranded.

Only **claim/launch** depends on credentials beyond the board reads: a working
ADO PAT (claiming a slice does host-side git remote ops — worktree create off
`origin/main`, then push — over HTTPS+PAT, no SSH key) and a working `claude`
(the contained runner is the fleet's single LLM call — finalize and reap are
deterministic). A tick with claim work pending runs **two preflights** first, in
order, and short-circuits on the first failure:

1. **PAT preflight** — `git ls-remote` against the target remote (the live
   checkout's `origin`, else `FLEET_APP_REPO_URL`) using the env-var PAT
   credential helper, non-interactively (`GIT_TERMINAL_PROMPT=0`) with a 30s
   timeout. This exercises the exact auth path every host-side git op uses, so it
   cannot pass while a real claim's git op fails. A rejected/expired/wrong-scope
   PAT, an unreachable remote, a timeout, or no `git` all read as a failure.
2. **claude preflight** — a throwaway
   `claude -p 'reply READY' --dangerously-skip-permissions --model "$FLEET_MODEL"`
   probe with a 120s hard timeout, passing only on exit 0 plus `READY` in stdout
   (dead auth, a transient API outage, and an unavailable model read identically).

On either failed probe the tick **degrades to the reap pass only** — every
claim/launch decision is dropped (no slice is claimed, no `To Do → Doing`, no
`fleet:claimed`), while in-flight finalize and reap still proceed — and retries
next tick. The PAT failure logs one actionable line naming the `fleet-ado-pat`
Key Vault secret and the rotation runbook (the consuming repo's
`docs/contributing/afk-fleet.md` → *Key Vault secrets & PAT rotation*). The PAT
probe runs first, so a dead PAT never pays to spawn `claude`. Idle, saturated,
and finalize-only ticks never pay for either probe.

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

**Dry run** — `flotilla tick --dry-run` (or `FLEET_DRY_RUN=1`, or
`python -m flotilla.supervisor --dry-run`) runs the same three passes' read+plan logic
and reports the would-be actions, with every side effect suppressed at the
`TickSeams` boundary (`dry_run_seams`): board writes become logged
`[dry-run] WOULD …` no-ops, no runner pane launches, neither preflight probe runs
(no `git ls-remote` for the PAT, no `claude` spawn for auth), no worktree is
archived, no local status/marker file is written. Safety is the wrapped boundary,
not a flag inside the passes, so a future pass cannot forget to honor it. Only the
tick lock and the supervisor log are still written — coordination artifacts,
not fleet state.

Scoping: `[board].parent_scope_ids` (a list of parent work-item ids, optional)
restricts claiming to slices under those parents; it supersedes the legacy
`FLEET_EPIC_IDS` env (comma-separated Epic ids), which is still honored. Empty
(the default) means every unblocked `queued` work item in the project is
eligible.

### Host-side git hardening (sandbox-escape control)

The slice agent runs in a sandbox with the slice worktree bind-mounted as
`/work`, and it commits there — so a prompt-injected or misbehaving agent can
plant a git hook (`pre-push`, `post-checkout`, …) or set `core.hooksPath` to an
agent-controlled directory inside the worktree. Host-side git ops later run
against that same worktree in the **supervisor** context, which holds the ADO PAT
and the VM managed identity. A hook firing there would be a sandbox escape into
the credential-holding host.

flotilla closes this by routing **every** host-side git invocation through
`flotilla.git_host`, which:

- pins `-c core.hooksPath=/dev/null` on the argv (a command-line `-c` outranks any
  config the agent set, so neither a planted hook file nor an agent-set
  `core.hooksPath` can execute); and
- on a checkout op, adds `-c safe.directory=<that exact path>` to clear git's
  dubious-ownership refusal when the op runs as a different OS user than the
  `FLEET_HOME`/worktree owner — **scoped to the path, never `safe.directory=*`**
  (a wildcard would trust every repo on the host and, paired with a planted hook,
  reopen the escape).

Both are transient command-line overrides — never written to `.git/config`, so the
agent cannot strip them. The worktree create/archive/prune, branch delete, the
`base..HEAD` commit count, and the app-repo bootstrap all inherit this automatically
by going through the builder. The host-side **push** of a slice's commits — the
credential-holding op the agent does not perform — is not yet wired in code (the
deferred write-tail in the supervisor's `_handoff`); when it is, it must be built as
`host_git_argv(*credential_helper, "push", "origin", branch, work_dir=worktree)`, and
`tests/test_git_host.py` already proves that contract neutralizes a planted `pre-push`
hook (with a bare-push control), so a push that bypasses the builder is a reviewable
regression rather than a silent escape.

**Manual operator pushes** against a fleet worktree must carry the same guard —
run e.g. `git -c core.hooksPath=/dev/null -c safe.directory="$PWD" push …` (or
export `GIT_CONFIG_PARAMETERS`) rather than a bare `git push`, so an operator's
hands-on op cannot trip a planted hook either.

## Activation (manual, opt-in)

Nothing starts the fleet automatically. Scope claiming with
`[board].parent_scope_ids` (or the legacy `FLEET_EPIC_IDS`) before enabling. Two
levers compose:

- **A dry run first** (the safest first step): `flotilla tick --dry-run` (or
  `FLEET_DRY_RUN=1`) runs the full finalize/reap/claim read+plan logic and logs
  every action a real tick WOULD take, but cannot mutate — board writes, runner
  launches, the preflight probes (the PAT `git ls-remote` and the `claude` auth
  spawn), and local fleet-state writes are all suppressed at the seams. Ticks log to
  `$FLEET_ROOT/supervisor.log`, so review the plan with `flotilla log`.
- **One tick by hand**: `flotilla tick` — logs to `$FLEET_ROOT/supervisor.log`.
  Note that `FLEET_MAX_RUNNERS=0` is **not** a read-only tick: it only zeroes
  the *claim budget*; finalize and reap still mutate the board (drop fleet tags,
  comment PR links, run the headless cleanup, move `Doing → To Do`). For a tick
  that cannot mutate, use `--dry-run`.
- **The fleet-host (production): systemd.** The dedicated fleet-host VM schedules
  ticks with systemd, not tmux (ADR-0002 §11): a `flotilla.timer` fires a oneshot
  `flotilla.service` running `flotilla fleet-tick`, which fetches the PAT +
  `ANTHROPIC_API_KEY` from Key Vault via the VM managed identity, syncs the app
  repo, and runs one tick. Render + install the units with `flotilla install-units
  --key-vault <kv> --fleet-home <checkout> [...]`; this does **not** enable the
  timer. Provisioning, the on-host acceptance (`goss`), and activation
  (`systemctl enable --now flotilla.timer`) live in
  [`docs/fleet-host/`](docs/fleet-host/SMOKE.md).
- **Local / dev on demand: tmux.** `flotilla {start|stop|status|log}` drives a
  detached `fleet-ticker` tmux session whose loop fires one tick every
  `FLEET_TICK_INTERVAL_SECONDS` (default 180): `start` is idempotent, `stop` kills
  it (in-flight runners in the separate `fleet` session keep going), `status`
  reports + tails, `log` tails (`-f` to follow). This is for hands-on local runs;
  the fleet-host uses systemd, above. (The boot-time `fleet-autostart.sh` / cron
  autostart paths have been retired in favor of systemd.)

Each fire is a fresh supervisor process under the same lock (the timer is only the
schedule, so crash-only semantics are preserved). In-flight slice state is
reconstructed from the board plus the bind-mounted `.claude/fleet/` status files,
so a re-started ticker resumes cleanly.

Watch the fleet: `flotilla status` (is-it-running + recent log), `flotilla log
-f` (follow the supervisor log live), the board (`fleet:*` tags) is the macro
view, per-slice `status.json` is the micro view, `docker compose logs` against a
slice's compose project is the live agent view.

## Development

```bash
uv sync                        # env + deps
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run pyright                 # strict type check
uv run pytest                  # unit + hermetic shell tests
```

The shell glue (`runner-wrap.sh`, `fleet-tick.sh`, `fleetctl.sh`) ships as package
data under `src/flotilla/_scripts/`. Anything that needs to invoke it — the
supervisor's `SandboxAccess` launch, the `flotilla` dispatcher, the tick entry
point — resolves it via `flotilla._resources.resolve_script(...)` (`importlib.resources` +
`chmod +x`), never a path relative to `FLEET_HOME`. The fleet-host systemd unit
templates ship the same way under `src/flotilla/_units/` (resolved via
`resolve_unit(...)`, rendered by `flotilla.units`).
