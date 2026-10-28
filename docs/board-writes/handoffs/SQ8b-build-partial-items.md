# Handoff: SQ8b · build the partial-items read (board-writes, part 8b)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: PR #61 (SQ8's proposal and Rich's ruling, ledger N31) is merged on
`main`, and no other board-writes unit (SQ5f, SQ7) is in flight; they share the
`feat/board-writes` branch. Fetch first, then fast-forward the
`feat/board-writes` worktree (`.claude/worktrees/board-writes`) to
`origin/main`; if it can't fast-forward, stop and tell Rich.

## Context

SQ8 (claude-skills DB-D13) settled the shape with Juval and the names with
Eric, and Rich ruled on 2026-10-27 (N31): Juval's shape, exit 3 on a
board-level duplicate Origin, Juval's build spec with all of Eric's names,
and partial item in both glossaries (already done). The spec below is that
merge; it is the ruling, so build it as written. claude-skills S9 (DB11) is
blocked until this merges. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` row SQ8 and notes N14,
   N18, N19, N30, N31.
2. board-knowledge `sessions/2026-10-27-juval-sq8-partial-items-shape.md`
   (why: the new-id argument, the rejected options, the open window) and
   `sessions/2026-10-27-eric-sq8-partial-items-names.md` (why each name).
   Both Choice sections hold the ruling. Read for reasons; the spec here is
   what to build.
3. `docs/board-writes/verb-contract.md`; `docs/adr/adr-0005-increment-verbs.md`;
   `src/squadra/engines.py` `check_queue_finishes`, `index_by_origin`;
   `src/squadra/domain.py` `IncrementRequest`, `OriginRecord`;
   `src/squadra/increments.py` `queue_increment`,
   `increments_and_partial_items`, `parent_in_claim_scope` (SQ5e's pattern);
   `src/squadra/cli.py` `_board_parser`, `_cmd_board`, `_parse_board_args`,
   `_run_board_verb`; `tests/test_cli_board.py`;
   `tests/contract/test_increment_contract.py`
   (`test_parent_in_claim_scope_agrees_with_queue_and_the_reported_scope`).

## The ruled build spec

### engines.py

N14's rule and `check_queue_finishes`'s message bytes stay unchanged; the
existing refusal tests are the guard.

- `RequestField = Literal["parent", "predecessors", "title", "body"]`.
  Docstring: "The compared fields of an `IncrementRequest`; Origin is the
  key, not a field."
- `partial_item_disagreements(existing: OriginRecord, request:
  IncrementRequest) -> tuple[tuple[RequestField, str], ...]`: today's four
  checks in today's order (parent once set, extra predecessors on the item,
  title, body), each giving `(field, clause)` with today's clause text.
- `refusal_to_finish(existing, request) -> str | None`: today's
  `QueueRefusedError` message built from those clauses, or `None` when there
  are none.
- `check_queue_finishes(existing, request) -> None`: raises
  `QueueRefusedError(refusal)` when `refusal_to_finish` returns one. `queue`
  and the new read then share one predicate, as SQ5e's scope does.
- `item_ids_carrying(origins: Sequence[str], records: Mapping[str,
  OriginRecord]) -> tuple[int, ...]`: pure. Each Origin carried by an item
  (an Increment or a partial item) gives that item's id; an Origin no item
  carries is dropped. Docstring: the drop is sound only for N14's subset
  check (a predecessor not yet on the board gets a new id, which no partial
  item can carry, assuming a board never reuses an item id; Juval, N31), so
  this is not a general Origin-to-id map; a queue path must not use it.

### domain.py

- `ProposedRequest(origin: str, parent: int, predecessor_origins:
  tuple[str, ...], title: str, body: str)`, frozen, slots. Docstring: "An
  `IncrementRequest` the planner has not made yet. Its predecessors are
  named by Origin, because some have no item id until this run queues them."
- `PartialItemReport(item_id: int, origin: str, requested: bool,
  disagrees_on: tuple[RequestField, ...], refusal: str | None)`, frozen,
  slots, with the derived property `queue -> Literal["finishes", "refuses"]
  | None`: `None` when not requested, `"refuses"` exactly when `refusal` is
  set, else `"finishes"`. Stored `requested` and `refusal`, derived `queue`,
  as `OriginRecord.partial` derives from `lifecycle` (Eric). Not
  `PartialItem`: a partial item is never a domain object.

If `RequestField` must live in `domain.py` so `PartialItemReport` can use it
without an import cycle, put it there and import it into `engines.py`.

### increments.py

- `IncrementBoard.partial_items(requests: Sequence[ProposedRequest]) ->
  tuple[PartialItemReport, ...]`: one `items_with_origin()` read, one
  `index_by_origin` (so a duplicate Origin raises `DuplicateOriginError`).
  For each partial record, sorted by item id: the request carrying its
  Origin, if any; map its `predecessor_origins` with `item_ids_carrying`
  over the same index; build the `IncrementRequest`; take
  `partial_item_disagreements` and `refusal_to_finish`. A record no request
  names gets `requested=False`, no disagreements, no refusal.
- It answers only N14. Requests aimed at an Increment, an absent Origin, an
  out-of-scope parent or a withdrawn Origin get no entry; it does not check
  scope (that is `scope`'s question, and `queue` checks scope first).
- Docstring line: unlike `increments_and_partial_items`, which returns
  partial items as `OriginRecord`s for `origins`' stderr lines, this returns
  reports with `queue`'s verdict.
- Only `IncrementBoard` reads `items_with_origin`, so ADR-0005's rule holds.
  Duplicate requests (an Origin twice) are the CLI's usage error; the method
  may assume distinct Origins (assert or raise `ValueError`, your call).

### cli.py

```text
squadra board partial-items --requests-file PATH|-
```

- Input: a JSON list; each entry exactly `{"origin": str, "parent": int,
  "predecessor_origins": [str, ...], "title": str, "body": str}`. `[]` is
  valid. Bad JSON, a non-list, a missing or extra key, a wrong type (a bool
  is not an int), an Origin twice, or an unreadable file is exit 2, one
  prefixed stderr line, before any board read (as N19). Read the file
  exactly as given (bodies compare exactly, N14).
- Composition as for every verb: `load_config` → `build_board` →
  `validate_config()` → `IncrementBoard`. A Client: parse, call, dump.
- Output, one JSON document:

  ```json
  {"partial_items": [
    {"item_id": 1005, "origin": "A:I1", "queue": "refuses",
     "disagrees_on": ["title"],
     "refusal": "origin 'A:I1' is on partial item 1005, which disagrees with this request: title 'Old' != 'New'"},
    {"item_id": 1006, "origin": "A:I2", "queue": "finishes", "disagrees_on": [], "refusal": null},
    {"item_id": 1007, "origin": "B:I9", "queue": null, "disagrees_on": [], "refusal": null}
  ]}
  ```

  Every partial item on the board, sorted by item id, never filtered by
  prefix (design-to-board's job).
- Exits: `0` for any verdicts (a refusing verdict is an answer); `2` usage
  or configuration (`ConfigError`, `BoardValidationError`); `3` only for a
  board-level `DuplicateOriginError`, as `origins`; `1` anything else.
  Never writes. `origins`' stderr lines stay as they are.
- The fake answers it with no file-format change.

### Docs

- `verb-contract.md`: the contract row (`partial_items(requests)`), the CLI
  block, the input and output JSON, the exit codes (the one exit-3 case),
  the assumption "a board never reuses an item id", and a line on why it is
  not a `queue --check` (it answers only N14).
- README: the `board` rows.
- ADR-0005: a note that squadra now maps predecessor Origins to ids for this
  read; only `IncrementBoard` reads `items_with_origin`.
- Glossary: done in PR #61; nothing to add.

## Tests

- Agreement test (Juval's), squadra side, on the fake file: for each
  `fail_create_after_step` step, a real stopped create of row R whose
  predecessor P is already an Increment. Requests: agree; title differs;
  body differs; parent differs; P's Origin left out (the extra
  predecessor); an extra predecessor Origin not on the board (must agree);
  a predecessor Origin only another partial item carries (maps to its id).
  For each: the read leaves the board file byte-identical; then, on a copy,
  queue any absent predecessors first, as design-to-board would, and
  `queue` R with ids mapped. `QueueRefusedError` exactly when
  `queue == "refuses"`, and `str(exc) == refusal` byte for byte. The
  parent-differs request agrees before the parent link is written and
  refuses after.
- The fence: a request aimed at an Increment with different arguments (an
  A3 refusal) gets no entry.
- The same agreement on all four board shapes in the contract suite.
- Engine unit tests: `partial_item_disagreements` order and fields;
  `refusal_to_finish` is `None` exactly when there are none;
  `item_ids_carrying` drops an absent Origin and maps a partial one.
- CLI exits: 0 with a refusing verdict; `[]` lists every partial item with
  `queue: null`; 2 for bad JSON, a missing key, an extra key, a wrong type,
  an Origin twice, a config error, a validation error; 3 for a board-level
  duplicate; 1 for a provider failure.
- Check the agreement test by mutation, as SQ5e did (for example, a read
  that ignores the parent once set, or maps an absent Origin to a made-up
  id, must fail).

Estimated work: ~60K (hard stop 120K). Cut line: if the agreement test isn't
green by about 80K, commit what's green, record the rest in the ledger,
write a handoff, and stop.

## Out of scope for this session

- SQ5f (`[[boards]]`, `in_claim_scope`) and SQ7 (host-side git credentials).
- Changing N14's rule, `origins`' shape, or its stderr lines.
- Dropping the partial half of `increments_and_partial_items` (Juval's
  later step, after DB11 uses this read).
- Any write in claude-skills or board-knowledge. design-to-board's half is
  claude-skills DB11 (S9); its handoff is written there once this merges.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ8 done, a note on the build's
readings, session log), run the four validation commands from `CLAUDE.md`,
commit (no Claude/Anthropic trailers), push, open the PR. Tell Rich the PR
URL, and that claude-skills S9's gate is this merge.
