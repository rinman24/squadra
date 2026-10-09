# board-writes: ledger

Branch: `feat/board-writes`
Goal: squadra owns every board write design-to-board needs (claude-skills
DB-D1): `Lifecycle.WITHDRAWN`, then the verbs frozen in DB-D4,
`queue_increment(origin, parent, predecessors, title, body) -> item_id`,
`withdraw_increment(item_id)`, `increments_by_origin() -> {origin: Increment}`,
behind CLI subcommands, with a registered fake provider, then the GitHub
adapter. Contract: [`verb-contract.md`](verb-contract.md).

Decisions come from claude-skills `docs/design-to-board/LEDGER.md` (DB-D1
and later DB-D<n>); cite them by ID, don't copy them. That ledger is
read-only from here. This file is the source of truth for squadra's part.

## Session budget

Under ~100K tokens per session, hard ceiling 120K, one unit per session.

## Work items

| ID | Item | DB-D1 activity | Depends on | Status |
|---|---|---|---|---|
| SQ1 | `Lifecycle.WITHDRAWN`, transitions, second blocked reason, unmapped states fail `validate_config`, glossary rows Origin and Withdrawn | A (part) | DB-D1 | done (PR #43, merged) |
| SQ2 | Verb contract (`IncrementBoard` + two `BoardAccess` primitives, ADR-0005) + CLI surface; Origin stored opaquely; ACTIVE → WITHDRAWN rule (DB-D2) | A (rest) | SQ1; DB-D2–DB-D5 | done (PR #44, merged) |
| SQ2b | Consult Juval on N6–N8, settle them on Rich's delegation, build what the settlement changes (`handoffs/SQ2b-settle-n6-n8.md`) | A (rest) | SQ2 | done (PR #46, merged); N7, N8 ruled by Rich, built in SQ2c |
| SQ2c | Build Rich's N7 and N8 rulings: `withdraw_increment(origin)`; crash-safe `create_increment` (completeness on `OriginRecord`, the consistency rule, `incomplete_item`, `increments_by_origin` omits incomplete items); Eric names the two new terms (`handoffs/SQ2c-build-n7-n8.md`) | A (rest) | SQ2b | todo |
| SQ3 | Fake provider implementing the two primitives (`create_increment`, `items_with_origin`) and the read half, registered in `PROVIDERS`; run the `BoardAccess` and increment contract suites against it | B | SQ2, SQ2b, SQ2c | todo |
| SQ4 | `squadra board {queue,withdraw,origins}` as Clients over `IncrementBoard`, per `verb-contract.md` (the rules already live in `IncrementBoard`) | C | SQ2, SQ3 | todo |
| SQ5 | GitHub adapter, reads and writes; `[[boards]]` and `in_claim_scope` (WSQ1) | D | SQ2; after design-to-board F per DB-D1 order | todo |

## Notes from squadra sessions

Choices made while building, inside what DB-D1 settles. Not decisions in the
DB-D sense; ADR-0004 and ADR-0005 record the model and contract changes.

- N1 (SQ1): `[board.states].withdrawn` is optional. ADO-Basic has no withdrawn
  state, so a required key would break every existing config. Consequence for
  SQ2: `withdraw_increment` on a board with no withdrawn state raises
  (`set_state` into an unmapped bucket raises `BoardValidationError`).
- N2 (SQ1): `squadra.engines.check_transition(current, target)` is the rule
  `withdraw_increment` must call. WITHDRAWN → WITHDRAWN is a no-op, so a
  repeated withdraw is idempotent. SQ2 kept ACTIVE → WITHDRAWN refused, now as
  the DB-D2 ruling (no cancel path), and updated ADR-0004 decision 2.
- N3 (SQ1): the tick does not query the WITHDRAWN bucket, so withdrawn items do
  not appear in `states={…}`; their successors appear as
  `predecessor-withdrawn`. If design-to-board's acceptance test (DB-D1 step G)
  needs withdrawn items listed, that is a tick change, not an adapter one.
- N4 (SQ1): one native state name may map to one bucket only (`ConfigError`
  otherwise); `validate_config` fails on any Issue state the map leaves out.
- N5 (SQ2): the handoff said "`BoardAccess` gains the three verbs". SQ2 split
  them instead (ADR-0005): `BoardAccess` gains two raw primitives,
  `create_increment(IncrementRequest)` and `items_with_origin()` (unkeyed, so
  duplicates stay visible); `squadra.increments.IncrementBoard` holds the three
  verbs and applies every rule once. DB-D1 A2 puts the rules in squadra's
  orchestration, and the tick, the query and the writes now share one scope
  predicate, `engines.parent_in_claim_scope`. The ADO adapter raises
  `NotImplementedError` for both primitives.
- N6 (SQ2), **settled by SQ2b** (board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`): two fail-closed narrowings beyond the rulings:
  `queue_increment` refuses an Origin that is on the board as WITHDRAWN
  (glossary: never reused) instead of returning it, and `withdraw_increment`
  refuses an item outside the claim scope (Juval case 5: write nothing).
  Settlement: keep both (Juval). Each refusal now names its way out: a new
  Origin; `squadra stop`, restore scope, withdraw again, `squadra start`
  (restoring scope first lets the next tick claim the item). ADR-0005
  decision 3 amended; contract and signatures unchanged.
- N7 (SQ2), **ruled by Rich, 2026-10-09: option (iii), withdraw by Origin; built in SQ2c**. Put to him by SQ2b (board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`): `withdraw_increment` accepts any in-scope QUEUED item,
  including one queued by hand with no Origin. Restricting it to Origin-bearing
  items costs one board-wide read per withdraw.
  Juval: option (iii), `withdraw_increment(origin)`, CLI `--origin`. It changes
  the signature DB-D1 and DB-D4 froze, so SQ2b did not settle it (handoff
  rule). If Rich takes it, record it as reopening DB-D1/DB-D4 in claude-skills;
  Eric names the not-found refusal; the verb re-reads `item_state` just
  before `set_state`.
- N8 (SQ2), **ruled by Rich, 2026-10-09: Juval's design; contract built in SQ2c, fault-injecting fake in SQ3, GitHub in SQ5**. Put to him by SQ2b (board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`): `create_increment` is one business write. If GitHub needs
  several calls (create issue, sub-issue link, dependencies, project status),
  the adapter must order them so no tick can claim the item before its links
  exist. Under `"whole-board"` an unlinked QUEUED issue is claimable, so links
  go on before the item enters the QUEUED status. A crash mid-create leaves
  the item with its Origin and missing links, and A3 then refuses the retry
  for good. Juval: the guarantee stays in the adapter as four obligations
  (Origin on the first call; the item in no bucket until one last write puts
  it QUEUED; visible to `items_with_origin` meanwhile), and a retry finishes
  a partial item whose fields agree with the request (predecessors a subset),
  refusing only on real disagreement; `OriginRecord` gains a completeness
  field, `create_increment` an `incomplete_item` parameter, and
  `increments_by_origin` omits incomplete items. That writes where A3 refuses
  today and changes DB-D4's "every Origin", so SQ2b did not settle it. If Rich
  takes it, it is built (contract, engines, primitive docstring, contract
  tests) before SQ3, whose fake then fails `create_increment` on demand
  mid-way (Juval's tests 1–5).
- N9 (SQ2): Juval's discriminating cases 2, 4, 5, 6 and 7 are contract tests in
  `tests/contract/test_increment_contract.py` (both fakes). Cases 1 and 3 are
  design-to-board behaviour with no squadra-side assertion.
- N10 (SQ2b, open; raised by Juval in board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`):
  `withdraw_increment` reads the state, then writes it, and the ticker claims
  in its own process, so a claim can land in between: WITHDRAWN on an item a
  runner is working, the ACTIVE withdrawal DB-D2 refuses. SQ2 had the same
  window. Closing it needs a compare-and-set write from the provider (an SQ5
  fact) or a tick rule for a live sandbox on a WITHDRAWN item. Does not block
  SQ3.

## Session log

| Session | Date | Unit | Outcome | Handoff written |
|---|---|---|---|---|
| SQ1 | 2026-10-09 | SQ1 | `Lifecycle.WITHDRAWN` (terminal), `check_transition`, `predecessor-withdrawn` tick state, unmapped states fail `validate_config` and `item_state`, fakes + `[board.states].withdrawn`, ADR-0004, glossary Origin + Withdrawn. ruff, pyright, 399 tests green | `handoffs/SQ2-verb-contract.md` |
| SQ2 | 2026-10-09 | SQ2 | Gate checked (DB-D2–DB-D5 ruled, PR #43 merged). `IncrementBoard` verbs, `BoardAccess.create_increment` / `items_with_origin`, engine rules (scope, A2, A3), DB-D2 in `check_transition` and ADR-0004, ADR-0005, `verb-contract.md` with the SQ4 CLI surface. ruff, pyright, 462 tests green | `handoffs/SQ3-fake-provider.md` |
| SQ2b | 2026-10-09 | SQ2b | Juval consulted on N6–N8 (method). N6 settled: both refusals kept, each naming its way out; ADR-0005 decision 3, `verb-contract.md`, contract tests (Juval's 9, 10). N7 (withdraw by Origin) and N8 (crash-safe create) put to Rich: both change DB-D rulings. N10 opened. ruff, pyright, tests green | `handoffs/SQ3-fake-provider.md` (gate: SQ2b merged + Rich's N7/N8 ruling) |
| SQ2b (ruling) | 2026-10-09 | SQ2b | Rich ruled N7 (withdraw by Origin) and N8 (Juval's crash-safe create), both reopening DB-D1/DB-D4; recorded in the session file's Choice, ADR-0005 and `verb-contract.md`. Build deferred to SQ2c (session budget) | `handoffs/SQ2c-build-n7-n8.md` |
