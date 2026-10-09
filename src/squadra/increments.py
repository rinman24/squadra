"""The increment verbs squadra offers an outside planner (board-writes SQ2).

claude-skills DB-D1 puts every board write design-to-board needs in squadra, in
three verbs; DB-D2–DB-D5 freeze their shape. :class:`IncrementBoard` is that
contract. It applies the rules once, here, over the raw
:class:`~squadra.board.BoardAccess` primitives (``create_increment``,
``items_with_origin``, ``item_state``, ``set_state``), so no adapter repeats
them:

- claim scope (DB-D1 A2): a verb never writes outside ``[board].claim_scope``,
  using the tick's own definition (:func:`~squadra.engines.parent_in_claim_scope`);
- the transition rule (ADR-0004, DB-D2): withdrawal goes through
  :func:`~squadra.engines.check_transition`, so only a QUEUED increment can be
  withdrawn;
- duplicate Origins raise (DB-D4 A2), and a repeated ``queue_increment`` returns
  the existing item only on identical arguments (DB-D4 A3).

Every refusal raises before any write. The Origin is opaque: squadra stores it
and compares it for equality, never parses it. The contract is per board: a
predecessor on another board is not visible here. The CLI subcommands that
expose these verbs (SQ4) are designed in ``docs/board-writes/verb-contract.md``.
"""

from collections.abc import Sequence

from squadra.board import BoardAccess
from squadra.config import ClaimScope
from squadra.domain import Increment, IncrementRequest, Lifecycle, OriginRecord, WorkItemLinks
from squadra.engines import (
    ClaimScopeRefusedError,
    as_increment,
    check_queue_matches,
    check_transition,
    index_by_origin,
    parent_in_claim_scope,
)


class IncrementBoard:
    """``queue_increment``, ``withdraw_increment`` and ``increments_by_origin`` on one board."""

    def __init__(
        self, board: BoardAccess, claim_scope: ClaimScope, parent_scope_ids: tuple[int, ...]
    ) -> None:
        """Wrap ``board`` under the configured claim scope (``[board]`` in squadra.toml)."""
        self._board = board
        self._claim_scope = claim_scope
        self._parent_scope_ids = parent_scope_ids

    def queue_increment(
        self, origin: str, parent: int, predecessors: Sequence[int], title: str, body: str
    ) -> int:
        """Queue one increment under ``parent`` and return its item id.

        Refuses a parent outside the claim scope (:class:`ClaimScopeRefusedError`).
        If ``origin`` is already on the board, returns that item when every
        argument matches and refuses any difference, or a withdrawn Origin
        (:class:`QueueRefusedError`). Raises :class:`DuplicateOriginError` if
        the board already carries ``origin`` twice. The new item lands QUEUED.
        """
        request: IncrementRequest = IncrementRequest(
            origin=origin,
            parent=parent,
            predecessors=tuple(predecessors),
            title=title,
            body=body,
        )
        if not self._in_scope(parent):
            raise ClaimScopeRefusedError(
                f"cannot queue origin {origin!r} under #{parent}: the parent is outside "
                f"[board].claim_scope ({self._scope_text()})"
            )
        existing: OriginRecord | None = index_by_origin(self._board.items_with_origin()).get(origin)
        if existing is not None:
            check_queue_matches(existing, request)
            return existing.item_id
        return self._board.create_increment(request)

    def withdraw_increment(self, item_id: int) -> None:
        """Move a QUEUED increment to WITHDRAWN.

        Refuses an item outside the claim scope (:class:`ClaimScopeRefusedError`),
        naming the safe recovery order: restoring scope first would make the
        item claimable before the withdrawal lands (SQ2b, N6). Refuses an
        ACTIVE or DONE item (:class:`~squadra.engines.TransitionRefusedError`).
        Withdrawing a withdrawn increment again writes nothing. On a board with
        no withdrawn state the write raises
        :class:`~squadra.board.BoardValidationError`, leaving the item QUEUED.
        """
        links: WorkItemLinks = self._board.item_links(item_id)
        if not self._in_scope(links.parent_id):
            raise ClaimScopeRefusedError(
                f"cannot withdraw #{item_id}: its parent #{links.parent_id} is outside "
                f"[board].claim_scope ({self._scope_text()}). To withdraw it: `squadra stop`, "
                "add the parent back to the claim scope, withdraw it again, then "
                "`squadra start` (restoring scope while the fleet runs lets the next tick "
                "claim the item first)"
            )
        current: Lifecycle = self._board.item_state(item_id)
        check_transition(current, Lifecycle.WITHDRAWN)
        if current is Lifecycle.WITHDRAWN:
            return
        self._board.set_state(item_id, Lifecycle.WITHDRAWN)

    def increments_by_origin(self) -> dict[str, Increment]:
        """Return every Origin on the board, in every bucket, with its item.

        Board-wide: claim scope is reported in each :class:`Increment`, never
        applied as a filter (DB-D4 A1). Raises :class:`DuplicateOriginError` on
        an Origin carried by two items, naming both.
        """
        records: dict[str, OriginRecord] = index_by_origin(self._board.items_with_origin())
        return {
            origin: as_increment(record, self._claim_scope, self._parent_scope_ids)
            for origin, record in records.items()
        }

    def _in_scope(self, parent: int | None) -> bool:
        return parent_in_claim_scope(parent, self._claim_scope, self._parent_scope_ids)

    def _scope_text(self) -> str:
        if self._claim_scope is ClaimScope.WHOLE_BOARD:
            return self._claim_scope.value
        return f"{self._claim_scope.value} {list(self._parent_scope_ids)}"
