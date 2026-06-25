# Slice runner skill (consumer-owned template)

<!--
  SCAFFOLDED BY `squadra init`. This is YOUR copy — edit it freely.

  squadra ships the deterministic machinery (supervisor, status CLI, runner
  wrapper, host-side manifest I/O); the *skill* that actually implements one
  board slice is yours, because only you know your repo's gates, conventions,
  and review process.

  Wire this file in as the skill named by `[pipeline].runner_skill` in
  squadra.toml (default `/afk-slice-runner`). The runner wrapper invokes it
  headlessly, once per claimed slice, with these prompt arguments:

      <runner-skill> issue-id=<id> branch=<branch> attempt=<n> \
                     tdd-skill=<tdd> qa-skill=<qa>

  PRODUCE-ARTIFACTS CONTRACT: this runner is CONTAINED and CREDENTIAL-FREE. It
  reads its slice context from a host-injected `.squadra/slice.json` and its
  ONLY outputs are (1) the commits it makes and (2) a `.squadra/outcome.json`
  manifest it writes as its final act. It performs NO remote/board writes — no
  push, no PR open, no tags, no comments, no state moves. The supervisor (which
  holds the credentials host-side) reads those two artifacts and performs the
  entire remote/board tail. Keep it that way: do not add credentialed actions
  to this skill.

  PROVIDER-AGNOSTIC: nothing here is tied to a specific board provider. "The
  board" means whatever provider squadra.toml configures; "the slice" is one
  work item; fleet tags (e.g. `fleet:qa-ready`) are applied by the supervisor
  through squadra's machinery, never by you. Keep it that way so this skill
  survives a provider swap.
-->

## You are contained — no credentials, no board, no remote

You run inside a per-slice sandbox: **no credentials, no board client, no remote,
no push.** You therefore perform **zero remote/board writes**. Your entire output
to the outside world is:

- the **commits** in `base..HEAD` (the substance the supervisor pushes), and
- the **`.squadra/outcome.json`** manifest you write as your **final act** (the
  intent the supervisor acts on).

You are headless. There is no user. **Never block waiting on input** — any point
that needs a human resolves to a manifest `parked_state` (and a `status.json`
park) and you exit. Never open, complete, or merge a PR; the supervisor owns the
remote/board tail and the finalize tick owns cleanup.

## Inputs — injected, not queried

The wrapper passes these in your prompt; parse them:

- `issue-id` — the board work-item id for this slice.
- `branch` — the feature branch the wrapper already derived for you.
- `attempt` — the attempt counter (1-based); higher means a prior attempt was
  reaped/retried, so start from a clean worktree.
- `tdd-skill` — the skill to invoke for the implement-with-tests phase. **Invoke
  THIS skill name; do not hardcode `/tdd`.** Defaults to `/tdd` when unset.
- `qa-skill` — the skill to invoke for the quality-gate phase. **Invoke THIS
  skill name; do not hardcode `/qa`.** Defaults to `/qa` when unset.

You have **no board access**, so the supervisor read the board for you and
injected a read-only **`.squadra/slice.json`** in the worktree root. That is your
slice context (squadra `SliceContext` schema):

```json
{
  "issue_id": 146,
  "title": "Slice G2 — …",
  "tasks": [ { "task_id": 178, "title": "…", "state": "Doing" } ],
  "predecessor_states": { "143": "Done" }
}
```

## Status reporting

Report progress through squadra's status CLI (never edit `status.json` by hand)
so the supervisor's heartbeat/reap/finalize logic stays coherent:

```bash
# `squadra slice` if on PATH, else the module form (always available):
python -m squadra.status update --issue-id <id> --phase <phase> \
  [--parked-state <state>] [--add-worker <name>] [--last-error <msg>]
```

Update `phase`, the park state, and the worker roster (`--add-worker`) as you
move through the lifecycle. The wrapper owns the liveness heartbeat — you do not
stamp it. There is **no `--pr-url` to set** in this mode: the supervisor opens
the PR host-side from your manifest.

## Lifecycle

Execute these phases in order. Stop and park on any unrecoverable point.

1. **Verify the claim from `.squadra/slice.json`.** Read the Issue, its Tasks, and
   `predecessor_states` from the injected file (you have no board to query). The
   supervisor only claims unblocked slices, but verify: if any predecessor is
   **not `Done`**, or the Issue/Tasks are too ambiguous to implement, park
   `needs-decision` (step 6) instead of guessing. If a guard makes this slice
   un-runnable in the sandbox (e.g. it requires network egress your sandbox
   blocks), park `needs-decision` with a clear message rather than attempting it.
2. **You already start on the slice worktree.** Do **not** enter or create a
   worktree. The wrapper created the branch worktree off fresh `origin/main`, put
   your HEAD on the branch, and bind-mounted it; just commit into it. All edits
   happen here, never in the primary checkout.
3. **Write shared seams BEFORE fan-out.** Set `--phase seams`. If you will
   parallelize work, first write and commit the shared interfaces/types/contracts
   the parallel tasks depend on, so fanned-out work cannot diverge on the seam. A
   slice with no shared seams skips this step.
4. **Run the tdd skill (produce-artifacts mode).** Set `--phase tdd`, then invoke
   the skill named by `tdd-skill`. It implements the slice test-first with clean
   commits and your gates green — but **no board moves and no PR open**. Instead
   it **drafts the PR title + body** and reports them up to you; hold them for the
   manifest (step 6). Record any spawned workers with `--add-worker`. Workers are
   collaborators on this one slice — spawn them WITHOUT worktree isolation, only
   in parallel when file-disjoint. Stay inside your slice; do not start other
   work items.
5. **Run the qa skill (produce-artifacts mode).** Set `--phase qa`, then invoke
   the skill named by `qa-skill`. It writes the QA plan to a file in the worktree
   (e.g. `qa.md`) and reports the path up to you — **no board QA task, no PR
   comment**. Hold that path for the manifest (step 6).
6. **Emit the outcome manifest and exit.** Your **final act** is one write:
   `.squadra/outcome.json` in the worktree root (schema below). Park the
   `status.json` breadcrumb to mirror the same outcome **first**, then write the
   manifest **last**, so a valid manifest implies a completed run. Exit 0 once
   parked. The wrapper backstops any non-parked exit, and a run that dies before
   writing the manifest is a crash edge the supervisor detects from the
   `(exit, manifest, commits)` triple — not yours to fake. For a transient
   infrastructure failure (network/git/model blip), just exit non-zero **without**
   a manifest; the supervisor reaps and retries from a fresh worktree.

## Outcome manifest (`.squadra/outcome.json`)

`parked_state` is **required** and one of squadra's parked states
(`awaiting-pr-approval`, `qa-ready`, `needs-decision`, `failed`); the optional
`pr_title` / `pr_body` / `qa_path` carry the work-tail the supervisor lands. The
supervisor reads this and performs the push, PR open, work-item links, QA task,
comments, and board state — you do none of it.

```json
{
  "parked_state": "awaiting-pr-approval",
  "pr_title": "<type>[(scope)]: <summary>",
  "pr_body": "## Summary\n…\n## What changed\n…\n## Testing\n…",
  "qa_path": "qa.md"
}
```

Choose the outcome:

| Outcome | `parked_state` | other fields | status.json park |
|---|---|---|---|
| TDD done + QA written (normal) | `awaiting-pr-approval` | `pr_title`, `pr_body` (from tdd), `qa_path` (from qa) | `--phase parked --parked-state awaiting-pr-approval` |
| Ambiguity / human decision needed | `needs-decision` | none — no PR; put the question in `--last-error` + the run log | `--phase parked --parked-state needs-decision --last-error "<question>"` |
| Terminal failure (unresolvable red, contradictory spec) | `failed` | none | `--phase parked --parked-state failed --last-error "<diagnostic>"` |

- **Normal:** write `pr_title` + `pr_body` (from the tdd skill) and `qa_path`
  (from the qa skill); `parked_state=awaiting-pr-approval`. The supervisor pushes
  the commits, opens the PR with that exact title/body linking the Issue + Tasks,
  lands the QA task from `qa_path`, and parks the slice awaiting approval.
- **`needs-decision`:** write only `parked_state=needs-decision` (no PR fields).
  The supervisor opens **no** PR and leaves it for a human; put the exact
  question in `--last-error` and the run log.
- **`failed`:** write only `parked_state=failed`. The supervisor reaps/escalates
  per its failure-edge policy.

## Gates

<!-- FILL IN: your repo's lint / type / test commands. The qa skill (step 5)
     runs these; the slice is not park-ready until they all pass. Replace the
     examples below with your real commands. -->

```bash
# EXAMPLE — replace with your project's gates:
# <your linter>      # e.g. ruff check .
# <your type-check>  # e.g. pyright
# <your tests>       # e.g. pytest
```

## Conventions

<!-- FILL IN: branch/commit/PR conventions, required reviewers, labels, and any
     repo-specific guardrails the runner must honor. Keep these provider- and
     repo-agnostic where squadra already abstracts them (tags, states, branch
     template come from squadra.toml). The PR body in your manifest is posted
     verbatim by the supervisor — hold it to your repo's PR-description rules. -->
