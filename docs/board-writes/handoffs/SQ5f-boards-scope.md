# Handoff: SQ5f · `[[boards]]` and adapter-owned claim scope (board-writes, part 5f)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: SQ9 (the CRLF body fix, with this refresh) merged on squadra `main`,
and no other board-writes unit in flight; SQ5e (PR #57) and SQ8b (PR #62)
are already merged. Fetch first, then fast-forward the `feat/board-writes`
worktree (`.claude/worktrees/board-writes`) to `origin/main`; if it can't
fast-forward, stop and tell Rich.

## Context

These are WSQ1's deferred parts, carried since SQ5c. SQ5d built the relinquish
rule (N27) and closed N10. SQ5e added `squadra board scope --parent ID` and
made `IncrementBoard._in_scope` the public `IncrementBoard.parent_in_claim_scope`,
the one predicate behind `queue_increment`'s and `withdraw_increment`'s
`ClaimScopeRefusedError`; `Increment.in_claim_scope` reaches the same
`engines.parent_in_claim_scope` through `as_increment` (N30). Since this
handoff was first written, SQ8b added a fifth verb, `squadra board
partial-items` (`IncrementBoard.partial_items`, N31, N32): a read that
reports `queue`'s partial-item verdict, maps predecessor Origins to ids from
one `items_with_origin()` read, and checks no scope. SQ9 made `--body-file`
read exactly (N33). Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5e–SQ5f, SQ8,
   SQ9 and notes N27, N30, N32, N33 (N31 for the reasons only).
2. WSQ1 as recorded in board-knowledge
   `sessions/2026-10-08-juval-mandatory-claim-scope.md` (Choice item 3, and
   Answer items 4–5) and
   `sessions/2026-10-09-juval-parent-and-lookup-scope.md` (Answer and
   Choice; "the contract is per board").
3. `src/squadra/config.py` (`[board]`, `claim_scope`, `parent_scope_ids`);
   `src/squadra/supervisor.py` `_queued_gates`, `TickSeams`;
   `src/squadra/increments.py` `IncrementBoard.parent_in_claim_scope`,
   `IncrementBoard.partial_items`; `src/squadra/cli.py` `_cmd_board` (where
   `IncrementBoard` is composed from `[board]`);
   `src/squadra/engines.py` `parent_in_claim_scope`, `as_increment`;
   `docs/board-writes/verb-contract.md` (the CLI surface).

## This session's unit

Ledger item: SQ5f
Goal: WSQ1's deferred parts.

- `[[boards]]` with `claim_scope` per entry. The mandatory-scope check fires
  wherever a board is added. WSQ1 left the exact shape open, so settle it
  first, before building anything. In particular, decide how the five
  `squadra board` verbs (`queue`, `withdraw`, `origins`, `scope`,
  `partial-items`) pick a board when there is more than one. The contract is per board,
  and design-to-board never reads `squadra.toml` (claude-skills DB-D5). If
  the answer needs a new flag or any other change to the CLI surface, that
  is a verb-contract change: put it to Rich (after `/ask-juval` if he wants)
  and record his ruling before building. Keeping a single-`[board]` config
  valid as it is today is the expected default; note it if you go another way.
  `partial-items` stays per board: its Origin-to-id map and its assumption
  that a board never reuses an item id (N32, `verb-contract.md`) hold within
  one board. If the shape you settle would let one board's verbs see another
  board's items, that assumption must hold across them too: raise it with
  Rich rather than build it.
- An adapter-owned `in_claim_scope(item_id)`: the supervisor stops reading
  parent links for scope.
- Rename `seams.ado` (it holds any provider). Leave `seams.pat_ok` and the
  `_ado_pat_*` probe symbols alone: they are SQ7's.
- Keep the `scope` verb, `queue_increment`'s `ClaimScopeRefusedError` and
  `Increment.in_claim_scope` on **one** predicate through the rework. SQ5e's
  two guard tests must still pass unchanged:
  `tests/test_cli_board.py::test_scope_says_in_scope_exactly_when_queue_is_not_refused_for_scope`
  and
  `tests/contract/test_increment_contract.py::test_parent_in_claim_scope_agrees_with_queue_and_the_reported_scope`.
  SQ8b's agreement tests must pass unchanged too, since `IncrementBoard`'s
  composition may move:
  `tests/test_cli_board.py::test_partial_items_says_refuses_exactly_when_queue_refuses_with_that_message`
  and
  `tests/contract/test_increment_contract.py::test_partial_items_agrees_with_queue_on_every_stop`.

Estimated work: ~100K (hard stop 120K). Cut lines, in order: the rename goes
last and can go to an SQ5g; if the `[[boards]]` shape needs Rich's ruling,
build the adapter-owned `in_claim_scope` first and cut `[[boards]]` to SQ5g.
Record any cut in the ledger.

## Out of scope for this session

- Changing the verb contract (beyond keeping `scope` agreeing) without Rich's
  ruling, or the fake's file format.
- SQ7 (host-side git credentials, `pat_ok`, `_ado_pat_*`).
- SQ8's later step (N31): dropping the partial half of
  `increments_and_partial_items` and `origins`' stderr line waits until
  claude-skills DB11 uses `partial-items`.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5f status, findings, session log, the
next handoff: `handoffs/SQ7-host-git-credentials.md`, or an SQ5g if cut), run
the four validation commands from `CLAUDE.md`, commit, push, open the PR.
Tell Rich the PR URL.
