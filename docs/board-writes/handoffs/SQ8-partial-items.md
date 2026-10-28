# Handoff: SQ8 · partial items as data (board-writes, part 8)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: no other board-writes unit (SQ5f, SQ7) is in flight; they share the
`feat/board-writes` branch. Fetch first, then fast-forward the
`feat/board-writes` worktree (`.claude/worktrees/board-writes`) to
`origin/main`; if it can't fast-forward, stop and tell Rich.

## Context

claude-skills DB-D13 (Rich's ruling of DB11, Juval's way). The case, inferred
by Juval and not yet probed:
1. A design-to-board first run stops mid-create of queue 1, leaving a
   partial item that carries `<map>:I1`.
2. The map is revised (say, a title changes).
3. `origins` leaves partial items out, so the re-run takes the first-run
   path and its checks pass.
4. `queue` is refused (`QueueRefusedError`, exit 3), and design-to-board's
   exit-3 fix ("re-run, whose checks name what to do") loops.

The rule SQ5e restored for scope applies here too: every exit 3 that
`queue`/`withdraw` can raise must be visible to a board check before any
write. Today a partial item shows only as `origins`' one stderr line per
item (N18), whose wording is not contract. This is now the only squadra item
design-to-board is waiting on: claude-skills S9 (DB11) is blocked until SQ8
merges, since S8 (DB10) is done. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` row SQ8 and notes
   N8, N14, N18, N30.
2. board-knowledge `sessions/2026-10-27-juval-first-run-parent-scope.md`,
   sections "The smallest squadra contract change" (why a question beats
   handing over the rule, and why scope stayed out of `origins`) and "A
   second break in the same rule".
3. claude-skills, read-only, on branch `feat/design-to-board`: get the
   ledger with `git -C ~/Code/claude-skills fetch`, then
   `git -C ~/Code/claude-skills show origin/feat/design-to-board:docs/design-to-board/LEDGER.md`.
   Read only rows DB11 (what S9 will build on your answer) and DB-D13, and
   the exit-3 coverage table under Decisions (the `QueueRefusedError`
   partial-item cell).
4. `docs/board-writes/verb-contract.md` (the partial-item notes and the CLI
   surface); `docs/adr/adr-0005-increment-verbs.md` (only `IncrementBoard`
   reads `items_with_origin`);
   `src/squadra/increments.py` `increments_and_partial_items`,
   `queue_increment`, `parent_in_claim_scope` (SQ5e's pattern);
   `src/squadra/engines.py` `check_queue_finishes`; `src/squadra/cli.py`
   `_run_board_verb` (`origins`' stderr lines); `tests/test_cli_board.py`.

## This session's unit

Ledger item: SQ8
Goal: report partial items as data in a fixed, contracted format, so that a
caller can see before any write which rows a `queue` would refuse because of
a partial item.

Settle the shape first (`/ask-juval`, and `/ask-eric` for names if wanted).
Rich rules, and the ruling is recorded before building. The constraints
already ruled:
- Not in `origins`' `{origin: …}` shape, for the reason DB-D12 kept scope
  out of it.
- No `queue --check`. It would widen to every refusal; Juval rejected it in
  DB-D12.
- Keep N14's retry judgment in squadra behind `check_queue_finishes` if you
  can, as SQ5e kept scope behind one predicate, rather than handing the
  caller the fields and the rule.
- One board read, like N18, so a concurrent retry can't make an item fall
  between two reads.

A design point to put to Juval: design-to-board's check runs before any
write, when some of a row's predecessors may not have item ids yet (they are
queued earlier in the same run).
- So a question verb that takes the full `queue` arguments can't always be
  asked.
- A listing gives each partial item's id, Origin, title, body, parent and
  predecessor ids, which the caller maps back to Origins through `origins`.
  But that moves N14's rule to the caller.
- A middle way: squadra judges what it can (title, body, parent) and reports
  the predecessor ids for the caller to map.

Pick with him. Whichever it is:
- DB11 needs the Origins partial items carry, for its withdrawn-row report
  line (the "on the board for good" case).
- It needs a disagreement it can name in its report.

Then build it:
- an `IncrementBoard` read, so the CLI stays a Client;
- a `squadra board` verb;
- exit codes like the other verbs: 0, 2 for config, 1 for provider;
  `validate_config()` first; it never writes.

The fake answers it with no file-format change. `origins`' stderr lines stay
as they are unless the ruling says otherwise. Add the verb to
`verb-contract.md`, the README's `board` rows, and an ADR-0005 note if the
ruling changes who reads what.

Tests:
- Juval's case as a squadra-side agreement test. Over a partial item from a
  real stopped create (`fail_create_after_step`, as in
  `test_a_crashed_queue_exits_1_and_a_retry_in_a_second_process_finishes_it`)
  and requests that agree and that disagree (title, body, parent, an extra
  predecessor): the new verb reports a disagreement exactly when `queue`
  with those arguments is refused with `QueueRefusedError`, and it leaves
  the board file byte-identical.
- The same agreement on all four board shapes in the contract suite.
- The new verb's exit codes.

Estimated work: ~80K, with the consult (hard stop 120K). Cut line: if Rich's
ruling isn't in by about 50K, record the proposal and his open question in
the ledger, write the build as a fresh handoff, and stop.

## Out of scope for this session

- SQ5f (`[[boards]]`, `in_claim_scope`) and SQ7 (host-side git credentials).
- Changing `check_queue_finishes`'s rule (N14), or `origins`' shape.
- Any write in claude-skills or board-knowledge. design-to-board's half is
  claude-skills DB11 (S9); its handoff is written there once this merges.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ8 status, the ruling and findings as
a note, session log, the next handoff if any), run the four validation
commands from `CLAUDE.md`, commit, push, open the PR. Tell Rich the PR URL,
and that claude-skills S9's gate is this merge.
