# ADR-0002 — Sandbox containment + dedicated fleet-host

- Status: **Accepted** — resolved via `/grill-with-docs` 2026-06-15; implemented
  across slices F1–F5 (the pure `LifecycleEngine`, the `SandboxAccess` /
  `CleanupAccess` / `WorktreeAccess` seams, the orchestration cutover, and the
  fleet-host runtime + secret bootstrap).
- Date: 2026-06-15
- Context origin: gswa ADR-0007 (+ 2026-06-10 addendum) — the fleet's original,
  uncontained tmux design. The gswa-consumer side of this decision (sandbox image
  stage, `.flotilla/` compose, skill-contract rewrite) is recorded in the gswa
  ADR-0007 containment addendum.
- Builds on: [ADR-0001](adr-0001-board-provider-seam.md) (the `BoardAccess` seam +
  configure-not-customize posture this extends to the new ResourceAccess seams).

## Context

The original AFK fleet (ADR-0007) ran each slice's Claude agent **uncontained** on
the developer dev container: the agent shared the host's credentials (the ADO PAT,
the Anthropic key), had unrestricted network, and did its own board/remote writes.
That is an unacceptable security boundary for an unattended agent that executes
model-authored code — a prompt-injection or a runaway run reaches the PAT, the
remote, and the whole network.

This ADR gives the fleet a real boundary: run each slice's agent in a **per-slice
ephemeral Docker compose project** (a *sandbox*) with no credentials and no network
except the Anthropic API, and move the supervisor onto a **dedicated Azure Linux
fleet-host VM** that holds the PAT + the Docker socket and performs all board /
remote / secret operations host-side. The agent becomes **commit-only**.

It realizes this in Python, extending flotilla, and **aligns with — but does not
complete** — the Löwy closed-architecture target (new ResourceAccess seams + a pure
engine; no `SupervisorManager` restructure yet, which stays the standing P2).

## Decision

The resolved design — the spine the slices implement. Section numbers are stable:
the code cites them (e.g. `§5`, `§11`, `decision 2`).

1. **Handoff model — outcome manifest + host finalize.** The agent is a single
   end-to-end headless session that writes a structured `.flotilla/outcome.json`
   into the bind-mounted `/work` worktree as its final act, then exits 0. The
   supervisor reads it host-side and performs **all** board/remote writes via
   `BoardAccess` (push, PR open, work-item links, QA Task, comments, tags). Per-Task
   live board mirroring during TDD is dropped in the fleet path.

2. **Worktree ownership is host-side.** Because the agent has no remote, the
   supervisor owns git: it fetches `origin/main`, creates the slice worktree on the
   branch, and bind-mounts it as `/work` **before** launch. The agent starts already
   on the branch (no `EnterWorktree` in the agent). Sandbox git identity is a
   throwaway local `user.name/email`, no credentials. Realized by `WorktreeAccess`.

3. **Completion signal = the `(exit, manifest, commits)` triple.** The FSM keys the
   transition on `(container_exit_code, manifest_present_and_valid, commits in
   base..HEAD)`: exited-0 + valid manifest + commits → finalize per `parked_state`;
   `parked_state=needs-decision` → tag + comment, no PR; exited-nonzero or
   malformed manifest → crash edge; alive + heartbeat stale → `agent-timeout`.

4. **`LifecycleEngine` = subsuming, derived-state, fact-driven FSM.** One pure
   `facts → (State, actions)` engine subsumes the old finalize/reap/claim passes.
   `State` is a **projection of observed reality each tick**, not independently
   persisted — preserving crash-only idempotence. The claim **budget** is
   cross-slice, so it stays an orchestrator concern (the engine emits per-slice
   `CLAIMABLE`; the orchestrator claims up to `cap`).

5. **Sandbox exec model — agent-as-command, one-shot, inspect-driven.** The compose
   `agent` service's command *is* `runner-wrap → claude`. `SandboxAccess.launch`
   does build + `compose up -d` and returns immediately (non-blocking tick
   preserved); container lifecycle == agent lifecycle; `docker inspect
   .State.ExitCode` *is* the agent exit code. `SandboxAccess` = `launch / inspect /
   logs / teardown / exec`; it **replaces the old `Launcher`** and absorbs
   `pid_alive` into `inspect`. tmux leaves the runner path.

6. **Finalize cleanup — deterministic `CleanupAccess`, no LLM.** The supervisor
   knows the merged branch (it opened the PR), so finalize deletes the branch +
   prunes the worktree + `compose down -v` deterministically. The fleet makes
   exactly **one** claude call (the contained runner); the auth-probe guards the
   claim path only.

7. **Failure-edge policy — differentiated.** `build-failed` + agent-crash +
   `agent-timeout` → retry under `max_attempts`, escalate on exhaustion.
   `egress-denied` → **escalate immediately** (security signal; comment the denied
   host, extracted from the egress-proxy log). `teardown-failed` → orthogonal
   non-blocking leak-sweep; never blocks the slice's board lifecycle.

8. **Egress — tight allowlist + dual-homed proxy + `internal` net.** agent +
   throwaway `sql`/`redis` + egress-proxy on an `internal: true` network (the agent
   has no default route out). The dual-homed egress-proxy is the only path out;
   agent sets `HTTPS_PROXY=http://egress-proxy:8888` + `NO_PROXY` for the sidecar
   names. Allowlist is **`api.anthropic.com:443` only** (resolved empirically). The
   CLI's auto-updater + telemetry + error reporting are disabled via the umbrella
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. IMDS `169.254.169.254` is
   unreachable two ways (no route on the internal net; the proxy refuses CONNECT to
   it). The proxy is **tinyproxy** (`FilterDefaultDeny On` + anchored
   `^api\.anthropic\.com$` + `ConnectPort 443 563`), chosen over squid on footprint.

9. **Sandbox image — purpose-built `sandbox` stage on `builder-base`.** Reuses
   gswa's `builder-base` venv (= CI deps, zero parity gap) + pinned `claude`
   (updater off) + `git` + the test toolchain; strips `az`, `tmux`, `sudo`, and the
   baked `COPY . .` (source arrives via `/work`). gswa-owned (the consumer side).

10. **Dependency-changing slices park `needs-decision`.** A `uv.lock`/`pyproject`
    edit makes the baked venv stale and `uv sync` needs blocked PyPI egress. The
    runner detects this early and parks `needs-decision` — keeps egress tight.

11. **Fleet-host VM — systemd + managed-identity → Key Vault.** A dedicated Azure
    Linux VM with native Docker. `flotilla.timer` fires a oneshot `flotilla.service`
    running `flotilla fleet-tick` every N minutes (crash-only per tick fits
    `Type=oneshot`). flotilla is installed into a pinned VM venv; cloud-init
    bootstraps Docker + the venv + the units. The VM **managed identity → IMDS → Key
    Vault** `get` reads the PAT + `ANTHROPIC_API_KEY`: the supervisor holds the PAT
    in its own process env (for `BoardAccess`); the compose `agent` env carries
    **only `ANTHROPIC_API_KEY`** (`flotilla.secrets.agent_environ` is the enforced
    projection; the PAT-exclusion is contract-tested). Secrets are fetched per tick
    into the process env and **never written to disk** (no `EnvironmentFile`). This
    retires `fleet-autostart.sh` / `fleet-cron.example` / the boot-time tmux ticker
    in favor of systemd. `flotilla install-units` renders the packaged unit
    templates against the host; installing them does **not** enable the timer.

12. **Resource bounds — two-lever.** `FLEET_MAX_RUNNERS` keeps its meaning (claim
    budget = max concurrent slices), re-sized to *VM capacity ÷ per-slice
    footprint*; per-container `cpu`/`mem` limits live in the `.flotilla/` compose.
    `MAX_RUNNERS=0` stays claim-suppression-only.

13. **Skill contracts — fleet "produce-artifacts" mode.** Reads move host-side (the
    supervisor injects a read-only `.flotilla/slice.json`); write-tails move
    host-side via the manifest (`/tdd` fleet mode commits + drafts PR title/body into
    the manifest; `/qa` fleet mode writes `qa.md` + records its path; the runner
    emits the manifest instead of writing tags). Skills stay human-usable.

14. **Löwy fit — align, defer formal restructure.** New ResourceAccess contracts
    (`SandboxAccess`, `CleanupAccess`, `WorktreeAccess`) + the pure `LifecycleEngine`;
    `run_tick` refactored to facts→engine→execute; dry-run wrappers extended to the
    new seams. **No** `SupervisorManager` restructure — that stays the P2.

15. **Scope — gswa-only execution, multi-repo-ready seam shape.** Ship containment
    for the gswa fleet only; the `.flotilla/` compose + `sandbox` stage are
    target-repo-owned by convention; `SandboxAccess` is provider-blind in shape.
    Multi-repo is a later config extension, not a rearchitecture.

16. **Cutover — clean, seam-granular.** The container path **replaces** the tmux
    path (no dual-substrate flag). Safety = contract tests + flotilla dry-run, then a
    staged live smoke on the new VM **before** enabling the systemd timer. Fleet
    activation is manual/opt-in, so there is no live prod to regress.

## Consequences

- The agent can no longer leak the PAT or reach the network beyond Anthropic; a
  compromised run is contained to a throwaway compose project and a commit-only
  worktree.
- The supervisor is the single writer to the board/remote, simplifying idempotence
  and auditing, at the cost of a host-side finalize step the agent used to do.
- Operating the fleet now requires a provisioned VM + Key Vault + a managed-identity
  `get` grant; the on-host acceptance is codified as an executable `goss` spec rather
  than a manual checklist (see `docs/fleet-host/`).
- The tmux *ticker* is retired on the fleet-host (systemd owns scheduling);
  `fleetctl.sh`'s `start/stop/status/log` remain only for hands-on local/dev runs.
- Host-side git ops are hardened against the sandbox-escape vector the commit-only
  worktree opens (Issue #193). Every host-side git invocation is built through
  `flotilla.git_host` and pins `-c core.hooksPath=/dev/null`, so a hook the agent
  plants in the bind-mounted worktree (or an agent-set `core.hooksPath`) cannot
  execute when a supervisor-context git op (worktree create/archive/prune, branch
  delete, `base..HEAD` commit count, the app-repo bootstrap, and the future
  push/PR-create) later runs against that worktree. The same builder clears git's
  dubious-ownership refusal with a `safe.directory` **scoped to the exact checkout
  path** — never the `safe.directory=*` wildcard, which (paired with a planted
  hook) would reopen the escape this control closes.

## Alternatives considered

- **TypeScript / sandcastle rewrite** — rejected (`/grill-me` 2026-06-15): a
  Python + Docker-`SandboxAccess` containment reuses flotilla wholesale and the
  existing `builder-base` venv (zero parity gap).
- **DooD / DinD on the dev container** — rejected: native Docker on a dedicated VM
  avoids socket-sharing escape surface and the dev container's credential blast
  radius; the VM is the trust anchor.
- **squid** for the egress proxy — rejected on footprint; tinyproxy is ~10× lighter
  and equally precise for an exact-host CONNECT allowlist (§8).
- **Secrets via a systemd `EnvironmentFile`** — rejected: writing the PAT to disk,
  even 0600, violates the no-secret-on-disk posture; `fleet-tick` fetches into the
  process env and execs the tick (§11).
- **Keep the tmux ticker on the fleet-host** — rejected: a oneshot service under a
  timer is crash-only by construction and needs no tmux/cron on the VM (§11, §16).
