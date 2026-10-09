# The increment verb contract and its CLI surface

Frozen by SQ2 (2026-10-09). Rulings: claude-skills DB-D1–DB-D5. Layering:
[ADR-0005](../adr/adr-0005-increment-verbs.md). Code: `squadra.increments`.

## The contract

`IncrementBoard(board, claim_scope, parent_scope_ids)`, per board (the one
`squadra.toml` names; a predecessor on another board is not visible).

| Verb | Does | Refuses (nothing written) |
|---|---|---|
| `queue_increment(origin, parent, predecessors, title, body) -> item_id` | Lands a QUEUED item under `parent` with the predecessor links, carrying `origin` opaquely. If `origin` is already on the board with identical arguments, returns that item | Parent outside claim scope (`ClaimScopeRefusedError`); `origin` present with any differing argument, or withdrawn (`QueueRefusedError`); `origin` carried twice (`DuplicateOriginError`) |
| `withdraw_increment(item_id)` | QUEUED → WITHDRAWN. Already WITHDRAWN: no write | Item outside claim scope (`ClaimScopeRefusedError`); ACTIVE or DONE (`TransitionRefusedError`); a board with no withdrawn state (`BoardValidationError`) |
| `increments_by_origin() -> {origin: Increment}` | Every Origin on the board, every bucket. `Increment(item_id, parent, lifecycle, in_claim_scope)`; scope is reported, never filtered | A duplicate Origin (`DuplicateOriginError`, naming both items) |

Notes:
- `parent` is required under both claim scopes (DB-D5). `Increment.parent` is
  `None` only for an item unparented by hand after it was queued.
- Predecessors compare as a set; title and body compare exactly.
- squadra does not check that predecessors exist; design-to-board fails loudly
  on a missing, withdrawn or out-of-scope predecessor Origin (DB-D4).
- Filtering Origins by map prefix (`<map>:`) is design-to-board's job; squadra
  never parses an Origin.

## The CLI surface (built in SQ4)

`squadra increment` is taken by the status-file ops, so the verbs go under
`squadra board`. design-to-board runs these in the target repo (or with
`FLEET_HOME` set); squadra resolves its own `squadra.toml` through the usual
`defaults < squadra.toml < FLEET_* env < flag` lookup, and design-to-board never
reads it (DB-D5). Every subcommand runs `validate_config()` first.

```text
squadra board queue    --origin ORIGIN --parent ID [--predecessor ID ...]
                       --title TITLE --body-file PATH|-
squadra board withdraw --item ID
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
  - `origins` → `{"A:I1": {"item_id": 123, "parent": 200, "lifecycle": "queued", "in_claim_scope": true}, ...}`
- Exit codes: `0` done; `2` usage or configuration (argparse, `ConfigError`,
  `BoardValidationError`); `3` refused by a rule (`ClaimScopeRefusedError`,
  `QueueRefusedError`, `TransitionRefusedError`, `DuplicateOriginError`), with
  nothing written; `1` anything else, such as a provider failure.
- No `--dry-run` on the writes: design-to-board's own dry run (DB-D1 E) is the
  preview, and `origins` is read-only.
