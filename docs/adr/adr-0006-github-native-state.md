# ADR-0006 — GitHub's native state: closure wins, partial by commit marker

- Status: **Accepted** — 2026-10-09; implemented by the PR that adds this ADR
  (board-writes SQ5a). Rich's ruling on ledger N20, after `/ask-juval` and
  `/ask-eric` (board-knowledge
  `sessions/2026-10-09-juval-github-native-state.md`,
  `sessions/2026-10-09-eric-github-native-state.md`).
- Date: 2026-10-09
- Context origin: claude-skills DB-D2 (WITHDRAWN is closed, reason "not
  planned"); ADR-0004 (unmapped states fail loud); ADR-0005 obligations 1–4
  and the fallback its Consequences name (a commit marker).
- Code: `squadra.github_board` (`native_state`, `GhApiGitHub`).

## Context

`[board.states]` maps *native states* (the provider-side names) to `Lifecycle`
buckets. ADO stores one per item (`System.State`). A GitHub issue has two
things that could carry it: its open/closed state with a close reason, and the
`Status` single-select of the Projects v2 board it sits on. The two can
disagree. The "Item closed" workflow sets Status to Done on close, a human can
drag a closed issue's Status anywhere, and an open issue can be off the
project or have no Status at all. The names chosen here become the vocabulary
of every GitHub `squadra.toml`, which is costly to change once a real board is
configured.

ADR-0005 obligation 2 also needs a create that spans several GitHub calls to
leave its item in no bucket until the last write. A queued Status set by a
workflow or a human must not make a half-created item claimable.

## Decision

1. **Composition, closure wins.** An issue's native state is `closed:<reason>`
   once it is closed, whatever its Status says. While it is open, it is its
   project Status option name. One function, `native_state`, holds the
   precedence, and a test pins it: a closed issue whose Status names a queued
   column is never QUEUED.
2. **The closed names are squadra's constants**: `closed:completed`,
   `closed:not_planned`, `closed:duplicate`, plus `closed:` for a closed issue
   with no reason (GitHub's `stateReason` is nullable; issues closed before
   reasons existed carry none). They are translated from GitHub's spelling at
   the boundary. An unknown reason raises and is never defaulted.
   `validate_config` checks `[board.states]` both ways against the project's
   Status options plus these names (N4). It refuses:
   - a Status option that starts with `closed:`;
   - a closed name mapped to queued or active;
   - a map where `closed:completed` is not in done, or `closed:not_planned`
     is not in withdrawn.
3. **Every `set_state` is one write, or none.** DONE closes the issue as
   completed and WITHDRAWN closes it as not planned (DB-D2). QUEUED and ACTIVE
   write the bucket's first Status option. An item already in the target
   bucket gets no write, as when finalize runs after a merged PR has already
   closed the issue. A Status option mapped to DONE is valid only on a closed
   issue, so open + DONE raises in `item_state` (a reopened issue, or a hand
   drag to Done).
4. **A closed issue is never reopened.** `set_state` refuses a Status write
   onto a closed issue, and refuses a close that would change its reason;
   either would take two writes or a silent reopen. No tick path requeues a
   closed issue. Reporting a refused requeue per item and going on with the
   tick is the tick's half, built in SQ5b.
5. **Partial by commit marker, not by structure.** An issue that carries an
   Origin is *partial* until it carries the label `squadra:committed`. The
   Origin is a hidden HTML comment at the end of the body
   (`<!-- squadra-origin: "<JSON, '-' escaped>" -->`), written with the
   title and body by the create's first call (obligation 1, N14). The label
   is the create's last write, its commit (obligation 4). It is an additive
   write and never a read-modify-write of the body. A partial item is in no
   bucket, whatever its Status or closure: `items_in_state` leaves it out and
   `item_state` raises. The marker gates Origin-bearing items only. An issue
   with no Origin maps from closure and Status, as on ADO. Rules for the
   label itself:
   - It lies outside the fleet's tag namespace (`validate_config` refuses a
     `tag_prefix` that covers it), so finalize's clear-by-prefix never
     touches it and no exemption is added there.
   - It is never reported as a tag.
   - It never appears in `[board.states]`.
6. **Off the project or an empty Status is loud on a committed item.** Being
   off the project or having an empty Status puts an issue in no bucket only
   when it is partial. On any other open issue the native state is unmapped
   and `item_state` raises (ADR-0004 rule 3). So a committed increment that a
   human takes off the project is never reported partial and requeued.
7. **The project workflows.**
   - "Item added to project" is tolerated only if the Status it sets maps to
     QUEUED; otherwise it must be off. The API exposes only a workflow's
     `name` and `enabled`, not the Status it sets (verified 2026-10-09, ledger
     N21), so `validate_config` cannot check this. The scaffold documents the
     requirement, and SQ5b reads Status back once after the marker write
     (detection, not a guarantee). On GitHub, obligation 4 therefore holds
     only by configuration.
   - "Item closed" is tolerated; it is safe under decision 1. The scaffold
     advises turning it off, because the project view is not authoritative
     for closed issues.

## Consequences

- A GitHub board's map always has a withdrawn bucket (`closed:not_planned`
  must be mapped there), unlike ADO-Basic's (N1).
- The tick's DONE scan lists the project's closed issues for as long as they
  stay on the project. Archived items count as off the project.
- `item_links` refuses a parent or "blocked by" link into another repository.
  An issue number names an item only within the board's repository, and
  dropping a predecessor would let its successor be claimed early.
- "Native state" stays out of `GLOSSARY.md` (Eric): it is defined once in the
  board seam's docs (`squadra.board`), with GitHub's rule in
  `squadra.github_board` and the scaffold's comment beside the default map.
- In adapter code, `completed` names GitHub's close reason only, never
  delivery (N13). Delivery is whatever `[board.states].done` maps.
