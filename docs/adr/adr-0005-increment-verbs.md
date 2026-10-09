# ADR-0005 — The increment verbs: rules in squadra, primitives in each adapter

- Status: **Accepted** — 2026-10-09; implemented by the PR that adds this ADR
  (board-writes SQ2). Decision 3 amended by SQ2b, 2026-10-09 (ledger N6);
  decisions 1 and 2 amended by SQ2c, 2026-10-09 (ledger N7, N8; Rich's
  ruling, see Consequences).
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
   - `create_increment(IncrementRequest, partial_item=None) -> item_id`: one
     business write that lands a QUEUED item with its Origin, parent and
     predecessor links, title and body. If the provider needs several calls,
     the adapter keeps four call-order obligations (SQ2c, ledger N8), so no
     tick can claim the item before its links exist (DB-D1 A1) and a crash
     part-way leaves an item a retry can find:
     1. the Origin goes on with the first call that creates the item (title
        and body with it);
     2. until the last write the item is in no bucket, an absence the adapter
        recognises structurally, not a native state;
     3. meanwhile `items_with_origin` still returns it, as a partial item;
     4. one write commits the item, the one that puts it in QUEUED, and it
        comes last.
     With `partial_item`, the adapter finishes that item instead: it adds
     what is missing, each write idempotent, commit write last, and returns
     the same id. `item_state` on a partial item raises.
   - `items_with_origin() -> tuple[OriginRecord, ...]`: every Origin-bearing item
     on the board, in every bucket, unkeyed, so duplicates stay visible.
     Partial items are included, with `lifecycle` `None`.
   Withdrawal reuses `item_state` and `set_state`.

   A **partial item** is an item `create_increment` has stamped with its
   Origin but not yet put in QUEUED: in no bucket, not an Increment, and a
   `queue_increment` retry finishes it. The word is the adapter contract's,
   not the glossary's (Eric, board-knowledge
   `sessions/2026-10-09-eric-increment-verb-partial-item.md`).
2. `squadra.increments.IncrementBoard` holds **the contract**, the three verbs in
   DB-D4's shape as amended by Rich's N7 and N8 ruling, and applies every rule
   once, using pure functions in `squadra.engines`: `parent_in_claim_scope`
   (now also the tick's claim gate), `check_transition`, `index_by_origin`
   (A2), `check_queue_matches` (A3) and `check_queue_finishes` (N8).
   Every refusal raises before any write.
   - `withdraw_increment(origin) -> item_id` goes by Origin, not item id
     (N7). It refuses an Origin no Increment carries (`UnknownOriginError`;
     an Origin only a partial item carries counts as absent), checks scope
     against the record's parent, and re-reads `item_state` just before
     `set_state`.
   - `queue_increment` on an Origin only a partial item carries finishes it
     when what is on the board agrees with the request (title and body
     equal, the parent equal once set, the predecessors present a subset of
     those requested), and refuses any other difference (N8).
   - `increments_by_origin` leaves partial items out. `index_by_origin` runs
     on the raw records, so a partial item still counts as a duplicate.
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
- Ruled by Rich, 2026-10-09 (ledger N7, N8; board-knowledge
  `sessions/2026-10-09-juval-increment-verb-settlement.md`), reopening
  DB-D1/DB-D4. SQ2c built both and amended decisions 1 and 2:
  - `withdraw_increment` took an Origin instead of an item id (CLI
    `--origin`). It refuses an Origin that is not on the board, checks scope
    against the record's parent, and re-reads `item_state` just before
    `set_state`. A hand-queued item with no Origin is out of its reach; a
    human withdraws that on the board.
  - `create_increment` became crash-safe. The adapter keeps the guarantee
    through the four call-order obligations in decision 1. A
    `queue_increment` retry finishes a partial item whose fields agree with
    the request (its predecessors may be a subset) and refuses any other
    difference. `OriginRecord.lifecycle` became `Lifecycle | None` (`None` for
    a partial item, read through `OriginRecord.partial`), `create_increment`
    gained `partial_item`, and `increments_by_origin` leaves partial items out.
  - The GitHub adapter (SQ5) must prove obligations 1–4. If GitHub has no
    structural "in no bucket yet" that `items_with_origin` can still see, the
    fallback stays in the adapter: it writes its own completion marker last
    and filters its bucket reads on it.
  - A row withdrawn before its crashed create is finished leaves its partial
    item for good: unclaimable, invisible to the planner, its Origin never
    reused. `squadra board origins` names each one on stderr (SQ4).
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
