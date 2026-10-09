# Handoff: SQ5 · the GitHub adapter (board-writes, part 5)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until both have merged:** SQ4's PR (`squadra board` CLI) on
squadra `main`, and claude-skills DB4 (design-to-board F, the executor over
`squadra board`; DB-D1 puts D after F). If either is still open, stop and tell
Rich.

## Context

SQ1–SQ4 built the provider-neutral half: `Lifecycle.WITHDRAWN` (ADR-0004), the
verb contract `IncrementBoard` over two raw `BoardAccess` primitives
(`create_increment`, `items_with_origin`; ADR-0005), the registered fake
(`provider = "fake"`, ledger N16, N17) and the `squadra board` CLI (N18, N19).
design-to-board runs end to end against the fake. The ADO adapter still raises
`NotImplementedError` for both primitives. SQ5 is DB-D1 activity D: a GitHub
`BoardAccess`, reads and writes, so DB-D1 G (integration on a real GitHub
board) can run.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md`, ADR-0001, ADR-0004, ADR-0005 (decision 1:
   the four call-order obligations).
2. `docs/board-writes/LEDGER.md` (notes N1, N8, N10, N14, N15, N16–N19) and
   `docs/board-writes/verb-contract.md`.
3. `src/squadra/board.py` (`BoardAccess`, `AzCliAdo` as the one live adapter,
   `PROVIDERS`), `src/squadra/fake_board.py` (the primitives as k writes),
   `src/squadra/config.py`.
4. `tests/contract/` (the `board` and `fake_board` fixtures every provider runs).
5. claude-skills `docs/design-to-board/LEDGER.md`: DB-D1 (G's acceptance test),
   DB-D2 (GitHub's withdrawn state: closed, reason "not planned"), and WSQ1
   wherever it is recorded (`[[boards]]`, `in_claim_scope`).

First step: create the worktree and branch `feat/board-writes` afresh from
`main`, and set SQ5 to in progress in the ledger.

## This session's unit

Ledger items: SQ5 (split first)
Goal: SQ5 does not fit one session (reads, writes, config, N10, live
verification). Before any code, split it in the ledger into units under the
budget, each a mergeable PR, for example: SQ5a the read half and
`validate_config` against a real board; SQ5b `create_increment` and
`items_with_origin` proving obligations 1–4 (N15, with N14's title and body on
the first call), plus `set_state` into WITHDRAWN; SQ5c `[[boards]]` /
`in_claim_scope` (WSQ1) and N10's compare-and-set if GitHub offers one. Put the
split to Rich if a unit's order or scope is a real choice; otherwise record it
and build the first unit. How the adapter reaches GitHub (`gh api`, as ADO
goes through `az`) is the first unit's choice; record it as a ledger note.
Every unit runs the existing `board` and `increment` contract suites against
the new provider, offline (recorded or stubbed transport), as SQ3 did for the
fake.
Estimated work: the split ~10K; the first unit ~60–80K (budget: under 100K
total, hard stop at 120K)

## Out of scope for this session

- Changing the verb contract, the CLI surface or the fake's file format (raise
  a ledger note instead).
- Any write in claude-skills or board-knowledge.
- The ADO write primitives (no ruling asks for them).

## Wrap-up

Update `docs/board-writes/LEDGER.md` (the split, status, notes, session log,
next handoff in `docs/board-writes/handoffs/`), commit, push, open the PR. Tell
Rich the PR URL and the next handoff path.
