# squadra fleet

squadra drives unattended Claude implementation fleets, each against one target
repository and one board; the board is the source of truth. At the board adapter, an Increment is represented by
one Issue (ADO: work item); its Tasks are the Issue's child items.

## Language

**Fleet**:
The named claimant that takes Increments from one board and delivers them into one repository. Its identity is its name (`[fleet] name`): the name marks its claims on the board, through the tag prefix derived from it, and its resources on the host (units, sandbox projects, fleet root). A fleet with a different name is a different fleet, so the name never changes while any of its Increments is in flight. One `squadra.toml` configures one fleet; a second board is a second fleet. A host running one or more fleets is a fleet host.
_Avoid_: Instance, squadra instance (a deployment word), deployment, board (the board is what a fleet claims from, never the fleet)

**Increment**:
One board item taken through the fleet as a single unit of delivery. Its identity is the board item: every attempt, and every claim, branch and PR an attempt makes, belongs to the same Increment. It is delivered when one of its PRs merges.
- _Vertical_: delivers a behaviour observable end to end.
- _Foundation_: delivers no behaviour of its own; exists so that the later increments it names can. Not vertical, by definition.

An increment with neither attribute is still an increment; the kind of change it makes is carried by its commit type, not by a name.
_Avoid_: Slice, vertical slice, infrastructure increment

**Attempt**:
One claim of an Increment and all the work done under it: its branch, worktree, sandbox and, if any, PR. Its identity is the Increment plus its attempt number, counted from 1 and bounded by the attempt budget. A failed Attempt requeues the Increment, and the next claim starts the next Attempt from a fresh worktree. An Attempt also ends when its Increment is withdrawn in flight. The fleet relinquishes the claim, and nothing is requeued.
_Avoid_: Run (a run is the Runner's session), retry as a noun (Retry stays a verb, as in RetryIncrement)

**Withdrawn**:
The terminal state of an Increment that will never be delivered: not claimable, not counted as done, reachable only before delivery. See ADR-0004.
_Avoid_: Cancelled, dropped, closed (a provider's word: on GitHub a closed issue can be done or withdrawn)

**Relinquish**:
What the fleet does when an Increment it holds in flight (carries the fleet's claim) is withdrawn by someone else: it stops the sandbox, archives the worktree, parks `status.json` as `withdrawn`, removes its tags and drops the claim last. It writes no lifecycle state, and it leaves the branch and any PR for a human. The Increment stays Withdrawn and is not requeued. The Attempt's number is kept. See ADR-0004.
_Avoid_: Release (squadra and its target repos release software), roll back and reap (both requeue), abandon, drop

**Origin**:
The opaque reference an outside caller attaches when it queues an Increment. It is unique on the board, never reused, and never parsed by squadra. It is not the Increment's identity.
_Avoid_: Key, external ID

**Partial item**:
A board item a crash left mid-create: it carries its Origin but is in no Lifecycle bucket, so it is not an Increment and the tick never sees it. Queuing its Origin again finishes it when the request agrees with what is on the item, and is refused otherwise. See ADR-0005.
_Avoid_: Incomplete item (complete means delivered here), partial increment (partial qualifies item, never Increment)
