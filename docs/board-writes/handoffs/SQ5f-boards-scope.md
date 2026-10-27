# Handoff: SQ5f · `[[boards]]` and adapter-owned claim scope (board-writes, part 5f)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5e's PR has merged on squadra `main`.** If it hasn't,
stop and tell Rich.

## Context

These are WSQ1's deferred parts, carried since SQ5c. SQ5d built the relinquish
rule (N27) and closed N10. SQ5e added `squadra board scope`, which shares one
scope predicate with `queue_increment`'s refusal and `Increment.in_claim_scope`.
Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5e–SQ5f and
   note N27.
2. WSQ1 as recorded in board-knowledge
   `sessions/2026-10-08-juval-mandatory-claim-scope.md` and
   `sessions/2026-10-09-juval-parent-and-lookup-scope.md` (Answer and
   Choice).
3. `src/squadra/config.py` (`[board]`, `claim_scope`);
   `src/squadra/supervisor.py` `_queued_gates`, `TickSeams`;
   `src/squadra/increments.py` `_in_scope`; `src/squadra/engines.py`
   `parent_in_claim_scope`.

## This session's unit

Ledger item: SQ5f
Goal: WSQ1's deferred parts.

- `[[boards]]` with `claim_scope` per entry. The mandatory-scope check fires
  wherever a board is added.
- An adapter-owned `in_claim_scope(item_id)`: the supervisor stops reading
  parent links for scope.
- Rename `seams.ado` (it holds any provider).
- Keep the `scope` verb, `queue_increment`'s `ClaimScopeRefusedError` and
  `Increment.in_claim_scope` on **one** predicate through the rework. SQ5e's
  agreement test must still pass unchanged.

Estimated work: ~100K (hard stop 120K). Cut line: the rename goes last and can
go to an SQ5g, recorded in the ledger.

## Out of scope for this session

- Changing the verb contract (beyond keeping `scope` agreeing), or the fake's
  file format.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5f status, findings, session log, the
next handoff if any), run the four validation commands from `CLAUDE.md`,
commit, push, open the PR. Tell Rich the PR URL.
