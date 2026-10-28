# Handoff: SQ5g · `[[boards]]` (board-writes, part 5g)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: SQ5f and SQ5f2 merged on squadra `main` (SQ5f2 records Juval's and
Eric's answers on N34), Rich's ruling on N34's question recorded in
`docs/board-writes/LEDGER.md` (N34), and no other board-writes unit in
flight. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`; if it can't
fast-forward, stop and tell Rich. If N34 has no ruling yet, stop and ask
him for it.

## Context

SQ5f built WSQ1's adapter-owned scope (`BoardAccess.in_claim_scope`, scoped
from `[board]` at construction) and renamed `seams.ado` to `seams.board`. It
cut `[[boards]]` here, because its shape needs Rich's ruling: every local
artifact of the tick is keyed by the bare item id, and the `board` verbs
would need a selector, which is a verb-contract change. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5f, SQ5g, SQ7
   and notes N30, N32, N34.
2. WSQ1 in board-knowledge `sessions/2026-10-08-juval-mandatory-claim-scope.md`
   (Choice items 3–4, "How several boards fit") and
   `sessions/2026-10-09-juval-parent-and-lookup-scope.md` (A1's last
   paragraph: "the contract is per board").

## This session's unit

Ledger item: SQ5g
Goal: settle `[[boards]]` as Rich ruled on N34.

- **If Rich ruled (A), one instance per board:** no code. Record in the
  ledger that WSQ1 Choice 3 is answered by (A): a second board is a second
  `FLEET_HOME` with its own `squadra.toml`, whose required `claim_scope` is
  the check that fires wherever a board is added. Add a short README
  paragraph under Scoping saying so. Mark SQ5g done.
- **If Rich ruled (B), `[[boards]]` in one instance:** split it before
  building. First unit: the config (`[[boards]]` entries with `name`, each
  validated as `[board]` is today, a single `[board]` still valid) and
  `--board NAME` on the five `board` verbs, written into `verb-contract.md`
  first. Second unit: the tick over several boards, with every local
  artifact (status, claimed-at, archive, sandbox project, worktree, branch)
  namespaced by board, and Rich's call on `cap`. `partial-items` stays per
  board (N32's id assumption holds only within one board): no verb may see
  another board's items.

Estimated work: (A) ~30K; (B) the first unit ~100K (hard stop 120K).

## Out of scope for this session

- SQ7 (host-side git credentials, `pat_ok`, `_ado_pat_*`).
- SQ8's later step (N31).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5g status, findings, session log, the
next handoff: `handoffs/SQ7-host-git-credentials.md`), run the four
validation commands from `CLAUDE.md`, commit, push, open the PR. Tell Rich
the PR URL.
