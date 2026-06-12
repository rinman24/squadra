# ADR-0001 — Provider-agnostic `BoardAccess` seam (configure-not-customize)

- Status: **Proposed (draft)** — resolved via `/grill-me` 2026-06-12; finalized by the
  implementation PR (PR2 below).
- Date: 2026-06-12
- Context origin: gswa ADR-0007 (+ 2026-06-10 addendum) — the fleet's original design.
- Detail: [docs/design/board-provider-seam.md](../design/board-provider-seam.md).

## Context

flotilla was extracted from `gswa` carrying ADO-specific assumptions: the
`AdoClient` Protocol speaks ADO semantics (`"To Do"/"Doing"/"Done"` state literals,
HTML comments, ADO relation URIs, `fleet:`-prefixed tags, WIQL `WorkItemType='Issue'`,
target branch `main`), and skill names / branch naming / worktree paths are literals.
The flotilla → public/PyPI path requires flotilla be usable against other boards.
This ADR covers **step 1**: make the package provider-agnostic and configurable.

flotilla is also targeted for an eventual Löwy "Righting Software" closed-architecture
refactor (API → Manager → pure Engines → ResourceAccess → Resource). This step must
**align** with that target without committing to the full restructure.

## Decision

Introduce a provider-agnostic **`BoardAccess`** ResourceAccess seam and lift the
genshift-specific literals to configuration. Concretely:

1. **`BoardAccess`** (renames `AdoClient`) is the board ResourceAccess contract.
   Core (supervisor + engines) becomes provider-blind:
   - state is a neutral **`Lifecycle`** enum (QUEUED/ACTIVE/DONE — a genuine 3-bucket
     domain invariant); the adapter maps native↔neutral via config (many-native→one).
   - comments are **structured `CommentEvent`s** rendered by the adapter
     (ADO→HTML, GitHub→Markdown) — no markup in core.
   - tags use a **configurable namespace prefix** (default `fleet:`); the five
     suffixes stay fixed canonical vocabulary; detection stays prefix-based.
   - a **`validate_config()`** method resolves config against the live board and
     fails loud — this, not mandatory config, is the safety mechanism.
2. **Configuration** follows the modern layered pattern
   `defaults < flotilla.toml < FLEET_* env < CLI flag`. Only the un-defaultable is
   required (`provider`; `[board.states]` unless provider is ADO-Basic); everything
   else defaults. `flotilla init` scaffolds a complete annotated `flotilla.toml` plus
   the runner/cleanup skill templates.
3. **CLI**: a single argparse `flotilla` (the API/composition root) with
   `init`/`tick`/`start`/`stop`/`status`/`log` and `flotilla slice {…}` for the
   status-file ops; the provider **registry** (name → adapter factory) lives here.
   The `flotilla-supervisor`/`flotilla-status` console scripts are dropped.
4. **Runner skill** is delivered as a genericized, consumer-owned template scaffolded
   by `flotilla init`; skill names are config and threaded into the runner prompt.
   flotilla stays Claude-specific (the agent CLI is not abstracted in this step).
5. **Architecture alignment**: flat role-named modules (`domain`/`config`/`board`/
   `engines` + `supervisor` orchestration) matching the target layers; **no** Manager
   class / nested package tree yet (deferred to the formal-decomposition item).
6. **Proof**: a `BoardAccess` contract-test suite run against two divergent fakes
   (ADO-shaped + GitHub-shaped) — the GitHub-shaped fake exercises the supervisor
   against divergent semantics so provider-blindness is proven in code.

**Shipped now: the seam + the ADO (`AzCliAdo`) adapter only.** GitHub and GitLab
adapters, the agent-runner seam, and the formal closed-architecture decomposition are
tracked follow-ups.

**Delivery**: PR1 = behavior-preserving refactor (module split + rename + engine
extraction, zero logic change); PR2 = the generalization above; + a small coupled
gswa PR (runner skill `flotilla-status`→`flotilla slice`, runtime-pin bump).

## Consequences

- The supervisor and engines contain no provider literals; a new board is a new
  `BoardAccess` implementation that passes the contract suite + a config schema.
- `validate_config()` makes misconfiguration a loud startup failure, not a silent
  mis-mutation — stronger than the rejected "declare everything" posture.
- Near-zero config for the common ADO-Basic adopter (provider + inferred states);
  modern layered override for everyone else.
- The seam grows one method (`validate_config`); comments become a closed event union
  (also a cleaner test surface than today's inline HTML f-strings).
- The eventual closed-arch refactor is mechanical: move modules into layer dirs, wrap
  orchestration in a `SupervisorManager`.

## Alternatives considered

- **Defer the whole step** until a real external consumer appears — rejected: the
  PyPI path needs it, and a concrete second provider (GitHub) defines the seam.
- **Ship a second adapter inline / build both adapters now** — rejected for scope; a
  contract-test suite with a GitHub-shaped fake proves generality without dragging
  `gh` auth + live integration into this PR.
- **Required config file, no implicit defaults** (initially chosen, then revised) —
  rejected once the governing criteria became *minimum adoption friction + modern
  best practice*: strong defaults + layered override + validate-against-board serves
  adoption, convention, and the original safety intent better.
- **Entry-point provider plugins** — deferred: benefits provider *authors* (rare),
  not provider *users*; a hardcoded registry suffices and can grow a plugin layer
  later without changing the config surface.
- **Abstract the agent runner** (drive non-Claude agents) — deferred to a P1 item;
  the model/effort/permission flags are Claude-specific and expand scope materially.
- **Opaque configurable state strings** instead of a neutral enum — rejected: leaves
  board-native strings in the supervisor.
