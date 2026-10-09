# Handoff: SQ4 · `squadra board {queue,withdraw,origins}` (board-writes, part 4)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ3's PR has merged** (it registers the fake provider
these subcommands are tested against). If it is still open, stop and tell Rich.

## Context

SQ2–SQ2c froze the verb contract as `squadra.increments.IncrementBoard`
(ADR-0005), and `verb-contract.md` designs its CLI surface. SQ3 registered
`provider = "fake"` (`squadra.fake_board.JsonFileBoard`, one JSON file at
`<FLEET_HOME>/.squadra/fake-board.json`; ledger N16, N17), so the CLI can run
end to end, one process per verb, with no live board. The ADO adapter still
raises `NotImplementedError` for both write primitives; GitHub is SQ5.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md`, ADR-0001, ADR-0005.
2. `docs/board-writes/LEDGER.md` (notes N7, N8, N10, N11, N13, N16, N17) and
   `docs/board-writes/verb-contract.md` ("The CLI surface", the spec).
3. `src/squadra/cli.py` (the composition root and its existing subcommand
   style), `src/squadra/increments.py`, `src/squadra/fake_board.py`
   (module docstring: the file format a test seeds).
4. `tests/test_cli.py`, `tests/test_fake_board.py`.

First step: create the worktree and branch `feat/board-writes` afresh from
`main`, and set SQ4 to in progress in the ledger.

## This session's unit

Ledger items: SQ4
Goal: the three subcommands as thin Clients over `IncrementBoard`, exactly as
`verb-contract.md` specifies:
- `load_config` → `build_board` → `validate_config()` → `IncrementBoard(board,
  config.claim_scope, config.parent_scope_ids)`; no rule in the CLI.
- `queue` reads the body from `--body-file PATH` or `-` (stdin), never argv.
- stdout one JSON document; errors one stderr line prefixed
  `squadra board <verb>:`; exit codes 0 / 2 (usage, `ConfigError`,
  `BoardValidationError`) / 3 (the five refusals) / 1 (anything else,
  including `InjectedCrashError` and `NotImplementedError` on ADO).
- `origins` writes one stderr line per partial item (item id and Origin) and
  still exits 0. That needs the raw records: decide whether `IncrementBoard`
  grows a read for them or the CLI reads `items_with_origin` itself, which
  ADR-0005 says nothing but `IncrementBoard` does. Record the choice as a
  ledger note.
- Tests run the real CLI (`cli.main([...])`, and at least one subprocess per
  verb) against `provider = "fake"` with a seeded board file, covering each
  exit code, a crash (`fail_create_after_step`) and its retry across two
  processes, and that a refused verb leaves the file byte-identical.
- DB-D7 (claude-skills design-to-board ledger, S1c): a test changes the
  fake's state between two `squadra board` calls, from its own process, and
  the next call sees it: move an item QUEUED → ACTIVE → DONE, and inject a
  second item carrying an existing Origin (`DuplicateOriginError`, exit 3).
  design-to-board's F runs its T4–T6 and Juval's seven cases this way.
Then a PR to squadra `main`.
Estimated work: ~50–70K tokens (budget: under 100K total, hard stop at 120K)

## Out of scope for this session

- The GitHub adapter (SQ5) and N10's compare-and-set.
- Changing the verb contract or the fake's file format (raise a ledger note
  instead).
- Any write in claude-skills or board-knowledge. The N7/N8 reopening of
  DB-D1/DB-D4 is recorded there as DB-D10 (claude-skills PR #14); it matches
  `verb-contract.md`. If it differs, raise a ledger note.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (status, notes, session log, next handoff
in `docs/board-writes/handoffs/`), commit, push, open the PR. Tell Rich the PR
URL and the next handoff path.
