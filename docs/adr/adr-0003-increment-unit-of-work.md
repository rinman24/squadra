# ADR-0003 — "Increment" is the fleet's unit of work (renames "slice")

- Status: **Accepted** — 2026-10-06; implemented by the PR that adds this ADR.
- Date: 2026-10-06
- Supersedes the *naming* (not the decisions) in
  [ADR-0001](adr-0001-board-provider-seam.md),
  [ADR-0002](adr-0002-sandbox-containment-fleet-host.md) and
  [docs/design/board-provider-seam.md](../design/board-provider-seam.md). Those
  records are left as written; read "slice" there as "increment".

## Context

squadra called its unit of work a **slice** (from "vertical slice"): one board
item, claimed by the fleet, driven by one runner, delivered as a PR, retired by
finalize when that PR merges, and persisting across retry attempts.

"Slice" is now reserved for Löwy's architectural unit — a subsystem in the
closed-architecture decomposition squadra is aligning with (ADR-0001 §5,
ADR-0002). "Vertical slice" collides with it directly. Keeping both meanings in
one codebase makes every sentence about squadra's own architecture ambiguous.

"Vertical" is also wrong as part of the *name*: it is an attribute some units of
work have (an end-to-end behaviour) and others don't (foundation work).

## Decision

1. The fleet's unit of work is an **increment**: one board item taken through the
   fleet as a single unit of delivery. Its identity is the board item; each retry
   attempt makes its own claim, branch (`-aN`) and PR, all belonging to the same
   increment. "Vertical" is an attribute of an increment, not part of its name.
2. Board-level words are unchanged. At the `BoardAccess` boundary an increment is
   represented by one **Issue** / **work item**; `issue_id`, `Lifecycle`, runner,
   claim, park, seams, `worker_roster`, the `fleet:*` tags and the
   `phase`/`parked_state` values keep their names.
3. The rename covers every public surface, with **no deprecated aliases**:

   | Surface | Was | Now |
   |---|---|---|
   | CLI | `squadra slice {init\|update\|heartbeat\|show}` | `squadra increment {…}` |
   | `[pipeline].branch_template` default | `feat/slice-{id}-{slug}` | `feat/increment-{id}-{slug}` |
   | Empty-slug fallback | `slice` | `increment` |
   | `[pipeline].runner_skill` / `FLEET_RUNNER_SKILL` default | `/afk-slice-runner` | `/afk-increment-runner` |
   | Host→agent context file | `.squadra/slice.json` | `.squadra/increment.json` |
   | Per-increment compose project | `squadra-slice-{id}` | `squadra-increment-{id}` |
   | Python API | `SliceView`, `SliceContext`, `SliceTask`, `RetrySlice`, `slice_branch`, `write_slice_context`, `slice_context_path`, `SLICE_CONTEXT_FILENAME`, `status.slice_dir` | `Increment…` / `increment_…` equivalents |

   Aliases would keep the old word alive, which is the problem this rename solves.
   squadra is pre-1.0 (`0.1.0a1`), and the known consumers are the maintainer's own
   repos, so a clean break costs one coordinated consumer PR. The
   `flotilla-status` → `squadra slice` move set the precedent of dropping the old
   name outright.

## Consequences

**Upgrade procedure (drain first).** Upgrade only when no increment is in flight:
nothing `fleet:claimed` that is active or parked. Draining is required because of
the compose project name, which is derived from the item id on every tick and never
persisted. A sandbox started as `squadra-slice-{id}` is invisible to an upgraded
supervisor (it inspects `squadra-increment-{id}`). The lifecycle engine then
classifies the item as provisioning indefinitely: the old sandbox's exit, manifest
and handoff are never observed, and any later teardown/finalize leaks the old
project.

The branch name is less fragile than it looks. While an item's `status.json`
exists, the supervisor reads the branch from it (`_branch_for` fast path), so an
in-flight item keeps finalizing against its original `feat/slice-…` branch. Only an
item with no status file falls back to deriving the branch from `branch_template`.
If you cannot drain, pin `branch_template = "feat/slice-{id}-{slug}"` in
`squadra.toml` for one cycle. That pin does not cover the compose project, so you
still have to tear down any leftover `squadra-slice-*` projects by hand.

**Consumer migration (one PR per consuming repo, landed with the runtime bump):**

- rename the runner skill to `afk-increment-runner`, or pin
  `[pipeline].runner_skill = "/afk-slice-runner"`;
- replace `squadra slice …` calls with `squadra increment …`;
- read `.squadra/increment.json` instead of `.squadra/slice.json`. There is no pin
  for this one: the skill must change.

Re-running `squadra init` in a scratch directory shows the updated runner-skill
template to diff against.

**Language.** "Increment" (rejected: slice, vertical slice) is settled in the
squadra glossary. "Slice" in squadra prose now means only Löwy's subsystem.

## Alternatives considered

- **Keep "slice"** — rejected: it collides with the architectural unit squadra is
  being decomposed into.
- **"Work item" / "Issue"** — rejected: that is the board's word for the record,
  correct at the `BoardAccess` boundary. The fleet's unit of delivery (claim,
  attempts, branch, PR, finalize) is a fleet concept the record only represents.
- **"Task"** — rejected: on the board, Tasks are an Issue's child items (the
  increment context carries them).
- **Deprecated aliases for one release** (`squadra slice`, old defaults, with a
  warning) — rejected for now: no known external consumer. Revisit only if one
  surfaces before the next release.
