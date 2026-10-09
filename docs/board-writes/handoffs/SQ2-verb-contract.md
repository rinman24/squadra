# Handoff: SQ2 · the verb contract on `BoardAccess` (board-writes, part 2)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until design-to-board's S1b has ruled DBQ2, DBQ3, DBQ4 and DBQ7**
(claude-skills `docs/design-to-board/LEDGER.md`, Decisions). If any is still
open, stop and tell Rich which.

## Context

SQ1 (PR on `feat/board-writes`, ADR-0004) gave squadra `Lifecycle.WITHDRAWN`,
the transition rule `squadra.engines.check_transition`, the
`predecessor-withdrawn` tick state, and fail-closed state mapping. Nothing
writes WITHDRAWN yet. SQ2 adds the verbs DB-D1 names, as a contract.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md` (Origin, Withdrawn), ADR-0001, ADR-0004.
2. `docs/board-writes/LEDGER.md`, especially notes N1–N4.
3. claude-skills DB-D1 and the S1b rulings on DBQ2/3/4/7, read-only.
4. `src/squadra/board.py` (`BoardAccess`), `src/squadra/engines.py`
   (`check_transition`), `tests/contract/`.

First step: once SQ1's PR has merged, create the worktree and branch
`feat/board-writes` afresh from `main` (the ledger's branch; SQ1's is deleted
on merge), and set SQ2 to in progress in the ledger.

## This session's unit

Ledger items: SQ2
Goal: the verb contract, no provider yet:
- `BoardAccess` gains `queue_increment(origin, parent, predecessors, title,
  body) -> item_id`, `withdraw_increment(item_id)`,
  `increments_by_origin(parent) -> {origin: (item_id, Lifecycle)}`, shaped by
  the DBQ2 (cross-parent lookup) and DBQ7 (queued vs held) rulings.
- Origin stored opaquely: squadra never parses it.
- `withdraw_increment` goes through `check_transition`; replace its ACTIVE
  branch with the DBQ3 ruling (and update ADR-0004 decision 2 to match).
- Withdrawing on a board with no withdrawn state fails loudly (N1).
- The CLI surface (DBQ4: which parent, how the target `squadra.toml` is found)
  is designed and recorded here; the subcommands themselves are SQ4.
Then a PR to squadra `main`.
Estimated work: ~60–80K tokens (budget: under 100K total, hard stop at 120K)

## Out of scope for this session

- The fake provider's write half and its registration in `PROVIDERS` (SQ3).
- CLI subcommands and claim-scope orchestration (SQ4).
- The GitHub adapter (SQ5).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (status, notes, session log, next handoff
in `docs/board-writes/handoffs/`), commit, push, open the PR. Tell Rich the PR
URL and the next handoff path.
