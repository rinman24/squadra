# Handoff: SQ5g2 · one fleet per repository on a host (board-writes, part 5g2)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: SQ5g's PR merged on squadra `main`, and no other board-writes unit in
flight. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`; if it can't
fast-forward, stop and tell Rich.

## Context

Rich ruled N34 (A′): one fleet per repository. SQ5g built the fleet name
and every host-global name derived from it (N35). One rule is left from the
ruling: a second fleet on one host must not serve a repository an existing
fleet already serves. On GitHub issue numbers are per repository, so two
fleets on one repository would be two fleets working one issue. A per-fleet
branch would hide that collision rather than prevent it, and
`completed_pr_url` would finalize one fleet's item on the other's PR. So the
configuration is refused; the branch template stays as it is. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5g and SQ5g2
   and notes N34 (Rich's ruling, item 2) and N35.
2. board-knowledge `sessions/2026-10-28-juval-boards-instance-shape.md`
   ("Branch template: enforce the precondition, not the template").
3. `src/squadra/units.py` (`install_units`, `rendered_fleet_home`,
   `UnitConflictError`), `src/squadra/cli.py` (`_cmd_install_units`,
   `_check_install_name`), `src/squadra/repo.py` (`target_remote_url`),
   `src/squadra/github_board.py` (`github_repository_of`).

## This session's unit

Ledger item: SQ5g2
Goal: `install-units` refuses a second fleet on the host whose repository is
an existing fleet's, and the fleet-host acceptance follows the derived unit
names.

1. **One fleet per repository on a host.** Before writing, `install-units`
   reads every `squadra-*.service` in the unit dir that belongs to another
   fleet. For each one, it resolves the repository from that unit's
   `FLEET_HOME` (its `origin`, falling back to its `FLEET_APP_REPO_URL`) and
   compares it with the new fleet's, refusing on a match. Decide, and record,
   how repositories are compared across providers. The GitHub path already
   has `github_repository_of`; ADO remotes need an equivalent, or a
   normalised URL compare. Fail closed when the new fleet's repository can't
   be resolved, and say what the operator should do (on a fresh host, pass
   the repository or clone first).
2. **Close N35's residue.** On a fresh host `--fleet-name` can disagree
   with the `squadra.toml` that is cloned later. Choose one: either the
   rendered service carries the name it was installed for and the tick
   refuses a different `[fleet] name`, as a check that never sets the name,
   or record why it isn't worth it.
3. **Fleet-host acceptance.** `docs/fleet-host/goss.yaml` and `SMOKE.md`
   still expect `squadra.service` / `squadra.timer`. Template them on the
   name (goss `--vars`, from `FLEET_NAME` in `fleet-host.env`), and write the
   upgrade steps for an existing host: add `FLEET_NAME`, remove the old
   units, and set `tag_prefix = "fleet:"` (or have no increment dirs) before
   the first tick.

Tests: two fleets on one repository in a temp unit dir, the second refused
with nothing written; two fleets on different repositories both install; an
unresolvable repository refuses. Mutation-check the refusal.

Claim scope stays adapter-owned and fail-closed; nothing here may broaden it.

Estimated work: ~60K (hard stop 120K). Cut line if long: item 3 moves to its
own unit, with a ledger note.

## Out of scope for this session

- `[[boards]]`, `--board`, `{fleet}` in the branch template, any
  `verb-contract.md` change.
- SQ7 (host-side git credentials, `pat_ok`, `_ado_pat_*`) and SQ8's later
  step (N31).
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5g2 status, findings, session log,
the next handoff), run the four validation commands from `CLAUDE.md`, commit,
push, open the PR. Tell Rich the PR URL.
