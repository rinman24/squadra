# Handoff: SQ5b · the GitHub adapter's board writes (board-writes, part 5b)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5a's PR has merged on squadra `main`** (it adds this
file, `squadra.github_board`, ADR-0006 and ledger N21–N23). If it hasn't, stop
and tell Rich.

## Context

SQ5a shipped `provider = "github"`. It covers:
- reads, `validate_config` and the adapter's tick writes;
- the native-state model Rich ruled in N20 (ADR-0006): closure wins, the
  `closed:` names, partial items by the `squadra:committed` marker;
- the `board` contract suite over an in-memory GitHub.

It left `create_increment` and `items_with_origin` raising
`NotImplementedError`. SQ5b builds them and unblocks claude-skills DB5 (DB-D1
activity G). It also takes SQ5a's cut: the tick's half of N20 ruling 6. Read
only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5a–SQ5b and
   notes N14, N15, N20 (the ruling and the tests it gives SQ5b), N21–N23.
2. `docs/adr/adr-0006-github-native-state.md`; ADR-0005's four obligations
   (in `BoardAccess.create_increment`'s docstring, `src/squadra/board.py`).
3. `src/squadra/github_board.py`: the marker helpers, `_Issue.partial`,
   `_ISSUE_QUERY`, `set_state`, and the transport section.
4. `tests/helpers/github_stub.py` (`InMemoryGitHub`, `StubbedGitHubBoard`) and
   `tests/contract/conftest.py` (`seed_stubbed_github`, `SHAPES` /
   `BOARD_SHAPES`, the three-shaped `board` override in
   `test_increment_contract.py`).
5. For the fault-injecting pattern: `squadra.fake_board`'s one-shot fault (N17)
   and `tests/contract/test_crash_safe_create_contract.py`.

## This session's unit

Ledger item: SQ5b
Goal: GitHub's two primitives, and the tick's refused-requeue report.
- `create_increment(request, partial_item=None)` as k ordered `gh` writes:
  1. Create the issue with title and body, the Origin marker already in the
     body (obligation 1, N14).
  2. Link the sub-issue parent.
  3. Add each "blocked by" link.
  4. Add the issue to the project.
  5. Set a QUEUED Status.
  6. Add `squadra:committed`. This is the commit, last (obligation 4).
  Each write is idempotent under `partial_item`. Verify the write endpoints
  through `gh api` before relying on them (N21: sub-issue add, dependency
  add, whether the labels POST creates a missing label). Read Status back
  once after the marker (ADR-0006 decision 7: detection, not a guarantee).
- `items_with_origin()`: every Origin-bearing issue in the repository, on the
  project or not, in every bucket. Partial ones come back with `lifecycle`
  `None`. Decide how to enumerate (repository issues by REST with the marker
  in the body, or search) and record it.
- `set_state(WITHDRAWN)` already closes as not planned. Check that
  `withdraw_increment` works end to end through `IncrementBoard`.
- Tests:
  - Move the increment and crash-safe suites to four shapes (drop the
    three-shaped `board` override, add a `gh` shape to `fake_board` with
    `seed_origin` / `seed_partial`).
  - Add a stub fault after each write j < k.
  - Add Juval's interloper test: after each j < k an outsider adds the item
    to the project and sets every QUEUED-mapped Status;
    `items_in_state(QUEUED)` leaves it out, `items_with_origin` returns it
    with `lifecycle` `None`, and `item_state` raises.
  - Add Eric's cleared-Status test: a committed ACTIVE item with its Status
    cleared, or taken off the project, raises and is not requeued.
  - Mind N23's `\r\n` note when checking a partial item agrees with its
    request.
- The tick (SQ5a's cut, N20 ruling 6): a requeue (`_reap`, the launch
  rollback in `supervisor.py`) that finds the issue closed reports it for
  that item and the tick goes on, never ending on it. `set_state` raises
  `BoardValidationError` there today. Add the contract case: an ACTIVE item
  closed between ticks with each reason is neither reopened nor ends the next
  tick, and is reported.
- Run the adapter's writes once on Rich's sandbox (`rinman24/squadra-sandbox`,
  user project #1; ask him first, since it mutates the board): a queue, a
  claim's Status write, a label, a comment, a withdraw. Record the result in
  the session log.
- README: GitHub's `squadra board` row.

Estimated work: ~80–95K (budget under 100K, hard stop 120K). Cut line: the
tick change and the live run to SQ5c's session, recorded in the ledger.

## Out of scope for this session

- `[[boards]]`, `in_claim_scope`, the `seams.ado` rename, N10 (SQ5c).
- Changing the verb contract, the CLI surface or the fake's file format.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5b status, findings, session log, next
handoff `handoffs/SQ5c-boards-scope.md`), run the four validation commands
from `CLAUDE.md`, commit, push, open the PR. Tell Rich the PR URL and the next
handoff path, and tell claude-skills that DB5 is unblocked.
