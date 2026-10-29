# Handoff: SQ7g · the Attempt's workspace, the commit import and `ResolvedRemote` (board-writes, part 7g, split SQ7g1–SQ7g4)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file,
once per unit. Each session builds the first of SQ7g1, SQ7g2, SQ7g3 whose
ledger row isn't done. SQ7g4 floats and isn't started from this file without
Rich asking.
Gate: the previous unit's PR (for SQ7g1, this handoff's PR) merged on squadra
`main`, and no other board-writes unit in flight. Fetch first, then
fast-forward the `feat/board-writes` worktree (`.claude/worktrees/board-writes`)
to `origin/main`. If it can't fast-forward, stop and tell Rich.

## Context

SQ7d's test (N41) found two defects with one cause. First, the increment's
linked worktree shares `FLEET_HOME`'s config, refs and objects. Under
app-backend's mount the agent can't commit at all, because git in `/work`
can't find its git dir. Second, the push as specified reads the worktree's git
dir, so the agent picks the push destination, runs `core.sshCommand` on the
host and gets the ADO PAT sent to its host. Rich ruled N41 (Juval's proposal,
options 3 + 2). The rules this file builds:

- Each Attempt gets a standalone repository, a **Workspace**: a host-side clone
  of `FLEET_HOME` at the base commit, with no remotes, and that base recorded
  on the host.
- The agent's commits reach the host by one import into a quarantine ref in
  `FLEET_HOME`. They are verified there by a pure Engine and pushed from
  there. No host-side git op runs in the workspace once the agent has it.
- The push's URL and credential come from one value. The push stays unwired
  (G2 is out of scope); SQ7g writes its contract and pins it with tests.

Juval sized it at ~3.5 d (3–4.5). That is three sessions under the ~100K
budget, so it is split as SQ5 was. Juval's cut line holds: his activities A–E
(contract docs and ADR addendum, the workspace, the import and verification,
the pair and typed `work_dir`, the acceptance tests) are all in SQ7g1–SQ7g3
and can't be cut. The ADO host binding (Rich's call, not ruled) and the
dry-run approximation's polish moved to SQ7g4, which floats.

Read only these, in every unit:

1. squadra `CLAUDE.md` (hard invariant: no silent broadening on the claim
   path); `docs/board-writes/LEDGER.md` rows SQ7g–SQ7g4, SQ7f and SQ7h, notes
   N41 (with Rich's rulings) and N42, and the previous SQ7g unit's session log
   row and note, if any.
2. Juval's session, sections Decision, "The contract as it should read",
   "Every host read of the workspace", "The test that decides 2a or 2b" and
   Sequencing. At writing it is uncommitted in a board-knowledge worktree:
   `~/Code/board-knowledge/.claude/worktrees/sq7d-session/sessions/2026-10-28-juval-sq7d-push-contract.md`
   (if it has merged since, read it from board-knowledge `main`). Read it;
   don't write, move or commit it.
3. Eric's naming session, `~/Code/board-knowledge/sessions/2026-10-28-eric-sq7g-workspace-names.md`
   (uncommitted at writing). Its names are binding (below). Read; don't write it.
4. The code your unit touches (listed per unit). The one consumer compose file,
   `~/Code/gswa-backend/.squadra/compose.yaml` (app-backend), is read-only.

## Names (Eric, 2026-10-28)

Use these; don't invent others. If a unit needs a name not here, `/ask-eric`
by hand (`~/.claude/skills/ask-eric/SKILL.md` steps 2–4, as SQ2c did) before
writing it in.

- **Workspace** (glossary entry, Eric's wording in his session): the git
  repository an Attempt's agent works in, mounted as `/work`. One per
  Attempt. Never "increment workspace", never "worktree" for it. Billet's
  Workspace is a berth in fleet docs. In **Attempt**, "worktree" becomes
  "workspace" (twice).
- Code renames, Eric's table: `worktree.py` → `workspace.py`; `WorktreeAccess`
  → `WorkspaceAccess`; `GitWorktreeAccess` → `FetchWorkspaceAccess` (2a;
  `BundleWorkspaceAccess` names 2b if it's ever built); `WorktreeCreateResult`
  → `WorkspaceCreateResult`; `DryRunWorktree` → `DryRunWorkspace`;
  `_worktree_for` / `SandboxSpec.worktree` / `TickSeams.worktree` →
  `_workspace_for` / `.workspace` / `.workspace`; `CleanupAccess.remove_worktree`
  → `remove_workspace`; `prune_worktrees` deleted; `delete_branch` →
  `delete_quarantine`; `[pipeline] worktree_dir` → `workspace_dir`, and a toml
  that still sets `worktree_dir` is refused with a message naming the new key
  (never silently ignored). `commits_present` keeps its name.
- **The quarantine ref** (code and contract name, not glossary): one function
  builds it, `refs/squadra/quarantine/<item>/<attempt>`. Keyed by Attempt,
  not Increment, so a stale ref from Attempt n can't pass Attempt n+1's
  verification when `main` hasn't moved. Commits are *pushed from*
  quarantine, never "released" or "promoted".
- **`WorkspaceAccess.import_commits(attempt)`** returns a Whole Value,
  `QuarantinedTip(attempt, ref, sha)`. "Import" is the contract and "fetch" is
  2a's transport. Write `import_commits` in full.
- **`ResolvedRemote(url, credential)`**, returned by `TargetRemote.resolve() ->
  ResolvedRemote | None` (never a `ResolvedRemote` with no credential). It
  asserts at construction that the credential's host is the URL's host. Never
  call it "the origin".
- **`FleetHome`**: the distinct type for `host_git_argv`'s host-owned
  `work_dir`, so a workspace path fails pyright by name.

## Facts found while writing this handoff (verify them, don't trust them)

1. **A plain local clone shares object files with `FLEET_HOME`.** On git
   2.56.0, `git clone <path>` hardlinked 5 of 5 object files; `--no-hardlinks`
   shared none. A shared inode means the agent, as the files' owner, can
   `chmod` and rewrite an object in `FLEET_HOME`. The create must use
   `--no-hardlinks` (or `file://`), and Juval's test 4 must assert it.
2. **Finalize would stall on a standalone workspace.** `_finalize` requires
   `result.branch_deleted`, and `delete_branch` is `git branch -D <branch>` in
   `FLEET_HOME`. Once the workspace isn't a linked worktree, `FLEET_HOME` has
   no such branch, so `branch -D` fails every tick, or deletes a human's
   branch with the same name (Eric). `update-ref -d` on an absent ref exits 0,
   so `delete_quarantine` is safe before any quarantine ref exists.
3. **A failed import leaves an old ref in place.** `git fetch <ws>
   refs/heads/<missing>:<ref>` exits 128 and doesn't touch `<ref>`. The import
   deletes the Attempt's quarantine ref before it fetches.
4. **The import is a write inside fact-gathering.** `commits_present` is read
   when the container has exited, and after SQ7g it's counted from the
   quarantine ref, so the import (an `update-ref`) runs during the gather. It
   must be a mutating `TickSeams` seam that `dry_run_seams` wraps, under the
   exhaustiveness test in `tests/test_supervisor_dry_run.py`. Today the
   `TickSeams` docstring lists `commits_present` as a pure read.
5. **Making the workspace runs git inside it, once, before launch.** Checking
   out the Attempt's branch at the base and stripping `origin` both run with
   the workspace as the working directory. That's before the agent has it, on
   a repo the host just made. Allow it only inside `create`, and say so in the
   addendum. Everywhere else, `FleetHome` (SQ7g3) shuts the door.
6. **The doc contracts still say "push from the worktree".** `git_host.py`
   and `remote_auth.py` docstrings, and README § Host-side git hardening, read
   `remote_git_argv(credential, "push", "origin", branch, work_dir=worktree)`.
   SQ7g3 rewrites them; until then, N41's tests pin that wording.
7. **No contained agent has ever run claude** (Juval, confirmed by grep in
   N41): `ComposeSandbox.launch` is `up -d --build` with no command override,
   and app-backend's agent defaults to `sleep infinity`. SQ7g1 confirms this
   in its first hour. If it holds, nothing live has a workspace or quarantine
   ref to migrate, beyond `FLEET_HOME`'s existing linked worktrees.
8. **`FLEET_WORKTREE` is set by nobody, and its default already resolves to
   the workspace.** `ComposeSandbox` passes no env, and app-backend's `..` is
   relative to the compose file, which lives in the workspace
   (`<workspace>/.squadra/compose.yaml`). So squadra setting `FLEET_WORKSPACE`
   (Eric's name) changes nothing until app-backend renames its variable.
9. **The uid question is open** (N41, Rich's ruling 5). `squadra-<name>.service`
   renders `User=azureuser` (`units.DEFAULT_USER`); app-backend's `AGENT_UID`
   defaults to 1000; nothing records azureuser's uid. app-backend's own
   SMOKE.md step 4 already requires the agent uid to equal `/work`'s owner,
   "so commits into the bind mount need no chown". So a mismatch breaks the
   agent's writes before it reaches the import, and the cheaper fix is
   setting app-backend's `AGENT_UID` to azureuser's uid, not 2b.
   `gswa-fleet-host` is deallocated. **Never start it from a session**:
   `squadra.timer` fires `OnBootSec=2min`, so starting it starts live ticks.

## Decisions carried, not blocking

- **The uid question** (fact 9): not blocking, as N41 proposed. The import
  fails closed when the effective uid is 0, or when the workspace directory's
  or its `.git`'s owner isn't the effective uid. The message names both uids.
  The cross-user acceptance step runs on the fleet-host as Rich's step (below,
  after SQ7g2 merges), and it settles 2a or 2b.
- **Eric's blocking question** (N42), for Rich: when an Attempt fails or is
  relinquished, is its workspace archived or removed, and does its quarantine
  ref survive? Proposed default, which SQ7g2 builds unless Rich rules otherwise
  before it starts: archive the workspace (as today) and delete the ref. The
  unpushed commits then live in the archived workspace, and Relinquish says so.
  The quarantine ref stays out of the glossary.
- **Where workspaces live** (N42, Juval's call per Eric): Eric says the default
  `.claude/worktrees` must go, since it's Claude Code's own worktree directory.
  Until Rich (or Juval) names a location, `workspace_dir` keeps the current
  default value. Juval's nested-repo test runs wherever it points.

## The units

### SQ7g1 · the workspace (~1.25 d; Juval's B, A for §2)

Goal: each Attempt's `/work` is a standalone repository that git works in,
made host-side, with nothing shared with `FLEET_HOME`; the worktree vocabulary
retired.

Code: `src/squadra/worktree.py`, `dry_run.py`, `cleanup.py`, `sandbox.py`
(`ComposeSandbox`, `_run_docker`), `supervisor.py` (`_worktree_for`,
`_spec_for`, `_claim_and_launch`, `_build_facts`, `_retry`, `_finalize`,
`_read_commits_present`, `TickSeams`, `dry_run_seams`, `build_seams`),
`config.py` (`worktree_dir`), `status.py` (if the base goes there);
`tests/test_worktree_git_dir.py`, `tests/test_supervisor_dry_run.py` and the
worktree/cleanup tests.

- First hour: confirm fact 7 (no command override anywhere). Record it.
- `WorkspaceAccess.create`: fetch through the existing credential path, then
  record the base commit (`rev-parse` of `origin/<base_branch>` in
  `FLEET_HOME`), then `clone --no-hardlinks --no-checkout` into the workspace
  path, check out the Attempt's branch at the base and remove every remote
  (fact 5: these pre-launch steps run only here). The base is recorded
  host-side, under `fleet_root/<item>/` beside `claimed-at` or in
  `status.json` (your call; record it), through a `TickSeams` write seam that
  `dry_run_seams` wraps.
- squadra sets `FLEET_WORKSPACE=<workspace>` on every compose call
  (`ComposeSandbox`'s runner gains an env; compose interpolates on `ps`,
  `logs`, `down` and `exec` too). app-backend's rename of `FLEET_WORKTREE`, and
  its required form `${FLEET_WORKSPACE:?…}`, is a coordinated app-backend
  change for Rich, not written here (fact 8 says nothing breaks meanwhile).
  Note it in the ledger.
- Cleanup: `remove_workspace` removes the directory without following
  symlinks (test it with a planted symlink pointing out of the workspace);
  `delete_quarantine(attempt)` is `update-ref -d` on the Attempt's ref (fact 2;
  a no-op until SQ7g2); `prune_worktrees` goes. For the upgrade, one README
  line: `FLEET_HOME`'s existing linked worktrees are left to a manual `git
  worktree prune`, or `prune` stays for one release. Your call; record it.
- `commits_present`, for this unit only, counts `<recorded base>..HEAD` in the
  workspace (it has no `origin/main` any more). That is still the agent's
  answer, as it is today (N41), and SQ7g2 moves it to `FLEET_HOME`. Say so in
  the docstring.
- All of Eric's renames, the config key refusal, the glossary's Workspace
  entry and the two Attempt edits, plus a GLOSSARY-SETTLED row (Workspace;
  rejected Worktree, Increment workspace; session
  `2026-10-28-eric-sq7g-workspace-names`). Leave Relinquish for SQ7g2.
- ADR-0002 addendum, §2: the restatement Eric asked for. Host-side git is
  `FLEET_HOME`'s; the workspace is made by the host and owned by the agent,
  and the host runs no git in it after launch.
- Tests: N41's (a) tests inverted (git works in the mount, `git commit` in
  `/work` succeeds, and nothing the agent does there touches `FLEET_HOME`'s
  config). Juval's test 3 (nested repo: `FLEET_HOME`'s fetch, `reset --hard`
  and prune read nothing from the workspace) and test 4 (no file in the
  workspace shares an inode with `FLEET_HOME/.git`). Finalize completes
  against a standalone workspace. `FLEET_WORKSPACE` reaches every compose
  call. The dry-run exhaustiveness test covers the base-record seam.

Cut line: the workspace, fact 1's `--no-hardlinks`, fact 2's cleanup and
tests 3–4 can't be cut. If the renames run long, the config-key rename and its
refusal can go to SQ7g2's start; the module and type renames can't, since
SQ7g2 builds on them.

### SQ7g2 · the import and its verification (~1.25 d; Juval's C, A for §3, E's discriminating test)

Gate addition: Rich has ruled Eric's question (N42), or the proposed default
stands.

Code: `workspace.py`, `engines.py`, `supervisor.py` (`_build_facts`,
`_read_commits_present`, `_retry`, the relinquish path, `TickSeams`,
`dry_run_seams`), `dry_run.py`, `cleanup.py`; `docs/fleet-host/SMOKE.md`.

- `FetchWorkspaceAccess.import_commits(attempt) -> QuarantinedTip | None`:
  refuse as root or on an owner mismatch (the uid rule above). Delete the
  Attempt's quarantine ref first (fact 3), then run `fetch <workspace path>
  refs/heads/<branch>:<quarantine ref>` from `FLEET_HOME`
  (`work_dir=FLEET_HOME`), and read the tip there. No git runs with the
  workspace as `work_dir`.
- The verification Engine, pure, in `engines.py`: takes the recorded base and
  the facts read in `FLEET_HOME` about the `QuarantinedTip` (is-ancestor,
  `base..tip` count) and returns a verdict. `commits_present` is that verdict,
  counted in `FLEET_HOME`; the workspace path leaves its signature.
- The import is a mutating seam (fact 4). `DryRunWorkspace.import_commits`
  runs a read-only `ls-remote <workspace>` from `FLEET_HOME`, logs `[dry-run]
  WOULD import`, and approximates commits as "the branch exists and its tip ≠
  base". Fix the `TickSeams` docstring's read/write lists.
- The quarantine ref never outlives its Attempt under the ruled default:
  `_retry`, relinquish and finalize delete it (`delete_quarantine`).
  Relinquish's glossary entry gets the ruled wording (Eric's two branches).
- Juval's test 2 (alternates: a blob that exists only in a host repo named by
  the workspace's `objects/info/alternates` never lands in quarantine; if it
  does, refuse a workspace with alternates, or record 2b's trigger). His
  test 1 (cross-user) as a script, `docs/fleet-host/workspace-import-check.sh`,
  pure bash and git, with no squadra import, so it runs on the fleet-host
  before any release. It makes a temp `FLEET_HOME` and a workspace owned by
  `--agent-uid`, imports as `--import-user` with only
  `safe.directory=<FLEET_HOME>`, plants marker keys (`core.fsmonitor`,
  `core.sshCommand`, `uploadpack.packObjectsHook`, `core.pager`,
  `diff.external`) in the workspace, and prints PASS or FAIL per check with
  `git --version`. The suite runs it same-user, so it can't rot. A SMOKE.md
  section gives Rich's step (below).
- ADR-0002 addendum, §3: the completion triple's commits are `base..tip` of
  the Attempt's quarantine ref in `FLEET_HOME`.
- Tests: N41's `test_b_the_commit_count_reads_the_agent_repo…` inverted (a
  fake `refs/remotes/origin/main` and one commit in the workspace read as
  nothing unless the branch imports and verifies). A stale ref from Attempt n
  never passes Attempt n+1. The import refuses on root and on an owner
  mismatch. The dry-run decorator writes no ref.

Cut line: the import, the Engine, `commits_present` in `FLEET_HOME`, the
dry-run seam and tests 1–2 can't be cut. If the script runs long, its SMOKE
section can trail into SQ7g3's start; the script itself can't.

**Rich's fleet-host step (after SQ7g2 merges; ~1 h; never run by a session).**
Starting the VM starts live ticks, so stop the timer within two minutes of
boot. `<rg>` is the VM's resource group (not recorded in squadra).

```bash
az vm start -g <rg> -n gswa-fleet-host
ssh azureuser@<fleet-host>
# within 2 minutes of boot (OnBootSec=2min):
systemctl list-timers --all 'squadra*'
sudo systemctl stop 'squadra*.timer'
while systemctl is-active --quiet 'squadra*.service'; do sleep 5; done

# the uid question
id -u azureuser
systemctl show -p User --value "squadra-${FLEET_NAME}.service"   # squadra.service on a pre-name host
docker image inspect gswa-fleet-sandbox:dev --format '{{.Config.User}}'   # if the image exists
grep -s AGENT_UID /opt/squadra/fleet-host.env

# the cross-user import check (copy the script from the merged SQ7g2)
sudo bash workspace-import-check.sh --agent-uid 1000 --import-user azureuser
sudo bash workspace-import-check.sh --agent-uid 1000 --import-user root   # record what git does; squadra's import refuses root anyway

# leave it as found
sudo systemctl start 'squadra*.timer'   # only if it was active before; else skip
az vm deallocate -g <rg> -n gswa-fleet-host
```

Outcomes, recorded by the next session in N42: azureuser's uid equals the
agent's, and the check passes → 2a holds (the import refuses root anyway).
They differ → first set app-backend's `AGENT_UID` to azureuser's uid (fact 9),
then re-run. The check fails as the service user → 2b's trigger: a new row
(`BundleWorkspaceAccess`, +~0.5 d, and a claude-skills change for the
fleet-mode skill to write the bundle).

### SQ7g3 · `ResolvedRemote`, `FleetHome` and the push contract (~0.75 d; Juval's D, A for §1, the rest of E)

Code: `repo.py` (`TargetRemote`), `remote_auth.py`, `git_host.py`,
`workspace.py`, `cleanup.py`, `supervisor.py` (`build_seams`), README §
Host-side git hardening; `tests/test_worktree_git_dir.py` (renamed with the
module), `tests/test_git_host.py`, `tests/test_remote_auth.py`.

- `TargetRemote.resolve() -> ResolvedRemote | None`, one read; `ResolvedRemote`
  asserts the credential's host is the URL's host. `TargetRemote.credential()`
  is retired. It has two callers, not the one Eric's session counts: the
  app-repo sync's fetch (`repo.py` ~line 138) and the create's fetch (via
  `build_seams`). Both move to `resolve()`, so there is one path to a
  credential for the target remote. The bootstrap clone keeps
  `credential_for(repo_url)`, since there's no remote to resolve yet. The probe keeps its
  behaviour (SQ7f later shares the supported-remote check with it).
- The push argv builder, unwired: from a `ResolvedRemote`, a `QuarantinedTip`
  and `FleetHome`, it returns `remote_git_argv(r.credential, "push", r.url,
  f"{tip.ref}:refs/heads/{branch}", work_dir=fleet_home)`. Nothing calls it
  but its contract test (G2 wires it). The ref pushed is the `QuarantinedTip`'s.
- `FleetHome`: `host_git_argv`'s and `remote_git_argv`'s `work_dir` takes it,
  not `str | Path`. Every caller passes `FLEET_HOME` through it; the create's
  pre-launch steps (fact 5) use their own narrow path, named in the code. A
  workspace path fails pyright.
- The contract text, Juval's paragraph in Eric's names, replaces the push
  paragraphs in `git_host.py`, `remote_auth.py` and README § Host-side git
  hardening (fact 6). ADR-0002 addendum, §1: the push runs from `FLEET_HOME`
  to the `ResolvedRemote`, from the verified quarantine ref, under the tick
  lock. The Consequences bullet on #193 gets one line: the workspace is no
  longer a host-side git target after launch.
- Tests: the rest of N41's (b) tests inverted. With the workspace's `.git`
  replaced or redirected, `url.<base>.insteadOf`, `core.sshCommand` and
  `receivepack` set, the specified push lands in the target remote, runs
  nothing of the agent's, and the loopback listener sees no PAT for an
  agent-chosen host. Cleanup removes a workspace whose `.git` was replaced.
  `ResolvedRemote` refuses a host mismatch.

Cut line: none inside the unit; all of it is Juval's D and E. If it runs long,
stop at a green commit and add an SQ7g3b row for what's left. Never cut the
`ResolvedRemote` and its test.

### SQ7g4 · floated (~0.5 d; not started from this file without Rich)

- The ADO helper answers only for its own `host=` (Juval's optional item). It
  changes bytes SQ7 pinned, so it waits on Rich's call.
- The dry-run approximation's polish (e.g. reporting the would-be commit count
  from `ls-remote` plus the recorded base instead of "tip ≠ base").

## Out of scope for every unit

- Wiring the push (G2), the PR create, or the in-container runner (the
  command override).
- SQ7f (the shared supported-remote check) and SQ7h (the compose-boundary
  guard and host-held compose copy). SQ7h's host-held copy changes what
  app-backend's `..` resolves to, so it needs app-backend's `FLEET_WORKSPACE`
  rename first (noted on SQ7h's row); don't build it.
- SQ7c, N38's `agent_environ` gap.
- Any write to app-backend (`gswa-backend`), claude-skills or board-knowledge.
  The app-backend rename and `AGENT_UID` change are Rich's.
- Any tick, live or dry, against a real board; any network git op; starting
  `gswa-fleet-host`; any container run on Rich's Mac.

## Wrap-up (each unit)

Run the four validation commands in `CLAUDE.md`. Update
`docs/board-writes/LEDGER.md`: the unit's row status, a note for anything a
later unit or Rich needs (recorded choices, cuts, the fleet-host outcome), and
the session log. Commit, push, open the PR. Tell Rich the PR URL, what was
built, what was cut, and (after SQ7g2) that the fleet-host step is ready for
him.
