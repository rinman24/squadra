# ADR-0004 — `Lifecycle` gains a fourth, terminal bucket: WITHDRAWN

- Status: **Accepted** — 2026-10-09; implemented by the PR that adds this ADR.
- Date: 2026-10-09
- Amends [ADR-0001](adr-0001-board-provider-seam.md) decision 1, which called
  QUEUED/ACTIVE/DONE "a genuine 3-bucket domain invariant". ADR-0001 is left as
  written; read its bucket list as superseded here.
- Amended 2026-10-27 (squadra board-writes SQ5d, ledger N25): decision 1 and
  its Consequences line, for an increment withdrawn in flight. Ruled by Rich in
  board-knowledge `sessions/2026-10-27-juval-withdrawn-in-flight-release.md`;
  named in `sessions/2026-10-27-eric-withdrawn-in-flight-names.md`.
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
   or reaped. *(Amended 2026-10-27.)* An item can still reach WITHDRAWN while
   the fleet holds it in flight: a human withdraws it (on GitHub, closes the
   issue as not planned, as a duplicate or with no reason), or
   `withdraw_increment` re-reads it QUEUED and a claim lands before its write
   (ledger N10). Such an item is neither finalized nor reaped: the fleet
   **relinquishes** the claim, a third thing.
   - The tick gathers WITHDRAWN items that still carry the fleet's `claimed`
     tag, alongside QUEUED, ACTIVE and DONE. An unclaimed withdrawn item is
     not the fleet's and is not gathered.
   - The engine's first guard (step 0, above the tag guards) derives
     `withdrawn-in-flight` with `RelinquishClaim` for a fleet-claimed WITHDRAWN
     item, whatever its `failed`, `needs-decision` and parked facts: closure
     wins over the fleet's own parks, and a decision park the human closes is
     relinquished too. An unclaimed WITHDRAWN item stays `withdrawn`.
   - Relinquishing writes **no lifecycle state**, so squadra still never moves
     an item into WITHDRAWN from ACTIVE nor out of it (decision 2 holds). In
     order, each step idempotent: tear the sandbox down; archive the worktree
     and prune; park `status.json` as `withdrawn`; remove the other fleet tags;
     post one `Relinquished` comment; remove `claimed` last. `claimed` is the
     commit: a tick that ends partway leaves the item claimed and the next tick
     relinquishes it again (at worst a second comment); once it is gone the
     item is not gathered, so it is reported once.
   - It does not delete the branch (finalize's job, gated on a merged PR),
     close any PR, or delete `status.json` (losing it would reset the attempt
     count on a reopen and bypass the retry cap).
   - A fleet-claimed item moved to DONE by hand (on GitHub, closed as
     completed) is delivery, not withdrawal: it stays `awaiting-pr` as before,
     and a sandbox still running on it is stopped (`StopContainer`); the tag,
     worktree and status stay for finalize.
2. Transitions touching WITHDRAWN are a rule of the model, in
   `squadra.engines.check_transition`, not a property of a board:
   - QUEUED → WITHDRAWN is allowed.
   - DONE → WITHDRAWN is refused. A delivered increment stays delivered.
   - ACTIVE → WITHDRAWN is refused. squadra has no cancel path for a claimed,
     in-flight increment (claude-skills DB-D2, ruling DBQ3, 2026-10-09). To
     recover, let the attempt finish or stop it by hand, then re-plan; the
     planner's re-run is idempotent.
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

The writer for WITHDRAWN is `withdraw_increment`
([ADR-0005](adr-0005-increment-verbs.md)), which goes through
`check_transition`.

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
  `predecessor_withdrawn`. *(Amended 2026-10-27.)* The tick queries the
  WITHDRAWN bucket for fleet-claimed items only: one appears in `states={…}` as
  `withdrawn-in-flight` for the tick that relinquishes it, and never after.
  Unclaimed withdrawn items are not fleet candidates and do not appear. On
  GitHub the extra bucket costs no extra listing: the adapter memoizes the
  project listing until its next write, so a gather lists the project once.
- *(2026-10-27.)* `status.json`'s `parked_state` gains `withdrawn`, set by the
  fleet only. An agent's `outcome.json` has its own list of parked states and
  cannot declare it.
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
