# Handoff: SQ2c · build Rich's N7 and N8 rulings (board-writes, part 2c)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ2b's ruling PR (the one adding this file) has merged.**
If it is still open, stop and tell Rich.

## Context

SQ2b consulted Juval on ledger notes N6–N8 and settled N6. Rich ruled the
other two on 2026-10-09, both as Juval recommended and both reopening
DB-D1/DB-D4:

- **N7**: `withdraw_increment` takes an **Origin** instead of an item id
  (CLI `--origin`).
- **N8**: Juval's crash-safe create. The adapter keeps "no tick claims it
  before its links exist" through four call-order obligations. A
  `queue_increment` retry finishes an item left partial by a crash mid-create
  when the item agrees with the request, and refuses only on real
  disagreement.

The design, the semantics and the tests are in Juval's answer: board-knowledge
`sessions/2026-10-09-juval-increment-verb-settlement.md` (sections "N7", "N8"
and "The tests that discriminate"). Build to it. Don't reopen it.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md`, ADR-0004, ADR-0005 (Consequences hold
   the ruling).
2. `docs/board-writes/LEDGER.md` (notes N6–N10) and
   `docs/board-writes/verb-contract.md`.
3. The session file above, in full.
4. `src/squadra/increments.py`, `src/squadra/domain.py` (`OriginRecord`,
   `IncrementRequest`), the increment rules in `src/squadra/engines.py`,
   `BoardAccess` and the ADO stubs in `src/squadra/board.py`, `ReadOnlyBoard`
   in `src/squadra/supervisor.py`, `tests/helpers/board_fakes.py` and
   `tests/contract/test_increment_contract.py`.

First step: create the worktree and branch `feat/board-writes` afresh from
`main`, and set SQ2c to in progress in the ledger.

## Step 1: Eric names two terms

Juval left two names to Eric: the refusal for a withdraw by an Origin that is
not on the board, and the `OriginRecord` field that marks an item whose create
has not finished. `/ask-eric` is user-invoked only, so follow
`~/.claude/skills/ask-eric/SKILL.md` steps 2–4 by hand: spawn the
`board-eric` agent with the question, quote `GLOSSARY.md`, ADR-0005 and Juval's
N7 and N8 sections unedited, relay the answer in full, and write the session
file in `~/Code/board-knowledge/sessions/` (do not commit it). Use Eric's
names. If he names none, or proposes a change beyond naming, stop and ask Rich.
A glossary row follows if Eric says the term belongs to the language.

## This session's unit

Ledger items: SQ2c
Goal: Rich's N7 and N8 rulings, in the contract, the engines and both test fakes.

N7: `withdraw_increment(origin)`, per Juval's steps 1–5:
- `index_by_origin(items_with_origin())`, so a duplicate raises
  `DuplicateOriginError`.
- If the Origin is absent, raise Eric's refusal error, a rule refusal
  (CLI exit 3), and write nothing. An Origin carried only by an incomplete
  item counts as absent: `increments_by_origin` doesn't show it either.
  Record that as a ledger note.
- Check scope against the record's parent (keep N6(b)'s message).
- Re-read `item_state` just before `set_state`. WITHDRAWN is a no-op;
  ACTIVE or DONE is refused.
- Tests 6–8, and rewrite every existing withdraw test to go by Origin.

N8, the contract half (the fault-injecting fake is SQ3's):
- `OriginRecord` gains the completeness field. Decide whether it is a flag or
  a `lifecycle: Lifecycle | None` (None for an item in no bucket). Record the
  choice as a ledger note.
- `create_increment(request, incomplete_item: int | None = None)`. Its
  docstring states the four obligations and the finishing semantics: add
  what's missing, each write idempotent, commit write last, return the same
  id. The `items_with_origin` docstring says incomplete items are included and
  marked. `item_state` on an incomplete item raises.
- A pure rule in `engines` for finishing an item: every field already on the
  board (title, body, parent when set) equals the request, and the
  predecessors present are a subset of the requested set. Anything else raises
  `QueueRefusedError`, naming the item and what disagrees.
- `queue_increment`: an existing incomplete record goes through that rule and
  then `create_increment(request, incomplete_item=id)`; a complete record keeps
  A3 as now.
- `increments_by_origin` leaves incomplete items out. `index_by_origin` still
  runs on the raw records, so duplicate detection includes them.
- Both fakes: honour the parameter, and add a `seed_incomplete(...)` helper so
  the contract suite can place a partial item by hand. The ADO stub and
  `ReadOnlyBoard` take the new signature.
- Contract tests against both fakes: Juval's tests 2–5 with a seeded partial
  item (test 1, the tick, waits for SQ3's fake), plus a retry that finishes a
  partial item with some predecessors already present.

Docs:
- ADR-0005 decisions 1 and 2 amended (status line, and the Consequences entry
  "Ruled by Rich" becomes past tense).
- `verb-contract.md`: the table, `squadra board withdraw --origin ORIGIN`, and
  `origins` writing one stderr line per incomplete item, naming its item id and
  Origin (SQ4 builds that).
- The ledger records what SQ5 must prove on GitHub: obligations 1–4, or the
  adapter's own completion marker as the fallback (Juval, "Where the work
  lands").

Estimated work: ~60–80K tokens (budget: under 100K total, hard stop at 120K)

## Out of scope for this session

- The registered fake provider and its fault injection (SQ3), CLI subcommands
  (SQ4), the GitHub adapter (SQ5).
- N10, the withdraw read-then-write race, stays open.
- Any write in claude-skills. DB-D1 and DB-D4 there still name
  `withdraw_increment(item_id)` and A3 without the finishing rule; their
  reopening is recorded by the design-to-board effort, not here. Tell Rich it is
  still owed.
- Any board-knowledge write other than Eric's session file.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (status, notes, session log), and point
the next handoff at the existing `handoffs/SQ3-fake-provider.md` (its gate
already reads "SQ2c's PR has merged"). Run the four validation commands in
`CLAUDE.md`. Commit, push and open the PR. Tell Rich the PR URL, Eric's
session file path, and that the claude-skills DB-D1/DB-D4 entries are still
owed.
