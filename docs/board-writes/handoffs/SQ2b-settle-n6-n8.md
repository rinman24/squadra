# Handoff: SQ2b · ask Juval about N6–N8, then settle them (board-writes, part 2b)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Rich has delegated this: the session consults Juval, settles N6, N7 and N8
itself under the rules below, and builds what the settlement changes. It stops
and asks Rich only where those rules say to.

## Context

SQ2 (PR #44, merged) froze the increment verb contract as
`squadra.increments.IncrementBoard` over two raw `BoardAccess` primitives
(ADR-0005). It left three ledger notes open:

- **N6**: two fail-closed narrowings beyond the rulings. `queue_increment`
  refuses an Origin that is on the board as WITHDRAWN instead of returning it;
  `withdraw_increment` refuses an item outside the claim scope.
- **N7**: `withdraw_increment` accepts any in-scope QUEUED item, including one
  queued by hand with no Origin.
- **N8**: `create_increment` is one business write, but GitHub needs several
  calls, so the guarantee "no tick claims it before its links exist" has to
  live somewhere. Found while writing this handoff: if `create_increment`
  crashes partway through, the item carries its Origin with missing links, and
  the retry is refused for good by A3 ("predecessors differ"). Crash-retry
  idempotency, A3's whole purpose, fails in exactly that case.

Read first, in order:
1. squadra `CLAUDE.md`, `GLOSSARY.md`, ADR-0004, ADR-0005.
2. `docs/board-writes/LEDGER.md` (notes N5–N9) and
   `docs/board-writes/verb-contract.md`.
3. `src/squadra/increments.py`, the increment rules in `src/squadra/engines.py`
   (`check_queue_matches`, `index_by_origin`, `parent_in_claim_scope`),
   `tests/contract/test_increment_contract.py`.
4. Read-only: claude-skills `docs/design-to-board/LEDGER.md` on
   `origin/feat/design-to-board` (DB-D1–DB-D5; local `main` lacks it, so use
   `git -C ~/Code/claude-skills show origin/feat/design-to-board:docs/design-to-board/LEDGER.md`),
   and board-knowledge `sessions/2026-10-09-juval-parent-and-lookup-scope.md`.

First step: create the worktree and branch `feat/board-writes` afresh from
`main`, and add SQ2b to the ledger as in progress.

## Step 1: ask Juval

`/ask-juval` is user-invoked only, so follow
`~/.claude/skills/ask-juval/SKILL.md` steps 2–4 by hand, exactly:
- **Consult:** spawn the Agent with `subagent_type: "board-juval"`. Pass
  the question below verbatim, then quote these files unedited: ledger notes
  N5–N9, `verb-contract.md`, ADR-0005, `increments.py`, and DB-D1–DB-D5 from
  the claude-skills ledger. End with `Mode override: method`. Add no analysis
  of your own.
- **Relay:** print the answer in full under `## Juval Löwy`.
- **Record:** write the session file the skill describes, in
  `~/Code/board-knowledge/sessions/`. Do not commit it.

The question:

> SQ2 froze squadra's increment verbs as `IncrementBoard` over two raw
> `BoardAccess` primitives (ADR-0005). Three choices made while building, beyond
> DB-D2–DB-D5, need settling before SQ3–SQ5.
>
> **N6.** Two fail-closed narrowings: (a) `queue_increment` refuses an Origin
> that is on the board as WITHDRAWN, where A3 would return the existing item;
> (b) `withdraw_increment` refuses an item outside the claim scope. Keep, drop
> or change each?
>
> **N7.** `withdraw_increment(item_id)` accepts any in-scope QUEUED item,
> including one queued by hand with no Origin. Options: (i) leave it; (ii)
> require the item to carry an Origin (one board-wide `items_with_origin` read
> per withdraw); (iii) withdraw by Origin instead of item id. Which, and does
> your answer change the frozen contract?
>
> **N8.** `create_increment` must be one business write, but GitHub takes
> several calls (create the issue, set the sub-issue parent, add dependencies,
> set the project status). Where should "no tick claims it before its links
> exist" live: in the adapter's call order (links before the QUEUED status), in
> a native state the tick never reads until the write completes, or in a
> claim-time check? And what should a retry do after a crash mid-create,
> which leaves an item with its Origin but missing links that A3 then
> refuses because the predecessors differ: complete the partial item, refuse
> loudly with a named recovery, or something else?

## Step 2: settle

Settle each note on Juval's named decision, except in these cases. If any
applies, stop that note and put it to Rich as one question with Juval's answer
attached:
- It changes a ruled DB-D decision (DB-D1–DB-D5), including the frozen verb
  signatures. Option N7 (iii) does.
- It broadens what a verb permits, such as dropping a refusal (CLAUDE.md: no
  silent broadening of board automation). Keeping or adding a refusal is
  settled here.
- Juval names a blocking question that only Rich can answer.
- Juval names no decision for that note.

Record each settlement:
- In the session file's `## Choice`, open with "Settled by squadra SQ2b on
  Rich's delegation, 2026-MM-DD:" and give one line per note. Leave a note that
  went to Rich blank there.
- In the ledger, mark N6–N8 settled, naming the session file.

## This session's unit

Ledger items: SQ2b
Goal: build what the settlement changes, inside SQ2's contract:
- Code and tests in `increments.py` / `engines.py` and the increment contract
  suite, on both fakes.
- ADR-0005 (decision 3 and Consequences) and `verb-contract.md` updated to match.
- If N8 moves the guarantee into the tick or the primitive's contract, change
  the `BoardAccess.create_increment` docstring and the contract tests. The
  GitHub implementation itself stays SQ5; record what it must do in the ledger.
- If a note changes nothing in code, the ledger and ADR record that.
Then a PR to squadra `main`.
Estimated work: ~40–60K tokens (budget: under 100K total, hard stop at 120K)

## Out of scope for this session

- The fake provider (SQ3), CLI subcommands (SQ4), the GitHub adapter (SQ5).
- Any write in claude-skills, and any board-knowledge write other than the
  session file above.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (status, notes, session log), and point
the next handoff at the existing `handoffs/SQ3-fake-provider.md`, changing its
gate to "SQ2b's PR has merged". Run the four validation commands in
`CLAUDE.md`. Commit, push and open the PR. Tell Rich the PR URL, the session
file path, and any note that went to him.
