# Settled terms

Rulings domain-modeling must not reopen on its own initiative. Match Term or Rejected within the same Context, case-insensitive. Reopen only on explicit user instruction; a reopened row is superseded when the term is settled again. Never delete.

| Term | Rejected | Context | Ruling | Status | Settled | Ref |
|---|---|---|---|---|---|---|
| Increment | Slice, Vertical slice, Infrastructure increment | squadra fleet | Use Increment for the fleet's unit of work; "slice" is reserved for Löwy's architectural unit (a subsystem), see ADR-0003 | settled | 2026-10-06 | session:2026-10-06 |
| Attempt | Run, Retry (noun) | squadra fleet | Use Attempt for one claim of an Increment and the work done under it, numbered from 1; "run" means the Runner's session; Retry stays a verb | settled | 2026-10-06 | session:2026-10-06 |
