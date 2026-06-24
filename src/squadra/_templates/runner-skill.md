# Slice runner skill (consumer-owned template)

<!--
  SCAFFOLDED BY `squadra init`. This is YOUR copy — edit it freely.

  squadra ships the deterministic machinery (supervisor, status CLI, runner
  wrapper); the *skill* that actually implements one board slice is yours,
  because only you know your repo's gates, conventions, and review process.

  Wire this file in as the skill named by `[pipeline].runner_skill` in
  squadra.toml (default `/afk-slice-runner`). The runner wrapper invokes it
  headlessly, once per claimed slice, with these prompt arguments:

      <runner-skill> issue-id=<id> branch=<branch> attempt=<n> \
                     tdd-skill=<tdd> qa-skill=<qa>

  PROVIDER-AGNOSTIC: nothing here is ADO-specific. "The board" means whatever
  provider squadra.toml configures; "the slice" is one work item; the fleet
  tags (e.g. `fleet:qa-ready`) are applied through squadra's machinery, not by
  the provider's CLI directly. Keep it that way so this skill survives a
  provider swap.
-->

## Inputs

Parse these from the prompt arguments you were invoked with:

- `issue-id` — the board work-item id for this slice.
- `branch` — the feature branch the wrapper already derived for you.
- `attempt` — the attempt counter (1-based); higher means a prior attempt was
  reaped/retried, so start from a clean worktree.
- `tdd-skill` — the skill to invoke for the implement-with-tests phase. **Invoke
  THIS skill name; do not hardcode `/tdd`.** Defaults to `/tdd` when unset.
- `qa-skill` — the skill to invoke for the quality-gate phase. **Invoke THIS
  skill name; do not hardcode `/qa`.** Defaults to `/qa` when unset.

## Status reporting

Report progress through squadra's status CLI (never edit `status.json` by
hand) so the supervisor's heartbeat/reap/finalize logic stays coherent:

```bash
# `squadra slice` if on PATH, else the module form (always available):
python -m squadra.status update --issue-id <id> --phase <phase> \
  [--pr-url <url>] [--add-worker <name>] [--parked-state <state>]
```

Update `phase`, `pr_url`, and the worker roster (`--add-worker`) as you move
through the lifecycle below. The wrapper owns the liveness heartbeat — you do
not need to stamp it.

## Lifecycle

Execute these phases in order. Stop and park on any unrecoverable error.

1. Verify the claim. Confirm this slice is genuinely yours (claimed for this
   `issue-id`/`branch`) before doing any work. If the claim does not hold,
   park `needs-decision` and exit rather than racing another runner.
2. Enter the worktree. The wrapper created the branch worktree under the
   configured `worktree_dir`; `cd` into it. All edits happen here, never in the
   primary checkout.
3. Write shared seams BEFORE fan-out. If you will parallelize work, first write
   the shared interfaces/types/contracts that the parallel tasks depend on, and
   commit them, so fanned-out work cannot diverge on the seam.
4. Run the tdd skill. Invoke the skill named by the `tdd-skill` argument to
   implement the slice test-first. Record any spawned workers with
   `--add-worker`.
5. Run the qa skill. Invoke the skill named by the `qa-skill` argument to run
   the quality gates (below) and address findings.
6. Open the PR, then park. Push the branch, open the PR, record its URL with
   `--pr-url`, then park with the matching fleet park state and a comment on the
   work item summarizing what shipped:
   - `awaiting-pr-approval` — work is complete, PR is open, awaiting human/CI.
   - `qa-ready` — implementation done, queued for the QA gate to run.
   - `needs-decision` — blocked on a human decision; explain what is needed.
   Set the park state via `--parked-state` and let squadra apply the matching
   `<tag_prefix><state>` board tag. Do NOT tag the board item directly.
7. Exit. Exit 0 once parked. The wrapper backstops any non-parked exit as
   `failed`.

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
     template come from squadra.toml). -->
