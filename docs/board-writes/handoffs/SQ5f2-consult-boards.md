# Handoff: SQ5f2 · consult Juval and Eric on N34, `[[boards]]` (board-writes, part 5f2)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: SQ5f (PR #64) merged on squadra `main`, and no other board-writes unit
in flight. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`; if it can't
fast-forward, stop and tell Rich.

## Context

SQ5f built the adapter-owned claim scope (`BoardAccess.in_claim_scope`,
scoped from `[board]` at construction) and cut `[[boards]]` to SQ5g, because
its shape is Rich's to rule (ledger N34). Before he rules, he wants Juval's
read on the structure and Eric's on the names. This session runs those
consults and records them. It builds nothing. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5f, SQ5f2,
   SQ5g, SQ7 and notes N30, N32, N34.
2. board-knowledge `sessions/2026-10-08-juval-mandatory-claim-scope.md`
   (Choice items 3–4, "How several boards fit", "When to revisit (b)") and
   `sessions/2026-10-09-juval-parent-and-lookup-scope.md` (A1's last
   paragraph: "the contract is per board").
3. squadra `GLOSSARY.md` (Increment, Origin, Partial item) and
   `GLOSSARY-SETTLED.md`.
4. To check N34's facts yourself, not to re-derive them:
   `src/squadra/status.py` (`fleet_root / str(issue_id)`);
   `src/squadra/supervisor.py` (claimed-at marker, archive root, sandbox
   project `squadra-increment-{id}`); `src/squadra/config.py`
   (`DEFAULT_BRANCH_TEMPLATE`, `[board]`, `ClaimScope`);
   `docs/board-writes/verb-contract.md` (the five `board` verbs, the CLI
   surface, N32's per-board id assumption).

## This session's unit

Ledger item: SQ5f2
Goal: Juval's answer, and Eric's if names move, on N34, recorded for Rich's
ruling. No code.

### 1. `/ask-juval`, blind

Put the question to him **without** N34's recommendation, so his read is not
anchored on SQ5f's. Give him the two facts, the constraints, and both
options as N34 states them, then ask him to choose, amend, or propose
another. The facts:
- Every local artifact of the tick is keyed by the bare item id: status,
  claimed-at marker and archive under `fleet_root/<id>/`, the sandbox project
  `squadra-increment-<id>`, and branches from `feat/increment-{id}-{slug}`.
  Two boards' ids can be equal (ADO item 105, GitHub issue 105).
- design-to-board never reads `squadra.toml` (claude-skills DB-D5), so it
  can't discover board names. Any selector comes from its own caller.

The constraints already ruled:
- WSQ1 Choice 3: `[[boards]]` with `claim_scope` per entry is "the
  direction", and the scope check fires wherever a board is added.
- WSQ1 Choice 4: fleets split by board; name each fleet and derive its
  `tag_prefix` from that name before a second fleet VM exists.
- The contract is per board (2026-10-09 A1). `partial-items`' Origin-to-id
  map assumes a board never reuses an item id (N32). No verb may see
  another board's items unless that assumption holds across them.
- Claim scope stays adapter-owned and fail-closed (SQ5f). It must not be
  silently broadened.

Points to ask him about directly:
- (A) one instance per board vs (B) `[[boards]]` in one instance. Does (A)
  answer Choice 3, or does it reverse a ruling Rich should know he is
  reversing?
- Under (A): two fleets delivering into one repository could push the same
  branch name for equal ids and slugs. Is a per-fleet branch template
  enough, or must it be enforced? Does Choice 4's fleet naming have to be
  built before a second fleet exists, and is that SQ5g's job?
- Under (B): the verbs' selector (`--board NAME` or something else), where
  design-to-board gets it, whether `cap` is shared or per board, and how to
  split the build.
- His estimate and risk for each, in his usual priced-options form.

### 2. `/ask-eric`, if the answer moves vocabulary

It very likely does, so ask unless Juval's answer clearly changes no term.
Give him Juval's answer and the glossary entries, and ask:
- Under (B): an Increment's identity is "the board item" and an Origin is
  "unique on the board". With several boards, what are the identity and the
  Origin's uniqueness, and what is the thing a selector names (board,
  board name, something else)?
- Under (A): "fleet", "instance", `FLEET_HOME` and "board" are used loosely
  across the ledger, README and Juval's sessions. Does "Fleet" need a
  glossary entry (one `squadra.toml`, one board, one `tag_prefix`, one
  `fleet_root`), and what should be avoided?

### 3. Record

- Both session files stay in board-knowledge as the skills write them, with
  their Choice sections left blank for Rich.
- Amend N34 in `docs/board-writes/LEDGER.md` with a short summary of each
  answer, the session file paths, and the questions Rich must rule on, in
  the order he should take them. Where Juval disagrees with N34's
  recommendation, say so plainly.
- If the answer changes what SQ5g builds, update `handoffs/SQ5g-boards.md`
  to match. Otherwise leave it as it is.

Estimated work: ~40K (hard stop 60K). No cut line: if a consult can't finish,
record what was asked and answered so far and stop.

## Out of scope for this session

- Any code, and any change to `verb-contract.md` or `GLOSSARY.md`. Eric's
  names go in once Rich rules, in SQ5g.
- Ruling on Rich's behalf, even if both advisers agree.
- SQ7 (host-side git credentials) and SQ8's later step (N31).
- Any write in claude-skills. In board-knowledge, only the session files the
  skills write.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5f2 status, N34, session log, next
handoff: `handoffs/SQ5g-boards.md`, gated on Rich's ruling), commit, push,
open the PR. Tell Rich the PR URL and the session files whose Choice he
needs to fill in.
