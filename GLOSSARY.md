# squadra fleet

squadra drives an unattended Claude implementation fleet against a target repository;
the board is the source of truth. At the board adapter, an Increment is represented by
one Issue (ADO: work item); its Tasks are the Issue's child items.

## Language

**Increment**:
One board item taken through the fleet as a single unit of delivery. Its identity is the board item: every attempt, and every claim, branch and PR an attempt makes, belongs to the same Increment. It is delivered when one of its PRs merges.
- _Vertical_: delivers a behaviour observable end to end.
- _Foundation_: delivers no behaviour of its own; exists so that the later increments it names can. Not vertical, by definition.

An increment with neither attribute is still an increment; the kind of change it makes is carried by its commit type, not by a name.
_Avoid_: Slice, vertical slice, infrastructure increment

**Attempt**:
One claim of an Increment and all the work done under it: its branch, worktree, sandbox and, if any, PR. Its identity is the Increment plus its attempt number, counted from 1 and bounded by the attempt budget. A failed Attempt requeues the Increment, and the next claim starts the next Attempt from a fresh worktree.
_Avoid_: Run (a run is the Runner's session), retry as a noun (Retry stays a verb, as in RetryIncrement)
