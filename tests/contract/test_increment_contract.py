"""The increment verb contract (board-writes SQ2; claude-skills DB-D2–DB-D5).

Two layers, both run against BOTH fakes:

- the ``BoardAccess`` primitives every adapter owes (``create_increment``,
  ``items_with_origin``): an item lands QUEUED, its Origin and arguments read
  back exactly, and the read is board-wide and unkeyed;
- the verbs on :class:`squadra.increments.IncrementBoard`, which apply the rules
  once over those primitives: claim scope, the transition rule, duplicate
  Origins, and idempotency on identical arguments only.

The ``case N`` tests are squadra's half of Juval's discriminating tests (board-
knowledge ``sessions/2026-10-09-juval-parent-and-lookup-scope.md``): map A
(I1 → I2) under parent P, map B's I5 under parent Q depends on ``A:I2``. Cases 1
and 3 are design-to-board behaviour with no squadra-side assertion.
"""

import pytest

from squadra.board import BoardAccess, BoardValidationError
from squadra.config import ClaimScope
from squadra.domain import Increment, IncrementRequest, Lifecycle, OriginRecord
from squadra.engines import (
    ClaimScopeRefusedError,
    DuplicateOriginError,
    QueueRefusedError,
    TransitionRefusedError,
)
from squadra.increments import IncrementBoard
from tests.contract.conftest import ACTIVE_ID, DONE_ID, PARENT_ID, QUEUED_ID, WITHDRAWN_ID
from tests.helpers.board_fakes import AdoShapedFakeBoard, GitHubShapedFakeBoard

P: int = PARENT_ID
Q: int = 300
OUT: int = 999  # a parent outside the claim scope

# An Origin with every character a provider encoding could trip on: squadra
# stores it as given and never parses it.
AWKWARD_ORIGIN: str = 'A:I1 --> "quoted" \\ <b>π</b>\n-->'


def _verbs(board: BoardAccess, *parents: int) -> IncrementBoard:
    """The verbs under ``claim_scope = "parents"`` (default: P and Q in scope)."""
    return IncrementBoard(board, ClaimScope.PARENTS, parents or (P, Q))


def _whole_board(board: BoardAccess) -> IncrementBoard:
    return IncrementBoard(board, ClaimScope.WHOLE_BOARD, ())


def _origin_count(board: BoardAccess) -> int:
    return len(board.items_with_origin())


# --- BoardAccess primitives -----------------------------------------------------


def test_create_increment_lands_queued_and_reads_back_exactly(board: BoardAccess) -> None:
    request: IncrementRequest = IncrementRequest(
        origin=AWKWARD_ORIGIN,
        parent=P,
        predecessors=(QUEUED_ID, DONE_ID),
        title="I1: first",
        body="line one\n\n<!-- not a marker -->\n",
    )
    item_id: int = board.create_increment(request)
    assert board.item_state(item_id) is Lifecycle.QUEUED
    links = board.item_links(item_id)
    assert (links.parent_id, set(links.predecessor_ids)) == (P, {QUEUED_ID, DONE_ID})
    assert board.items_with_origin() == (
        OriginRecord(
            item_id=item_id,
            origin=AWKWARD_ORIGIN,
            parent=P,
            predecessors=(QUEUED_ID, DONE_ID),
            title="I1: first",
            body="line one\n\n<!-- not a marker -->\n",
            lifecycle=Lifecycle.QUEUED,
        ),
    )


def test_created_increment_is_a_queued_work_item(board: BoardAccess) -> None:
    item_id: int = board.create_increment(IncrementRequest("A:I1", P, (), "I1", ""))
    assert item_id in {item.item_id for item in board.items_in_state(Lifecycle.QUEUED)}


def test_items_with_origin_leaves_out_items_without_one(board: BoardAccess) -> None:
    assert board.items_with_origin() == ()


def test_items_with_origin_covers_every_bucket_and_keeps_duplicates(
    fake_board: AdoShapedFakeBoard | GitHubShapedFakeBoard,
) -> None:
    for item_id in (QUEUED_ID, ACTIVE_ID, DONE_ID, WITHDRAWN_ID):
        fake_board.seed_origin(item_id, "dup")
    records: tuple[OriginRecord, ...] = fake_board.items_with_origin()
    assert sorted((r.item_id, r.lifecycle) for r in records) == [
        (QUEUED_ID, Lifecycle.QUEUED),
        (ACTIVE_ID, Lifecycle.ACTIVE),
        (DONE_ID, Lifecycle.DONE),
        (WITHDRAWN_ID, Lifecycle.WITHDRAWN),
    ]


# --- queue_increment ------------------------------------------------------------


def test_queue_increment_creates_a_queued_item_under_the_parent(board: BoardAccess) -> None:
    verbs: IncrementBoard = _verbs(board)
    i1: int = verbs.queue_increment("A:I1", P, (), "I1", "body")
    i2: int = verbs.queue_increment("A:I2", P, (i1,), "I2", "body")
    assert board.item_state(i2) is Lifecycle.QUEUED
    assert board.item_links(i2).predecessor_ids == (i1,)


def test_queue_increment_refuses_a_parent_outside_the_claim_scope(board: BoardAccess) -> None:
    with pytest.raises(ClaimScopeRefusedError, match="outside"):
        _verbs(board).queue_increment("A:I1", OUT, (), "I1", "")
    assert _origin_count(board) == 0


def test_queue_increment_under_whole_board_takes_any_parent(board: BoardAccess) -> None:
    item_id: int = _whole_board(board).queue_increment("A:I1", OUT, (), "I1", "")
    assert board.item_links(item_id).parent_id == OUT


def test_queue_increment_retried_with_identical_arguments_returns_the_same_item(
    board: BoardAccess,
) -> None:
    verbs: IncrementBoard = _verbs(board)
    first: int = verbs.queue_increment("A:I2", P, (QUEUED_ID, DONE_ID), "I2", "b")
    again: int = verbs.queue_increment("A:I2", P, (DONE_ID, QUEUED_ID), "I2", "b")
    assert again == first
    assert _origin_count(board) == 1


def test_case_4_a_conflicting_parent_is_refused_and_nothing_written(board: BoardAccess) -> None:
    verbs: IncrementBoard = _verbs(board)
    verbs.queue_increment("B:I5", Q, (), "I5", "b")
    with pytest.raises(QueueRefusedError, match=f"parent {Q} != {P}"):
        verbs.queue_increment("B:I5", P, (), "I5", "b")
    assert _origin_count(board) == 1


@pytest.mark.parametrize(
    ("predecessors", "title", "body", "named"),
    [((DONE_ID,), "I5", "b", "predecessors"), ((), "I5'", "b", "title"), ((), "I5", "c", "body")],
)
def test_queue_increment_refuses_any_other_difference(
    board: BoardAccess, predecessors: tuple[int, ...], title: str, body: str, named: str
) -> None:
    verbs: IncrementBoard = _verbs(board)
    verbs.queue_increment("B:I5", Q, (), "I5", "b")
    with pytest.raises(QueueRefusedError, match=named):
        verbs.queue_increment("B:I5", Q, predecessors, title, body)
    assert _origin_count(board) == 1


def test_queue_increment_never_reuses_a_withdrawn_origin(board: BoardAccess) -> None:
    verbs: IncrementBoard = _verbs(board)
    item_id: int = verbs.queue_increment("A:I2", P, (), "I2", "b")
    verbs.withdraw_increment(item_id)
    with pytest.raises(QueueRefusedError, match="never reused"):
        verbs.queue_increment("A:I2", P, (), "I2", "b")
    assert _origin_count(board) == 1


def test_case_7_queue_increment_raises_on_a_duplicate_origin(
    fake_board: AdoShapedFakeBoard | GitHubShapedFakeBoard,
) -> None:
    fake_board.seed_origin(QUEUED_ID, "A:I1")
    fake_board.seed_origin(DONE_ID, "A:I1")
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {DONE_ID}"):
        _whole_board(fake_board).queue_increment("A:I1", P, (), "I1", "")
    assert _origin_count(fake_board) == 2


# --- increments_by_origin -------------------------------------------------------


def test_case_2_a_cross_parent_predecessor_resolves_board_wide(board: BoardAccess) -> None:
    verbs: IncrementBoard = _verbs(board)
    a_i1: int = verbs.queue_increment("A:I1", P, (), "I1", "")
    a_i2: int = verbs.queue_increment("A:I2", P, (a_i1,), "I2", "")
    found: Increment = verbs.increments_by_origin()["A:I2"]
    b_i5: int = verbs.queue_increment("B:I5", Q, (found.item_id,), "I5", "")
    assert board.item_links(b_i5).predecessor_ids == (a_i2,)
    assert verbs.increments_by_origin() == {
        "A:I1": Increment(a_i1, P, Lifecycle.QUEUED, in_claim_scope=True),
        "A:I2": Increment(a_i2, P, Lifecycle.QUEUED, in_claim_scope=True),
        "B:I5": Increment(b_i5, Q, Lifecycle.QUEUED, in_claim_scope=True),
    }


def test_case_5_a_parent_dropped_from_scope_is_reported_not_filtered(
    board: BoardAccess,
) -> None:
    b_i5: int = _verbs(board).queue_increment("B:I5", Q, (), "I5", "")
    narrowed: IncrementBoard = _verbs(board, P)  # Q removed from parent_scope_ids
    assert narrowed.increments_by_origin() == {
        "B:I5": Increment(b_i5, Q, Lifecycle.QUEUED, in_claim_scope=False)
    }
    with pytest.raises(ClaimScopeRefusedError):
        narrowed.queue_increment("B:I5", Q, (), "I5", "")
    with pytest.raises(ClaimScopeRefusedError):
        narrowed.withdraw_increment(b_i5)
    assert board.item_state(b_i5) is Lifecycle.QUEUED
    assert _origin_count(board) == 1


def test_case_6_a_withdrawn_origin_is_reported_withdrawn(board: BoardAccess) -> None:
    verbs: IncrementBoard = _verbs(board)
    a_i2: int = verbs.queue_increment("A:I2", P, (), "I2", "")
    verbs.withdraw_increment(a_i2)
    assert verbs.increments_by_origin()["A:I2"].lifecycle is Lifecycle.WITHDRAWN


def test_case_7_increments_by_origin_raises_on_a_duplicate_naming_both(
    fake_board: AdoShapedFakeBoard | GitHubShapedFakeBoard,
) -> None:
    fake_board.seed_origin(QUEUED_ID, "A:I1")
    fake_board.seed_origin(ACTIVE_ID, "A:I1")
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {ACTIVE_ID}"):
        _verbs(fake_board).increments_by_origin()


def test_increments_by_origin_is_empty_on_a_board_with_no_origins(board: BoardAccess) -> None:
    assert _verbs(board).increments_by_origin() == {}


# --- withdraw_increment ---------------------------------------------------------


def test_withdraw_increment_moves_a_queued_increment_to_withdrawn(board: BoardAccess) -> None:
    _whole_board(board).withdraw_increment(QUEUED_ID)
    assert board.item_state(QUEUED_ID) is Lifecycle.WITHDRAWN


@pytest.mark.parametrize(("item_id", "reason"), [(ACTIVE_ID, "in flight"), (DONE_ID, "delivered")])
def test_withdraw_increment_refuses_active_and_done(
    board: BoardAccess, item_id: int, reason: str
) -> None:
    before: Lifecycle = board.item_state(item_id)
    with pytest.raises(TransitionRefusedError, match=reason):
        _whole_board(board).withdraw_increment(item_id)
    assert board.item_state(item_id) is before


def test_withdraw_increment_again_writes_nothing(
    fake_board: AdoShapedFakeBoard | GitHubShapedFakeBoard,
) -> None:
    _whole_board(fake_board).withdraw_increment(WITHDRAWN_ID)
    assert fake_board.state_writes == []


def test_withdraw_increment_refuses_an_item_outside_the_claim_scope(board: BoardAccess) -> None:
    with pytest.raises(ClaimScopeRefusedError, match="outside"):
        _verbs(board).withdraw_increment(QUEUED_ID)  # no parent: outside "parents"
    assert board.item_state(QUEUED_ID) is Lifecycle.QUEUED


def test_withdraw_increment_fails_loudly_on_a_board_with_no_withdrawn_state() -> None:
    board: AdoShapedFakeBoard = AdoShapedFakeBoard()  # ADO-Basic: no withdrawn state
    board.add(QUEUED_ID, "queued increment", Lifecycle.QUEUED)
    with pytest.raises(BoardValidationError, match="withdrawn"):
        _whole_board(board).withdraw_increment(QUEUED_ID)
    assert board.item_state(QUEUED_ID) is Lifecycle.QUEUED
