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
would need a selector, which is a verb-contract change. SQ5f2 consulted
Juval (blind) and Eric: Juval chose (A′), one fleet per repository, named,
with every host-global name derived from the name, built now, against N34's
ledger-only (A); Eric named it Fleet and `[fleet] name`. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5f, SQ5f2,
   SQ5g, SQ7 and notes N30, N32, N34 (with its SQ5f2 amendment and Rich's
   ruling).
2. board-knowledge `sessions/2026-10-28-juval-boards-instance-shape.md`
   ("What SQ5g builds under (A′)", "Branch template", the acceptance test)
   and `sessions/2026-10-28-eric-fleet-name-boards.md` (the decision, the
   rules, the glossary entry, "Loose usages"), with Rich's Choice in each.
3. WSQ1 in board-knowledge `sessions/2026-10-08-juval-mandatory-claim-scope.md`
   (Choice items 3–4) and `sessions/2026-10-09-juval-parent-and-lookup-scope.md`
   (A1's last paragraph: "the contract is per board").

## This session's unit

Ledger item: SQ5g
Goal: settle `[[boards]]` as Rich ruled on N34.

- **If Rich ruled (A′), Juval's amended (A):** build it, with the names
  Rich took from Eric. Record in the ledger that WSQ1 Choice 3's rule is
  answered and its direction reversed, and Choice 4 amended as Rich worded
  it. Then:
  1. A required fleet name (Eric: `[fleet] name`, a new top-level table; no
     default, no env or flag layer), scaffolded empty by `squadra init`, in
     a conservative charset checked against systemd unit and compose
     project naming.
  2. Every host-global name derived from it: units
     `squadra-<name>.service` / `.timer`, with `install-units` refusing to
     overwrite a unit that renders a different `FLEET_HOME`; compose
     project `squadra-<name>-increment-<id>`; `tag_prefix` defaulting to
     `<name>:` (an explicit `[board].tag_prefix` still overrides); and, if
     Rich took Eric's addition, `fleetctl.sh`'s tmux names (or delete the
     stale `fleet` runner line).
  3. The name stamped into `fleet_root`; the tick refuses when the stamp
     differs while increment dirs exist, and (Eric's addition, if taken)
     when there is no stamp, increment dirs exist and `tag_prefix` is not
     set explicitly.
  4. For `provider = "github"`, `validate_config()` fails closed unless
     `[board.github].repository` is `FLEET_HOME`'s `origin`
     (`repo.target_remote_url`). The `GitHubBoardConfig` docstring loses
     its `[[boards]]` line.
  5. README Scoping (one fleet per repository, the name rule, why there is
     no `[[boards]]`, ADO's unchecked repository; one example where the
     fleet name and tag prefix differ), and Eric's glossary entry, settled
     row and header.

  Juval's acceptance test: two fleets both carrying item 105, rendered to a
  temp unit dir and a fake docker runner. Cut line if long: item 4 moves to
  SQ5g2. SQ5g2 (its own session): `install-units` refuses a second fleet on
  the host whose board repository matches an existing one's, unless Rich
  ruled `{fleet}` in the branch template instead.
- **If Rich ruled (A) as N34 states it, one fleet per board, docs only:** no
  code. Record in the ledger that WSQ1 Choice 3 is answered by (A): a second
  board is a second `FLEET_HOME` with its own `squadra.toml`, whose required
  `claim_scope` is the check that fires wherever a board is added. Add a
  short README paragraph under Scoping saying so, naming the host
  collisions SQ5f2 found as known and unbuilt. Mark SQ5g done.
- **If Rich ruled (B), `[[boards]]` in one fleet:** (A′) items 1–4 come
  first (Juval: boards over different repositories are still separate
  fleets). Then split before building. First unit: the config
  (`[[boards]]` entries with `name`, each validated as `[board]` is today,
  a single `[board]` still valid), `--board NAME` on the five `board` verbs
  (Juval: required even with one entry) and `squadra board list`, written
  into `verb-contract.md` first. Second unit: the tick over several boards, with every local
  artifact (status, claimed-at, archive, sandbox project, worktree, branch)
  namespaced by board, and Rich's call on `cap`. `partial-items` stays per
  board (N32's id assumption holds only within one board): no verb may see
  another board's items.

Estimated work: (A′) ~100K (hard stop 120K); (A) ~30K; (B) (A′) first,
then ~100K per unit.

## Out of scope for this session

- SQ7 (host-side git credentials, `pat_ok`, `_ado_pat_*`).
- SQ8's later step (N31).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5g status, findings, session log, the
next handoff: under (A′) a new `handoffs/SQ5g2-one-fleet-per-repository.md`
with a ledger row, else `handoffs/SQ7-host-git-credentials.md`), run the four
validation commands from `CLAUDE.md`, commit, push, open the PR. Tell Rich
the PR URL.
