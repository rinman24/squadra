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
| SQ5f | From SQ5d's cut, WSQ1's deferred parts: `[[boards]]` with `claim_scope` per entry (the check fires wherever a board is added), adapter-owned `in_claim_scope(item_id)` (the supervisor stops reading parent links for scope), rename `seams.ado`. Keep SQ5e's `scope` verb, `queue_increment`'s scope refusal and `Increment.in_claim_scope` on one predicate through the rework | D | SQ5e | todo |
| SQ6 | claude-skills DB7 finding (i): `squadra init --check` validated with `--provider`'s default `ado`, not the toml's provider. `--provider` now defaults to `None`; the scaffold falls back to `ado`, and the check passes `None` so `load_config`'s precedence (toml < env < flag) holds. Tests: toml wins with no flag or env, `FLEET_PROVIDER` beats the toml, the flag beats both | — (DB7) | — | done (PR #56); N29 |
| SQ7 | claude-skills DB7 finding (iii), Juval's option D (board-knowledge `sessions/2026-10-27-juval-squadra-init-preflight-findings.md`): host-side git credentials are ADO-shaped (`repo._CREDENTIAL_HELPER` supplies `$AZURE_DEVOPS_EXT_PAT`), and the claim's own fetch (`worktree.py:107`) bypasses that helper, so the preflight and the claim auth differently. Put credential acquisition for host-side git ops behind one boundary, resolved from the target remote's host rather than the board provider (an ADO board over a GitHub repo is valid). The probe and every host-side clone, fetch and push go through it, so the "exact auth path" claim is true. Keep it fail-closed. The rejection message names the credential actually used. Rename the `_ado_*` probe symbols and sweep ADO assumptions outside `board.py` (e.g. the `scaffold.py` comment on the provider PAT). Not wanted: relabelling the log line, skipping the probe when the provider isn't ADO (silent broadening on the claim path), folding into SQ5f. Latent on Rich's Mac (SSH origin, N29); must land before the first live GitHub tick over an HTTPS origin | — (DB7) | SQ5f, for order only: no design dependency, but both edit `TickSeams` on this branch | todo; `handoffs/SQ7-host-git-credentials.md` |
| SQ8 | claude-skills DB-D13 (DB11; Rich's ruling of Juval's "A second break in the same rule", board-knowledge `sessions/2026-10-27-juval-first-run-parent-scope.md`): report partial items as data, in a fixed format, instead of only `squadra board origins`' one stderr line per partial item (N18), which is not contract. The case: a first run stops mid-create of a queue, leaving a partial item; the map is revised (a title changes); `increments_by_origin` leaves the partial item out, so design-to-board's re-run takes the first-run path, its checks pass, and `queue` is refused (`QueueRefusedError`, exit 3) with a fix that loops. Each entry carries at least the item id and the Origin, and enough for a caller to tell whether a `queue_increment` with given arguments would finish it or be refused under N14's retry rule; better, keep that judgment in squadra behind `check_queue_finishes`, as SQ5e did for scope. Verb or shape is this effort's to settle (`/ask-juval`, `/ask-eric` if wanted); not in `origins`' `{origin: …}` shape, for the reason DB-D12 kept scope out of it. Add it to `verb-contract.md`; the fake answers it too. Unblocks claude-skills DB11 (S9), which first probes the loop on the fake | — (DB11) | — (scheduled around SQ5f, Rich's call; never alongside another unit on this branch) | todo; shape proposed by Juval and named by Eric, awaiting Rich's ruling (N31); build: `handoffs/SQ8b-build-partial-items.md` |

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
  and Eric's names, awaiting Rich's ruling.** Consulted blind on the
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
  - For Rich to rule (fill the Choice in both session files): (1) Juval's
    shape over the listing and the middle way; (2) exit 3 on a board-level
    duplicate Origin; (3) Eric's names, all or which; (4) partial item into
    `GLOSSARY.md`, and whether into `GLOSSARY-SETTLED.md`.
  - Later, not SQ8 (Juval): `IncrementBoard` goes to six operations; once
    DB11 uses this read, consider dropping the partial half of
    `increments_and_partial_items` and `origins`' stderr line.

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
