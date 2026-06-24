# Design note — provider-agnostic `BoardAccess` seam ("configure-not-customize")

Status: resolved design, pre-implementation. Origin: `/grill-me` session
"abstract-squadra-step-1", 2026-06-12. This is the working reference the
implementation PRs draw from; the durable decision record is
[ADR-0001](../adr/adr-0001-board-provider-seam.md). The fleet's original design
predates this extraction and lives in a private backend project.

## Goal & scope

Abstract away the project/ADO-specific assumptions so squadra is usable by
others (the deferred "configure-not-customize" refactor) — step 1 of the
squadra → public/PyPI path.

- **Build the provider-agnostic seam now; ship ADO-only.** Prove generality with a
  contract-test suite, not a second shipped adapter.
- Deferred to tracked backlog items: the **GitHub** adapter (P1), the **GitLab**
  adapter (P1), the pluggable **agent-runner** seam (P1), and the **formal
  closed-architecture decomposition** (P2). squadra stays Claude-specific in this
  step (skill *names* become config; the agent CLI is not abstracted).

## Architecture — Löwy closed-architecture *alignment* (not decomposition)

squadra is targeted for an eventual Löwy "Righting Software" closed-architecture
refactor (API → Manager → pure Engines → ResourceAccess → Resource, strong DDD).
This step **aligns** with that target but does **not** do the full package/Manager
restructure (that is the P2 item). Mapping:

| Layer | squadra |
|---|---|
| Resource | ADO / GitHub / GitLab APIs, tmux, git, fs, the `claude` CLI |
| ResourceAccess | `BoardAccess` (provider adapters), `LauncherAccess`, `CleanupAccess`, `ProcessAccess`, `WorktreeAccess`, `StatusAccess`, `ConfigAccess`, `AuthAccess` — today's `TickSeams` collaborators |
| Engine (pure) | `ClaimEngine`, `ReapEngine`, `FinalizeEngine`, branch-naming/attempt logic — data in → decision out, no I/O |
| Manager | tick orchestration (`run_tick`/`*_pass`); becomes a `SupervisorManager` class in the P2 step |
| API / composition root | the `squadra` CLI: builds config + the chosen `BoardAccess` + Access objects, injects into orchestration; the provider **registry** lives here |
| Utilities / contracts | `SquadraConfig`, logging, the domain model (`Lifecycle`, `WorkItem`, `WorkItemLinks`, comment events) |

### Module layout (this step)

Flat, role-named modules — no nested `api/manager/engine/access` dirs, no Manager
class yet (the P2 refactor adds those):

```
src/squadra/
├── domain.py     # Lifecycle, WorkItem, WorkItemLinks, comment events, outcome DTOs
├── config.py     # SquadraConfig + tomllib loader + precedence + validation
├── board.py      # BoardAccess Protocol + AzCliAdo adapter + provider registry
├── engines.py    # PURE claim/reap/finalize/naming decision functions (no I/O)
├── supervisor.py # orchestration (lock, auth preflight, finalize→reap→claim) — proto-Manager
├── status.py     # per-slice status.json convention + ops (unchanged contract)
├── cli.py        # unified argparse `squadra` (API/composition root)
└── _scripts/, _resources.py
```

## The `BoardAccess` seam

Renames `AdoClient` → `BoardAccess` (a ResourceAccess contract). The supervisor and
engines become **provider-blind**: no native state strings, no markup, no ADO
relation URIs in core. Sketch:

```python
class Lifecycle(Enum):          # the 3-bucket domain invariant
    QUEUED = "queued"           # claimable / not started
    ACTIVE = "active"           # claimed / in-flight
    DONE = "done"               # finalize-eligible

@dataclass(frozen=True, slots=True)
class WorkItem:                 # was IssueRef
    item_id: int
    title: str
    tags: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class WorkItemLinks:            # was IssueLinks
    parent_id: int | None
    predecessor_ids: tuple[int, ...]

# Structured comment events — the adapter renders to native markup.
@dataclass(frozen=True, slots=True)
class Claimed:   runner_id: str; branch: str; when: str
@dataclass(frozen=True, slots=True)
class RolledBack: reason: str
@dataclass(frozen=True, slots=True)
class Finalized: pr_url: str; branch: str
@dataclass(frozen=True, slots=True)
class Reaped:    evidence: str; attempt: int
@dataclass(frozen=True, slots=True)
class Escalated: attempt: int; cap: int
CommentEvent = Claimed | RolledBack | Finalized | Reaped | Escalated

class BoardAccess(Protocol):
    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]: ...
    def completed_pr_url(self, branch: str) -> str | None: ...   # vs configured base_branch
    def item_links(self, item_id: int) -> WorkItemLinks: ...
    def item_state(self, item_id: int) -> Lifecycle: ...
    def set_state(self, item_id: int, state: Lifecycle) -> None: ...
    def add_tag(self, item_id: int, tag: str) -> None: ...
    def remove_tag(self, item_id: int, tag: str) -> None: ...
    def add_comment(self, item_id: int, event: CommentEvent) -> None: ...
    def validate_config(self) -> None: ...   # resolve config vs live board; raise loud on mismatch
```

Notes:
- **State** is the neutral `Lifecycle` everywhere in core; the adapter owns the
  native-name mapping (many-native→one-neutral allowed) and translates at the
  boundary. No `"To Do"/"Doing"/"Done"` literals outside the ADO adapter.
- **Comments**: core emits `CommentEvent`s; the adapter renders (ADO→HTML,
  GitHub→Markdown). Removes the inline HTML f-strings at today's
  `supervisor.py:399/456/643/661`.
- **Tags**: configurable namespace **prefix** (default `fleet:`); the five suffixes
  (`claimed`/`failed`/`needs-decision`/`qa-ready`/`awaiting-pr-approval`) stay fixed
  canonical vocabulary; detection stays prefix-based (`startswith(prefix)`). GitHub
  label pre-creation is that adapter's concern.
- **`completed_pr_url`** stays on `BoardAccess` (integrated SCM for ADO/GitHub/
  GitLab); a separate SCM seam is a Jira-era concern, deferred.
- **Provider selection**: a hardcoded registry (name → adapter factory) in the CLI
  composition root; `TickSeams` DI preserved for tests/advanced wiring. Out-of-tree
  entry-point plugins are a possible future volatility, not built now.

## Configuration

Modern layered precedence, **superseding** any "everything required" notion:

```
built-in defaults  <  squadra.toml  <  FLEET_* env  <  CLI flag
```

- **Required only where un-defaultable**: `provider`, and `[board.states]` *unless*
  the provider's process is inferable (`provider="ado"` defaults to Basic's
  `To Do/Doing/Done`; `github`/`gitlab` must declare states — their statuses are
  user-defined). squadra requires the *target* (like a kubeconfig context) and
  defaults the *how*.
- **Safety = validate-against-board, not mandatory typing.** `validate_config()`
  resolves the configured state names / tag prefix / base branch against the live
  board at `squadra init --check` and at each tick's startup, and fails loud
  (e.g. "configured active state 'Doing' not found among this project's states").
- **`squadra init`** scaffolds a complete annotated `squadra.toml` (every key with
  its default, `provider` from `--provider`) + the runner-skill and cleanup-skill
  templates — the whole adoption ritual is one command + a few edits.

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
branch_template = "feat/slice-{id}-{slug}"   # squadra owns the -a{attempt} retry suffix
worktree_dir    = ".claude/worktrees"
runner_skill    = "/afk-slice-runner"
tdd_skill       = "/tdd"
qa_skill        = "/qa"
cleanup_skill   = "/cleanup-merged-branches"
```

Operational/secret knobs stay **env-only** with today's defaults: `FLEET_MAX_RUNNERS`,
`FLEET_TICK_INTERVAL_SECONDS`, `FLEET_HEARTBEAT_INTERVAL_SECONDS`,
`FLEET_STALENESS_THRESHOLD_SECONDS`, `FLEET_MAX_ATTEMPTS`, `FLEET_MODEL`,
`FLEET_EFFORT`, `FLEET_HOME`/`FLEET_ROOT`/`FLEET_PYTHON`, the PAT.

## CLI (API layer)

A single argparse `squadra`, Python-native dispatch:

- `squadra init` — scaffold config + skill templates.
- `squadra tick` — one supervisor tick (calls `run_tick` directly; no shell).
- `squadra start | stop | status | log` — ticker control; tmux ops still shell to
  the packaged `fleetctl.sh`, but presented as argparse subcommands (one surface,
  one `--help`).
- `squadra slice {init|update|heartbeat|show}` — the per-slice `status.json` ops
  (distinct noun; resolves the `status` overload). Used by `runner-wrap.sh` + the
  runner skill.

Drop the `squadra-supervisor` / `squadra-status` **console scripts**; keep the
`python -m squadra.supervisor` / `squadra.status` module entry points (internal).

## Runner skill

`squadra init` scaffolds a **genericized, consumer-owned** runner-skill template
(provider/repo-agnostic lifecycle: claim-verify → worktree → seams → tdd → qa →
park) with clearly-marked fill-in sections (`## Gates`, shared-seam conventions)
that work out of the box. Skill names are config; the wrapper threads the
`tdd`/`qa` names into the runner prompt (the skill stops hardcoding `/tdd`,`/qa`).
Scaffolding ≠ owning — squadra copies the template out, then invokes it only by
*skill name* through `claude`; the "machinery + tests" runtime boundary holds
(README amends to "machinery + tests + scaffolding").

## Proof — `BoardAccess` contract-test suite

A conformance suite that any `BoardAccess` implementation must pass, run now against
**two divergent fakes**:
- an **ADO-shaped** fake (HTML comments, `To Do/Doing/Done`, `;`-tags), and
- a **GitHub-shaped** fake (Markdown comments, arbitrary Projects-v2 status names
  mapped via config, label tags, sub-issue/dependency links).

Running the supervisor/engine tests against the GitHub-shaped fake catches any
hardcoded-`Doing`-type leak (proves provider-blindness in code); the real GitHub/
GitLab adapters later become two more implementations the same suite validates.

## Delivery

- **PR1 (squadra) — behavior-preserving refactor.** Split `supervisor.py` into
  `domain`/`config`-stub/`board`/`engines`; rename `AdoClient`→`BoardAccess`;
  extract pure decision functions into `engines.py`. Tests green, **zero** logic
  change — verifiable on the security-sensitive claim/reap/finalize paths.
- **PR2 (squadra) — the generalization.** Config system + `Lifecycle` mapping +
  comment events + tag prefix + `validate_config` + registry + CLI unification +
  `squadra init` scaffolding + the contract-test suite. Docs: this note, ADR-0001,
  README amendments, remove the "Out of scope" stanza from `CLAUDE.md`.
- **Coupled PR in the consuming repo (small).** Migrate the `/afk-slice-runner` skill's
  `squadra-status` → `squadra slice`; bump the runtime install pin.

## Asserted (not separately chosen — flag to revisit)

- squadra owns the `-a{attempt}` retry suffix (fixed rule; not templated).
- `parent_scope_ids` replaces `FLEET_EPIC_IDS` (the parent-link claim filter).
- squadra starts its own `docs/adr/` (this is ADR-0001).
- The Claude auth probe stays as-is (Claude-specific; generalized only by the P1
  agent-runner item).
