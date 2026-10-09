"""A create that really crashes part-way (board-writes SQ3; ledger N8).

Juval's tests 1–5 (board-knowledge
``sessions/2026-10-09-juval-increment-verb-settlement.md``, "The tests that
discriminate"), against the registered fake provider, whose
``create_increment`` is k separate writes and can be told to stop after
write j. Every test runs for every j < k, so a partial item is left in each
shape a crash can leave it: Origin, title and body only; with its parent; with
some of its predecessors; with all of them, but uncommitted. The tick's half
of test 1 is in ``tests/test_tick_dry_run_contract.py``.
"""

from pathlib import Path

import pytest

from squadra.config import ClaimScope
from squadra.domain import Increment, IncrementRequest, Lifecycle, OriginRecord
from squadra.engines import DuplicateOriginError, QueueRefusedError
from squadra.fake_board import InjectedCrashError, create_step_count
from squadra.increments import IncrementBoard
from tests.contract.conftest import DONE_ID, PARENT_ID, QUEUED_ID, seed_file_board
from tests.helpers.board_fakes import RecordingFileBoard

P: int = PARENT_ID
Q: int = 300
REQUEST: IncrementRequest = IncrementRequest("A:I2", P, (QUEUED_ID, DONE_ID), "I2", "b")
K: int = create_step_count(REQUEST)  # create, parent, two predecessors, commit
PARTIAL_STEPS: list[int] = list(range(1, K))


@pytest.fixture
def board(tmp_path: Path) -> RecordingFileBoard:
    return seed_file_board(tmp_path)


def _verbs(board: RecordingFileBoard) -> IncrementBoard:
    return IncrementBoard(board, ClaimScope.PARENTS, (P, Q))


def _queue(verbs: IncrementBoard, request: IncrementRequest) -> int:
    return verbs.queue_increment(
        request.origin, request.parent, request.predecessors, request.title, request.body
    )


def _records(board: RecordingFileBoard, origin: str) -> list[OriginRecord]:
    return [record for record in board.items_with_origin() if record.origin == origin]


def _crash(board: RecordingFileBoard, after_step: int, request: IncrementRequest = REQUEST) -> int:
    """Queue ``request`` and crash after write ``after_step``; return the partial item."""
    board.arm_create_fault(after_step)
    with pytest.raises(InjectedCrashError, match=f"after create step {after_step} of"):
        _queue(_verbs(board), request)
    (record,) = _records(board, request.origin)
    return record.item_id


def test_k_counts_every_write_of_the_create() -> None:
    assert K == 5


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_1_a_crashed_create_leaves_an_item_in_no_bucket(board: RecordingFileBoard, j: int) -> None:
    partial: int = _crash(board, j)
    for bucket in Lifecycle:
        assert partial not in {item.item_id for item in board.items_in_state(bucket)}
    (record,) = _records(board, "A:I2")
    assert (record.title, record.body, record.partial) == ("I2", "b", True)
    assert board.state_writes == []


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_1_the_partial_item_carries_exactly_the_writes_before_the_crash(
    board: RecordingFileBoard, j: int
) -> None:
    _crash(board, j)
    (record,) = _records(board, "A:I2")
    assert record.parent == (P if j >= 2 else None)
    assert record.predecessors == REQUEST.predecessors[: max(j - 2, 0)]


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_2_increments_by_origin_omits_it_and_raises_no_duplicate(
    board: RecordingFileBoard, j: int
) -> None:
    verbs: IncrementBoard = _verbs(board)
    a_i1: int = verbs.queue_increment("A:I1", P, (), "I1", "")
    _crash(board, j)
    assert verbs.increments_by_origin() == {
        "A:I1": Increment(a_i1, P, Lifecycle.QUEUED, in_claim_scope=True)
    }


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_3_a_same_argument_retry_returns_the_same_item_now_complete(
    board: RecordingFileBoard, j: int
) -> None:
    partial: int = _crash(board, j)
    assert _queue(_verbs(board), REQUEST) == partial
    (record,) = _records(board, "A:I2")
    assert record == OriginRecord(
        item_id=partial,
        origin="A:I2",
        parent=P,
        predecessors=REQUEST.predecessors,
        title="I2",
        body="b",
        lifecycle=Lifecycle.QUEUED,
    )
    assert _queue(_verbs(board), REQUEST) == partial
    assert len(_records(board, "A:I2")) == 1


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_3_a_retry_that_crashes_too_still_leaves_one_item_to_finish(
    board: RecordingFileBoard, j: int
) -> None:
    """The finishing writes crash as well; the next retry still finds the one item."""
    partial: int = _crash(board, j)
    board.arm_create_fault(1)
    with pytest.raises(InjectedCrashError):
        _queue(_verbs(board), REQUEST)
    assert [record.item_id for record in _records(board, "A:I2")] == [partial]
    assert _queue(_verbs(board), REQUEST) == partial
    assert board.item_state(partial) is Lifecycle.QUEUED
    assert len(_records(board, "A:I2")) == 1


@pytest.mark.parametrize("j", [step for step in PARTIAL_STEPS if step >= 2])
def test_4_a_different_parent_retry_is_refused_naming_the_item(
    board: RecordingFileBoard, j: int
) -> None:
    partial: int = _crash(board, j)
    moved: IncrementRequest = IncrementRequest("A:I2", Q, REQUEST.predecessors, "I2", "b")
    with pytest.raises(QueueRefusedError, match=f"item {partial}.*parent {P} != {Q}"):
        _queue(_verbs(board), moved)
    (record,) = _records(board, "A:I2")
    assert (record.partial, record.parent) == (True, P)


def test_4_before_the_parent_link_a_different_parent_is_not_a_disagreement(
    board: RecordingFileBoard,
) -> None:
    """Crashed after write 1, nothing on the board names a parent: the retry's is taken (N14)."""
    partial: int = _crash(board, 1)
    moved: IncrementRequest = IncrementRequest("A:I2", Q, REQUEST.predecessors, "I2", "b")
    assert _queue(_verbs(board), moved) == partial
    assert board.item_links(partial).parent_id == Q


@pytest.mark.parametrize("j", PARTIAL_STEPS)
def test_5_a_complete_and_a_partial_item_with_one_origin_raise_naming_both(
    board: RecordingFileBoard, j: int
) -> None:
    partial: int = _crash(board, j)
    board.seed_origin(QUEUED_ID, "A:I2")
    verbs: IncrementBoard = _verbs(board)
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {partial}"):
        verbs.increments_by_origin()
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {partial}"):
        _queue(verbs, REQUEST)


def test_a_crash_after_the_commit_is_a_lost_response_and_the_retry_returns_the_item(
    board: RecordingFileBoard,
) -> None:
    board.arm_create_fault(K)
    with pytest.raises(InjectedCrashError):
        _queue(_verbs(board), REQUEST)
    (record,) = _records(board, "A:I2")
    assert record.lifecycle is Lifecycle.QUEUED
    assert _queue(_verbs(board), REQUEST) == record.item_id


def test_a_fault_past_the_last_write_is_refused_before_any_write(
    board: RecordingFileBoard,
) -> None:
    board.arm_create_fault(K + 1)
    with pytest.raises(ValueError, match=f"has {K}"):
        _queue(_verbs(board), REQUEST)
    assert _records(board, "A:I2") == []
