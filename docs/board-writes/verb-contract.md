# The increment verb contract and its CLI surface

Frozen by SQ2 (2026-10-09), amended by SQ2c (2026-10-09) to Rich's N7 and N8
ruling, which reopens DB-D1/DB-D4, by SQ5e (2026-10-27) with the `scope`
read (claude-skills DB-D12), and by SQ8 (2026-10-27) with the `partial-items`
read (claude-skills DB-D13, ledger N31). Rulings: claude-skills DB-D1–DB-D5. Layering:
[ADR-0005](../adr/adr-0005-increment-verbs.md). Code: `squadra.increments`.

## The contract

`IncrementBoard(board, claim_scope, parent_scope_ids)`, per board (the one
`squadra.toml` names; a predecessor on another board is not visible).

| Verb | Does | Refuses (nothing written) |
|---|---|---|
| `queue_increment(origin, parent, predecessors, title, body) -> item_id` | Lands a QUEUED item under `parent` with the predecessor links, carrying `origin` opaquely. If `origin` is already on the board with identical arguments, returns that item. If only a partial item carries `origin` and agrees with the request, finishes it and returns its id | Parent outside claim scope (`ClaimScopeRefusedError`); `origin` present with any differing argument, or withdrawn, or on a partial item that disagrees (`QueueRefusedError`); `origin` carried twice (`DuplicateOriginError`) |
| `withdraw_increment(origin) -> item_id` | The Increment carrying `origin`: QUEUED → WITHDRAWN. Already WITHDRAWN: no write | No Increment carries `origin`, including one only a partial item carries (`UnknownOriginError`); `origin` carried twice (`DuplicateOriginError`); item outside claim scope (`ClaimScopeRefusedError`); ACTIVE or DONE on a fresh read (`TransitionRefusedError`); a board with no withdrawn state (`BoardValidationError`) |
| `increments_by_origin() -> {origin: Increment}` | Every Origin an Increment carries, every bucket. `Increment(item_id, parent, lifecycle, in_claim_scope)`; scope is reported, never filtered. Partial items are left out | A duplicate Origin (`DuplicateOriginError`, naming both items, a partial one included) |
| `parent_in_claim_scope(parent) -> bool` | Whether an item under `parent` is inside the claim scope. Reads nothing from the board. The one predicate behind `queue_increment`'s and `withdraw_increment`'s `ClaimScopeRefusedError` and `Increment.in_claim_scope`, so the answer and a queue never disagree | Nothing: it is a question, not a write |
| `partial_items(requests) -> tuple[PartialItemReport, ...]` | Every partial item on the board, sorted by item id, each with `queue_increment`'s verdict on the `ProposedRequest` in `requests` that names its Origin. A `ProposedRequest(origin, parent, predecessor_origins, title, body)` names predecessors by Origin; each maps to the id of the item carrying it, an Increment or a partial item, from the same board read, and an Origin no item carries is dropped. `PartialItemReport(item_id, origin, requested, disagrees_on, refusal)`, with `queue` derived: `None` when no request names the item, `"refuses"` exactly when `refusal` is set, else `"finishes"`. `refusal` is byte for byte the `QueueRefusedError` message `queue_increment` would raise; `disagrees_on` ⊆ `parent \| predecessors \| title \| body`, in that order. One predicate with `queue_increment`'s partial-item check, as `scope` shares one with its scope check | A duplicate Origin on the board (`DuplicateOriginError`); an Origin `requests` names twice (`ValueError`). Writes nothing |

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
- `parent_in_claim_scope` answers the question, never hands over the rule:
  `claim_scope` and `parent_scope_ids` stay squadra's (claude-skills DB-D12,
  after Juval, board-knowledge
  `sessions/2026-10-27-juval-first-run-parent-scope.md`). It lets
  design-to-board fail a map's first-run `--parent` that is out of scope
  before any write, when no Origin of the map is yet on the board to report
  `in_claim_scope`.
- `partial_items` answers only the partial-item rule (ledger N14): a request
  aimed at an Increment, a withdrawn Origin or an Origin on no item gets no
  report, and scope is not checked (`scope` answers that, and `queue_increment`
  checks it first). So it is not a `queue --check`: a `"finishes"` verdict
  says only that N14 will not refuse the request.
- Dropping a predecessor Origin no item carries is sound only for N14's
  subset check, and rests on one assumption: **a board never reuses an item
  id**. A predecessor not yet on the board gets a new id when it is queued,
  which no partial item can already carry, so leaving it out cannot change a
  verdict (Juval, ledger N31). The whole contract already keys on item ids.
  A planner therefore asks before any write, when some of a row's
  predecessors have no id yet.
- `origins` and `partial-items` are two board reads. A retry that finishes a
  partial item between them costs at most one exit `3` from `queue`, and the
  next run settles; the read cannot recreate a refusal loop, since that needs
  a disagreement that persists, which the one read sees (ledger N31; cf. N10).
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
squadra board scope    --parent ID
squadra board partial-items --requests-file PATH|-
```

- `--parent` is required on every `queue`, under either scope. squadra decides
  only whether it is in scope; which parent a map uses is design-to-board's
  rule (DB-D5: required on a map's first run, read off its items after).
- The body comes from a file or stdin, never argv (size limits, `ps` exposure);
  so does `partial-items`' request list, read exactly as given.
- stdout is one JSON document; errors are one line on stderr prefixed
  `squadra board <verb>:`.
  - `queue` → `{"item_id": 123}`
  - `withdraw` → `{"item_id": 123, "lifecycle": "withdrawn"}`
  - `origins` → `{"A:I1": {"item_id": 123, "parent": 200, "lifecycle": "queued", "in_claim_scope": true}, ...}`.
    Partial items are not in it; `origins` writes one stderr line per partial
    item, naming its item id and Origin, and still exits `0`. A partial item
    whose row was withdrawn before a retry finished it stays on the board for
    good, and this line is the only place it shows. Both halves come from one
    board read, `IncrementBoard.increments_and_partial_items()` (ledger N18).
  - `scope` → `{"parent": 200, "in_claim_scope": false}`. A read: it exits `0`
    whether the parent is in scope or not, runs `validate_config()` first like
    every verb (so `2` for configuration, `1` for a provider failure), and never
    writes.
  - `partial-items` reads a JSON list of requests, each exactly
    `{"origin": str, "parent": int, "predecessor_origins": [str, ...], "title": str, "body": str}`;
    `[]` is valid. Bad JSON, a non-list, a missing or extra key, a wrong type (a
    bool is not an int), an Origin twice or an unreadable file is exit `2`, one
    line, before any board read. Output, every partial item on the board,
    sorted by item id, never filtered by Origin prefix:

    ```json
    {"partial_items": [
      {"item_id": 1005, "origin": "A:I1", "queue": "refuses",
       "disagrees_on": ["title"],
       "refusal": "origin 'A:I1' is on partial item 1005, which disagrees with this request: title 'Old' != 'New'"},
      {"item_id": 1006, "origin": "A:I2", "queue": "finishes", "disagrees_on": [], "refusal": null},
      {"item_id": 1007, "origin": "B:I9", "queue": null, "disagrees_on": [], "refusal": null}
    ]}
    ```

    It exits `0` for any verdicts (a refusing verdict is an answer), runs
    `validate_config()` first, and never writes. Its one exit `3` is a
    board-level `DuplicateOriginError`, as for `origins`. The fake answers it
    from its file as it stands.
- Exit codes: `0` done; `2` usage or configuration (argparse, `ConfigError`,
  `BoardValidationError`); `3` refused by a rule (`ClaimScopeRefusedError`,
  `QueueRefusedError`, `TransitionRefusedError`, `DuplicateOriginError`,
  `UnknownOriginError`), with nothing written; `1` anything else, such as a provider failure.
  `scope` never exits `3`: an out-of-scope parent is an answer, not a refusal.
  `partial-items` exits `3` only for a duplicate Origin on the board.
- No `--dry-run` on the writes: design-to-board's own dry run (DB-D1 E) is the
  preview, and `origins`, `scope` and `partial-items` are read-only. Neither
  read is a `queue --check`: `scope` answers only the claim-scope question and
  `partial-items` only the partial-item one, not every refusal `queue` can
  raise.
