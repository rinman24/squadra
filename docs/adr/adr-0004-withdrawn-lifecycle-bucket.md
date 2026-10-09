# ADR-0004 — `Lifecycle` gains a fourth, terminal bucket: WITHDRAWN

- Status: **Accepted** — 2026-10-09; implemented by the PR that adds this ADR.
- Date: 2026-10-09
- Amends [ADR-0001](adr-0001-board-provider-seam.md) decision 1, which called
  QUEUED/ACTIVE/DONE "a genuine 3-bucket domain invariant". ADR-0001 is left as
  written; read its bucket list as superseded here.
- Context origin: claude-skills `docs/design-to-board/LEDGER.md` decision DB-D1
  (board writes live in squadra), with the reasoning in board-knowledge
  `sessions/2026-10-09-eric-design-to-board-vocabulary.md` ("`Lifecycle.WITHDRAWN`").

## Context

An outside planner (claude-skills' `design-to-board`) will queue increments on
squadra's board and, when a design revision replaces an increment, withdraw the
old one. A withdrawn increment is a real, lasting state: it will never be
delivered. The three buckets cannot express it:

- **QUEUED** would make it claimable.
- **DONE** would count it as delivered, so its successors would unblock and the
  fleet would build on work that never happened.
- **No bucket** was not an option either: `_lifecycle_of` defaulted any unmapped
  native state to QUEUED. A board whose "Removed" column was left out of
  `[board.states]` would have every removed item claimed. That default was a
  silent failure waiting for the first withdrawn column.

And a successor of a withdrawn increment was reported `blocked`, the same label
as one whose predecessor is still in flight. The first clears itself on a later
tick; the second never does.

## Decision

1. `Lifecycle` has four buckets: QUEUED, ACTIVE, DONE and **WITHDRAWN**.
   WITHDRAWN is terminal: never claimed, never counted as done, never finalized
   or reaped.
2. Transitions touching WITHDRAWN are a rule of the model, in
   `squadra.engines.check_transition`, not a property of a board:
   - QUEUED → WITHDRAWN is allowed.
   - DONE → WITHDRAWN is refused. A delivered increment stays delivered.
   - ACTIVE → WITHDRAWN is refused **for now**. Whether a claimed, in-flight
     increment may be withdrawn, and what happens to its attempt, is not yet
     decided (claude-skills DBQ3). Refusing is the safe answer until it is.
   - No transition leaves WITHDRAWN. WITHDRAWN → WITHDRAWN is a no-op.
   Transitions among QUEUED, ACTIVE and DONE are unchanged and unconstrained.
3. **Every native state maps to exactly one bucket, or the board fails
   validation.** `validate_config` now checks both ways: a configured name the
   board lacks (as before), and a board state the map leaves out (new).
   `item_state` raises on an unmapped native state instead of defaulting to
   QUEUED. `[board.states]` refuses one native name in two buckets.
4. `[board.states].withdrawn` is **optional**. ADO-Basic has no withdrawn state,
   and a board without one simply has an empty WITHDRAWN bucket. A write to an
   unmapped bucket raises.
5. The tick reports a queued item with a withdrawn predecessor as the derived
   state **`predecessor-withdrawn`**, apart from `blocked`. It outranks `blocked`
   (one withdrawn predecessor is enough, whatever the others are) and is
   outranked by `out-of-scope`.

squadra has no writer for WITHDRAWN yet. The verbs that queue and withdraw
increments come later and must go through `check_transition`.

## Consequences

- A board with a state the config does not map now stops the tick at the
  preflight, every tick, until the operator maps it. That is deliberate, and it
  can bite an existing board that has, say, an ADO "Removed" state that was never
  configured. The fix is one line in `[board.states]`.
- A board state added while the fleet runs fails the next tick's preflight, or
  the first `item_state` read of an item in it, loudly.
- `validate_config` checks the Issue work-item type's states only. A predecessor
  of another type in a state the map does not cover raises at `item_state`.
- `State` gains `WITHDRAWN` and `PREDECESSOR_WITHDRAWN`; `LifecycleFacts` gains
  `predecessor_withdrawn`. The tick does not query the WITHDRAWN bucket: withdrawn
  items are not fleet candidates and do not appear in `states={…}`.
- Every `BoardAccess` adapter, including the GitHub adapter to come, must honour
  rule 3. The contract suite's fakes do.

## Alternatives considered

- **Map withdrawal to DONE.** Rejected: successors would unblock on work that
  was never delivered.
- **Keep the QUEUED default and require adapters to map withdrawn.** Rejected:
  it relies on every operator remembering, and fails open when they forget.
- **Make `withdrawn` required in `[board.states]`.** Rejected: ADO-Basic has no
  such state, and every existing config would stop loading for a bucket nothing
  writes yet.
- **Report a withdrawn predecessor as `blocked`.** Rejected: it would hide a
  permanent strand among transient waits; an increment whose planner never
  revisits it would wait forever unnoticed.
