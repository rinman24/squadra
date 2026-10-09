# Settled terms

Rulings domain-modeling must not reopen on its own initiative. Match Term or Rejected within the same Context, case-insensitive. Reopen only on explicit user instruction; a reopened row is superseded when the term is settled again. Never delete.

| Term | Rejected | Context | Ruling | Status | Settled | Ref |
|---|---|---|---|---|---|---|
| Increment | Slice, Vertical slice, Infrastructure increment | squadra fleet | Use Increment for the fleet's unit of work; "slice" is reserved for Löwy's architectural unit (a subsystem), see ADR-0003 | settled | 2026-10-06 | session:2026-10-06 |
| Attempt | Run, Retry (noun) | squadra fleet | Use Attempt for one claim of an Increment and the work done under it, numbered from 1; "run" means the Runner's session; Retry stays a verb | settled | 2026-10-06 | session:2026-10-06 |
| Withdrawn | Cancelled, Dropped, Closed | squadra fleet | Use Withdrawn for the terminal Lifecycle bucket of an Increment that will never be delivered; reachable only before delivery, see ADR-0004 | settled | 2026-10-09 | claude-skills DB-D1 |
| Origin | Key, External ID | squadra fleet | Use Origin for the opaque reference an outside caller attaches when it queues an Increment; squadra stores it and never parses it, and it is not the Increment's identity | settled | 2026-10-09 | claude-skills DB-D1 |
