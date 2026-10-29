# Handoff: SQ7d · origin-writability test, then one "supported remote" check (board-writes, part 7d)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: this handoff's PR merged on squadra `main`, and no other board-writes
unit in flight. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`. If it can't fast-forward,
stop and tell Rich.

## Context

SQ7 (PR #69) put an SSH host gate inside `remote_auth.credential_for`. Juval
(SQ7b) found that for SSH the gate isn't choosing a credential: it decides
which remotes squadra talks to. It also disagrees with N36, because
`install-units` accepts any two-segment remote (`git@gitlab.com:o/r`, an
ssh-config alias) and then every tick refuses at the probe. Which kind of
control the gate is depends on one fact: can the agent change the remote
that host-side git uses? If it can, the gate is an egress control. If it
can't, the gate only records what squadra supports. Rich ruled (N37, 3b):
run the test first, then build S3, scoped by the result, before the push is
wired. S2 (let any SSH host pass) is not taken (N37, 3a). SQ7e's merge is
the trigger, since nothing in squadra tracks the deferred push (G2).

Read only these:

1. squadra `CLAUDE.md` (hard invariant: no silent broadening on the claim
   path); `docs/board-writes/LEDGER.md` rows SQ7d and SQ7c, and notes N36
   (its first two bullets), N37 (the "Point 3" SQ7b bullet and Rich's
   rulings) and N40.
2. Juval's session, sections Choice and "3. The two narrowings: keep both,
   rule them, test before push". It is on board-knowledge branch
   `docs/sq7-credential-sessions` (not merged at writing):
   `git -C ~/Code/board-knowledge fetch`, then
   `git -C ~/Code/board-knowledge show origin/docs/sq7-credential-sessions:sessions/2026-10-28-juval-sq7-credential-open-points.md`
   (or from `origin/main`, if it has merged by then). Don't merge or write it.
3. The code:
   - `src/squadra/remote_auth.py`: `credential_for` (the SSH host gate at
     ~line 146), `remote_host`, `remote_git_argv`.
   - `src/squadra/repo.py`: `target_remote_url`, `repository_identity` (drops
     the host on purpose), `fleet_repository`, `TargetRemote`.
   - `src/squadra/units.py` `_check_repository` and `src/squadra/cli.py`
     ~line 402 (the `install-units` side of N36).
   - `src/squadra/worktree.py` `GitWorktreeAccess.create`;
     `src/squadra/supervisor.py` `_worktree_for`, the `SandboxSpec` built at
     ~line 642, `_read_commits_present`, and the `_handoff` write-tail.
   - `src/squadra/git_host.py` (the #193 threat model and the push contract).
   - The one consumer compose file: `~/Code/gswa-backend/.squadra/compose.yaml`
     (app-backend). Read it; never write it.

## Facts found while writing this handoff (verify them, don't trust them)

1. **app-backend's agent mounts only the worktree.** `volumes:
   ${FLEET_WORKTREE:-..}:/work`, and nothing in `src/` sets `FLEET_WORKTREE`.
   The compose file is the worktree's own `.squadra/compose.yaml`, so `..`
   is the worktree. `FLEET_HOME/.git` is not mounted, so the agent can't
   reach `FLEET_HOME/.git/config` through that file.
2. **The worktree is a linked worktree** (`git worktree add` from
   `FLEET_HOME`, at `FLEET_HOME/<worktree_dir>/<branch with / → +>`). Its
   `.git` is a file, `gitdir: <host path>/.git/worktrees/<name>`, and that
   path isn't in the container. It's not clear how in-container git works in
   `/work` at all, and nothing in docs or code records it. Find out
   (ADR-0002 §2 says the agent commits there).
3. **The worktree's `.git` is inside `/work`, which is read-write.** Whatever
   the mount, an agent can replace the `.git` file with a directory (a repo
   of its own), or point `gitdir:` somewhere it controls. Then any host-side
   git op run with `work_dir=<worktree>` reads the agent's config. That
   includes the push the README and `git_host.py` now specify:
   `remote_git_argv(credential, "push", "origin", branch,
   work_dir=worktree)`. There `origin` resolves in the worktree's git dir,
   not `FLEET_HOME`'s, so `remote.origin.url`, `url.<base>.insteadOf` and
   `core.sshCommand` would be the agent's. The credential, though, comes
   from `TargetRemote` (`FLEET_HOME`'s origin). Juval's question names
   `FLEET_HOME`'s config. This is the same question one level down.
4. Docker isn't installed on Rich's Mac, so a contained run is fleet-host
   only (Rich's step).

## This session's unit

Ledger item: SQ7d
Goal: answer the writability question with evidence, then build S3 as
scoped by the answer, or stop if the answer changes the push contract.

### 1. The test (~1–2 h). Record it as a ledger note before building anything.

Answer each question with a test that runs, not by reading:

- **(a) `FLEET_HOME`'s config.** Can the agent change the `remote.origin.url`
  or `core.sshCommand` that `target_remote_url` reads? Settle it from the
  mount (fact 1) and from how git works in `/work` (fact 2). Use a hermetic
  test that builds a temp `FLEET_HOME` and linked worktree, and acts only
  inside the worktree directory, as the agent's bind mount allows.
- **(b) The worktree's git dir.** With the worktree's `.git` replaced or
  redirected as in fact 3, what do these read: the push argv above (against
  local bare repos only), and the host-side ops that already run with
  `work_dir=<worktree>`? Find those by grepping for `work_dir=` and `-C`
  on worktree paths; `_read_commits_present` is one. Check whether the push
  lands in an agent-chosen bare repo, whether `url.<base>.insteadOf`
  rewrites it, and whether `core.sshCommand` would run. Use a marker
  script and a local `ssh://` URL that fails. No network.
- **(c) The fleet-host contained run** (Juval's version) is Rich's step,
  and only if (a) can't be settled without it. If it's needed, write the
  exact commands for Rich and stop there.

Outcomes:

- **(b) shows the agent can choose the push destination or run a command
  through the worktree's git dir:** stop before building. Record it, and
  put it to Juval (`/ask-juval`, give him facts and options only), because
  it changes the push contract, which isn't SQ7d's to decide. One option to
  include: push from `FLEET_HOME` (linked worktrees share refs) to the URL
  `TargetRemote` resolved, never to a remote name read in the worktree.
  Rich rules; S3 waits.
- **(a) origin writable, (b) clean:** build S3 with the declared-remote
  check.
- **(a) origin unwritable, (b) clean:** build S3 without it.

Fact 2 might show that git can't work in `/work` under app-backend's
compose (so the agent can't commit). If so, that's a separate defect: note
it for Rich, with no row and no fix here.

### 2. S3 (~1–2 d), only after the test allows it

- One check answers "is this a supported remote" (host and transport). It
  is shared by the probe (`TargetRemote.probe`) and `install-units`
  (`units._check_repository`), so they can't disagree any more (N36). After
  the split, `credential_for` answers only "which credential". Name the
  check in the session and record the name in the note.
- **Keep the supported set exactly as SQ7 built it and Rich ruled:**
  HTTPS to Azure DevOps and github.com, SSH to `github.com`,
  `ssh.dev.azure.com`, `vs-ssh.visualstudio.com`. Everything else fails
  closed, now at `install-units` too (that's a narrowing; say so in the
  README upgrade line SQ7e added). No broadening anywhere.
- **The declared-remote check** (only if (a) found origin writable): origin
  must match the operator-declared `FLEET_APP_REPO_URL`. Compare host and
  path. `repository_identity` drops the host on purpose (N36), so it is the
  wrong key for an egress control. If `FLEET_APP_REPO_URL` is unset (a dev
  checkout like Rich's Mac), the check would fail closed. Put that choice
  to Rich before building it; don't pick a default.
- The probe's and `install-units`' messages name the host, never the URL
  (an HTTPS URL can carry a token).
- Tests: the shared check's table (both transports, each supported host,
  aliases, GHE, GitLab, `http`, local path, `file://`); the probe and
  `install-units` refuse the same remotes; `credential_for` unchanged for
  supported remotes (ADO helper byte for byte); the declared-remote check,
  if built, refuses a different host with the same `owner/name`.

Estimated work: ~100K (hard stop 140K). Cut line: the test and its ledger
note never get cut. If (b) stops the build, the unit is the test, the note
and the Juval consult; S3 goes to an SQ7f after Rich rules. If S3 runs
long, the declared-remote check can go to its own row; the shared check
and its tests can't.

## Out of scope for this session

- Wiring the push (G2), or any change to the push contract beyond recording
  (b) and consulting.
- SQ7c (the fleet-host secret set), the `agent_environ` gap (N38).
- Any write to app-backend (`gswa-backend`), claude-skills or
  board-knowledge.
- Any tick, live or dry; any network git op; any container run on Rich's
  Mac (there's no Docker anyway). The fleet-host run in 1(c) is Rich's.

## Wrap-up

Run the four validation commands in `CLAUDE.md`. Update
`docs/board-writes/LEDGER.md` (SQ7d status, the test's note, the session
log, the next handoff if Rich asks for one), commit, push, open the PR. Tell
Rich the PR URL, what the test found, and whether S3 was built.
