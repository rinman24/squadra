# board-writes: ledger

Branch: `feat/board-writes`
Goal: squadra owns every board write design-to-board needs (claude-skills
DB-D1): `Lifecycle.WITHDRAWN`, then `queue_increment(origin, parent,
predecessors, title, body) -> item_id`, `withdraw_increment(item_id)`,
`increments_by_origin(parent) -> {origin: (item_id, Lifecycle)}`, behind
CLI subcommands, with a registered fake provider, then the GitHub adapter.

Decisions come from claude-skills `docs/design-to-board/LEDGER.md` (DB-D1
and later DB-D<n>); cite them by ID, don't copy them. That ledger is
read-only from here. This file is the source of truth for squadra's part.

## Session budget

Under ~100K tokens per session, hard ceiling 120K, one unit per session.

## Work items

| ID | Item | DB-D1 activity | Depends on | Status |
|---|---|---|---|---|
| SQ1 | `Lifecycle.WITHDRAWN`, transitions, second blocked reason, unmapped states fail `validate_config`, glossary rows Origin and Withdrawn | A (part) | DB-D1 | done (PR open) |
| SQ2 | Verb contract on `BoardAccess` + CLI surface; Origin stored opaquely; ACTIVE → WITHDRAWN rule | A (rest) | SQ1; design-to-board S1b rulings on DBQ2, DBQ3, DBQ4, DBQ7 | blocked |
| SQ3 | Fake provider implementing the verbs, registered in `PROVIDERS`; contract tests | B | SQ2 | todo |
| SQ4 | CLI subcommands as Clients; orchestration applies claim scope and the withdraw-while-active rule | C | SQ2, SQ3 | todo |
| SQ5 | GitHub adapter, reads and writes; `[[boards]]` and `in_claim_scope` (WSQ1) | D | SQ2; after design-to-board F per DB-D1 order | todo |

## Notes from squadra sessions

Choices made while building, inside what DB-D1 settles. Not decisions in the
DB-D sense; ADR-0004 records the model change.

- N1 (SQ1): `[board.states].withdrawn` is optional. ADO-Basic has no withdrawn
  state, so a required key would break every existing config. Consequence for
  SQ2: `withdraw_increment` on a board with no withdrawn state raises
  (`set_state` into an unmapped bucket raises `BoardValidationError`).
- N2 (SQ1): `squadra.engines.check_transition(current, target)` is the rule
  `withdraw_increment` must call. ACTIVE → WITHDRAWN raises
  `TransitionRefusedError` until DBQ3 is ruled; SQ2 replaces that branch with the
  ruling. WITHDRAWN → WITHDRAWN is a no-op, so a repeated withdraw is idempotent.
- N3 (SQ1): the tick does not query the WITHDRAWN bucket, so withdrawn items do
  not appear in `states={…}`; their successors appear as
  `predecessor-withdrawn`. If design-to-board's acceptance test (DB-D1 step G)
  needs withdrawn items listed, that is a tick change, not an adapter one.
- N4 (SQ1): one native state name may map to one bucket only (`ConfigError`
  otherwise); `validate_config` fails on any Issue state the map leaves out.

## Session log

| Session | Date | Unit | Outcome | Handoff written |
|---|---|---|---|---|
| SQ1 | 2026-10-09 | SQ1 | `Lifecycle.WITHDRAWN` (terminal), `check_transition`, `predecessor-withdrawn` tick state, unmapped states fail `validate_config` and `item_state`, fakes + `[board.states].withdrawn`, ADR-0004, glossary Origin + Withdrawn. ruff, pyright, 399 tests green | `handoffs/SQ2-verb-contract.md` |
