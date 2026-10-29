# Handoff: SQ7g0 · Rich rules N42's two questions (board-writes, part 7g0)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
Gate: PR #76 (the SQ7g handoff and this file) merged on squadra `main`, and no
other board-writes unit in flight. Fetch first, then fast-forward the
`feat/board-writes` worktree (`.claude/worktrees/board-writes`) to
`origin/main`. If it can't fast-forward, stop and tell Rich.

## Context

SQ7g's handoff (`handoffs/SQ7g-workspace-import.md`, ledger N42) left two
questions for Rich. SQ7g2 can't start until both are ruled.

1. **Archive or remove** (Eric's blocking question). When an Attempt fails
   (retry) or is relinquished (withdrawn in flight), is its workspace archived
   or removed, and does its quarantine ref survive?
2. **Where workspaces live.** Eric says the default `.claude/worktrees` must go
   and the location is Juval's call.

This session asks Rich, one question at a time, until he has decided both. It
records the rulings, fills in the Choice of every advisor session they answer,
and opens a PR. It builds nothing.

It can run before or after SQ7g1, not alongside it (one board-writes PR at a
time). If it runs before SQ7g1, SQ7g1 builds the ruled location. If it runs
after, SQ7g2 moves the default. Say which applies in the ledger.

Read only these:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ7g–SQ7g4, SQ7g0
   and SQ7h, and notes N41 (Rich's rulings) and N42; the SQ7g handoff,
   sections Names, Facts and "Decisions carried, not blocking".
2. Eric's naming session (Choice blank), in full:
   `~/Code/board-knowledge/sessions/2026-10-28-eric-sq7g-workspace-names.md`.
3. Juval's push-contract session (Choice already filled; read only), sections
   Decision, "Every host read of the workspace" and "The test that decides 2a
   or 2b": `~/Code/board-knowledge/.claude/worktrees/sq7d-session/sessions/2026-10-28-juval-sq7d-push-contract.md`,
   or from board-knowledge `main` if it has merged.
4. The code: `supervisor.py` `_retry`, `_relinquish`, `_finalize`;
   `worktree.py` (or `workspace.py` after SQ7g1) `archive`; `board.py` ~lines
   190–235 (the `Reaped` / `Relinquished` comment text); `config.py`
   `worktree_dir` (or `workspace_dir`); `cli.py` ~line 385 (the default
   `fleet_root`); `repo.py` `ensure_app_repo`; `GLOSSARY.md` Attempt and
   Relinquish.

## Facts found while writing this handoff (verify them, don't trust them)

1. **Retry and relinquish both archive today.** They move the directory to
   `fleet_root/<item>/archive/attempt-<n>` and prune. Finalize removes it.
   Nothing ever deletes an archive. The board comments say "worktree
   archived" (`board.py` ~196, 201, 231), so the ruling's words reach the
   board.
2. **The archive holds the agent's outcome manifest.** ADR-0002 §1:
   `.squadra/outcome.json` is written in the workspace. Removing the
   workspace loses it, along with any uncommitted work, unless something is
   copied out first.
3. **Inspecting an archived workspace with git means reading the agent's
   config.** A human running `git status` or `git log` in it runs the agent's
   `core.fsmonitor` or `core.pager`. That is N41's (b) class, moved from the
   supervisor to a person. A quarantine ref kept in `FLEET_HOME` can be
   inspected there (`git log refs/squadra/quarantine/<item>/<attempt>`)
   without touching the agent's config. Verify with a marker, hermetically,
   before you state it to Rich.
4. **Keeping refs needs a retention rule.** Refs under `refs/squadra/` aren't
   pushed or fetched by default, and `gc` keeps their objects. Nothing would
   delete a kept ref; today's design deletes it with its Attempt (N42's
   default). Keyed by Attempt, a kept ref can't pass a later Attempt's
   verification.
5. **Sync leaves untracked directories alone.** `ensure_app_repo` runs
   `fetch` then `reset --hard origin/<base>`, with no `git clean`. So any
   location inside `FLEET_HOME` survives a tick. A human's `git clean -fdx`
   in `FLEET_HOME` wouldn't spare it.
6. **app-backend ignores `.claude/worktrees/` and nothing else squadra
   uses.** `git check-ignore` matches `.claude/worktrees/` (its `.gitignore`
   line 163) but not `.claude/fleet/`. On the fleet-host, `fleet_root`
   defaults to `FLEET_HOME/.claude/fleet` (`cli.py` ~385; the activate script
   passes no `--fleet-root`), so it already sits untracked in
   `/opt/squadra/app-backend`. A new location inside `FLEET_HOME` either needs
   an app-backend `.gitignore` line (Rich's change) or shows as untracked.
7. **Size isn't a driver.** app-backend's pack is ~2 MiB. A `--no-hardlinks`
   workspace copies it, so one archive is a few MB plus the working tree.
8. **`.claude/worktrees` is also Claude Code's worktree directory** (Eric).
   That matters on a dev host where someone runs Claude Code in `FLEET_HOME`.
   It doesn't matter on the fleet-host.
9. **No hardcoded default path** (CLAUDE.md hard invariant on `FLEET_HOME`).
   Today `worktree_dir` is relative to `FLEET_HOME`. A location outside it
   needs an absolute setting, or one derived from something already
   configured.

## How to run the session

- **One question at a time, in order: Q1, then Q2, then Q3.** For each, state
  the question in one sentence, then give the options as a table (what
  happens, what it costs, what it changes in SQ7g1–SQ7g3 and the glossary)
  and the facts it rests on. Don't recommend unless Rich asks. If an advisor
  answered it in this session, quote his recommendation, attributed.
- **Rich answers or gives feedback.** If he gives feedback, asks for more, or
  proposes another option, answer it: check code or facts as needed, and
  update the table. Then ask again. Repeat until he states a decision.
- **Read the decision back in one sentence and record it only after he says
  yes.** Don't open the next question until this one is confirmed.
- **Advisors.** `/ask-juval` and `/ask-eric` are user-invoked only, so follow
  `~/.claude/skills/ask-<advisor>/SKILL.md` steps 2–4 by hand, as SQ2c did.
  Give the advisor the question, the facts and the options, quoted, with no
  recommendation. Relay his answer in full, then write his session file in
  `~/Code/board-knowledge/sessions/` (not committed).
  - Q2: Eric said the location is Juval's call, so before asking Rich, offer
    a `/ask-juval` consult on Q2. Run it unless Rich declines.
  - Q1: Eric's session already words both of his branches. Consult Eric again
    only if Rich picks an option Eric didn't word (for example, removing the
    workspace), or if a decision needs a new name. Rich can ask for either
    advisor on any question.
- **Choice.** When a question is confirmed, write the ruling into the
  `## Choice` of every advisor session it answers, in the form the earlier
  sessions use ("Rich, 2026-10-29 (…): 1. … 2. …"):
  - Eric's `2026-10-28-eric-sq7g-workspace-names.md`: Q1 (his blocking
    question) and Q3 (his names).
  - Any session opened here: the question it was asked for.
  - Juval's push-contract session: already filled. Don't touch it.

  Fill in Choice only: don't commit, move or otherwise edit board-knowledge
  files. If Rich asks to commit them, that's a board-knowledge branch and PR
  of its own, not this squadra PR.

## The questions

### Q1 · On a failed or relinquished Attempt: archive or remove the workspace, and keep or delete its quarantine ref?

Options to put (Rich may rule retry and relinquish differently):

| | Workspace | Quarantine ref | Relinquish wording (Eric) | Glossary |
|---|---|---|---|---|
| A (N42's default) | archived | deleted | "archives the workspace"; the unpushed commits are in it | quarantine stays out |
| B | archived | kept | "leaves the quarantined commits and any pushed branch for a human" | quarantine ref gets an entry (Eric names it) |
| C | removed | kept | as B, minus the archive | as B; the manifest is lost unless copied out (fact 2) |
| D | removed | deleted | not worded; consult Eric | nothing kept to inspect |

For each, say what fact 3 (inspecting with git) and fact 4 (retention)
mean. B and C need a retention rule (for example, every Attempt's ref is
deleted when the Increment finalizes, or refs stay until a human deletes
them), so ask that as part of Q1. Also ask whether the board comments'
"worktree archived" wording follows the ruling (SQ7g1 renames it anyway).

### Q2 · Where do workspaces live?

Options to put (Juval's answer, if consulted, may add or remove some):

| | Location | For | Against |
|---|---|---|---|
| a | `FLEET_HOME/.claude/worktrees` (today) | no change; already ignored by app-backend | the retired word; Claude Code's directory (fact 8); a repo nested in `FLEET_HOME` (Juval's test 3) |
| b | another squadra-owned directory in `FLEET_HOME` (name from Eric) | relative default stays; no Claude Code collision | needs app-backend's `.gitignore` (fact 6); still nested |
| c | under `fleet_root`, beside the Increment's state (e.g. `<fleet_root>/<item>/…`) | one place per Increment; archive is a move within one tree | `fleet_root` defaults inside `FLEET_HOME` and isn't ignored (fact 6); the agent's repo sits beside host state |
| d | outside `FLEET_HOME`, a configured directory | no nesting, no `.gitignore`, no collision | no natural default (fact 9); install-units, cloud-init and goss create it with the right owner; the archive move may cross filesystems |

Whatever Rich picks, settle the setting's shape: relative to what, its
default, and whether the old `worktree_dir` key is refused (it is, per
Eric). If a name is new, get it from Eric before recording it.

### Q3 · Eric's names, for his session's Choice

Eric's names are already binding in the SQ7g handoff. Ask Rich to confirm
them as given, or name the ones he wants changed. If Rich changes any name,
consult Eric on it before recording.

## Wrap-up

- `docs/board-writes/LEDGER.md`:
  - N42 gets a "Rich's rulings, 2026-10-29" bullet: Q1, Q2 and Q3, any
    advisor sessions with their paths, and where each ruling is built (SQ7g1
    or SQ7g2).
  - Rows SQ7g1 and SQ7g2 get any scope change. SQ7g2's dependency drops "or
    its default". SQ7g0 is marked done.
  - Add a session log row.
- The SQ7g handoff's "Decisions carried, not blocking" points at N42's
  rulings, and any unit text the rulings change is updated (the location in
  SQ7g1's create, retention in SQ7g2's ref handling).
- No code, no glossary edit (SQ7g1 and SQ7g2 build those), no write to
  app-backend or claude-skills. An app-backend `.gitignore` line, if Q2 needs
  one, is listed for Rich.
- Run the four validation commands in `CLAUDE.md`. Commit, push, open the
  PR. Tell Rich the PR URL, the three rulings, and the paths of the session
  files whose Choice was filled in.
