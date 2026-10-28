# Handoff: SQ8b · build the partial-items read (board-writes, part 8b)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: Rich has filled the Choice in both board-knowledge session files below
(ledger N31's four choices). If either is blank, stop and tell him. No other
board-writes unit (SQ5f, SQ7) is in flight; they share the `feat/board-writes`
branch. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`; if it can't fast-forward,
stop and tell Rich.

## Context

SQ8 (claude-skills DB-D13) consulted Juval on the shape and Eric on the names,
then took its cut line before a ruling. This session builds what Rich ruled.
claude-skills S9 (DB11) is blocked until this merges. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` row SQ8 and notes N14,
   N18, N30, N31.
2. board-knowledge `sessions/2026-10-27-juval-sq8-partial-items-shape.md`
   (the shape, the build spec, the tests) and
   `sessions/2026-10-27-eric-sq8-partial-items-names.md` (the names, the
   glossary entry). Each Choice section is the ruling; where it departs from
   the answer, the Choice wins.
3. `docs/board-writes/verb-contract.md`; `docs/adr/adr-0005-increment-verbs.md`;
   `src/squadra/engines.py` `check_queue_finishes`, `index_by_origin`;
   `src/squadra/domain.py` `IncrementRequest`, `OriginRecord`;
   `src/squadra/increments.py` `queue_increment`,
   `increments_and_partial_items`, `parent_in_claim_scope` (SQ5e's pattern);
   `src/squadra/cli.py` `_board_parser`, `_cmd_board`, `_parse_board_args`,
   `_run_board_verb`; `tests/test_cli_board.py`;
   `tests/contract/test_increment_contract.py`
   (`test_parent_in_claim_scope_agrees_with_queue_and_the_reported_scope`).

## This session's unit

Ledger item: SQ8 (part b)
Goal: `squadra board partial-items --requests-file PATH|-`, as N31 proposes
and Rich ruled. If Rich took the proposal unchanged:

- engines: `partial_item_disagreements(existing, request) ->
  tuple[tuple[RequestField, str], ...]` (today's four checks, same order and
  clause text), `refusal_to_finish(existing, request) -> str | None` (today's
  message bytes), `check_queue_finishes` raising `QueueRefusedError` on
  `refusal_to_finish`'s answer, `item_ids_carrying(origins, records)`.
  N14's rule and its message bytes stay unchanged; the existing refusal tests
  are the guard.
- domain: `ProposedRequest(origin, parent, predecessor_origins, title, body)`;
  `PartialItemReport(item_id, origin, requested, disagrees_on, refusal)` with
  `queue: Literal["finishes", "refuses"] | None` derived.
- `IncrementBoard.partial_items(requests) -> tuple[PartialItemReport, ...]`:
  one `items_with_origin()` read, one `index_by_origin`, sorted by item id.
  The docstring says how it differs from `increments_and_partial_items`.
- CLI: a Client over that read. Input is a JSON list with exactly the five
  keys and types, no Origin twice; anything else is exit 2 before any board
  read, one prefixed stderr line (as N19). `validate_config()` first. Exits 0
  for any verdicts, 2 usage/config, 3 for a board-level
  `DuplicateOriginError`, 1 for a provider failure. Never writes. `origins`'
  stderr lines stay.
- The fake answers it with no file-format change.

Docs: the verb in `verb-contract.md` (the contract row, the CLI block, the
JSON, the exit codes, the "a board never reuses an item id" assumption, and
how it differs from a `queue --check`); the README's `board` rows; an ADR-0005
note (squadra now maps predecessor Origins to ids for this read; only
`IncrementBoard` reads `items_with_origin`); `GLOSSARY.md`'s Partial item
entry if Rich ruled it in, and `GLOSSARY-SETTLED.md` if he said so; a ledger
note recording Eric's reversal of his October exclusion next to N13.

Tests (Juval's, in the session file):
- The agreement test over a real stopped create of row R (fake
  `fail_create_after_step`, every step), its predecessor P an Increment.
  Requests: agree; title, body, parent differ; P's Origin left out; an extra
  predecessor Origin not on the board (must agree); a predecessor Origin
  only another partial item carries (maps to its id). For each: the read
  leaves the board file byte-identical; then on a copy, queue any absent
  predecessors first, then `queue` R with ids mapped. `QueueRefusedError`
  exactly when `queue == "refuses"`, and `str(exc) == refusal` byte for byte.
  The parent-differs request agrees before the parent link is written and
  refuses after.
- The fence: a request aimed at an Increment with different arguments (A3)
  gets no entry.
- The same agreement on all four board shapes in the contract suite.
- CLI exits: 0 with a refusing verdict; 2 for bad JSON, a missing key, an
  Origin twice, a config error, a validation error; 3 for a board-level
  duplicate; 1 for a provider failure; `[]` lists every partial item with
  `queue: null`.

Check the agreement test by mutation, as SQ5e did (for example, a read that
ignores the parent once set must fail).

Estimated work: ~60K (hard stop 120K). Cut line: if the agreement test isn't
green by about 80K, commit what's green, record the rest in the ledger, write
a handoff, and stop.

## Out of scope for this session

- SQ5f (`[[boards]]`, `in_claim_scope`) and SQ7 (host-side git credentials).
- Changing N14's rule, `origins`' shape, or its stderr lines.
- Dropping the partial half of `increments_and_partial_items` (Juval's
  later step, after DB11 uses this read).
- Any write in claude-skills or board-knowledge. design-to-board's half is
  claude-skills DB11 (S9); its handoff is written there once this merges.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ8 done, a note on the build's readings,
session log), run the four validation commands from `CLAUDE.md`, commit, push,
open the PR. Tell Rich the PR URL, and that claude-skills S9's gate is this
merge.
