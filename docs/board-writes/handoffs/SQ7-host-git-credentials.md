# Handoff: SQ7 · one credential boundary for host-side git (board-writes, part 7)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until SQ5f's PR, and the SQ5g and SQ5g2 it led to, have merged
on squadra `main`.** SQ7 doesn't depend on SQ5f's design. The gate only keeps the two
in series, because both edit `supervisor.py`'s `TickSeams` and its builders on
the shared `feat/board-writes` branch. If it hasn't merged, stop and tell Rich.
Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`.

## Context

claude-skills DB7 finding (iii): a GitHub `tick --dry-run` logs "WOULD run the
ADO PAT auth preflight". Juval: the log line is accurate; the defect is under
it. Rich chose Juval's option D (ledger N29). Read only these:

1. squadra `CLAUDE.md` (hard invariant: no silent permission or behaviour
   broadening on the claim path); `docs/board-writes/LEDGER.md` row SQ7 and
   note N29.
2. Juval's session, sections Choice and "(iii): new row, and the label isn't
   the bug". It is on an unmerged board-knowledge branch, so read it with
   `git -C ~/Code/board-knowledge fetch` then
   `git -C ~/Code/board-knowledge show origin/docs/juval-squadra-init-preflight:sessions/2026-10-27-juval-squadra-init-preflight-findings.md`.
   Don't merge or write that branch.
3. The code, today's host-side git remote ops and their auth:
   - `src/squadra/repo.py`: `_CREDENTIAL_HELPER` (resets inherited helpers,
     then supplies `$AZURE_DEVOPS_EXT_PAT`), the bootstrap clone and fetch
     (both through it), `target_remote_url`, `remote_auth_ok` (the
     `ls-remote` probe, through it). `repository_identity` and
     `fleet_repository` (SQ5g2, N36) name a repository and drop its host on
     purpose; credentials resolve by host, so don't route them by that key.
   - `src/squadra/worktree.py` `GitWorktreeAccess.create`: the claim's own
     `fetch origin` (about line 107), with **no** credential helper (ambient
     credentials). This is why the probe's "exact auth path" claim is false.
   - `src/squadra/git_host.py` `host_git_argv` and `with_hooks_guard` (the
     hook hardening every host-side op must keep). Push is not wired today;
     the module docstring anticipates it.
   - `src/squadra/supervisor.py`: `_ado_pat_ok`, `_ado_pat_rejected_message`
     (tells the operator to rotate an ADO Key Vault secret), the claim-path
     gate in the tick, `_dry_run_pat_ok`, `TickSeams.pat_ok` and
     `build_seams`.
   - `src/squadra/secrets.py` (`ADO_PAT_ENV`, the Key Vault secret names),
     `src/squadra/scaffold.py` line ~83 comment, README "Supervisor" section.
   - Tests: `tests/test_repo.py`, `tests/test_supervisor_pat_preflight.py`,
     `tests/test_supervisor_dry_run.py`, `tests/conftest.py`.

## This session's unit

Ledger item: SQ7
Goal: Juval's option D, as Rich chose.

- Put credential acquisition for host-side git remote ops behind one
  boundary ("authenticate to this remote"), resolved from the target
  remote's host (`target_remote_url`), **not** from `[board].provider`. An
  ADO board over a GitHub repo is a valid combination.
- The probe and every host-side clone and fetch go through it, the claim's
  worktree fetch included, so the probe really is the claim's auth path.
  Leave a clear seam for push when it is wired.
- Fail closed. An unknown remote host, or no credential for it, means the
  probe fails and the tick runs only the non-claim decisions, as a dead PAT
  does today. For an SSH remote, credential helpers don't apply; say how the
  boundary treats SSH (the probe passing on ssh-agent is today's behaviour,
  N29) and test it.
- The rejection message names the credential actually used and how to fix
  that one (the ADO Key Vault rotation text only for an ADO remote).
- Rename the `_ado_pat_*` probe symbols, `TickSeams.pat_ok` and the dry-run
  line to match. Sweep ADO assumptions outside `board.py` (the `scaffold.py`
  comment on the provider PAT, the README Supervisor section, docstrings).
  Keep ADO's own behaviour byte-for-byte where the remote is ADO.
- Name the GitHub HTTPS credential source explicitly; don't invent one. If
  the codebase or `gh` already supplies one (e.g. `gh auth git-credential`),
  propose it. If it means a new secret or env name, put the choice to Rich
  before building and record it as a note.
- Tests: the probe and the worktree fetch get the same credential for each
  of ADO HTTPS, GitHub HTTPS and SSH remotes; an unknown host fails closed;
  the rejection message per host; the existing hook-hardening tests still
  pass; a GitHub board over an ADO remote, and the reverse, pick the
  credential by remote.

Not wanted (Juval, N29): relabelling the log line on its own; skipping the
probe when the provider isn't ADO (a silent broadening on the claim path);
folding this into SQ5f.

Estimated work: ~90K (hard stop 120K). Cut line: the README/docstring sweep
and the renames go last and can go to an SQ7b; the boundary, the worktree
fetch through it, fail-closed and the tests do not.

Never run a live tick or `squadra start`. A single read-only probe (one
`git ls-remote`) on Rich's machine is allowed only if Rich says yes in the
session, as in N29.

## Out of scope for this session

- SQ5f's work and SQ8 (partial items as data).
- Wiring push, or any change to `runner-wrap.sh`'s in-sandbox git.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ7 status, findings as a note,
session log, the next handoff if any), run the four validation commands from
`CLAUDE.md`, commit, push, open the PR. Tell Rich the PR URL.
