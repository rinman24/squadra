# Merged-branch cleanup skill (consumer-owned template)

<!--
  SCAFFOLDED BY `squadra init`. This is YOUR copy — edit it freely.

  Wire this file in as the skill named by `[pipeline].cleanup_skill` in
  squadra.toml (default `/cleanup-merged-branches`). The supervisor's finalize
  pass invokes it HEADLESSLY and NON-INTERACTIVELY, once per candidate branch,
  to retire branches whose work has already landed on the base branch.

  PROVIDER-AGNOSTIC: this skill reasons about git branches and the base branch
  (`[board].base_branch`), not about the board provider. Do not call any
  provider CLI here.
-->

## Contract

You are invoked once per candidate branch. Your job: decide whether the branch's
work is already present on the base branch and, if so, delete the branch — with
zero prompts.

- Non-interactive. Never wait for input. No confirmation prompts, no pager.
  Assume `--no-pager` and pre-answer every yes/no.
- Idempotent. Re-running on an already-deleted branch is a no-op success.
- Conservative. When in doubt, leave the branch and report why. Never delete a
  branch whose work is NOT proven to have landed.

## Patch-equivalence detection

A branch is safe to delete when its changes are already on the base branch,
even if it was squash- or rebase-merged (so the merge commit / SHAs differ).
Detect equivalence, not just ancestry:

1. Fast path — ancestry: if every commit on the branch is reachable from the
   base branch, it is merged. Delete.
2. Squash/rebase path — patch-equivalence: compare the branch's net diff
   against the base branch (e.g. `git cherry`, or diff the branch's merge-base
   range against base). If the net change is already present, treat as merged.
3. Otherwise: not merged. Leave it and report the divergence.

<!-- FILL IN: the exact detection commands you trust for YOUR history model
     (merge vs. squash vs. rebase). Examples — adapt to your conventions:
       git fetch --prune
       git branch --merged <base_branch>
       git cherry <base_branch> <branch>          # '+' = unmerged commits
       git log --oneline <base_branch>..<branch>  # empty => contained
-->

## Sweep

```bash
# EXAMPLE — replace with your project's safe, non-interactive sweep:
# git fetch --prune --no-tags
# for each candidate branch proven patch-equivalent to <base_branch>:
#   git branch -D <branch>          # local
#   git push origin --delete <branch>  # remote, if you sweep remotes
```

## Reporting

Print a one-line outcome per branch (`deleted <branch>` / `kept <branch>: <why>`)
so the finalize pass log is auditable. Exit 0 on success (including no-op);
non-zero only on an unexpected error, never merely because a branch was kept.
