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
| SQ2c | Build Rich's N7 and N8 rulings: `withdraw_increment(origin)`; crash-safe `create_increment` (completeness on `OriginRecord`, the consistency rule, `incomplete_item`, `increments_by_origin` omits incomplete items); Eric names the two new terms (`handoffs/SQ2c-build-n7-n8.md`) | A (rest) | SQ2b | done (PR #48); recorded in claude-skills as DB-D10, amending DB-D1/DB-D4 (claude-skills PR #14) |
| SQ3 | Fake provider implementing the two primitives (`create_increment`, `items_with_origin`) and the read half, registered in `PROVIDERS`; run the `BoardAccess` and increment contract suites against it | B | SQ2, SQ2b, SQ2c | done (PR #49); N16, N17 |
| SQ4 | `squadra board {queue,withdraw,origins}` as Clients over `IncrementBoard`, per `verb-contract.md` (the rules already live in `IncrementBoard`) | C | SQ2, SQ3 | done (PR #50); N18, N19 |
| SQ5 | GitHub adapter, reads and writes; `[[boards]]` and `in_claim_scope` (WSQ1). Split by the SQ5 session into SQ5a–SQ5c below | D | SQ2; after design-to-board F per DB-D1 order (claude-skills DB4, PR #16, merged) | split (PR #51, merged); see SQ5a–SQ5c |
| SQ5a | `provider = "github"` registered: transport (the unit's choice, a ledger note), `[board.github]` config, the native-state model (N20), the read half (`items_in_state`, `item_state`, `item_links`, `completed_pr_url`), `validate_config` against a real board, and the tick's writes (`set_state` into Status buckets, labels as tags, Markdown comments). The `board` contract suite runs against it offline over a stubbed transport; `create_increment` / `items_with_origin` raise `NotImplementedError` as ADO's do. Cut line if long: the tick's writes to SQ5b | D | SQ5 split; N20 ruled by Rich (after Juval, Eric) | done (PR #52); ADR-0006, N21–N23. Cut taken in part: the adapter's writes are in, the tick's half of N20 ruling 6 (report a refused requeue per item, go on) moved to SQ5b |
| SQ5b | `create_increment` (with `partial_item`) and `items_with_origin` on GitHub, proving obligations 1–4 (N15; N14's title and body on the first call), `set_state` into WITHDRAWN (closed, not planned, DB-D2); the `increment` and crash-safe contract suites against the stubbed transport with a fault after each write j < k. From SQ5a's cut: the tick reports a refused requeue of a closed issue per item and goes on (N20 ruling 6), with the contract case "an ACTIVE item closed between ticks with each reason is neither reopened nor ends the next tick, and is reported"; the first live run of the adapter's writes on the sandbox (N23). Unblocks claude-skills DB5 (G) | D | SQ5a | done (PR #53); N24. Cut taken: the tick's refused-requeue report and the live sandbox run moved to SQ5c |
| SQ5c | From SQ5b's cut, first: the tick reports a refused requeue of a closed issue per item and goes on (N20 ruling 6; settle N24's open question before building), and the first live run of the adapter's writes on the sandbox (N23, N24's unverified facts). Then WSQ1's deferred parts: `[[boards]]` with `claim_scope` per entry (the check fires wherever a board is added), adapter-owned `in_claim_scope(item_id)` (the supervisor stops reading parent links for scope), rename `seams.ado`; N10's compare-and-set if GitHub offers one, else record that it doesn't. Not needed by DB5 | D | SQ5b | done (PR #54); N25, N26. Cut taken: the release rule (N25), `[[boards]]`, `in_claim_scope`, the rename and N10 to SQ5d |
| SQ5d | From SQ5c's cut: relinquishing a fleet-claimed WITHDRAWN item and the hand-DONE sandbox stop (N25, once Rich rules on Juval's answer and Eric names the state and action); then WSQ1's deferred parts: `[[boards]]` with `claim_scope` per entry, adapter-owned `in_claim_scope(item_id)`, rename `seams.ado`; N10 closes by the relinquish rule if ruled, else GitHub's compare-and-set is checked | D | SQ5c; Rich's N25 choice | done (PR #55); N27. N10 closed. Cut taken: `[[boards]]`, `in_claim_scope` and the rename to SQ5f (after SQ5e) |
| SQ5e | claude-skills DB-D12 (Rich's Choice in board-knowledge `sessions/2026-10-27-juval-first-run-parent-scope.md`): the read verb `squadra board scope --parent ID` → `{"parent": N, "in_claim_scope": bool}`; exit 0 either way, 2 for config, 1 for provider; `validate_config()` first; never writes. Behind the one predicate `queue_increment`'s `ClaimScopeRefusedError` and `Increment.in_claim_scope` use (today `engines.parent_in_claim_scope`). Agreement test: scope ∈ {whole-board, parents [1]} × parent ∈ {1, 2}: `scope` says in scope exactly when `queue` is not refused for scope, and leaves the board unchanged. Added to `verb-contract.md`. Not wanted: exposing `claim_scope` itself, scope in `origins`, a `queue --check` flag. Requested by Rich mid-SQ5d; ordered before `[[boards]]` because design-to-board's first-run check waits on it | D | SQ5d | done (PR #57); N30 |
| SQ5f | From SQ5d's cut, WSQ1's deferred parts: `[[boards]]` with `claim_scope` per entry (the check fires wherever a board is added), adapter-owned `in_claim_scope(item_id)` (the supervisor stops reading parent links for scope), rename `seams.ado`. Keep SQ5e's `scope` verb, `queue_increment`'s scope refusal and `Increment.in_claim_scope` on one predicate through the rework | D | SQ5e | done (PR #64); N34. Cut taken: `[[boards]]` to SQ5g, its shape put to Rich (N34) |
| SQ5f2 | Before Rich rules N34: `/ask-juval` blind on the `[[boards]]` shape (N34's two facts, both options, the ruled constraints, without SQ5f's recommendation), then `/ask-eric` on the names if they move (Increment identity and Origin uniqueness across boards; "Fleet"). Recorded in N34 for Rich's ruling; no code | D | SQ5f | done (PR #65); N34. Juval: (A′), against N34's ledger-only (A) |
| SQ5g | From SQ5f's cut: `[[boards]]`, ruled (A′) by Rich (N34): one fleet per repository, no `[[boards]]`. Build a required `[fleet] name` (no default, no env or flag layer, scaffolded empty) with every host-global name derived from it (`squadra-<name>.service`/`.timer` with `install-units` refusing to overwrite a unit for another `FLEET_HOME`, compose project `squadra-<name>-increment-<id>`, `tag_prefix` defaulting to `<name>:`, `fleetctl.sh`'s tmux names); the name stamped into `fleet_root`, the tick refusing a differing stamp, or no stamp with increment dirs and no explicit `tag_prefix`, while increment dirs exist; GitHub `validate_config()` fails closed unless the board's repository is `origin`; README Scoping; Eric's Fleet glossary entry, settled row and header | D | SQ5f2; Rich's N34 ruling (2026-10-28) | done (PR #67); N35. No cut: all five items and both of Eric's additions built |
| SQ5g2 | From Rich's N34 ruling: `install-units` refuses a second fleet on the host whose board repository matches an existing fleet's (Juval's first branch: issue numbers are per repository, so two fleets on one repository would be two fleets on one issue). No `{fleet}` in the branch template | D | SQ5g | done (PR pending); N36. No cut: all three items built (the repository refusal, the installed-name check, the fleet-host acceptance and upgrade steps) |
| SQ6 | claude-skills DB7 finding (i): `squadra init --check` validated with `--provider`'s default `ado`, not the toml's provider. `--provider` now defaults to `None`; the scaffold falls back to `ado`, and the check passes `None` so `load_config`'s precedence (toml < env < flag) holds. Tests: toml wins with no flag or env, `FLEET_PROVIDER` beats the toml, the flag beats both | — (DB7) | — | done (PR #56); N29 |
| SQ7 | claude-skills DB7 finding (iii), Juval's option D (board-knowledge `sessions/2026-10-27-juval-squadra-init-preflight-findings.md`): host-side git credentials are ADO-shaped (`repo._CREDENTIAL_HELPER` supplies `$AZURE_DEVOPS_EXT_PAT`), and the claim's own fetch (`worktree.py:107`) bypasses that helper, so the preflight and the claim auth differently. Put credential acquisition for host-side git ops behind one boundary, resolved from the target remote's host rather than the board provider (an ADO board over a GitHub repo is valid). The probe and every host-side clone, fetch and push go through it, so the "exact auth path" claim is true. Keep it fail-closed. The rejection message names the credential actually used. Rename the `_ado_*` probe symbols and sweep ADO assumptions outside `board.py` (e.g. the `scaffold.py` comment on the provider PAT). Not wanted: relabelling the log line, skipping the probe when the provider isn't ADO (silent broadening on the claim path), folding into SQ5f. Latent on Rich's Mac (SSH origin, N29); must land before the first live GitHub tick over an HTTPS origin | — (DB7) | SQ5f, for order only: no design dependency, but both edit `TickSeams` on this branch | todo; `handoffs/SQ7-host-git-credentials.md` |
| SQ8 | claude-skills DB-D13 (DB11; Rich's ruling of Juval's "A second break in the same rule", board-knowledge `sessions/2026-10-27-juval-first-run-parent-scope.md`): report partial items as data, in a fixed format, instead of only `squadra board origins`' one stderr line per partial item (N18), which is not contract. The case: a first run stops mid-create of a queue, leaving a partial item; the map is revised (a title changes); `increments_by_origin` leaves the partial item out, so design-to-board's re-run takes the first-run path, its checks pass, and `queue` is refused (`QueueRefusedError`, exit 3) with a fix that loops. Each entry carries at least the item id and the Origin, and enough for a caller to tell whether a `queue_increment` with given arguments would finish it or be refused under N14's retry rule; better, keep that judgment in squadra behind `check_queue_finishes`, as SQ5e did for scope. Verb or shape is this effort's to settle (`/ask-juval`, `/ask-eric` if wanted); not in `origins`' `{origin: …}` shape, for the reason DB-D12 kept scope out of it. Add it to `verb-contract.md`; the fake answers it too. Unblocks claude-skills DB11 (S9), which first probes the loop on the fake | — (DB11) | — (scheduled around SQ5f, Rich's call; never alongside another unit on this branch) | done (PR #62); N32 |
| SQ9 | SQ8b's finding (N32): `squadra board queue --body-file PATH` read the file in text mode, whose universal newlines turned `\r\n` into `\n`, so a CRLF body was not passed exactly as given (N19) and a planner's own CRLF body would then meet an A3 or N14 refusal. Read the bytes and decode UTF-8 (shared with `partial-items --requests-file`); a body file that is not UTF-8 is exit 2 like any unreadable `--body-file`, not a traceback. Stdin already kept `\r\n` | — (SQ8b) | SQ8 | done (PR #63); N33 |

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
- N3 (SQ1; **amended by SQ5d**, N27): the tick does not query the WITHDRAWN
  bucket, so withdrawn items do not appear in `states={…}`; their successors
  appear as `predecessor-withdrawn`. If design-to-board's acceptance test
  (DB-D1 step G) needs withdrawn items listed, that is a tick change, not an
  adapter one. Since SQ5d the tick queries WITHDRAWN for items still carrying
  the `claimed` tag only: each shows once as `withdrawn-in-flight`, the tick
  that relinquishes it; unclaimed withdrawn items still do not appear.
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
- N10 (SQ2b; **closed by SQ5d** without a compare-and-set: the relinquish rule, N27; raised by Juval in board-knowledge `sessions/2026-10-09-juval-increment-verb-settlement.md`):
  `withdraw_increment` reads the state, then writes it, and the ticker claims
  in its own process, so a claim can land in between: WITHDRAWN on an item a
  runner is working, the ACTIVE withdrawal DB-D2 refuses. SQ2 had the same
  window. Closing it needs a compare-and-set write from the provider (an SQ5
  fact) or a tick rule for a live sandbox on a WITHDRAWN item. Does not block
  SQ3. Closed by the tick-rule route (Rich's N25 Choice, option A): the race
  leaves the same facts as a human withdrawing a claimed item, so the next
  tick relinquishes it. Contract test: a claim injected between
  `withdraw_increment`'s re-read and its write
  (`test_a_claim_racing_a_withdrawal_is_relinquished_next_tick`). GitHub's
  compare-and-set was not checked: not needed.
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
  ADR-0005 decision 1. Superseded in part by N31 (2026-10-27): "partial item" entered
  `GLOSSARY.md` and `GLOSSARY-SETTLED.md` once SQ8's read made it Published
  Language; `UnknownOriginError` stays out.
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
- N18 (SQ4): `origins` needs the partial items `increments_by_origin` leaves
  out, and ADR-0005 lets nothing but `IncrementBoard` read
  `items_with_origin`, so `IncrementBoard` grew one read,
  `increments_and_partial_items() -> (dict[str, Increment],
  tuple[OriginRecord, ...])`, and `increments_by_origin` now returns its
  first half. One board-wide read serves both, so an item a concurrent retry
  finishes cannot fall between two reads (shown in neither) and a GitHub or
  ADO read is not paid twice. Additive: the three verbs, their signatures and
  the CLI surface are unchanged, so no ADR amendment. The stderr line reads
  `squadra board origins: item 1005 is partial, carrying origin "A:P": a
  create that did not finish; it is not in the output` (the Origin JSON-quoted,
  so an Origin with escape characters stays on one line).
- N19 (SQ4): readings of `verb-contract.md` the CLI makes, none a change to
  it. Every usage error is one prefixed line: argparse's own (missing or
  malformed flag, unknown verb), an unrecognized argument (named with the
  verb, not argparse's top-level prog) and an unreadable `--body-file`, all
  exit 2 before any board read. A message spanning lines is folded onto one.
  Exit 1 lines name the exception class (`InjectedCrashError`,
  `NotImplementedError`) since its message alone may not. The body is passed
  exactly as read, never stripped, since A3 and the partial-item finish
  compare it exactly (N14). `--help` keeps argparse's usual output and exit 0.
  claude-skills DB-D10 (PR #14) matches `verb-contract.md` on every point SQ4
  builds; no difference to raise.
- N20 (SQ5 split), **ruled by Rich, 2026-10-09: the proposal with seven
  changes, below; partial by commit marker (N15's fallback)**. Put to him
  after `/ask-juval` and `/ask-eric` (board-knowledge
  `sessions/2026-10-09-juval-github-native-state.md`,
  `sessions/2026-10-09-eric-github-native-state.md`). The ruling wins over
  the proposal everywhere SQ5a's handoff cites N20. Proposal as put:
  GitHub's native state. DB-D2 fixes only WITHDRAWN (closed,
  reason "not planned"); the rest is open, and the names are user-facing
  vocabulary in every GitHub `squadra.toml`, costly to change once a real
  board is configured. Proposal: an item's native state is its Projects v2
  `Status` option name while the issue is open, and `closed:<state_reason>`
  (`closed:completed`, `closed:not_planned`, `closed:duplicate`) once it is
  closed. Closure wins over Status, so a closed issue whose Status still names
  a queued column is never claimable. An open issue that is not on the
  project, or has an empty Status, is in no bucket: the structural absence
  obligation 2 asks for (N15), so no completion marker is needed. The default
  map then reads `withdrawn = ["closed:not_planned"]`, and `validate_config`
  checks `[board.states]` against the project's Status options plus the closed
  names, both ways (N4), so `closed:duplicate` must be mapped too.
  `set_state` writes a Status name onto an open issue, closes the issue with
  the reason for a `closed:` name, and refuses (`BoardValidationError`) a
  Status name on a closed issue rather than silently reopening it; no tick
  path requeues a closed issue.
  Ruling (Rich accepted all seven recommendations; both advisors agreed on
  closure wins in the adapter, the `closed:` prefix guard, raising on an
  unknown reason, no glossary term, and no silent reopen):
  1. **Partial by marker, not by structure** (Juval; Eric: "no bucket" was a
     splinter, history and current state under one word, and empty Status is
     a native state in disguise). A partial item is an Origin-bearing issue
     without the adapter's commit marker. The Origin goes on first (with
     title and body, N14), the marker last: the marker write is obligation
     4's commit, and nobody else writes either. Add-to-project and Status
     may land anywhere before it, each idempotent under `partial_item`.
     The marker gates Origin-bearing items only (an issue with no Origin
     maps from Status and closure, as on ADO; gating every item would be
     WSQ1's shelved form (b)); it lives outside `tag_prefix` (finalize
     clears that namespace by prefix; no exemption is added to finalize);
     it is an additive write, never a read-modify-write of the body; it is
     adapter-internal and never appears in `[board.states]`. Off the project
     or an empty Status is "in no bucket" only for a partial item; on any
     other item it is unmapped and `item_state` raises (ADR-0004 rule 3), so
     a committed increment a human takes off the project is loud, never
     reported partial and requeued. ADR-0005 is unchanged: this is the
     fallback N15 and its Consequences already name.
  2. **The closed names are squadra's constants** (Juval, over Eric's
     byte-for-byte raw reason): `closed:completed`, `closed:not_planned`,
     `closed:duplicate`, defined in the adapter and translated from the
     transport's spelling at the boundary. `validate_config` refuses any
     Status option whose name starts with `closed:`. `item_state` raises on
     a closed reason outside the set, never defaulting; if a closed issue
     can carry no reason, its name is the reserved `closed:` (Eric), mapped
     like the others. The precedence lives in one named function whose
     docstring states it, pinned by a test: a closed issue whose Status names
     a queued column is never QUEUED.
  3. **DONE closes the issue.** `set_state(DONE)` closes it as completed,
     `set_state(WITHDRAWN)` closes it as not planned (DB-D2), QUEUED and
     ACTIVE write a Status option: every `set_state` is one write. A Status
     option mapped to DONE is valid only on a closed issue: open + DONE
     raises in `item_state` (catches a reopened withdrawn issue whose Status
     the "Item closed" workflow left at Done, and a hand drag to Done).
     `set_state` into the bucket the item is already in writes nothing
     (finalize after a merged PR closed the issue). The default map puts
     `closed:duplicate` in `withdrawn`, never `done`; the scaffold comment
     says a hand close as completed counts as delivered.
  4. **"Item added to project" workflow:** tolerated only if the Status it
     sets maps to QUEUED, otherwise required off. `validate_config` checks it
     if the API exposes the workflow's setting; if not, the scaffold
     documents the requirement, the adapter reads Status back once after the
     marker write (detection, not a guarantee), and the ADR records that
     obligation 4 then holds on GitHub only by configuration.
  5. **"Item closed" workflow:** tolerated, safe under 3. The scaffold
     advises turning it off and says the project view is not authoritative
     for closed issues. N21's "harmless" holds only with 3's guard.
  6. **A closed issue is never reopened, and the tick goes on.** `set_state`
     still refuses a Status name on a closed issue (a reopen would be two
     writes). No tick path requeues a closed issue; a requeue that finds one
     reports it for that item and the tick continues, never ending on it.
  7. **Where it is written:** SQ5a writes an ADR for GitHub's native state
     (composition, closure wins, the closed names, the marker, both
     workflows). "Native state" is not a new term and stays out of
     `GLOSSARY.md` (Eric): it is defined once in the board-seam docs (the
     provider-side name `[board.states]` maps from; on GitHub computed, not
     stored), GitHub's rule in the adapter's docs, and a comment beside the
     default GitHub map in the scaffold. Eric's optional reason under
     Withdrawn's _Avoid_ "closed" ("a provider's word; on GitHub a closed
     issue can be done or withdrawn") is SQ5a's call. In adapter code,
     `completed` names only GitHub's reason, never delivery (N13).
  Changes to the SQ5a handoff: the reads recognise partial items, so SQ5a
  fixes how the Origin and the marker are encoded and reads both
  (`items_in_state` leaves partial items out, `item_state` raises on one);
  SQ5b writes them. `validate_config` gains the `closed:` prefix guard and,
  if the API allows, the workflow check (4). `item_state` raises on an
  unknown reason, open + DONE, and a non-partial item off the project or
  with an empty Status. `set_state` follows 3 and 6, and the tick reports a
  refused requeue per item: a tick change, in SQ5a's scope with the tick's
  writes (the existing cut line moves both to SQ5b). SQ5a writes the ADR,
  the scaffold comment and the native-state definition (7). The handoff's
  N20 unit tests become: closed beats Status; partial vs committed, each
  off the project and with an empty Status; `closed:duplicate` unmapped and
  a `closed:`-prefixed Status option each fail `validate_config`; an
  unknown reason and open + DONE raise; a Status write on a closed issue
  refuses; a same-bucket `set_state` writes nothing; plus a contract case:
  an ACTIVE item closed between ticks with each reason is neither reopened
  nor ends the next tick, and is reported. N21 gains four facts to verify:
  every `state_reason` value the transport returns and whether one can be
  null; whether the API exposes the project workflows' settings; whether a
  merged PR's closing reference closes its issue; whether add-to-project
  can set Status in one call. SQ5b gains Juval's interloper test (after each
  write j < k an outside actor adds the item to the project and sets every
  QUEUED-mapped Status: `items_in_state(QUEUED)` leaves it out,
  `items_with_origin` returns it with `lifecycle` `None`, `item_state`
  raises) and Eric's cleared-Status test (a committed ACTIVE item with its
  Status cleared, or taken off the project, raises and is not requeued).
- N21 (SQ5 split): GitHub facts each unit verifies before relying on them,
  none checked by the split session: the sub-issue and issue-dependency
  ("blocked by") APIs and whether `gh api` reaches both (SQ5a reads them,
  SQ5b writes them); the project's built-in workflows, "Item added to project"
  (sets Status on add, which would make the add the commit write: SQ5b orders
  add-to-project after every link, so either way it comes last, but it must
  say which write commits) and "Item closed" (sets Status to Done, harmless
  under N20's closure-wins rule); and any conditional write for N10 (SQ5c).
  **Verified by SQ5a, 2026-10-09** (GraphQL introspection and live reads with
  `gh api`, account rinman24):
  - Sub-issue parent (`Issue.parent`) and "blocked by" (`Issue.blockedBy`,
    also `blocking`, `subIssues`) are both readable through `gh api graphql`.
    `item_links` uses them, and a live read of `rinman24/squadra-sandbox#1`
    returned no parent and no predecessors.
  - `stateReason` (`IssueStateReason`) is nullable. Its values are
    `COMPLETED`, `NOT_PLANNED`, `DUPLICATE` and `REOPENED`; `closeIssue`
    accepts `COMPLETED`, `NOT_PLANNED` and `DUPLICATE` (`duplicateIssueId`
    with the last). A closed issue with a null reason reads as `closed:`, and
    `REOPENED` on a closed issue raises as unknown.
  - Project workflows (`ProjectV2.workflows`) expose only `name`, `enabled`
    and `number`, never the Status a workflow sets. So `validate_config`
    cannot check "Item added to project" (N20 ruling 4's fallback holds, as
    ADR-0006 records).
  - The sandbox project has six workflows, all enabled: "Item added to
    project", "Item closed", "Auto-close issue" (closes an issue whose Status
    becomes Done), "Auto-add sub-issues to project", "Pull request linked to
    issue" and "Pull request merged". Two of these were new to the split:
    - "Auto-close issue" is harmless: a Done Status on an open issue raises
      until it closes, and a close is what DONE means.
    - "Auto-add sub-issues to project" matters to SQ5b. Linking the parent
      can add the child to the project (and "Item added" then sets its
      Status) before the marker. It is tolerated only because a partial item
      is in no bucket whatever its Status.
  - `addProjectV2ItemById` takes only `projectId` and `contentId`, so it
    cannot set Status in the same call. Status is a separate
    `updateProjectV2ItemFieldValue`.
  - Not verified live: whether a merged PR's closing reference closes its
    issue. GitHub documents that it does, as completed, but only when the PR
    merges into the repository's default branch. With `base_branch` as the
    default, finalize then finds the item already DONE and writes nothing
    (ADR-0006 decision 3). A non-default `base_branch` leaves the close to
    finalize's `set_state(DONE)`.
- N22 (SQ5a): **transport is the `gh` CLI**: `gh api` for REST,
  `gh api graphql` for GraphQL, injected as `run` the way ADO takes `az`. Auth
  is `gh`'s own (keyring or `GH_TOKEN`), so no token reaches argv. Strings go
  as `-f` and integers as `-F`. Every GraphQL operation is named
  (`SquadraProject`, `SquadraProjectItems`, `SquadraIssue`,
  `SquadraCloseIssue`, `SquadraSetStatus`), which lets
  `tests/helpers/github_stub.py`'s in-memory GitHub dispatch on the name. That
  stub is the `board` fixture's fourth shape (`gh`), with pages of two to
  exercise pagination; `fake_board` and the increment suites stay three-shaped
  until SQ5b. GraphQL serves the reads, because Status and sub-issue/blocked-by
  links are GraphQL-only or GraphQL-first; REST serves labels, comments and
  PRs.
- N23 (SQ5a): choices inside N20's ruling, recorded in ADR-0006.
  - **Encoding.** The Origin is the GitHub fake's hidden body marker, now
    `squadra.github_board.with_origin_marker` / `split_origin_marker` (the
    fake imports them). A malformed marker raises rather than reading as "no
    Origin". The commit marker is the label `squadra:committed`.
  - **Partial wins over closure too.** A closed partial item is still
    partial, so obligation 2 has no exception.
  - **`validate_config` additions.** It also refuses a closed name in
    queued/active and a map without `closed:completed` in done and
    `closed:not_planned` in withdrawn: the two closes `set_state` makes, so
    WITHDRAWN can never land as "done".
  - **`set_state` refusals.** It refuses a close that would change a closed
    issue's reason, and any write onto a partial item.
  - **Listing scope.** `items_in_state` lists the project's unarchived items
    that are issues of `[board.github].repository`. `item_links` refuses
    links into another repository and more than 100 blockers.
  - **Scaffold default** (`squadra init --provider github`):
    `done = ["closed:completed", "Done"]`,
    `withdrawn = ["closed:not_planned", "closed:duplicate", "closed:"]`.
    `closed:` goes to withdrawn on purpose: a reasonless close is not taken
    as delivery.
  - **For SQ5b.** GitHub's web editor saves bodies with `\r\n`, so the body
    before the marker may not equal the request's byte for byte when SQ5b
    checks a partial item agrees with its request. The writes (close, Status,
    labels, comments) ran only against the stub in SQ5a; SQ5b runs them once
    on the sandbox.
- N24 (SQ5b): GitHub's create and Origin listing, inside ADR-0005 and ADR-0006.
  - **Write endpoints, verified 2026-10-27 by GraphQL introspection** (`gh api
    graphql`, read-only): `addSubIssue(issueId: <parent>, subIssueId: <child>)`
    (payload `subIssue`), `addBlockedBy(issueId: <blocked>, blockingIssueId)`
    (payload `issue`), `addProjectV2ItemById(projectId, contentId)` (payload
    `item { id }`). The issue itself is created by REST `POST
    repos/{repo}/issues` (title, body; returns `number`, `node_id`). The links
    take node ids, so each linked issue is fetched once for its id. **Not yet
    verified live** (they need writes; SQ5c's sandbox run): that the labels POST
    creates a missing `squadra:committed` label, that re-adding an issue to the
    project returns its existing item, and that `addSubIssue` refuses a child
    that already has a parent (the adapter never asks it to).
  - **k = 5 + n** (`squadra.github_board.create_step_count`): create (Origin,
    title, body), parent, one "blocked by" per predecessor, add to project,
    first queued Status, `squadra:committed` (the commit). Finishing a partial
    item makes only the missing writes: no parent write if one is set, only
    the absent predecessors, no project add if on the project, no Status write
    if its Status already maps to QUEUED; the commit always. A closed partial
    item is refused (`BoardValidationError`) before any write, since finishing
    it would need a reopen. A `partial_item` that is not a partial item
    carrying the request's Origin raises `ValueError` (N14's guard).
  - **Read-back** (ADR-0006 decision 7): after the commit, `item_state` once.
    Anything but QUEUED, or a refusal, raises `BoardValidationError` naming the
    "Item added to project" workflow. The issue stays committed; detection, not
    a guarantee.
  - **`items_with_origin` enumerates by GraphQL `repository.issues`** (open and
    closed, oldest first, pages of 100) and keeps the issues whose body carries
    the marker. Not search: the search index lags, so a just-created partial
    item could be missed and its retry would create a duplicate (obligation 3).
    Cost is one page per 100 issues of the repository per call. A committed
    Origin-bearing issue with no bucket (Status cleared, off the project, open
    with a done Status) makes the whole listing raise rather than read as
    partial, so a retry never "finishes" (requeues) it (Eric's cleared-Status
    test).
  - **`\r\n`** (N23): `split_origin_marker` turns `\r\n` into `\n` and
    drops whitespace after the marker before it reads the body. A web-edited
    body still carries its marker and still equals the body it was queued
    with. Consequence: a request body that itself contains `\r\n` reads back
    with `\n`, so its retry is refused as "body differs" (loud, not wrong).
  - **Tests.** The `increment` suite runs on four shapes (`gh` is now a
    `fake_board` shape, with `seed_origin` / `seed_partial` on
    `StubbedGitHubBoard`; the stub holds the suites' parents 200, 300, 999 off
    the project, since a parent must exist). The crash-safe suite runs on
    `file` and `gh`, each for every j < its own k, through a stub-wide one-shot
    fault (`InMemoryGitHub.arm_fault`, raising the fake's
    `InjectedCrashError`); "a fault past the last write is refused" stays
    file-only, being the fake's arming API (N17). GitHub only: Juval's
    interloper test (after each j < k, an outsider puts the issue on the
    project in each queued column: no bucket, `lifecycle` `None`, `item_state`
    raises, the retry still finishes it), finishing rewrites nothing already
    there, Eric's cleared-Status test, the read-back, a web-edited body.
    `withdraw_increment` runs end to end through `IncrementBoard` on `gh` in
    the increment suite (closes as not planned).
  - **Cut** (the handoff's cut line): the tick's refused-requeue report and the
    live sandbox run, to SQ5c. **Open question for SQ5c**, found while
    scoping the tick change: under closure wins an ACTIVE issue closed between
    ticks reads DONE (`closed:completed`) or WITHDRAWN at the next gather, so
    the next tick never requeues it. `_retry` and the launch rollback meet a
    closed issue only when it closes within a tick, between the gather and the
    write. The contract case as N20 words it ("closed between ticks ... is
    reported") therefore needs a reporter for a fleet-claimed item that left
    ACTIVE by closure: a WITHDRAWN one is not gathered at all, and a DONE one
    goes to finalize. Settle what "reported" means there (and whether its
    sandbox is torn down) before building.
- N25 (SQ5c): **N24's open question, settled in part.** Within a tick (the
  issue closes between the gather and the write), GitHub's `set_state` raises
  `squadra.board.StateWriteRefusedError`, a provider-neutral
  `BoardValidationError` subclass; the supervisor catches only it, at the
  requeue (`_retry`), the launch rollback and the claim's own state write
  (`_set_state_or_report`), logs `<what> refused for #N: …` for that item and
  goes on. A refused requeue posts no `Reaped`, a refused rollback no
  `RolledBack`, a refused claim launches nothing and leaves its fresh worktree.
  Contract tests: `tests/contract/test_refused_requeue_contract.py`, the three
  paths × four close reasons on the `gh` shape, each with a second item the
  same tick still claims. **Between ticks** the case N20 words does not reach
  the requeue: closed as completed reads DONE (fleet-claimed, no PR:
  `awaiting-pr`, as on ADO after a hand drag to Done), and the other reasons
  read WITHDRAWN, which the tick does not gather (N3), so the sandbox, the
  claimed label and `status.json` leak unreported. Put to Juval (board-knowledge
  `sessions/2026-10-27-juval-withdrawn-in-flight-release.md`): option A without
  board-state writes. The engine releases a fleet-claimed WITHDRAWN item at
  guard step 0 (new state and action, names by Eric), the gather adds the
  WITHDRAWN bucket filtered on the claimed tag, the release tears down,
  archives, parks the status, posts one comment and drops `claimed` last (its
  commit); a hand-DONE item stays `awaiting-pr` but its running sandbox is
  stopped (`StopContainer`); a per-gather memo of the project listing if the
  GraphQL budget is unknown. It amends ADR-0004 decision 1, N3 and N10 and
  replaces N20's contract-case wording, so it waits for Rich's Choice (SQ5d).
- N26 (SQ5c): **live run on `rinman24/squadra-sandbox` + user project #1,
  2026-10-27** (scaffold's default map, `claim_scope = "parents"`, parent #1).
  `validate_config` passed. `squadra board queue` made issue #8: Origin body,
  sub-issue of #1, on the project with Status `Todo`, `squadra:committed`;
  the read-back saw QUEUED. Then `set_state(ACTIVE)` (Status → In Progress),
  `add_tag`, a `Claimed` comment (Markdown), `remove_tag` twice (the second a
  no-op), `set_state(QUEUED)`, `squadra board withdraw` (closed as not
  planned), and `set_state(QUEUED)` on the closed issue raised
  `StateWriteRefusedError` with nothing written. N24's three facts:
  - the labels POST creates a missing label: shown with `fleet:claimed`
    (absent before); `squadra:committed` already existed, made by the
    design-to-board run's issues #3–#7 earlier that day;
  - re-adding an issue to the project returns its existing item (same
    `PVTI_…` id);
  - `addSubIssue` on a child with a parent and no `replaceParent` is refused
    (`VALIDATION`: "Sub issue may only have one parent"), the parent unchanged.
  Workflows: "Item closed" set #8's Status to `Done` on close, and #8 still
  read WITHDRAWN (closure wins, ADR-0006 decision 1). "Item added to project"
  and "Auto-add sub-issues to project" could not be told apart from the
  adapter's own writes: the create writes the queued Status itself, #8 has one
  project item, and parent #1 is off the project. #8 is left closed.

- N27 (SQ5d): **relinquishing a claim on an increment withdrawn in flight**,
  built per Rich's N25 Choice (board-knowledge
  `sessions/2026-10-27-juval-withdrawn-in-flight-release.md`). Eric named it
  (`sessions/2026-10-27-eric-withdrawn-in-flight-names.md`, Choice blank for
  Rich): "release" clashes with shipping, so the state is
  `withdrawn-in-flight` (`State.WITHDRAWN_IN_FLIGHT`), the action
  `RelinquishClaim`, the orchestrator `_relinquish`, the comment event
  `Relinquished(attempt, branch)`, the park `withdrawn`; GLOSSARY gains
  Relinquish (_Avoid_: Release) and a sentence under Attempt. ADR-0004
  decision 1 and its Consequences are amended, with N3 and N10.
  - Engine: guard step 0 above the tag guards; `_classify_done` stops a
    running sandbox on a fleet-claimed DONE item with no completed PR
    (`awaiting-pr` + `StopContainer`; `_build_facts` now inspects the sandbox
    for DONE + claimed, and reads the egress log, manifest and commits for
    in-flight items only).
  - `_relinquish` archives the worktree only if its directory exists, so a
    tick that ends after the archive does not raise on the redo.
  - Q3 (the memo): ticks run every 180 s and a real project's size is
    unknown, so per the Choice the memo is in. `GhApiGitHub` memoizes the
    project listing until its own next write; a gather lists the project
    once, not four times. The whole suite passed with it, so no test relied
    on a fresh listing from one instance without a write (Juval's check).
  - Eric found `MANIFEST_PARKED_STATES` aliased `status.PARKED_STATES`, so
    adding `withdrawn` would have let an agent's `outcome.json` declare it and
    get a PR. The manifest now has its own tuple with the four values it had.
    **For Rich:** Eric also says `failed` is a fleet-only park an agent can
    declare today (it then gets the clean handoff); removing it from the
    manifest's list narrows agent behaviour, so it was not done unruled.
  - Contract tests (`tests/contract/test_withdrawn_in_flight_contract.py`):
    `closed_between_ticks` on `gh` for not planned, duplicate and no reason,
    and on the ADO-shaped fake with `Removed` mapped (two ticks: no lifecycle
    write, sandbox torn down, worktree archived, fleet tags gone but a
    non-fleet label kept, one comment, status kept with its attempt, logged
    `withdrawn-in-flight`, then absent with no writes); the completed variant
    (`awaiting-pr`, sandbox stopped, then finalized once the PR merges); the
    N10 variant. Engine: the guard-order test over `failed`,
    `needs-decision` and parked facts. These replace N20's contract-case
    wording ("closed between ticks … is reported").
  - Seen while testing: a fleet-claimed DONE item that also carries
    `needs-decision` is `parked-decision` (guard 1 outranks the done bucket),
    so its sandbox is not stopped by the new rule. `_park_needs_decision`
    already tore it down; not changed.

- N28 (S7, claude-skills DB7): **design-to-board's acceptance run (DB5,
  claude-skills session S5, 2026-10-27) against N23, N24 and N26.** squadra
  `34a2234` (SQ5b) installed as a uv tool, on `rinman24/squadra-sandbox` +
  project #1, `claim_scope = "parents"`, `--parent 1`. It ran before SQ5c's
  live run, so the adapter's first live writes were DB5's create path, not
  N26's (N26 already saw the `squadra:committed` label DB5 made).
  - Five `squadra board queue` calls (#3; #4 blocked by #3; #5 blocked by #4;
    #6 blocked by #3; #7 blocked by #6), each making all 5 + n writes (N24),
    and two `squadra board withdraw` calls (#4, #5: closed as not planned)
    worked. Each read-back saw QUEUED (Status `Todo`). DB5 credits `Todo` to
    the "Item added to project" workflow. Per N26 the create writes the
    queued Status itself, so the two cannot be told apart.
  - The labels POST created the missing `squadra:committed` on #3's commit
    write (N24's first fact, for that label; N26 showed it with
    `fleet:claimed`).
  - DB5 left two of N24's facts unverified: re-adding an issue to the project
    and `addSubIssue` refusing a parented child. N26 verified both later the
    same day, so none of N24's facts is still open.
  - "Item closed" set #4's and #5's Status to `Done`. They still read
    WITHDRAWN (closure wins), and claude-skills DB8 turns the workflow off.
  - Re-checked by S7 on `main` `e4083e6`, against a temp `FLEET_HOME` holding
    a copy of the sandbox's `squadra.toml`: `tick --dry-run` →
    `inflight=[] claimable=[3] states={3: 'claimable', 6: 'blocked', 7:
    'blocked'}`, the same as DB5's acceptance line.

- N29 (S7, claude-skills DB7): **DB5's three squadra findings, ruled by Rich
  after Juval** (board-knowledge
  `sessions/2026-10-27-juval-squadra-init-preflight-findings.md`). Each was
  reproduced on `main` `e4083e6` in a temp `FLEET_HOME` holding a copy of the
  sandbox's `squadra.toml`.
  - (i) **Fixed (SQ6).** Without `--provider`, the check built the ADO
    adapter and failed on an ADO project's Issue states (`Doing`/`Done`/`To
    Do`). argparse put `ado` at the flag layer, so the env and toml layers
    were never reached. After SQ6 the same command reads `config OK`.
  - (ii) **Dropped, no row.** The finding's "rewrites" was wrong: the two
    templates are written only when absent and are skipped otherwise unless
    `--force`. That matches the README's `init [--check]` (scaffold, then
    validate), and the templates are inert until a consumer wires them in.
  - (iii) **SQ7.** Still logged on `main`. Juval: the log line is accurate;
    the defect is that host-side git auth is ADO-shaped and the probe and the
    claim's fetch auth differently (confirmed: `worktree.py:107` uses
    `host_git_argv` with no credential helper). His blocking question is the
    origin form and `AZURE_DEVOPS_EXT_PAT` on the host of the first live
    GitHub tick. Probe run alone on Rich's Mac, against
    `~/Code/squadra-sandbox` (`remote_auth_ok(target_remote_url(...))`, one
    read-only `ls-remote`): SSH origin (`git@github.com`), the variable
    unset, `True`. So SQ7 is latent there, not blocking. Over an HTTPS
    origin to the private sandbox it would very likely fail every tick that
    has claim work and tell the operator to rotate an ADO Key Vault secret
    (code reading, not run).
  - **DB-D12's `scope` verb is not built.** `verb-contract.md` on `main`
    `e4083e6` has no `scope`. It is SQ5e (todo, ahead of SQ5f), as SQ5d
    recorded, so S7 added no row. claude-skills DB10/S8's gate is SQ5e's
    merge on `main`.
- N30 (SQ5e, claude-skills DB-D12): **`squadra board scope --parent ID`
  built as ruled.** Juval's Blocking question 1 held on `main` `500d42a`:
  `queue_increment`, `withdraw_increment` and `as_increment` (which sets
  `Increment.in_claim_scope`) all called `engines.parent_in_claim_scope` on
  the same `[board]` scope, so no unification was needed. Readings the
  build made, none a change to the ruling:
  - `IncrementBoard._in_scope` became the public
    `parent_in_claim_scope(parent: int | None) -> bool` (Juval's name), and
    both writes' refusals call it; `as_increment` still calls the `engines`
    function directly, since the tick shares it. The CLI calls only the
    method, so it stays a Client. Additive (a new read, the three verbs and
    their signatures unchanged), so no ADR-0005 amendment, as in N18.
  - The read reads nothing from the board. The board is touched only by
    `validate_config()`, which runs first as for every verb, so a mis-mapped
    board is exit 2 before an answer, and an unreachable provider is exit 1.
  - `scope` never exits 3: an out-of-scope parent is the answer `false`,
    not a refusal. The contract's exit-code line says so.
  - Tests: Juval's agreement test in `tests/test_cli_board.py` (scope ∈
    {whole-board, parents [1]} × parent ∈ {1, 2}; `scope` says in scope
    exactly when `queue` is not refused for scope, and the board file is
    byte-identical after `scope`), checked by mutation (a `scope` that always
    says `true` fails `parents-1-out`); a contract test on all four board
    shapes tying the read, `queue`'s refusal and `Increment.in_claim_scope`
    together; `scope`'s usage, config, validation and provider exits.
  - SQ5f must keep the three on one predicate when `in_claim_scope` moves
    to the adapter; both new tests are its guard.

- N31 (SQ8, claude-skills DB-D13): **partial items as data: Juval's shape
  and Eric's names, ruled by Rich 2026-10-27 (all four taken; see the last
  bullets).** Consulted blind on the
  handoff's three options (board-knowledge
  `sessions/2026-10-27-juval-sq8-partial-items-shape.md` and
  `sessions/2026-10-27-eric-sq8-partial-items-names.md`; both Choice
  sections blank, uncommitted there). The cut line was taken: no build.
  - Juval: the handoff's design point is false. N14 refuses only on a
    predecessor the partial item carries that the request leaves out, and a
    predecessor not yet on the board gets a new id, which no partial item
    can carry, so leaving it out cannot change the verdict (assumes a board
    never reuses an item id; to be written into `verb-contract.md`). So the
    request names predecessors by Origin and squadra maps them to ids from
    the same single read (an Increment's or a partial item's id; an absent
    Origin drops out). All of N14 stays in squadra. Rejected: the listing
    (hands N14, a provider-shaped rule, to the caller), the middle way (splits
    one rule across two repos by field), one question per row (N reads).
  - Proposed: one batched read, `squadra board partial-items
    --requests-file PATH|-`. Input: a JSON list of `{origin, parent,
    predecessor_origins, title, body}`; `[]` is valid; anything else, or an
    Origin twice, is exit 2. Output: `{"partial_items": [{item_id, origin,
    queue, disagrees_on, refusal}, ...]}`, every partial item on the board,
    sorted by item id, never filtered. `queue` is `"finishes"`, `"refuses"`
    or `null` (no request names it); `refusal` is byte for byte the
    `QueueRefusedError` message `queue` would raise; `disagrees_on` ⊆
    `parent | predecessors | title | body`. It answers only N14: an
    Increment, an out-of-scope parent or a withdrawn Origin gets no entry.
    Exits 0 for any verdicts, 2 usage/config, 1 provider, and 3 only for a
    board-level `DuplicateOriginError` (as `origins`), the one departure
    from the handoff's 0/2/1. `validate_config()` first; never writes.
  - Engine: `check_queue_finishes` becomes a wrapper over
    `refusal_to_finish(existing, request) -> str | None`, built from
    `partial_item_disagreements(...) -> tuple[tuple[RequestField, str], ...]`
    (today's four checks, order and message bytes unchanged), so `queue` and
    the read share one predicate as SQ5e's scope does. `item_ids_carrying`
    maps predecessor Origins (named so the drop of absent ones is visible;
    sound only for N14's subset check). Domain: `ProposedRequest`,
    `PartialItemReport` (stores `requested` and `refusal`, derives `queue`).
    `IncrementBoard.partial_items(requests)`, so only `IncrementBoard` reads
    `items_with_origin`; ADR-0005 gets a note that squadra now maps
    predecessor Origins to ids for this read.
  - Eric: `QueuePlan` rejected ("plan" has four meanings across the seam,
    DB-D7's `Plan` among them); `differs` → `disagrees_on`; `queue` values
    in one subject and tense; no helper starting "finish" or "resolve".
    Partial item enters `GLOSSARY.md` as Published Language, reversing his
    October exclusion (N13); his entry text is in the session file.
  - Juval's blocking question (does design-to-board ever name a predecessor
    by id?) answered from claude-skills DB-D4 and DB-D7: no, steps name
    predecessor Origins and a missing one fails loudly, so
    `predecessor_origins` alone.
  - Window left open: `origins` and `partial-items` are two reads. Juval:
    it cannot recreate the loop (that needs a persistent disagreement, which
    the one read sees); a retry finishing between them costs at most one
    exit 3, and the next run settles. A read-then-read window, kin to N10's
    read-then-write one.
  - **Rich's ruling, 2026-10-27** (both session files' Choice): (1) Juval's
    shape over the listing and the middle way; (2) exit 3 on a board-level
    duplicate Origin, yes; (3) Juval's build spec with all of Eric's names
    (the merged spec is in `handoffs/SQ8b-build-partial-items.md`); (4)
    partial item into `GLOSSARY.md` and `GLOSSARY-SETTLED.md`, done in this
    PR (rejected: Incomplete item, Partial increment). This reverses N13's
    "partial stays out of the glossary": a planner now consumes partial
    items through a contracted read, so the term is Published Language.
  - Later, not SQ8 (Juval): `IncrementBoard` goes to six operations; once
    DB11 uses this read, consider dropping the partial half of
    `increments_and_partial_items` and `origins`' stderr line.

- N32 (SQ8b, claude-skills DB-D13): **`squadra board partial-items
  --requests-file PATH|-` built as ruled (N31).** Readings the build made,
  none a change to the ruling:
  - `RequestField` lives in `domain.py` (engines imports domain, so
    `PartialItemReport` could not import it from engines) and engines
    imports it. `check_queue_finishes` is a wrapper over `refusal_to_finish`,
    built from `partial_item_disagreements`; its message bytes are unchanged
    (the SQ2c refusal tests pass untouched, and a unit test asserts
    `str(exc) == refusal_to_finish(...)`).
  - Duplicate requests: the CLI refuses them (exit 2, before any board
    read), and `IncrementBoard.partial_items` raises `ValueError` too, so a
    library caller cannot get a silently last-wins answer.
  - `_run_board_verb` lost its `verb` parameter (it is `args.verb`) to stay
    within ruff's five-argument limit once the parsed requests were threaded
    through; `_read_body` became `_read_input`, shared by `--body-file` and
    `--requests-file`.
  - The agreement test's row R has two predecessors, P (an Increment) and
    OTHER (a partial item), not P alone. With P alone the "predecessor
    Origin only another partial item carries" plan cannot change a verdict,
    so a read that failed to map it would pass. With OTHER on R, the `agree`
    plan refuses at the last link unless OTHER's Origin maps to its id, and
    an `other-left-out` plan is added. Seven plans × every
    `fail_create_after_step` j < k (k = 5) on the fake file through the CLI,
    and seven plans × four stops (`seed_partial`) × four board shapes in the
    contract suite. The parent-differs plan finishes before the parent link
    and refuses after, on every shape.
  - Mutation check (as SQ5e): a read that ignores the parent once set, one
    that maps only Increments' Origins, and one that applies A3's set
    equality to predecessors each fail the agreement tests on all four
    shapes. A read that maps an absent Origin to a made-up id passes, and
    must: an id no item carries can only add to the requested set, which
    N14's subset check never refuses on. That is Juval's soundness argument,
    so it is an equivalent mutant, not a gap.
  - Found, not fixed (out of scope): `queue --body-file` reads with
    `Path.read_text`, whose universal newlines turn `\r\n` into `\n`, so a
    CRLF body is not passed "exactly as read" (N19). `partial-items` is not
    affected (JSON escapes a CR inside a string), but a planner comparing
    its own CRLF body with what `queue` stored would see A3 or N14 refuse.
    For Rich: a one-line fix (`newline=""`) plus a test, in its own unit.
    Fixed in SQ9 (N33).
- N33 (SQ9): **`--body-file` is read exactly.** `cli._read_input` decodes
  the file's bytes as UTF-8 instead of `Path.read_text`, whose universal
  newlines folded `\r\n` to `\n` (`read_text(newline=...)` needs Python
  3.13; squadra supports 3.11). Stdin (`-`) already kept `\r\n`, checked
  through a real subprocess. A body file that is not UTF-8 now exits 2 with
  one `cannot read --body-file` line, as N19 asks of an unreadable file,
  instead of an uncaught `UnicodeDecodeError`. Tests: a CRLF body file is
  stored byte for byte and an identical retry still matches (A3); a CRLF
  body on stdin; the non-UTF-8 exit. The file and UTF-8 tests fail on the
  old read. No contract change: `verb-contract.md` already says the body is
  passed exactly. Shipped with the SQ5f handoff refresh (five verbs, N32's
  per-board id assumption, SQ8b's guard tests).
- N34 (SQ5f): **adapter-owned claim scope built; `[[boards]]` cut to SQ5g
  with a question for Rich.**
  - `BoardAccess.in_claim_scope(item_id) -> bool` (WSQ1 Answer item 5). Each
    adapter (`AzCliAdo`, `GhApiGitHub`, `JsonFileBoard`) takes
    `claim_scope` and `parent_scope_ids` as required keyword arguments, no
    default (a default would be a silent broadening), and `build_board`
    passes `[board]`'s. `_queued_gates` asks the adapter and no longer takes
    the config; it still reads `item_links` for the predecessors of an
    in-scope item. Cost: under `"parents"` an in-scope queued candidate now
    has two link reads (scope, then predecessors); under `"whole-board"` the
    adapter reads nothing for scope, so the count is unchanged. Not memoized:
    no test or tick relies on it, and GitHub's project memo (N27) is a
    separate listing.
  - One predicate: `parent_in_claim_scope` moved from `engines` to
    `config`, beside `ClaimScope`, and `engines` re-exports the same function
    object (a unit test asserts `is`). Reason: the adapters are Access, and
    Access calling up into an Engine breaks the closed layering. The
    adapters call `config.item_in_claim_scope(read_parent, …)`, which reads
    the parent only under `"parents"` and then calls the predicate.
    `IncrementBoard` keeps its own `(claim_scope, parent_scope_ids)`
    arguments: SQ5e's guard test runs one board object under two
    `IncrementBoard` scopes, so `IncrementBoard` cannot read the scope from
    the adapter without changing that test. `_cmd_board` and `build_board`
    take both from one `SquadraConfig`.
  - Tests: the four named guard tests pass with their files unchanged. New:
    the adapter's `in_claim_scope` agrees with `parent_in_claim_scope` and
    the reported `Increment.in_claim_scope` on all four shapes × both scopes
    × parent ∈ {in, out, none}, checked by mutation (a file adapter that
    always says in scope fails); `item_in_claim_scope` reads the parent only
    under `"parents"`. Test fakes default to `"whole-board"`, as
    `make_config` does; two tests that inject a pre-seeded `FakeBoard` past
    `build_board` now scope it from their config (`FakeBoard.set_claim_scope`),
    as the registry would.
  - `TickSeams.ado` is `TickSeams.board` (every `seams.ado` in `supervisor.py`
    and the tests). `pat_ok`, `_ado_pat_ok` and `_dry_run_pat_ok` are
    unchanged (SQ7's).
  - **For Rich: the `[[boards]]` shape (cut to SQ5g).** Two facts settle
    most of it. First, every local artifact of the tick is keyed by the bare
    item id: `fleet_root/<id>/` (status, claimed-at marker, archive), the
    sandbox project `squadra-increment-<id>`, and the branch template
    `feat/increment-{id}-{slug}`. ADO item 105 and GitHub issue 105 would
    share all of them, so several boards in one instance is not a config
    shape: it means namespacing every artifact by board. Second, design-to-board
    never reads `squadra.toml` (DB-D5), so it cannot discover board names; a
    selector would have to come from its own caller. The options:
    - **(A) One squadra instance per board (recommended).** Keep the single
      `[board]` table. A second board is a second `FLEET_HOME` with its own
      `squadra.toml`, `fleet_root` and `tag_prefix` (WSQ1 Choice 4: fleets
      split by board, name each fleet and derive its prefix). The rule "the
      scope check fires wherever a board is added" holds already: adding a
      board means adding a `squadra.toml`, whose `claim_scope` is required.
      The contract stays per board (2026-10-09 session) with no selector, and
      N32's id assumption never crosses boards. No CLI change. SQ5g closes in
      the ledger, with WSQ1 Choice 3 recorded as answered by (A). One
      residue: two fleets delivering into one repository could push the same
      branch name for equal item ids and equal slugs; the branch template is
      per `squadra.toml`, so give each fleet its own prefix if that ever
      happens.
    - **(B) `[[boards]]` in one instance.** Each entry has a `name`,
      provider, states, scope and provider table. Every `squadra board` verb
      takes `--board NAME` (required when there is more than one entry): a
      verb-contract change, and design-to-board grows a matching argument.
      The tick iterates boards, and status, markers, archives, sandbox
      projects, worktrees and branches gain the board name, with `cap`
      shared or per board to decide. Roughly a unit for the config and verbs
      and another for the tick.
    Asked of Rich in the SQ5f report. He asked for Juval's and Eric's read
    before ruling: SQ5f2 (`handoffs/SQ5f2-consult-boards.md`).
  - **SQ5f2: Juval's and Eric's answers (2026-10-28), for Rich's ruling.**
    Juval was asked blind: the two facts, the ruled constraints and both
    options, without (A)'s "(recommended)" or its "SQ5g closes in the
    ledger" line. Eric was then given Juval's answer and the glossary.
    Session files in board-knowledge, Choice blank for Rich:
    `sessions/2026-10-28-juval-boards-instance-shape.md` and
    `sessions/2026-10-28-eric-fleet-name-boards.md`.
    - **Juval disagrees with N34's recommendation.** He takes (A) but not
      as a ledger-only close: (A′), one fleet per repository, named, with
      every host-global name derived from the name, built now. His reason is
      that N34's "one residue" (equal branch names) is the mildest of four
      collisions when two fleets share a host, and the other three are on
      the path Rich plans (one VM, several repositories). Each was checked
      in the code in this session:
      - `install-units` writes fixed `squadra.service` / `squadra.timer`
        (`units.py:30`), so a second fleet's install overwrites the first's
        `FLEET_HOME` and the first stops ticking, unreported.
      - The compose project `squadra-increment-<id>` (`supervisor.py:578`)
        is torn down with `compose down -v` (`sandbox.py:173–176`), so
        fleet A's finalize or reap of its item 105 removes fleet B's
        running item 105 and its volumes, and A's `inspect` reads B's
        container as liveness for A's 105.
      - Every fleet's `tag_prefix` defaults to `fleet:`.
      He also found an unchecked invariant: the GitHub board's
      `[board.github].repository` (where `completed_pr_url` looks) and
      `FLEET_HOME`'s `origin` (where branches go) must be one repository,
      or finalize never fires. So a fleet is one repository, and a
      `[[boards]]` entry could only be a second board over the same
      repository. The motivating case (boards across rinman24 and
      genshift-energy) is separate repositories, hence separate fleets
      under either option. (B) as stated serves no named case.
    - On WSQ1: (A′) answers Choice 3's rule (a new board is a new
      `squadra.toml`, whose `claim_scope` is required) but **reverses
      Choice 3's direction** (`[[boards]]`), whose premise the GitHub
      adapter removed. Rich should reverse it knowingly. Juval also amends
      his own Choice 4: the trigger for naming is the second fleet on any
      host, not a second VM, and on Rich's plan that comes first.
    - (A′) as SQ5g (~1 unit): (1) a required fleet name with no default,
      scaffolded empty, conservative charset; (2) units
      `squadra-<name>.service` / `.timer`, `install-units` refusing to
      overwrite a unit with a different `FLEET_HOME`, compose project
      `squadra-<name>-increment-<id>`, `tag_prefix` defaulting to `<name>:`
      (explicit still overrides); (3) the name stamped into `fleet_root`,
      and a tick that refuses when it differs while increment dirs exist;
      (4) GitHub `validate_config()` fails closed unless the board's
      repository is `origin` (via `repo.target_remote_url`; ADO gets no
      equivalent, noted in README); (5) README Scoping. SQ5g2 (~0.5 unit):
      `install-units` refuses a second fleet on the host whose board
      repository matches an existing one's. No verb-contract, design-to-board
      or N32 change. Acceptance test: two fleets both carrying item 105,
      rendered to a temp unit dir and a fake docker runner.
    - Branch template: enforce the precondition, not the template. If issue
      numbers are unique per repository, two fleets on one repository are
      two fleets on one issue, and a per-fleet branch would turn a loud
      collision into silent duplicate work (and `completed_pr_url` would
      finalize on the other fleet's PR), so refuse the configuration
      (SQ5g2). If not unique, require `{fleet}` and `{id}` in the template
      at load.
    - If Rich rules (B) anyway: `--board NAME` required on every verb even
      with one entry, plus a `squadra board list` read verb; design-to-board
      gets it by DB-D5's parent rule one level up; `cap` shared, round-robin
      across boards; split (A′) first, then B1 (config and verbs), B2 (the
      tick), B3 (claude-skills). He rejects a (C) of entries each with its
      own `fleet_home`.
    - Priced: (A) as N34 states it ~0.25 unit, all four collisions left;
      (A′) ~1.5 units, no frozen contract reopened; (B) ~3–3.5 units plus
      0.5–1 in claude-skills, two contracts reopened. His risk number can't
      separate them on a serial chain (all 1.0); float against the second
      fleet's arrival and contracts reopened (0, 0, 2) do.
    - Juval's blocking questions: (1) is any board planned in the next few
      months on the same repository as an existing fleet? If none, (B) has
      no user. (2) On GitHub, are issue numbers unique per repository, and
      can one issue sit on two Projects? From GitHub's documented model,
      not checked live here: issues and PRs share one number sequence per
      repository, so yes; and an issue can be added to several Projects, so
      yes. Under (A′) that picks "refuse the configuration" (SQ5g2), not
      `{fleet}` in the template.
    - **Eric:** the term is **Fleet**, the named claimant one `squadra.toml`
      configures, and "instance" is to be avoided (a deployment word). The
      setting is `[fleet] name`, in a new top-level `[fleet]` table, not
      `[board] name`: none of the derived names is a property of the board,
      and under (B) `[board] name` and `[[boards]] name` would be one key
      on two concepts. The name is the identity, so it has no env or flag
      layer, and a rename is a different fleet. `tag_prefix` stays in
      `[board]`, defaulting from the name. He drafted the Fleet glossary
      entry and settled row (rejected: Instance, Squadra instance,
      Deployment), a new glossary header, and "fleet host" for a host
      running one or more fleets; `FLEET_*` env names stay. Two additions
      to Juval's build: the stamp check must also refuse an **unstamped**
      `fleet_root` with increment dirs and no explicit `tag_prefix` (an
      upgrade from the default `fleet:` is a rename the stamp can't see),
      and `fleetctl.sh`'s tmux names (`fleet-ticker` at line 40; the stop
      message's `fleet` runner session at line 79, likely stale) are a
      host-global collision on the dev path. Under (B): Increment identity
      keeps its wording with key (board, item id) and one added sentence;
      Origin stays "unique on the board", never compared across boards; the
      selector names a Board by the fleet's own name for it, with a Board
      glossary entry.
    - **For Rich, in this order:**
      1. (A) as N34 states it, (A′), or (B)? Juval's blocking question 1
         decides whether (B) has a user. Ruling (A′) or (A) knowingly
         reverses WSQ1 Choice 3's direction and amends Choice 4 to
         "fleets split by repository; each fleet has one board; name each
         fleet before the second fleet on any host".
      2. Under (A′): confirm the GitHub facts above, and so the branch rule:
         refuse a second fleet on one repository (SQ5g2) rather than
         `{fleet}` in the template.
      3. Eric's names: Fleet, `[fleet] name` (no env or flag layer),
         `tag_prefix` in `[board]` defaulting to `<name>:`; the glossary
         entry, settled row and header go in with SQ5g.
      4. Eric's two additions to the build: the unstamped-`fleet_root`
         refusal, and deriving the `fleetctl.sh` tmux names (or deleting the
         stale runner line).
      5. The split: SQ5g (~1 unit), then SQ5g2 (~0.5), both before a second
         fleet lands on gswa-fleet-host.
  - **Ruled by Rich, 2026-10-28 (after PR #65 merged): (A′), all five.**
    Recorded in the Choice of both session files.
    1. (A′). WSQ1 Choice 3's rule stands (each fleet's `squadra.toml`
       requires `claim_scope`); its `[[boards]]` direction is reversed,
       knowingly, because a fleet is one repository. WSQ1 Choice 4 now
       reads: fleets split by repository, each fleet has one board, and each
       fleet is named before the second fleet on any host.
    2. Juval's blocking question 2, checked by two subagents against
       GitHub's documentation, not live:
       - Issue numbers are scoped to the repository (documented,
         `issue(number:)`). That a number names one issue or PR in a
         repository is inferred from `issueOrPullRequest(number:)`, not
         stated. Reuse after deletion, transfer or conversion to a
         discussion is not documented either way. This replaces SQ5f2's "so
         yes" above, which overstated it.
       - One issue can be on several Projects (GitHub blog, 2023-07-11),
         with field values per project item, so Status can differ per
         project. The GitHub adapter already reads Status only from the item
         on the configured project (`github_board.py` `_issue`, matching
         `project { id }`), so no change.
       So the branch rule is Juval's first branch: SQ5g2 refuses a second
       fleet on a host whose board repository matches an existing fleet's;
       no `{fleet}` in the branch template.
    3. Eric's names, all of them: Fleet (avoid instance, squadra instance,
       deployment), `[fleet] name` in a new `[fleet]` table with no env or
       flag layer, `tag_prefix` in `[board]` defaulting to `<name>:`, "fleet
       host"; his glossary entry, settled row and header go in with SQ5g.
       His (B) half is not taken.
    4. Both of Eric's additions, in SQ5g.
    5. SQ5g, then SQ5g2.

- N35 (SQ5g): **one fleet per repository, `[fleet] name`, built as Rich
  ruled N34 (A′).** N34's ruling already records WSQ1 Choice 3's rule standing
  with its direction reversed, and Choice 4's new wording, so nothing was
  added for that.
  - `[fleet] name` (`SquadraConfig.fleet_name`): required, no default, no env
    or flag layer (a test sets `FLEET_NAME` and the toml wins),
    `^[a-z0-9][a-z0-9-]{0,23}$`. The charset was checked against compose's
    project rule (lowercase alphanumerics, `-`, `_`; alphanumeric first) and
    systemd's unit-name characters. It also excludes tmux's `.` and `:`, and
    the 24-character cap keeps `<name>:awaiting-pr-approval` within GitHub's
    50-character label limit. `squadra init` scaffolds `name = ""` and leaves
    `tag_prefix` commented out. Loading resolves `claim_scope` before the
    name, so a missing scope is still the first failure (SQ5d's guard test is
    unchanged). `load_fleet_name` reads only the name, for the ticker and
    `install-units`.
  - Derived names: units `squadra-<name>.service` / `.timer` (templates keep
    their packaged names; `units.unit_filename`, the timer's `Unit=`); compose
    project `squadra-<name>-increment-<id>` (`supervisor._spec_for`, its one
    construction site); `tag_prefix` `<name>:` unless `[board].tag_prefix` or
    `FLEET_TAG_PREFIX` is set (`SquadraConfig.tag_prefix_explicit`); tmux
    ticker `squadra-<name>-ticker`, set by `squadra start|stop|status|log` as
    `FLEET_TICKER_SESSION` (an explicit value still wins; `fleetctl.sh` now
    requires it). The stop message's `fleet` runner session was stale: no
    runner uses tmux (none in `src/`), so the line is replaced by "in-flight
    increments keep running in their sandboxes".
  - `install-units` refuses (`UnitConflictError`, exit 1, nothing written)
    when an existing `squadra-<name>.service` renders a different
    `FLEET_HOME`; a re-install over its own home is allowed. **Finding for
    Rich:** `install-units` takes the name as a required `--fleet-name`,
    because on a fresh host it runs before the first tick clones `FLEET_HOME`,
    so there is no `squadra.toml` to read. When the checkout's
    `squadra.toml` exists, a different `[fleet] name` is refused (exit 2).
    It is not an override layer: the tick reads the name from the toml only.
    The residue is a fresh host whose flag disagrees with the toml it later
    clones. The unit is then misnamed, but the stamp, the projects and the tag
    prefix still follow the toml. SQ5g2 reads existing units anyway and can
    close it. `fleet-host-activate.sh` and `cloud-init.fleet-host.yaml` now
    require `FLEET_NAME` in the env file and pass it.
  - Stamp: `fleet_root/fleet-name`. `engines.fleet_stamp_refusal` (pure)
    refuses with increment dirs (`fleet_root/<digits>/`) present when the
    stamp names another fleet, or when there is no stamp and `tag_prefix` is
    defaulted (Eric's addition). `run_tick` checks it under the lock before
    any board read and returns 1. The write goes through a new seam,
    `TickSeams.write_fleet_stamp`, which `dry_run_seams` absorbs, so a dry run
    checks but never stamps. Increment dirs outlive finalize (they hold the
    archive), so a rename is refused until they are moved aside, as ruled
    ("while increment dirs exist").
  - Origin check: `GhApiGitHub` takes a required `read_origin` (no default:
    a default would skip the check). `validate_config()` checks it first and
    fails closed unless `github_repository_of(origin)` equals
    `[board.github].repository`. The comparison ignores case and the host;
    all three remote forms are parsed. The error names only the parsed
    `owner/name`, never the URL, because an HTTPS origin can carry a token.
    `_build_github` passes `repo.target_remote_url(fleet_home)`, which falls
    back to `FLEET_APP_REPO_URL` before the first clone. ADO has no check
    (README says so).
  - Tests: `tests/test_fleet_name.py` is Juval's acceptance test plus the
    unstamped case and the dry-run stamp. Each of its four guards (compose
    name, origin check, stamp refusal, unit overwrite) was mutation-checked:
    removing the guard fails a named test. The existing suites now use
    `[fleet] name = "test-fleet"` and the derived project names. One dry-run
    contract fixture seeds claims under `fleet:`, so it sets
    `tag_prefix = "fleet:"`, the documented upgrade path.
  - **Not done, for SQ5g2:** `docs/fleet-host/goss.yaml` and `SMOKE.md`
    still expect `/etc/systemd/system/squadra.service` / `.timer`, so the
    on-host acceptance fails after an upgrade until they are templated on the
    name (goss `--vars`). Deploying this to gswa-fleet-host also needs
    `FLEET_NAME` in `/opt/squadra/fleet-host.env`, removal of the old
    `squadra.service` / `.timer`, and either `tag_prefix = "fleet:"` in its
    `squadra.toml` or no increment dirs.

- N36 (SQ5g2): **one fleet per repository on a host, built as Rich ruled N34
  (Juval's first branch).** The branch template is unchanged.
  - **How repositories compare across providers (decided here).** A
    normalised key, `repo.repository_identity(url)`: Azure DevOps remotes
    (HTTPS on `dev.azure.com`, legacy `<org>.visualstudio.com` with or without
    `DefaultCollection`, SSH on `ssh.dev.azure.com` / `vs-ssh.visualstudio.com`)
    become `org/project/repo`, with the project taken from the repo when the
    URL omits it. Any other remote with a two-segment path becomes
    `owner/name`, the key `github_repository_of` reads (a test pins the two
    agreeing). Userinfo, transport, `.git`, percent-encoding and case are
    dropped. The host is not compared, as SQ5g's origin check does not compare
    it, which errs toward refusing (a github.com and a GHE repository with
    one `owner/name` refuse). The two shapes differ in segment count, so an ADO
    and a GitHub key never collide. Anything else (a local path, a GitLab
    subgroup) has no key, and the check fails closed on it. Not chosen: asking
    each fleet's provider, which would mean reading the other fleet's
    `squadra.toml`, absent on a fresh host. The parser lives in `repo.py`
    rather than reusing `github_board`'s regex, so a host-side module never
    imports an adapter. The two-segment pattern is copied; the agreement test
    guards the copy.
  - `install_units` takes a required `repository_of` (no default, for SQ5g's
    `read_origin` reason); `cli` passes `repo.fleet_repository`, which is
    `target_remote_url` with the fallback set to that fleet's own clone URL
    (the unit's rendered `FLEET_APP_REPO_URL`, or `--app-repo-url` for the
    fleet being installed), never this process's env. Before writing, after
    SQ5g's same-name check, it reads every `squadra.service` and
    `squadra-*.service` in the unit dir except its own. The unnamed pre-SQ5g
    `squadra.service` counts, so an upgrade must remove it first. A file
    naming no `FLEET_HOME` is not squadra's and is skipped. On a match it
    raises `UnitConflictError` (exit 1, nothing written). **Both sides fail
    closed:** this fleet's repository must resolve ("pass --app-repo-url or
    clone the fleet home first"), and so must every other fleet's, since an
    unresolvable one could be the same repository ("remove that unit if the
    fleet is gone, else clone its home"). The first fleet on a host is not
    asked for a repository: there is nothing to compare. Messages name the
    key, never the URL (an HTTPS URL can carry a token; tested).
  - **N35's residue closed with the check, not recorded as not worth it.** The
    service renders `Environment=FLEET_INSTALLED_NAME=<name>`; `fleet-tick`,
    after syncing the checkout and before the tick, refuses (exit 2) when
    `squadra.toml`'s `[fleet] name` differs, naming both and saying to
    reinstall under the configured name. It never sets the name, and unset
    (a tick outside the unit) checks nothing. A distinct variable, not
    `FLEET_NAME`: `FLEET_NAME` is the env file's input to `install-units`,
    and `test_the_name_has_no_env_layer` pins that the tick ignores it. The
    goss dry-run passes `FLEET_INSTALLED_NAME` from the goss var, so the smoke
    catches a flag that disagrees with the cloned toml.
  - Fleet-host acceptance: `goss.yaml` is templated on `{{.Vars.fleet_name}}`
    (`--vars-inline` from `FLEET_NAME` in `fleet-host.env`), asserts
    `FLEET_INSTALLED_NAME` in the service and asserts the unnamed
    `squadra.service` / `.timer` absent. `SMOKE.md` has the templated run, the
    renamed activation commands and "Upgrading a host from before fleet
    names": name the fleet in the toml, keep `tag_prefix = "fleet:"` or have
    no increment dirs, add `FLEET_NAME` to the env file, stop and remove the
    unnamed units, re-run `fleet-host-activate.sh`. **Not verified on a
    host:** goss is not installed here. The rendered YAML was parsed with
    the name substituted; the `--vars-inline` flag and the template render are
    goss behaviour I did not run.
  - Tests: `tests/test_one_fleet_per_repository.py` (same repository refused
    with nothing written; different repositories both install; unresolvable
    new and unresolvable other both refuse; the unnamed unit counts; real-git
    resolution of two ADO remote forms, and of each fleet's own clone URL on
    a fresh host; the identity table; the installed-name check both ways).
    Mutation-checked: removing the refusal, never matching, allowing an
    unresolvable new or other repository, ignoring the unnamed unit, and
    switching off the installed-name check each fail a named test.
  - Still out of reach: two hosts on one repository (Choice 4's shelved case),
    and a fleet whose remote is not GitHub- or ADO-shaped, which refuses
    whenever a second fleet is on the host.

## Session log

| Session | Date | Unit | Outcome | Handoff written |
|---|---|---|---|---|
| SQ1 | 2026-10-09 | SQ1 | `Lifecycle.WITHDRAWN` (terminal), `check_transition`, `predecessor-withdrawn` tick state, unmapped states fail `validate_config` and `item_state`, fakes + `[board.states].withdrawn`, ADR-0004, glossary Origin + Withdrawn. ruff, pyright, 399 tests green | `handoffs/SQ2-verb-contract.md` |
| SQ2 | 2026-10-09 | SQ2 | Gate checked (DB-D2–DB-D5 ruled, PR #43 merged). `IncrementBoard` verbs, `BoardAccess.create_increment` / `items_with_origin`, engine rules (scope, A2, A3), DB-D2 in `check_transition` and ADR-0004, ADR-0005, `verb-contract.md` with the SQ4 CLI surface. ruff, pyright, 462 tests green | `handoffs/SQ3-fake-provider.md` |
| SQ2b | 2026-10-09 | SQ2b | Juval consulted on N6–N8 (method). N6 settled: both refusals kept, each naming its way out; ADR-0005 decision 3, `verb-contract.md`, contract tests (Juval's 9, 10). N7 (withdraw by Origin) and N8 (crash-safe create) put to Rich: both change DB-D rulings. N10 opened. ruff, pyright, tests green | `handoffs/SQ3-fake-provider.md` (gate: SQ2b merged + Rich's N7/N8 ruling) |
| SQ2b (ruling) | 2026-10-09 | SQ2b | Rich ruled N7 (withdraw by Origin) and N8 (Juval's crash-safe create), both reopening DB-D1/DB-D4; recorded in the session file's Choice, ADR-0005 and `verb-contract.md`. Build deferred to SQ2c (session budget) | `handoffs/SQ2c-build-n7-n8.md` |
| SQ2c | 2026-10-09 | SQ2c | Gate checked (PR #47 merged). Eric named `UnknownOriginError` and *partial* (N13). `withdraw_increment(origin)` (N11); crash-safe create contract: `OriginRecord.lifecycle` `None` for a partial item (N12), `create_increment(request, partial_item=None)` with the four obligations, `check_queue_finishes` (N14), `increments_by_origin` omits partial items; both fakes with `seed_partial`; Juval's tests 2–8 plus a subset-finish retry. ADR-0005 decisions 1–2 amended, `verb-contract.md` rewritten. claude-skills DB-D1/DB-D4 still owed. ruff, pyright, tests green | `handoffs/SQ3-fake-provider.md` (gate: SQ2c's PR merged) |
| SQ3 | 2026-10-09 | SQ3 | Gate checked (PR #48 merged). `provider = "fake"` registered: `JsonFileBoard` over `<FLEET_HOME>/.squadra/fake-board.json`, flock + atomic replace (N16); create as k locked writes with a one-shot, file-carried fault (N17). Third shape in the `board`/`fake_board` contract fixtures; Juval's tests 1–5 for every j < k, test 1 also end to end through `tick --dry-run` under both scopes; test 4 holds for j ≥ 2 only (N14, pinned). README provider row. ruff, pyright, 617 tests green | `handoffs/SQ4-board-cli.md` |
| SQ4 | 2026-10-09 | SQ4 | Gate checked (PR #49 merged). `squadra board {queue,withdraw,origins}` in `cli.py` as Clients over `IncrementBoard` (config → `build_board` → `validate_config` → verbs); own argparse tree, every error one prefixed stderr line; exits 0/2/3/1 per `verb-contract.md` (N19). `IncrementBoard.increments_and_partial_items()` for `origins`' partial-item lines (N18). `tests/test_cli_board.py` (22 tests): every exit code, every refusal leaves the file byte-identical, crash and retry across two processes, DB-D7 (QUEUED → ACTIVE → DONE and a duplicate Origin written between calls by the test). DB-D10 checked: matches. README row. ruff, pyright, 639 tests green | `handoffs/SQ5-github-adapter.md` (gate: SQ4 merged + claude-skills DB4) |
| SQ5 (split) | 2026-10-09 | SQ5 | PR #51. Gate checked (squadra PR #50 merged; claude-skills DB4 merged as PR #16). Branch fast-forwarded to `main` (`c36fe8f`). Split SQ5 into SQ5a (read half, `validate_config`, the tick's writes, `[board.github]`), SQ5b (the two primitives, obligations 1–4, WITHDRAWN as closed / not planned; unblocks DB5) and SQ5c (`[[boards]]`, `in_claim_scope`, `seams.ado` rename, N10). Order not a real choice: DB5 needs a and b, not c. N20 (native-state model) put to Rich with a proposal; N21 lists the GitHub facts to verify. No code: the handoff's reading took the session's budget, so SQ5a goes to a fresh session | `handoffs/SQ5a-github-read.md` |
| SQ5a | 2026-10-09 | SQ5a | PR #52. Gate checked (PR #51 merged, N20 ruled). Branch fast-forwarded to `main` (`2b70508`). `provider = "github"` registered: `squadra.github_board.GhApiGitHub` over `gh api` (N22), `[board.github]` (`GitHubBoardConfig`, required only for github), the native-state model per Rich's N20 ruling (ADR-0006, N23), reads, `validate_config`, the adapter's writes (Status, close with reason, labels, Markdown comments; `render_github_markdown` moved into `squadra.board`). `board` contract suite four-shaped over an in-memory GitHub; `tests/test_github_board.py` for N20's edges and the config. N21 verified (above). Real board: Rich named none this session; ran read-only against the only project on his account, `rinman24/squadra-sandbox` + user project #1 "squadra sandbox" (Todo/In Progress/Done), with the scaffold's default map: `validate_config` passed, all four buckets empty, `item_links(1)` empty, `item_state(1)` raised "not on the project" as designed. Cut: the tick's per-item report of a refused requeue to SQ5b. Scaffold default map and comment, README rows, glossary note under Withdrawn. ruff, pyright, tests green | `handoffs/SQ5b-github-writes.md` |
| SQ5b | 2026-10-27 | SQ5b | PR #53. Gate checked (PR #52 merged). Branch fast-forwarded to `main` (`9a324ed`). GitHub's `create_increment` (k = 5 + n ordered `gh` writes, idempotent under `partial_item`, commit marker last, Status read back) and `items_with_origin` (every Origin-bearing issue of the repository by GraphQL listing, not search); write endpoints verified by introspection (N24). `increment` suite four-shaped, crash-safe suite on `file` + `gh` with a fault after each j < k, Juval's interloper and Eric's cleared-Status tests, `\r\n` bodies (N23). README provider row. Cut: the tick's refused-requeue report and the live sandbox run to SQ5c, with an open question on the tick case (N24). ruff, pyright, 827 tests green | `handoffs/SQ5c-boards-scope.md` |
| SQ5c | 2026-10-27 | SQ5c | PR #54. Gate checked (PR #53 merged). Branch fast-forwarded to `main` (`34a2234`). In-tick refused state write reported per item and the tick goes on (`StateWriteRefusedError`, N25), contract-tested for every close reason. N24's between-ticks case put to Juval: option A (release), awaiting Rich's Choice. Live sandbox run (N26): every write, N24's three facts verified. Cut: the release rule, `[[boards]]`, `in_claim_scope`, the rename and N10 to SQ5d. ruff, pyright, tests green | `handoffs/SQ5d-release-and-boards.md` (gate: SQ5c merged + Rich's N25 choice) |
| SQ5d | 2026-10-27 | SQ5d | PR #55. Gate checked (PR #54 merged; Rich's N25 Choice filled in). Branch fast-forwarded to `main` (`5fbba34`). Eric named the rule (relinquish / `withdrawn-in-flight`, N27). Built: engine step 0 + hand-DONE sandbox stop, the claimed-WITHDRAWN gather, `_relinquish`, the `Relinquished` comment on both renderers, the `withdrawn` park (manifest vocabulary split from status), the GitHub listing memo; ADR-0004, GLOSSARY, README, N3, N10 (closed). Contract tests on `gh` + ADO-shaped fake, completed and N10 variants. Cut: `[[boards]]`, `in_claim_scope`, the rename to SQ5f. Rich's mid-session add-on (DB-D12's `squadra board scope` verb) recorded as SQ5e, not built (session budget). ruff, pyright, tests green | `handoffs/SQ5e-board-scope-verb.md`, then `handoffs/SQ5f-boards-scope.md` |
| S7 | 2026-10-27 | SQ6 (done), SQ7 (todo); claude-skills DB7 | PR #56. Gate checked (SQ5d merged, PR #55, `e4083e6`). Branch fast-forwarded to `main`. Recorded DB5's live facts against N23, N24 and N26 (N28): none of N24's facts is still open. Reproduced DB7's findings in a temp `FLEET_HOME` (never writing the sandbox); Rich asked `/ask-juval` before ruling, then ruled as Juval said: (i) fixed (SQ6), (ii) dropped, (iii) row SQ7. Juval's probe was run alone on Rich's Mac: SSH, passes, so SQ7 is latent there (N29). DB-D12's verb is not built; it stays SQ5e. ruff, pyright, 862 tests green | `handoffs/SQ5e-board-scope-verb.md` (unchanged) |
| SQ5e | 2026-10-27 | SQ5e | PR #57. Gate checked (SQ5d merged, PR #55; S7 merged, PR #56). Branch fast-forwarded to `main` (`500d42a`). Juval's Blocking question 1 confirmed: one predicate already. Built `IncrementBoard.parent_in_claim_scope` (was `_in_scope`) and `squadra board scope --parent ID` → `{"parent": N, "in_claim_scope": bool}`, exit 0 either way, `validate_config()` first, no write. Agreement test (2 × 2, byte-identical board), a four-shape contract test, exit 2/1 tests. `verb-contract.md` and README rows. N30. ruff, pyright, 887 tests green | `handoffs/SQ5f-boards-scope.md` (unchanged) |
| S6b | 2026-10-27 | — (claude-skills relay) | A claude-skills design-to-board session, at Rich's request. Branch fast-forwarded to `main` (`8ceb957`, PR #57). Added SQ8, the partial-items-as-data request from claude-skills DB-D13 (Rich's DB11 ruling, Juval's way). No code | — |
| Handoffs (SQ5f, SQ7) | 2026-10-27 | — | At Rich's request, after SQ5e merged (PR #57). Branch fast-forwarded to `main` (`0a83e8a`, PR #58). Wrote `handoffs/SQ7-host-git-credentials.md` from the SQ7 row, N29 and Juval's session (on board-knowledge's unmerged branch `docs/juval-squadra-init-preflight`). Refreshed `handoffs/SQ5f-boards-scope.md`: gate met, `_in_scope` → `parent_in_claim_scope`, N30 and SQ5e's two guard tests named, `[[boards]]` shape to settle first (how the `board` verbs pick a board is a verb-contract question for Rich), SQ7 and SQ8 out of scope. Order: SQ5f, then SQ7 (row's dependency set to SQ5f, for order only). SQ8 still has no handoff. No code | `handoffs/SQ5f-boards-scope.md`, then `handoffs/SQ7-host-git-credentials.md` |
| Handoff (SQ8) | 2026-10-27 | — | At Rich's request. Wrote `handoffs/SQ8-partial-items.md` from the SQ8 row, claude-skills DB11 and DB-D13 (read-only, `feat/design-to-board`) and Juval's session. The shape is left to the session (`/ask-juval`, Rich rules), with a design point for Juval: design-to-board checks before any write, when a row's predecessors may have no item ids yet, so a question verb over `queue`'s full arguments can't always be asked. claude-skills S8 (DB10) is done (PR #21), so SQ8 is the only squadra item design-to-board waits on (S9, DB11). No code | `handoffs/SQ8-partial-items.md` |
| SQ8 | 2026-10-27 | SQ8 | Gate checked (no other unit in flight; SQ5f and SQ7 todo, no open PR). Branch already at `main` (`14b9258`, PR #60). Juval consulted blind on the shape (batched `partial-items` question, predecessors by Origin), Eric on the names; N31. His blocking question answered from DB-D4/DB-D7. Cut line taken before a ruling: proposal and Rich's four choices recorded, build written as a fresh handoff. No code | `handoffs/SQ8b-build-partial-items.md` (gate: Rich's N31 ruling in both session files' Choice) |
| SQ8 (ruling) | 2026-10-27 | SQ8 | Rich ruled N31: Juval's shape, exit 3 on a board-level duplicate, all of Eric's names, partial item into `GLOSSARY.md` and `GLOSSARY-SETTLED.md` (added). Choices recorded in both session files. `handoffs/SQ8b-build-partial-items.md` rewritten with the merged build spec. No code | `handoffs/SQ8b-build-partial-items.md` (gate: this PR merged) |
| SQ8b | 2026-10-27 | SQ8 | PR #62. Gate checked (PR #61 merged; no other board-writes PR open). Branch fast-forwarded to `main` (`42778e8`). Built the ruled spec: `RequestField`, `ProposedRequest`, `PartialItemReport` (domain); `partial_item_disagreements`, `refusal_to_finish`, `item_ids_carrying`, `check_queue_finishes` as their wrapper (engines); `IncrementBoard.partial_items`; `squadra board partial-items --requests-file PATH\|-` (exit 0 for any verdicts, 2 usage/config, 3 only on a board-level duplicate, 1 provider). Agreement test on the fake file through the CLI (7 plans × j < 5) and on four shapes in the contract suite, fence test, exit tests, engine unit tests; checked by mutation. `verb-contract.md`, README rows, ADR-0005 note. N32. ruff, pyright, 1063 tests green | — (claude-skills S9's gate is this merge) |
| SQ9 + handoff (SQ5f) | 2026-10-27 | SQ9 | PR #63. At Rich's request after PR #62 merged. Branch fast-forwarded to `main` (`1ec3918`). SQ9: `--body-file` read as bytes, so CRLF bodies pass exactly; non-UTF-8 body file exit 2 (N33). Refreshed `handoffs/SQ5f-boards-scope.md`: gate is this PR, five `board` verbs, `partial-items` per board (N32's assumption), SQ8b's agreement tests as guards, SQ8's later step out of scope. claude-skills S9 (DB11) handoff written in claude-skills, not here. ruff, pyright, tests green | `handoffs/SQ5f-boards-scope.md` (gate: this PR merged) |
| SQ5f | 2026-10-27 | SQ5f | PR #64. Gate checked (SQ9 merged, PR #63; no board-writes PR open). Branch fast-forwarded to `main` (`e89d938`). Built `BoardAccess.in_claim_scope` on the three adapters, scoped at construction from `[board]` with no default; the tick asks the adapter, not the parent link; `parent_in_claim_scope` moved to `config` and re-exported (closed layering); `seams.ado` renamed `seams.board`. Agreement contract test on four shapes, mutation-checked; SQ5e and SQ8b guard tests unchanged and green. README, ADR-0005. Cut: `[[boards]]` to SQ5g, shape put to Rich with (A) one instance per board recommended (N34). ruff, pyright, tests green | `handoffs/SQ5g-boards.md` (gate: SQ5f merged + Rich's N34 ruling) |
| Handoff (SQ5f2) | 2026-10-28 | — | At Rich's request before he rules N34. Wrote `handoffs/SQ5f2-consult-boards.md`: `/ask-juval` blind on the `[[boards]]` shape, then `/ask-eric` on Increment identity, Origin uniqueness across boards and "Fleet". SQ5g now depends on SQ5f2. No code | `handoffs/SQ5f2-consult-boards.md` (gate: PR #64 merged) |
| SQ5f2 | 2026-10-28 | SQ5f2 | PR #65. Gate checked (SQ5f merged, PR #64; no other board-writes PR open). Branch fast-forwarded to `main` (`b09414d`). `/ask-juval` blind on N34: (A′), one fleet per repository with a required name and every host-global name derived from it, built now; he disagrees with N34's ledger-only (A) (units, compose `down -v` and `tag_prefix` collide on one host; board repository vs `origin` unchecked). `/ask-eric`: Fleet, `[fleet] name`, plus two additions (unstamped `fleet_root`, `fleetctl.sh` tmux names). His code claims checked. Both session files in board-knowledge, Choice blank. N34 amended with five questions for Rich in order; SQ5g row and handoff updated for (A′). No code | `handoffs/SQ5g-boards.md` (gate: this PR merged + Rich's N34 ruling) |
| N34 ruling | 2026-10-28 | SQ5f2 | PR #66. At Rich's request after PR #65 merged. Branch fast-forwarded to `main` (`70d9908`). Rich ruled (A′), Eric's names and both additions, SQ5g then SQ5g2. Juval's blocking question 2 checked against GitHub's documentation by two subagents: numbers per repository (one-number-one-issue inferred, reuse undocumented), an issue on several Projects (the adapter already reads only its own project's item). Choice filled in both session files. N34, SQ5g row, new SQ5g2 row, SQ5g handoff narrowed to (A′). No code | `handoffs/SQ5g-boards.md` (gate: this PR merged) |
| SQ5g | 2026-10-28 | SQ5g | PR #67. Gate checked (PR #66 merged; only dependabot PRs open). Branch fast-forwarded to `main` (`6c6d67c`). Built (A′) as ruled: required `[fleet] name`; units, compose project, default `tag_prefix` and tmux ticker derived from it; `install-units --fleet-name` refusing another fleet's unit; `fleet_root` stamp with the unstamped refusal; GitHub `validate_config()` checks `origin`. README Scoping, Eric's Fleet glossary entry, header and settled row. Juval's acceptance test, mutation-checked. N35. No cut. ruff, pyright, 1131 tests green | `handoffs/SQ5g2-one-fleet-per-repository.md` (gate: this PR merged) |
| SQ5g2 | 2026-10-28 | SQ5g2 | PR pending. Gate checked (PR #67 merged). Branch fast-forwarded to `main` (`ce35dcd`). `install-units` refuses a fleet whose repository another fleet's service on the host serves, the unnamed pre-SQ5g unit included, failing closed when either repository is unresolvable; repositories compared by `repo.repository_identity` (ADO `org/project/repo`, GitHub `owner/name`). N35's residue closed: the service carries `FLEET_INSTALLED_NAME` and `fleet-tick` refuses a differing `[fleet] name`. goss / SMOKE templated on the name, with the upgrade steps. N36. No cut. ruff, pyright, tests green | `handoffs/SQ7-host-git-credentials.md` (existing; gate widened to SQ5g2) |
