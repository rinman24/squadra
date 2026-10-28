# Handoff: SQ7b · consult Juval on N37's three open points (board-writes, part 7b)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: SQ7 (PR #69) merged on squadra `main`, and no other board-writes unit in
flight. Fetch first, then fast-forward the `feat/board-writes` worktree
(`.claude/worktrees/board-writes`) to `origin/main`. If it can't
fast-forward, stop and tell Rich.

## Context

SQ7 built Juval's option D (N29): one credential boundary for host-side git,
keyed on the target remote's host. Three of its points were settled in the
build, not ruled, and Rich wants Juval's read before he rules (ledger N37,
items 1–3 below). This session runs that consult and records it. It builds
nothing. Read only these:

1. squadra `CLAUDE.md` (hard invariant: no silent permission or behaviour
   broadening on the claim path); `docs/board-writes/LEDGER.md` rows SQ7,
   SQ7b and notes N29, N36, N37.
2. Juval's earlier session on this defect, sections Choice and "(iii): new
   row, and the label isn't the bug": board-knowledge
   `sessions/2026-10-27-juval-squadra-init-preflight-findings.md`. If it isn't
   on board-knowledge's `main`, read it with
   `git -C ~/Code/board-knowledge show origin/docs/juval-squadra-init-preflight:sessions/2026-10-27-juval-squadra-init-preflight-findings.md`.
3. The code as merged: `src/squadra/remote_auth.py` (the whole module),
   `repo.py` `TargetRemote`, `worktree.py` `GitWorktreeAccess.create`,
   `supervisor.py` `_remote_auth_rejected_message`; `secrets.py` (what the
   Key Vault bootstrap fetches and the env names it uses); `github_board.py`
   module docstring and `_run_gh` (how the board adapter authenticates);
   `docs/fleet-host/cloud-init.fleet-host.yaml` and
   `docs/fleet-host/fleet-host-activate.sh` (what the fleet-host installs and
   where its environment comes from).

## This session's unit

Ledger item: SQ7b
Goal: Juval's answer on N37's three points, recorded for Rich's ruling. No
code.

### `/ask-juval`

Put the three points to him in one consult. Give him the facts and the
options below, and have him read the code himself. Don't pass him N37's
reasoning paragraphs as a recommendation: say what was built, not why it is
right. Ask him to keep, amend or replace each choice, in his usual
priced-options form (estimate and risk per option).

**1. The GitHub HTTPS credential source.** Built: reset inherited helpers,
then `credential.helper=!gh auth git-credential`, so host-side git uses the
gh CLI's token (`GH_TOKEN`/`GITHUB_TOKEN`, else the `gh auth login`
session), the same token the GitHub board adapter uses for every board call.
It adds no secret or env name. Ask him:
- Is one token for board and git the right coupling, or should git's
  credential be separable from the board's (an ADO board over a GitHub repo
  already uses gh only for git)?
- Alternatives to price: an env-var helper over a squadra-named variable
  (mirroring the ADO PAT), a GitHub App installation token, an SSH deploy
  key (no helper), or keep gh.
- Does the probe still prove the claim's path if gh's token can change
  between the probe and the fetch (gh refreshes its own session)?

**2. Where a GitHub fleet's token comes from on the fleet-host.** Fact:
`secrets.py` bootstraps only `ANTHROPIC_API_KEY` and `AZURE_DEVOPS_EXT_PAT`
from Key Vault (secret names `anthropic-api-key`, `fleet-ado-pat`), into the
process environment, never to disk (ADR-0002 §11). cloud-init doesn't install
`gh`. So a GitHub fleet on the fleet-host has neither a board credential nor
a git credential today. Not built. Options to price:
- (a) Operator-managed: install gh and log it in for the unit's user, or put
  `GH_TOKEN` in the unit's environment. Check this against ADR-0002 §11's
  "secrets never touch disk".
- (b) A Key Vault secret (e.g. `fleet-github-token`, with a
  `FLEET_GITHUB_TOKEN_SECRET_NAME` knob) bootstrapped into `GH_TOKEN`. This
  is a new secret name, so it is Rich's call.
- (c) Something else he proposes (a GitHub App via the VM's managed
  identity, if that is feasible).
Ask also whether it must land before the first fleet-host GitHub fleet, and
whether it is SQ-row work or fleet-host provisioning work. Ask whether the
token must stay off the agent-facing surface: `agent_environ` carries only
`ANTHROPIC_API_KEY` today.

**3. The two narrowings.** Built: an SSH remote to any host other than
`github.com`, `ssh.dev.azure.com` and `vs-ssh.visualstudio.com`, and any
plain `http` remote, have no credential and fail closed. Before SQ7, an SSH
remote to any host passed on ssh-agent, and an `http` ADO remote got the PAT
in clear. Ask him:
- SSH: squadra supplies no credential for SSH at all, so is gating SSH by
  host a credential decision, or should any SSH host pass, with the host's
  ssh config owning auth? Weigh it against N36, where a remote that is
  neither GitHub- nor ADO-shaped already refuses a second fleet on the
  host.
- `http`: keep it refused? Is there any case for allowing it without a
  credential?
- Either way, is the narrowing a behaviour change Rich must know he made
  (the hard invariant forbids silent broadening; this is a narrowing)?

## Record

- The session file stays in board-knowledge as the skill writes it, with its
  Choice section left blank for Rich.
- Amend N37 in `docs/board-writes/LEDGER.md` with a short summary of
  Juval's answer per point, the session file path, and the questions Rich
  must rule on, in the order he should take them. Where Juval disagrees
  with what SQ7 built, say so plainly.
- If an answer means building, add a row (SQ7c, or SQ7c/SQ7d if it splits)
  with a one-line scope. Don't write its build handoff until Rich rules.

Estimated work: ~35K (hard stop 60K). No cut line: if the consult can't
finish, record what was asked and answered so far and stop.

## Out of scope for this session

- Any code, and any change to `remote_auth.py`, `secrets.py` or the
  fleet-host docs.
- Ruling on Rich's behalf.
- Running a probe or a tick, live or dry.
- Any write in claude-skills. In board-knowledge, only the session file the
  skill writes.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ7b status, N37, session log, next
handoff: none until Rich rules), commit, push, open the PR. Tell Rich the PR
URL and the session file whose Choice he needs to fill in.
