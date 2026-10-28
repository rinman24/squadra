# Handoff: SQ5e · `squadra board scope --parent ID` (board-writes, part 5e)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5d's PR has merged on squadra `main`.** If it hasn't,
stop and tell Rich.

## Context

claude-skills ruled DB-D12: design-to-board must fail a first-run `--parent`
that is out of the claim scope, before any write, and asks squadra for the
answer through a read verb. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5e–SQ5f and
   notes N18, N19.
2. board-knowledge `sessions/2026-10-27-juval-first-run-parent-scope.md`: its
   Named decision, Blocking question 1 (answered) and Rich's Choice.
3. `docs/board-writes/verb-contract.md` (the CLI surface section).
4. `src/squadra/increments.py` `IncrementBoard._in_scope` and
   `queue_increment`; `src/squadra/engines.py` `parent_in_claim_scope`;
   `src/squadra/cli.py`, the `board` subcommand tree; `tests/test_cli_board.py`.

## This session's unit

Ledger item: SQ5e
Goal: add the read verb, as ruled.

- `squadra board scope --parent ID` prints `{"parent": N, "in_claim_scope": bool}`.
  It exits 0 whether in scope or not, 2 for a config error, 1 for a provider
  error, like the other `board` verbs (N19). It runs `validate_config()` first
  and never writes.
- It goes through the one predicate that `queue_increment`'s
  `ClaimScopeRefusedError` and `Increment.in_claim_scope` already use (today
  `engines.parent_in_claim_scope`, via `IncrementBoard._in_scope`). Add an
  `IncrementBoard` read if the CLI needs one, so the CLI stays a Client.
- Agreement test: scope ∈ {whole-board, parents `[1]`} × parent ∈ {1, 2}.
  `scope` says in scope exactly when `queue` is not refused for scope, and
  `scope` leaves the board file byte-identical.
- Add the verb to `verb-contract.md` and the README's `board` rows.
- Not wanted: exposing `claim_scope` itself, scope in `origins`, a
  `queue --check` flag.

Estimated work: ~50K. If there's room left, start SQ5f
(`handoffs/SQ5f-boards-scope.md`) in a fresh session, not this one.

## Out of scope for this session

- `[[boards]]`, adapter-owned `in_claim_scope`, the `seams.ado` rename (SQ5f).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5e status, findings, session log), run
the four validation commands from `CLAUDE.md`, commit, push, open the PR. Tell
Rich the PR URL.
