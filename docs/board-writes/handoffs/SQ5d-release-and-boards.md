# Handoff: SQ5d · release a withdrawn in-flight item, then `[[boards]]` and claim scope (board-writes, part 5d)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5c's PR has merged on squadra `main` and Rich has filled
in the Choice of board-knowledge
`sessions/2026-10-27-juval-withdrawn-in-flight-release.md`** (ledger N25). If
either is missing, stop and tell Rich.

## Context

SQ5c built the in-tick half of N20 ruling 6: a requeue, rollback or claim
whose state write GitHub refuses (`StateWriteRefusedError`) is reported for
that item and the tick goes on. It ran the adapter's writes live once (N26).
The between-ticks case went to Juval, who recommends a release of a
fleet-claimed WITHDRAWN item and a sandbox stop on a hand-DONE one. Read only
these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5c–SQ5d and
   notes N3, N10, N20 (ruling 6), N25, N26.
2. The Juval session above: its Answer and Rich's Choice. The Choice wins
   wherever it differs from the Answer.
3. `docs/adr/adr-0004-withdrawn-lifecycle-bucket.md` decision 1.
4. `src/squadra/engines.py` `LifecycleEngine._classify` / `_classify_done`;
   `src/squadra/supervisor.py` `_gather`, `_build_facts`, `_retry`,
   `_escalate`.

## This session's unit

Ledger item: SQ5d
Goal: build Rich's N25 Choice, then WSQ1's deferred parts.

- **Names first.** `/ask-eric` names the release's state and action (Juval's
  placeholders are `RELEASED` / `ReleaseClaim`; "release" may clash with
  delivery vocabulary).
- **The release**, as ruled: the engine rule, the gather of the WITHDRAWN
  bucket filtered on the claimed tag, the orchestrator's ordered steps
  (`claimed` dropped last), the hand-DONE sandbox stop, and the memo of the
  project listing if ruled. Amend ADR-0004 decision 1 and its Consequences,
  N3 and N10 in the ledger. Add the contract tests the Answer lists
  (`closed_between_ticks` per reason over two ticks, the completed variant,
  the N10 variant, the engine guard-order test) on the `gh` shape and a fake
  shape with `withdrawn` mapped.
- **Then WSQ1's deferred parts:** `[[boards]]` with `claim_scope` per entry
  (the check fires wherever a board is added), an adapter-owned
  `in_claim_scope(item_id)` (the supervisor stops reading parent links for
  scope), renaming `seams.ado`. N10 is closed by the release rule if Rich
  rules it; otherwise check GitHub for a compare-and-set and record the
  result.

Estimated work: ~100K (budget under 100K, hard stop 120K). Cut line: if
long, `[[boards]]` / `in_claim_scope` / the rename go to an SQ5e, recorded in
the ledger. Build the release first.

## Out of scope for this session

- Changing the verb contract, the CLI surface or the fake's file format.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5d status, findings, session log, the
next handoff if any), run the four validation commands from `CLAUDE.md`,
commit, push, open the PR. Tell Rich the PR URL.
