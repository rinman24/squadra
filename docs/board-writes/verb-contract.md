# The increment verb contract and its CLI surface

Frozen by SQ2 (2026-10-09), amended by SQ2c (2026-10-09) to Rich's N7 and N8
ruling, which reopens DB-D1/DB-D4. Rulings: claude-skills DB-D1–DB-D5. Layering:
[ADR-0005](../adr/adr-0005-increment-verbs.md). Code: `squadra.increments`.

## The contract

`IncrementBoard(board, claim_scope, parent_scope_ids)`, per board (the one
`squadra.toml` names; a predecessor on another board is not visible).

| Verb | Does | Refuses (nothing written) |
|---|---|---|
| `queue_increment(origin, parent, predecessors, title, body) -> item_id` | Lands a QUEUED item under `parent` with the predecessor links, carrying `origin` opaquely. If `origin` is already on the board with identical arguments, returns that item. If only a partial item carries `origin` and agrees with the request, finishes it and returns its id | Parent outside claim scope (`ClaimScopeRefusedError`); `origin` present with any differing argument, or withdrawn, or on a partial item that disagrees (`QueueRefusedError`); `origin` carried twice (`DuplicateOriginError`) |
| `withdraw_increment(origin) -> item_id` | The Increment carrying `origin`: QUEUED → WITHDRAWN. Already WITHDRAWN: no write | No Increment carries `origin`, including one only a partial item carries (`UnknownOriginError`); `origin` carried twice (`DuplicateOriginError`); item outside claim scope (`ClaimScopeRefusedError`); ACTIVE or DONE on a fresh read (`TransitionRefusedError`); a board with no withdrawn state (`BoardValidationError`) |
| `increments_by_origin() -> {origin: Increment}` | Every Origin an Increment carries, every bucket. `Increment(item_id, parent, lifecycle, in_claim_scope)`; scope is reported, never filtered. Partial items are left out | A duplicate Origin (`DuplicateOriginError`, naming both items, a partial one included) |

Notes:
- `parent` is required under both claim scopes (DB-D5). `Increment.parent` is
  `None` only for an item unparented by hand after it was queued.
- Predecessors compare as a set; title and body compare exactly.
- squadra does not check that predecessors exist; design-to-board fails loudly
  on a missing, withdrawn or out-of-scope predecessor Origin (DB-D4).
- Filtering Origins by map prefix (`<map>:`) is design-to-board's job; squadra
  never parses an Origin.
- The two refusals beyond the rulings (a withdrawn Origin, an out-of-scope
  withdrawal) were kept by SQ2b (ledger N6). Each names its way out: queue the
  work under a new Origin; or `squadra stop`, restore scope, withdraw again,
  `squadra start`.
- A **partial item** is one a crash left mid-create: it carries its Origin but
  is in no bucket, so the tick never sees it and it is not an Increment
  (ADR-0005 decision 1). A `queue_increment` retry finishes it when what is on
  the board agrees with the request: title and body equal, the parent equal
  once set, the predecessors present a subset of those requested. Anything
  else is refused, naming the item and the disagreement (ledger N8).
- `withdraw_increment` goes by Origin (ledger N7), so a hand-queued item with
  no Origin is out of its reach; a human withdraws that on the board. It
  re-reads the item's state just before writing; the read-then-write gap
  stays open (ledger N10).

## The CLI surface (built in SQ4)

`squadra increment` is taken by the status-file ops, so the verbs go under
`squadra board`. design-to-board runs these in the target repo (or with
`FLEET_HOME` set); squadra resolves its own `squadra.toml` through the usual
`defaults < squadra.toml < FLEET_* env < flag` lookup, and design-to-board never
reads it (DB-D5). Every subcommand runs `validate_config()` first.

```text
squadra board queue    --origin ORIGIN --parent ID [--predecessor ID ...]
                       --title TITLE --body-file PATH|-
squadra board withdraw --origin ORIGIN
squadra board origins
```

- `--parent` is required on every `queue`, under either scope. squadra decides
  only whether it is in scope; which parent a map uses is design-to-board's
  rule (DB-D5: required on a map's first run, read off its items after).
- The body comes from a file or stdin, never argv (size limits, `ps` exposure).
- stdout is one JSON document; errors are one line on stderr prefixed
  `squadra board <verb>:`.
  - `queue` → `{"item_id": 123}`
  - `withdraw` → `{"item_id": 123, "lifecycle": "withdrawn"}`
  - `origins` → `{"A:I1": {"item_id": 123, "parent": 200, "lifecycle": "queued", "in_claim_scope": true}, ...}`.
    Partial items are not in it; `origins` writes one stderr line per partial
    item, naming its item id and Origin, and still exits `0`. A partial item
    whose row was withdrawn before a retry finished it stays on the board for
    good, and this line is the only place it shows.
- Exit codes: `0` done; `2` usage or configuration (argparse, `ConfigError`,
  `BoardValidationError`); `3` refused by a rule (`ClaimScopeRefusedError`,
  `QueueRefusedError`, `TransitionRefusedError`, `DuplicateOriginError`,
  `UnknownOriginError`), with nothing written; `1` anything else, such as a provider failure.
- No `--dry-run` on the writes: design-to-board's own dry run (DB-D1 E) is the
  preview, and `origins` is read-only.
