# Handoff: SQ3 · a registered fake provider (board-writes, part 3)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ2's PR (`feat/board-writes`) has merged.** If it is still
open, stop and tell Rich.

## Context

SQ2 froze the verb contract (claude-skills DB-D2–DB-D5) as
`squadra.increments.IncrementBoard`, over two new raw `BoardAccess` primitives,
`create_increment(IncrementRequest) -> int` and `items_with_origin() ->
tuple[OriginRecord, ...]` (ADR-0005). The test fakes in
`tests/helpers/board_fakes.py` implement them; no production provider does
(the ADO adapter raises `NotImplementedError`). DB-D1 A3 wants a fake provider
**registered in `PROVIDERS`**, so that SQ4's CLI and design-to-board's F tests
can run end to end without GitHub.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md`, ADR-0001, ADR-0004, ADR-0005.
2. `docs/board-writes/LEDGER.md` (notes N5–N9) and `docs/board-writes/verb-contract.md`.
3. `src/squadra/board.py` (`BoardAccess`, `PROVIDERS`, `build_board`),
   `src/squadra/increments.py`, `src/squadra/config.py`.
4. `tests/contract/` (`conftest.py`, `test_board_contract.py`,
   `test_increment_contract.py`) and `tests/helpers/board_fakes.py`.

First step: create the worktree and branch `feat/board-writes` afresh from
`main` (SQ2's branch is deleted on merge), and set SQ3 to in progress in the
ledger.

## This session's unit

Ledger items: SQ3
Goal: a production fake provider, `provider = "fake"`:
- Implements all of `BoardAccess`, read and write halves, against local state.
- Registered in `PROVIDERS`; `validate_config` honours ADR-0004 rule 3 like
  any adapter.
- The first design question: a CLI run is one process per verb, so the fake's
  state must outlive the process (a JSON file is the obvious shape). Decide
  where the file lives and how it is named (config key vs a path under
  `FLEET_HOME`), how a test seeds it, and how two concurrent CLI calls are kept
  from corrupting it. Record the choice as a ledger note; ADR only if it is hard
  to reverse.
- Add it to the `board` and `fake_board` contract fixtures so both suites run
  against it as a third shape.
Then a PR to squadra `main`.
Estimated work: ~50–70K tokens (budget: under 100K total, hard stop at 120K)

## Out of scope for this session

- CLI subcommands (SQ4), though the fake must be loadable via `build_board`.
- The GitHub adapter (SQ5).
- Ledger note N7 (Origin required to withdraw): Rich's call, before SQ4.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (status, notes, session log, next handoff
in `docs/board-writes/handoffs/`), commit, push, open the PR. Tell Rich the PR
URL and the next handoff path.
