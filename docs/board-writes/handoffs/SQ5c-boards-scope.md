# Handoff: SQ5c · SQ5b's cut, then `[[boards]]` and claim scope (board-writes, part 5c)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5b's PR has merged on squadra `main`** (it adds this
file, GitHub's `create_increment` / `items_with_origin` and ledger N24). If it
hasn't, stop and tell Rich.

## Context

SQ5b finished `provider = "github"`'s board writes. `squadra board queue` /
`withdraw` / `origins` work on GitHub against the stubbed transport, and the
`increment` and crash-safe suites run on it. It took its handoff's cut: the
tick's half of N20 ruling 6 and the first live run of the adapter's writes
come here, ahead of SQ5c's own scope. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5b–SQ5c and
   notes N10, N20 (ruling 6), N21, N23, N24 (its open question and its
   unverified facts).
2. `docs/adr/adr-0006-github-native-state.md` decisions 1, 4 and 7.
3. `src/squadra/supervisor.py`: `_gather`, `_retry`, `_claim_and_launch`
   (the launch rollback) and `main`'s `BoardValidationError` handler.
4. `src/squadra/github_board.py`: `set_state` and `create_increment`.

## This session's unit

Ledger item: SQ5c
Goal: settle and build the tick's refused-requeue report, run the adapter's
writes live once, then WSQ1's deferred parts.

- **Settle first** (N24's open question). Under closure wins, an ACTIVE issue
  closed between ticks reads DONE or WITHDRAWN at the next gather, so the next
  tick never requeues it. A requeue meets a closed issue only when the issue
  closes within a tick. Decide what N20 ruling 6's contract case means for a
  fleet-claimed item that left ACTIVE by closure: what is reported, and where,
  and whether its sandbox is torn down. If the answer changes N20's wording,
  put it to Rich before building.
- **The tick.** A requeue (`_retry`, the launch rollback) that finds the issue
  closed reports it for that item, and the tick goes on, never ending on it.
  Keep the supervisor provider-blind: no GitHub type crosses the seam. Add
  the contract case as settled, for each close reason.
- **Live run** on Rich's sandbox (`rinman24/squadra-sandbox`, user project
  #1). Ask him first, because it mutates the board. Run a queue, a claim's
  Status write, a label, a comment and a withdraw. Check N24's three
  unverified facts:
  - the labels POST creates a missing `squadra:committed` label;
  - re-adding an issue to the project returns its existing item;
  - `addSubIssue` on a child that already has a parent.

  Note what the project's workflows did, especially "Item added to project"
  and "Auto-add sub-issues to project", against the read-back. Record the
  results in the session log.
- **Then WSQ1's deferred parts:** `[[boards]]` with `claim_scope` per entry
  (the check fires wherever a board is added), an adapter-owned
  `in_claim_scope(item_id)` (the supervisor stops reading parent links for
  scope), renaming `seams.ado`, and N10's compare-and-set if GitHub offers
  one, else a record that it doesn't.

Estimated work: ~90–110K (budget under 100K, hard stop 120K). Cut line: if
long, `[[boards]]` / `in_claim_scope` / the rename / N10 go to an SQ5d,
recorded in the ledger. Build the tick change and the live run first.

## Out of scope for this session

- Changing the verb contract, the CLI surface or the fake's file format.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5c status, findings, session log, the
next handoff if any), run the four validation commands from `CLAUDE.md`,
commit, push, open the PR. Tell Rich the PR URL.
