# ADR-0005 — The increment verbs: rules in squadra, primitives in each adapter

- Status: **Accepted** — 2026-10-09; implemented by the PR that adds this ADR
  (board-writes SQ2). Decision 3 amended by SQ2b, 2026-10-09 (ledger N6).
- Date: 2026-10-09
- Context origin: claude-skills `docs/design-to-board/LEDGER.md` DB-D1 (writes
  live in squadra; amendment A2: squadra's orchestration applies claim scope
  and the withdraw rule) and DB-D2–DB-D5 (the contract's shape), with board-knowledge
  `sessions/2026-10-09-juval-parent-and-lookup-scope.md`.
- Detail: [docs/board-writes/verb-contract.md](../board-writes/verb-contract.md).

## Context

An outside planner (claude-skills' `design-to-board`) needs three verbs:
`queue_increment`, `withdraw_increment` and `increments_by_origin`. Each carries
rules: refuse a parent outside the claim scope, refuse ACTIVE and DONE
withdrawals, raise on a duplicate Origin, and return an existing item only when
every argument matches. These are security-sensitive board writes.

Putting the three verbs on `BoardAccess` as written would make every adapter
(the test fakes, the GitHub adapter to come, any later one) re-implement those
rules, and the claim-scope check would exist once in the tick and again in each
adapter. Juval's lookup ruling depends on the query and the write sharing one
definition of scope.

## Decision

1. `BoardAccess` gains two **raw primitives** and no rules:
   - `create_increment(IncrementRequest) -> item_id`: one business write that
     lands a QUEUED item with its Origin, parent and predecessor links, title and
     body. If the provider needs several calls, the adapter orders them so no
     tick can claim the item before its links exist (DB-D1 A1).
   - `items_with_origin() -> tuple[OriginRecord, ...]`: every Origin-bearing item
     on the board, in every bucket, unkeyed, so duplicates stay visible.
   Withdrawal reuses `item_state` and `set_state`.
2. `squadra.increments.IncrementBoard` holds **the contract**, the three verbs in
   DB-D4's shape, and applies every rule once, using pure functions in
   `squadra.engines`: `parent_in_claim_scope` (now also the tick's claim gate),
   `check_transition`, `index_by_origin` (A2) and `check_queue_matches` (A3).
   Every refusal raises before any write.
3. Two narrowings beyond the rulings, both fail-closed, kept by SQ2b (ledger
   N6; board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`):
   - A withdrawn Origin is never re-queued (the glossary's "never reused"). It
     is refused, not returned: returning it would let the caller link
     successors to work that will never be delivered. The refusal tells the
     caller to queue the work under a new Origin.
   - `withdraw_increment` refuses an item outside the claim scope (DB-D1 A2
     applies scope to writes). The refusal names the safe order: `squadra
     stop`, restore scope, withdraw again, `squadra start`. Restoring scope
     while the fleet runs lets the next tick claim the item, and the
     withdrawal is then refused as ACTIVE (DB-D2).
   Neither fires on design-to-board's legitimate path; both catch a broken
   document or reconcile.
4. The ADO adapter raises `NotImplementedError` for both primitives. Board writes
   ship with the GitHub adapter (DB-D1, SQ5).

## Consequences

- An adapter's write half is two methods and owes only storage: store the Origin
  opaquely and read every field back exactly. The contract suite checks that
  against both fakes, including an Origin full of comment and escape characters.
- The tick, the query and the writes cannot disagree about scope.
- `ReadOnlyBoard` (dry run) passes `items_with_origin` through and absorbs
  `create_increment`, returning 0, which is never a board item.
- The CLI (SQ4) composes `IncrementBoard` from the loaded config; nothing calls
  the primitives directly except `IncrementBoard`.
- Open with Rich (ledger N7, N8): Juval recommends withdrawing by Origin
  instead of item id, and a crash-safe `create_increment` that finishes a
  partial item on retry. Both change DB-D rulings, so SQ2b did not build them.
  Until Rich rules, decision 1 stands as written: the adapter orders its calls
  so no tick can claim the item before its links exist, and a retry after a
  crash mid-create is refused by A3.
- Open (ledger N10): `withdraw_increment` reads the state, then writes it. The
  ticker can claim the item in between, leaving an item WITHDRAWN while a
  runner works on it. Closing that gap needs a compare-and-set write from the
  provider or a tick rule for it.

## Alternatives considered

- **The three verbs on `BoardAccess`, rules in each adapter.** Rejected: the
  rules repeat per provider and the scope definition forks.
- **A separate write Protocol only write-capable providers implement.** Rejected:
  one Resource, one ResourceAccess (DB-D1). The ADO stub costs two methods.
- **Key the read by Origin in the adapter.** Rejected: a dict silently drops a
  duplicate, so squadra could never raise A2.
