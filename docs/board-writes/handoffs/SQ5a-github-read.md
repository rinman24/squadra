# Handoff: SQ5a · the GitHub adapter's read half (board-writes, part 5a)

Rich starts a fresh `claude` session in `~/Code/squadra` and gives it this file.
**Do not start until both hold:** the SQ5 split PR (#51) has merged on
squadra `main` (it adds this file, SQ5a–SQ5c and N20–N21 to the ledger), and
Rich has ruled on N20 after consulting Juval and Eric (their session files in
board-knowledge `sessions/`, the ruling recorded in N20). If either is
missing, stop and tell Rich; do not build the proposal in its place. Build
what the ruling says. Where it differs from the proposal, the ruling wins
everywhere this handoff cites N20.

## Context

SQ5 (DB-D1 activity D) was split by the previous session into SQ5a (this),
SQ5b (the two write primitives, which unblock claude-skills DB5) and SQ5c
(`[[boards]]`, `in_claim_scope`, N10). That session spent its budget on
reading, so this list is narrow on purpose. Read only these, and the parts named:

1. squadra `CLAUDE.md`; `docs/board-writes/LEDGER.md` rows SQ5a–SQ5c and notes
   N1, N4, N20, N21 (skip the rest unless a test points you there).
2. `src/squadra/board.py`: `BoardAccess`, then `AzCliAdo` as the pattern (an
   injectable `run: Callable[[Sequence[str]], str]` transport, parsing
   helpers at the bottom, `PROVIDERS`).
3. `src/squadra/config.py`: `load_config`, `_resolve_states` (GitHub must
   declare `[board.states]`; it already fails without them).
4. `tests/contract/conftest.py` (the `board` fixture and its shared seed) and
   `tests/helpers/board_fakes.py`'s `GitHubShapedFakeBoard` and
   `render_github_markdown`, the shape your adapter replaces in spirit.

Not needed for SQ5a: the ADRs beyond what `BoardAccess`'s docstrings say,
`verb-contract.md`, the fake provider, claude-skills.

## This session's unit

Ledger item: SQ5a
Goal: `provider = "github"` registered in `PROVIDERS`, a `BoardAccess` over
one repository's issues and one Projects v2 board:
- Transport: the unit's choice, recorded as a ledger note. Recommended: `gh
  api` (REST and `gh api graphql`), the way ADO goes through `az`, injected as
  `run` so tests stub it. Auth is `gh`'s own; never put a token in argv.
- Config: `[board.github]` with the repository (`owner/name`) and the project
  (owner and number), as a provider-scoped table so SQ5c's `[[boards]]` can
  carry one per entry. Additive, no ADR. A missing key is a `ConfigError`
  naming it, only when `provider = "github"`.
- Native state per Rich's N20 ruling (the proposal was: `Status` while open,
  `closed:<state_reason>` once closed, closure wins; not on the project or
  empty Status is in no bucket).
- Reads: `items_in_state`, `item_state` (an unmapped state raises, a
  no-bucket item raises), `item_links` (sub-issue parent, "blocked by" as
  predecessors), `completed_pr_url` (a merged PR from the branch into
  `base_branch`).
- `validate_config` against the project's Status options plus the closed
  names, both ways (N4). Run it once against a real board Rich names (ask
  him) and record the result in the session log. If he has none ready,
  record that and carry it to SQ5b.
- The tick's writes: `set_state` per N20, `add_tag` / `remove_tag` as labels,
  `add_comment` as Markdown (move `render_github_markdown` into
  `squadra.board` beside `render_ado_html` if the adapter uses it).
- `create_increment` and `items_with_origin` raise `NotImplementedError`, as
  ADO's do (SQ5b builds them).
- Tests: the `board` contract suite runs against the adapter offline,
  as a fourth shape of the `board` fixture, over a stubbed transport (an
  in-memory GitHub that answers the adapter's `gh api` calls). The
  `fake_board` fixture and the `increment` suites stay three-shaped until
  SQ5b. Unit tests for N20's mapping edges: closed beats Status, empty Status,
  not on the project, `closed:duplicate` unmapped fails `validate_config`, a
  Status write on a closed issue refuses.
- Verify N21's read-side facts (sub-issue parent and "blocked by" reachable
  through `gh api`) before relying on them; record what you find in N21.
- README provider row: `github` ships (reads and the tick's writes; board
  writes in SQ5b).
Estimated work: ~70–85K (budget: under 100K, hard stop at 120K). Cut line:
the tick's writes to SQ5b, with the `board` fixture's GitHub shape skipping
the write tests until then (say so in the ledger).

## Out of scope for this session

- `create_increment`, `items_with_origin`, WITHDRAWN writes beyond N20's
  `set_state` (SQ5b); `[[boards]]`, `in_claim_scope`, the `seams.ado` rename,
  N10 (SQ5c).
- Changing the verb contract, the CLI surface or the fake's file format.
- Any write in claude-skills or board-knowledge.

## Wrap-up

Update `docs/board-writes/LEDGER.md` (SQ5a status, the transport note, N21's
findings, session log, next handoff `handoffs/SQ5b-github-writes.md`), run the
four validation commands from `CLAUDE.md`, commit, push, open the PR. Tell
Rich the PR URL and the next handoff path.
