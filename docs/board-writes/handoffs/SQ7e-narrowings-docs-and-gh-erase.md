# Handoff: SQ7e · narrowings docs, fleet-host precondition, gh `erase` check (board-writes, part 7e)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: this handoff's PR merged on squadra `main`, and no other board-writes
unit in flight. Fetch first, then fast-forward the `feat/board-writes`
worktree (`.claude/worktrees/board-writes`) to `origin/main`. If it can't
fast-forward, stop and tell Rich.

## Context

SQ7 (PR #69) built one credential boundary for host-side git. SQ7b (PR #70)
put its three unruled points to Juval, and Rich ruled them (ledger N37's
"Rich's rulings, 2026-10-28"; N38). Three small items came out of those
rulings with no design left in them. This unit does them, plus one stale
README line found while writing this handoff. Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ7, SQ7c, SQ7d,
   SQ7e and note N37 (its SQ7b sub-bullets and Rich's rulings).
2. Juval's two sessions, sections Choice and the parts named below:
   board-knowledge `sessions/2026-10-28-juval-sq7-credential-open-points.md`
   (Answer, "3. The two narrowings": the README line's wording) and
   `sessions/2026-10-28-juval-when-to-build-sq7c.md` (Answer, "What comes
   out of the row now"). Both may be uncommitted in `~/Code/board-knowledge`;
   read them from the working tree.
3. `README.md` § Supervisor (the per-remote credential table, ~line 362) and
   § Host-side git hardening (~line 560); `docs/fleet-host/SMOKE.md`
   § Prerequisites; `src/squadra/remote_auth.py` (`credential_for`,
   `GITHUB_GH_HELPER`); `src/squadra/repo.py` `TargetRemote.probe`;
   `src/squadra/git_host.py` (the push contract).

## This session's unit

Ledger item: SQ7e
Goal: the README upgrade line, the fleet-host precondition line, the stale
push-contract line, and the gh `erase` check run and recorded. Docs plus one
recorded check; no change to `src/`.

### 1. README upgrade line (Rich, N37 ruling 3)

Under the per-remote credential table in § Supervisor, add the behaviour
change SQ7 made, so an operator upgrading knows. Juval's wording, to adapt
to the README's voice, not to soften: "As of SQ7, squadra refuses SSH
remotes to any host other than github.com, ssh.dev.azure.com and
vs-ssh.visualstudio.com (including ssh-config aliases), and plain `http`
remotes." Say what the operator sees: the preflight's "no git credential for
the target remote (host: …)" line, and no claims.

### 2. Fleet-host precondition line (Juval, when-to-build session)

In `docs/fleet-host/SMOKE.md` § Prerequisites (next to the Key Vault grant
bullet), one line: a GitHub board or a github.com HTTPS remote on the
fleet-host needs SQ7c first, because `fleet-tick` fetches only the
ADO-shaped pair (`anthropic-api-key`, `fleet-ado-pat`) and the host has no
`gh`. Point at the ledger row. The purpose is that whoever decides to run a
GitHub fleet there learns it at decision time, which is when SQ7c's roughly
one week of lead time has to start.

### 3. Stale push-contract line (found writing this handoff)

`README.md` § Host-side git hardening (~line 566) says the deferred push
"must be built as `host_git_argv(*credential_helper, "push", "origin",
branch, work_dir=worktree)`". N37's Sweep bullet says SQ7 changed
`git_host`'s push contract to `remote_git_argv(credential, "push", …)` with
`TargetRemote.credential()`. Check `git_host.py`'s docstring and
`tests/test_git_host.py` first; if they agree with N37, bring the README
line into line with them. If they don't, stop on this item and record the
disagreement in the ledger instead.

### 4. The gh `erase` check (Juval; Rich took it out of SQ7c)

Question: when git's credential fill fails with gh's helper, git sends the
helper `erase`. Does `gh auth git-credential erase` remove gh's stored
login? If yes, a single rejected `GH_TOKEN` on a dev host (Rich's Mac, a
berth) logs gh out, for the board adapter too. It doesn't matter on the
fleet-host (no gh session there under (b2)).

**Rich's real gh login must not be at risk.** Do it in this order:

1. **Read the source first.** `gh --version` (2.101.0 on Rich's Mac at
   writing). Read `pkg/cmd/auth/gitcredential/helper.go` at that tag, e.g.
   `gh api 'repos/cli/cli/contents/pkg/cmd/auth/gitcredential/helper.go?ref=v<version>' --jq .content | base64 -d`.
   Record what `erase` (and `store`) do, with the tag and file.
2. **If `erase` is a no-op in the source**, confirm through squadra's own
   path, once: a temp `FLEET_HOME` (`git init`, `origin` set to the
   sandbox's GitHub remote rewritten to HTTPS; take it from
   `git -C ~/Code/squadra-sandbox remote get-url origin`), then
   `TargetRemote(...).probe()` with `GH_TOKEN` set to a junk value. That is
   one read-only `ls-remote`. Expect a not-OK probe naming the github.com
   token. Then, with `GH_TOKEN` unset, `gh auth status` must still show the
   login. Never write the sandbox repo.
3. **If `erase` does anything to stored credentials**, don't run step 2.
   Record what the source says, and add a row for a dev-host fix (Rich's
   call; Juval: the fix is for dev hosts, not SQ7c). Don't build it.

Either way, record the result as a ledger note.

## Record

- A new ledger note (N39) with item 4's result (tag, file, what `erase`
  does, whether step 2 ran and what it showed) and one line per docs item.
- SQ7e's status, the session log, and the next handoff. The next unit is
  Rich's call: SQ7d is scheduled before the push is wired; SQ7c waits on its
  trigger. Write no build handoff for either unless Rich asks.

Estimated work: ~30K (hard stop 60K). Cut line: items 1–3 are one commit.
If item 4 can't finish (no gh, no network, the source unclear), record what
was found and stop; it can be its own session.

## Out of scope for this session

- Any change to `src/`, including `remote_auth.py` and `secrets.py` (SQ7c,
  SQ7d), and a fix for a gh `erase` that logs out (a row only).
- The `agent_environ` gap (N38): a note, no work.
- Any tick, live or dry. Any probe beyond item 4 step 2's one `ls-remote`.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Run the four validation commands in `CLAUDE.md` (docs only, but run them).
Update `docs/board-writes/LEDGER.md` (SQ7e status, N39, session log, next
handoff), commit, push, open the PR. Tell Rich the PR URL and what item 4
found.
