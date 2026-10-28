# Handoff: SQ5g · one fleet per repository, `[fleet] name` (board-writes, part 5g)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: PR #66 (Rich's N34 ruling) merged on squadra `main`, and no
other board-writes unit in flight. Fetch first, then fast-forward the
`feat/board-writes` worktree (`.claude/worktrees/board-writes`) to
`origin/main`; if it can't fast-forward, stop and tell Rich.

## Context

SQ5f cut `[[boards]]` here because its shape needed Rich's ruling (N34).
SQ5f2 consulted Juval (blind) and Eric, and Rich ruled (A′) on 2026-10-28:
one fleet per repository, no `[[boards]]`. A fleet is one repository (the
GitHub board's repository and `FLEET_HOME`'s `origin` must agree), and two
fleets on one host collide today on fixed systemd unit names, on the compose
project `squadra-increment-<id>` (torn down with `down -v`) and on the
default `tag_prefix` `fleet:`. So the fleet gets a required name, and every
host-global name derives from it. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5g, SQ5g2,
   SQ7 and note N34 (its SQ5f2 amendment and Rich's ruling).
2. board-knowledge `sessions/2026-10-28-juval-boards-instance-shape.md`
   ("What SQ5g builds under (A′)", the acceptance test, Rich's Choice) and
   `sessions/2026-10-28-eric-fleet-name-boards.md` (the decision, "Rules
   that follow", the glossary entry, "Loose usages", Rich's Choice).

## This session's unit

Ledger item: SQ5g
Goal: build (A′) as Rich ruled it, with Eric's names and both additions.

First record in the ledger, if N34 doesn't already say so plainly, that WSQ1
Choice 3's rule stands and its direction is reversed, and that Choice 4 now
reads "fleets split by repository; each fleet has one board; each fleet is
named before the second fleet on any host". Then:

1. **`[fleet] name`**, in a new top-level `[fleet]` table: required, no
   default, no env or flag layer (the name is the identity; an override is
   a rename), scaffolded empty by `squadra init` as `claim_scope` is, in a
   conservative charset (lowercase letters, digits, hyphens) checked
   against systemd unit and compose project naming rules.
2. **Every host-global name derived from it:**
   - units `squadra-<name>.service` / `.timer`; `install-units` refuses to
     overwrite an existing unit file that renders a different `FLEET_HOME`;
   - compose project `squadra-<name>-increment-<id>`;
   - `tag_prefix` defaults to `<name>:`, and an explicit `[board].tag_prefix`
     still overrides it;
   - `fleetctl.sh`'s tmux ticker session (`fleet-ticker`, line 40) from the
     name; the stop message's `fleet` runner session (line 79) derived too
     if runners still use it, else the stale line deleted.
3. **The name stamped into `fleet_root`.** The tick refuses when the stamp
   differs from the configured name while increment dirs exist, and when
   there is no stamp, increment dirs exist and `tag_prefix` is not set
   explicitly (Eric: an upgrade from the default `fleet:` is a rename the
   stamp can't see).
4. **Board repository is `origin`.** For `provider = "github"`,
   `validate_config()` fails closed unless `[board.github].repository` is
   `FLEET_HOME`'s `origin` (`repo.target_remote_url`). ADO gets no check
   (being dropped); say so in README. The `GitHubBoardConfig` docstring
   loses its `[[boards]]` line.
5. **Docs.** README Scoping: one fleet per repository, the name rule, why
   there is no `[[boards]]`, with one example where the fleet name and tag
   prefix differ. `GLOSSARY.md`: Eric's Fleet entry and new header;
   `GLOSSARY-SETTLED.md`: his Fleet row. Use "fleet", never "instance".

Juval's acceptance test: render two fleets into a temp unit dir and a fake
docker runner, both carrying item 105, and assert distinct unit files and
refusal on the overwrite; distinct compose projects, with teardown of A's 105
never naming B's project; a missing name fails config load; a board
repository that differs from `origin` fails `validate_config()`; a renamed
fleet with in-flight increment dirs refuses to tick. Add the unstamped case.

Claim scope stays adapter-owned and fail-closed; nothing here may broaden it.

Estimated work: ~100K (hard stop 120K). Cut line if long: item 4 moves to
SQ5g2, with a ledger note.

## Out of scope for this session

- SQ5g2 (`install-units` refusing a second fleet on one repository): write
  its handoff, don't build it.
- `[[boards]]`, `--board`, any `verb-contract.md` change.
- SQ7 (host-side git credentials, `pat_ok`, `_ado_pat_*`) and SQ8's later
  step (N31).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5g status, findings, session log, the
next handoff: a new `handoffs/SQ5g2-one-fleet-per-repository.md`), run the
four validation commands from `CLAUDE.md`, commit, push, open the PR. Tell
Rich the PR URL.
