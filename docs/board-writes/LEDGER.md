# board-writes: ledger

Branch: `feat/board-writes`
Goal: squadra owns every board write design-to-board needs (claude-skills
DB-D1): `Lifecycle.WITHDRAWN`, then the verbs frozen in DB-D4,
`queue_increment(origin, parent, predecessors, title, body) -> item_id`,
`withdraw_increment(item_id)` (by Origin since SQ2c, N7), `increments_by_origin() -> {origin: Increment}`,
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
| SQ2c | Build Rich's N7 and N8 rulings: `withdraw_increment(origin)`; crash-safe `create_increment` (completeness on `OriginRecord`, the consistency rule, `incomplete_item`, `increments_by_origin` omits incomplete items); Eric names the two new terms (`handoffs/SQ2c-build-n7-n8.md`) | A (rest) | SQ2b | done (PR #48); claude-skills DB-D1/DB-D4 reopening still owed |
| SQ3 | Fake provider implementing the two primitives (`create_increment`, `items_with_origin`) and the read half, registered in `PROVIDERS`; run the `BoardAccess` and increment contract suites against it | B | SQ2, SQ2b, SQ2c | done (PR #49); N16, N17 |
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
- N7 (SQ2), **ruled by Rich, 2026-10-09: option (iii), withdraw by Origin; built by SQ2c** (see N11). Put to him by SQ2b (board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`): `withdraw_increment` accepts any in-scope QUEUED item,
  including one queued by hand with no Origin. Restricting it to Origin-bearing
  items costs one board-wide read per withdraw.
  Juval: option (iii), `withdraw_increment(origin)`, CLI `--origin`. It changes
  the signature DB-D1 and DB-D4 froze, so SQ2b did not settle it (handoff
  rule). If Rich takes it, record it as reopening DB-D1/DB-D4 in claude-skills;
  Eric names the not-found refusal; the verb re-reads `item_state` just
  before `set_state`.
- N8 (SQ2), **ruled by Rich, 2026-10-09: Juval's design; contract built by SQ2c (see N12–N15), fault-injecting fake in SQ3, GitHub in SQ5**. Put to him by SQ2b (board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`): `create_increment` is one business write. If GitHub needs
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
- N11 (SQ2c): `withdraw_increment(origin)` follows Juval's steps 1–5 and
  returns the item id (the CLI's `{"item_id": …}`). An Origin carried only by
  a partial item counts as absent: `UnknownOriginError`, nothing written, the
  same as `increments_by_origin`, which does not show it either. The message
  names the partial item. A hand-queued item with no Origin is out of reach.
- N12 (SQ2c): completeness is `OriginRecord.lifecycle: Lifecycle | None`, `None`
  for a partial item, read through the `OriginRecord.partial` property, not a
  separate flag. A flag would still need `lifecycle` to hold a bucket for an
  item in none (every bucket is wrong, per Juval's N8), so two fields could
  disagree. Pyright strict now makes every reader handle `None`;
  `as_increment` raises on it. Eric agreed.
- N13 (SQ2c): Eric named the terms (board-knowledge
  `sessions/2026-10-09-eric-increment-verb-partial-item.md`):
  `UnknownOriginError` (not `…Refused`: no rule forbids the act, there is no
  Increment to act on; it sits beside `DuplicateOriginError`), and **partial**
  (`OriginRecord.partial`, `partial_item=`, `seed_partial`). Not "incomplete":
  "complete" already means delivered here (`completed_pr_url`, FINALIZING), so
  "incomplete item" reads as "undelivered". Partial qualifies an item, never
  an Increment. Neither term enters `GLOSSARY.md`: both belong to the
  primitive's model, not the planner's language. "Partial item" is defined in
  ADR-0005 decision 1.
- N14 (SQ2c): `check_queue_finishes` compares title and body exactly, because
  `OriginRecord` cannot show them absent. So obligation 1 reads "the Origin,
  title and body go on with the first call". An adapter that wrote either
  later would make every finishing retry refuse: loud, not wrong. The parent
  is compared only once set, and the predecessors present must be a subset.
  `item_state` on a partial item raises `BoardValidationError` (no new class),
  so a tick that reaches one through a predecessor link fails loudly. Both
  fakes refuse `partial_item` naming anything but a partial item that carries
  the request's Origin, as a guard against a caller bug; SQ5's adapter should
  do the same.
- N15 (SQ2c), **for SQ5**: the GitHub adapter must prove obligations 1–4
  (ADR-0005 decision 1) on GitHub, with N14's title and body on the first
  call. If GitHub has no structural "in no bucket yet" that `items_with_origin`
  can still see, the fallback stays in the adapter: it writes its own
  completion marker last and filters its bucket reads on it (Juval, "Where the
  work lands"). The invariant and its owner do not change.

- N16 (SQ3): `provider = "fake"` is `squadra.fake_board.JsonFileBoard`, one
  JSON file at `<FLEET_HOME>/.squadra/fake-board.json`. No config key: the
  file goes wherever `FLEET_HOME` goes, beside the `squadra.toml` that names
  the provider, and `SquadraConfig` grows nothing provider-specific. Adding a
  key later is additive, so no ADR. A missing file is an empty board whose
  states are the configured ones; a file's own `states` list is what
  `validate_config` checks `[board.states]` against both ways (ADR-0004 rule
  3). A test seeds it by writing the file (format in the module docstring;
  every key optional, `state: null` is a partial item) or through the
  seeding methods (`add`, `seed_pr`, `seed_origin`, `seed_partial`,
  `arm_create_fault`). Two concurrent CLI calls: every write is one
  read-modify-write under an exclusive `flock` on a sidecar `.lock`, saved
  by temp file and `os.replace`; reads take no lock and see one whole
  version. The lock keeps the file whole, not the verbs atomic: two
  concurrent `queue_increment` calls with one new Origin can both create,
  and every later call then raises `DuplicateOriginError`, loud, as on any
  provider without a compare-and-set (cf. N10). POSIX only (`fcntl`), as the
  fleet already is. A malformed file raises `BoardValidationError` naming it.
- N17 (SQ3): the fake's `create_increment` is k = 3 + len(predecessors)
  separate locked writes: `create` (Origin, title, body, in no bucket),
  `parent`, one per predecessor, `commit` (QUEUED). Finishing a partial item
  writes only what is missing, then commits. `fail_create_after_step: j` in
  the file is a one-shot fault (so it crosses processes, for
  design-to-board's F tests): the next create stops after write j and raises
  `InjectedCrashError` (exit 1 in SQ4: a provider failure); j > k is refused
  before any write. Juval's tests 1–5 run for every j < k
  (`tests/contract/test_crash_safe_create_contract.py`, and test 1's tick
  half end to end through `squadra tick --dry-run` on `provider = "fake"`
  under both scopes). One case does not hold for every j, by design: after a
  crash at j = 1 nothing on the board names a parent, so a different-parent
  retry finishes the item under the new parent instead of being refused
  (N14: the parent is compared only once set). Test 4 runs for j ≥ 2, and a
  test pins the j = 1 behaviour. j = k (the commit lands, the response is
  lost) is a plain A3 retry and returns the item. The fake is also the third
  shape of the `board` and `fake_board` contract fixtures; tests record
  state writes through `tests.helpers.board_fakes.RecordingFileBoard`, so
  the production class keeps no recording.

## Session log

| Session | Date | Unit | Outcome | Handoff written |
|---|---|---|---|---|
| SQ1 | 2026-10-09 | SQ1 | `Lifecycle.WITHDRAWN` (terminal), `check_transition`, `predecessor-withdrawn` tick state, unmapped states fail `validate_config` and `item_state`, fakes + `[board.states].withdrawn`, ADR-0004, glossary Origin + Withdrawn. ruff, pyright, 399 tests green | `handoffs/SQ2-verb-contract.md` |
| SQ2 | 2026-10-09 | SQ2 | Gate checked (DB-D2–DB-D5 ruled, PR #43 merged). `IncrementBoard` verbs, `BoardAccess.create_increment` / `items_with_origin`, engine rules (scope, A2, A3), DB-D2 in `check_transition` and ADR-0004, ADR-0005, `verb-contract.md` with the SQ4 CLI surface. ruff, pyright, 462 tests green | `handoffs/SQ3-fake-provider.md` |
| SQ2b | 2026-10-09 | SQ2b | Juval consulted on N6–N8 (method). N6 settled: both refusals kept, each naming its way out; ADR-0005 decision 3, `verb-contract.md`, contract tests (Juval's 9, 10). N7 (withdraw by Origin) and N8 (crash-safe create) put to Rich: both change DB-D rulings. N10 opened. ruff, pyright, tests green | `handoffs/SQ3-fake-provider.md` (gate: SQ2b merged + Rich's N7/N8 ruling) |
| SQ2b (ruling) | 2026-10-09 | SQ2b | Rich ruled N7 (withdraw by Origin) and N8 (Juval's crash-safe create), both reopening DB-D1/DB-D4; recorded in the session file's Choice, ADR-0005 and `verb-contract.md`. Build deferred to SQ2c (session budget) | `handoffs/SQ2c-build-n7-n8.md` |
| SQ2c | 2026-10-09 | SQ2c | Gate checked (PR #47 merged). Eric named `UnknownOriginError` and *partial* (N13). `withdraw_increment(origin)` (N11); crash-safe create contract: `OriginRecord.lifecycle` `None` for a partial item (N12), `create_increment(request, partial_item=None)` with the four obligations, `check_queue_finishes` (N14), `increments_by_origin` omits partial items; both fakes with `seed_partial`; Juval's tests 2–8 plus a subset-finish retry. ADR-0005 decisions 1–2 amended, `verb-contract.md` rewritten. claude-skills DB-D1/DB-D4 still owed. ruff, pyright, tests green | `handoffs/SQ3-fake-provider.md` (gate: SQ2c's PR merged) |
| SQ3 | 2026-10-09 | SQ3 | Gate checked (PR #48 merged). `provider = "fake"` registered: `JsonFileBoard` over `<FLEET_HOME>/.squadra/fake-board.json`, flock + atomic replace (N16); create as k locked writes with a one-shot, file-carried fault (N17). Third shape in the `board`/`fake_board` contract fixtures; Juval's tests 1–5 for every j < k, test 1 also end to end through `tick --dry-run` under both scopes; test 4 holds for j ≥ 2 only (N14, pinned). README provider row. ruff, pyright, 617 tests green | `handoffs/SQ4-board-cli.md` |
