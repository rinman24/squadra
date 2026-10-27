"""A create that really crashes part-way (board-writes SQ3; ledger N8).

Juval's tests 1–5 (board-knowledge
``sessions/2026-10-09-juval-increment-verb-settlement.md``, "The tests that
discriminate"), against the two creates made of k separate writes that can
be told to stop after write j: the registered fake provider's (k = 3 + n) and
the GitHub adapter's over an in-memory GitHub (k = 5 + n; ledger SQ5b). Every
test runs for every j < k, so a partial item is left in each shape a crash can
leave it: Origin, title and body only; with its parent; with some of its
predecessors; with all of them, but uncommitted (on GitHub also: on the
project, with a queued Status). The tick's half of test 1 is in
``tests/test_tick_dry_run_contract.py``.

GitHub only: Juval's interloper test. After each write j < k an outsider adds
the issue to the project and sets each queued Status; it stays in no bucket,
because only the commit marker commits it (ADR-0006 decision 5).
"""

from pathlib import Path

import pytest

from squadra.board import BoardValidationError
from squadra.config import ClaimScope
from squadra.domain import Increment, IncrementRequest, Lifecycle, OriginRecord
from squadra.engines import DuplicateOriginError, QueueRefusedError
from squadra.fake_board import InjectedCrashError, create_step_count as file_step_count
from squadra.github_board import create_step_count as gh_step_count
from squadra.increments import IncrementBoard
from tests.contract.conftest import (
    DONE_ID,
    PARENT_ID,
    QUEUED_ID,
    seed_file_board,
    seed_stubbed_github,
)
from tests.helpers.board_fakes import RecordingFileBoard
from tests.helpers.github_stub import GH_STATES, StubbedGitHubBoard

P: int = PARENT_ID
Q: int = 300
REQUEST: IncrementRequest = IncrementRequest("A:I2", P, (QUEUED_ID, DONE_ID), "I2", "b")
# k per shape: file = create, parent, two predecessors, commit; gh adds the
# project add and the Status write before its commit marker.
K: dict[str, int] = {"file": file_step_count(REQUEST), "gh": gh_step_count(REQUEST)}
PARTIAL_STEPS: list[tuple[str, int]] = [(shape, j) for shape, k in K.items() for j in range(1, k)]

CrashableBoard = RecordingFileBoard | StubbedGitHubBoard


@pytest.fixture(params=list(K))
def board(request: pytest.FixtureRequest, tmp_path: Path) -> CrashableBoard:
    if request.param == "gh":
        return seed_stubbed_github()
    return seed_file_board(tmp_path)


def _k(board: CrashableBoard) -> int:
    return K["gh" if isinstance(board, StubbedGitHubBoard) else "file"]


def _verbs(board: CrashableBoard) -> IncrementBoard:
    return IncrementBoard(board, ClaimScope.PARENTS, (P, Q))


def _queue(verbs: IncrementBoard, request: IncrementRequest) -> int:
    return verbs.queue_increment(
        request.origin, request.parent, request.predecessors, request.title, request.body
    )


def _records(board: CrashableBoard, origin: str) -> list[OriginRecord]:
    return [record for record in board.items_with_origin() if record.origin == origin]


def _crash(board: CrashableBoard, after_step: int, request: IncrementRequest = REQUEST) -> int:
    """Queue ``request`` and crash after write ``after_step``; return the partial item."""
    board.arm_create_fault(after_step)
    with pytest.raises(InjectedCrashError):
        _queue(_verbs(board), request)
    (record,) = _records(board, request.origin)
    return record.item_id


def test_k_counts_every_write_of_the_create() -> None:
    assert K == {"file": 5, "gh": 7}


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_1_a_crashed_create_leaves_an_item_in_no_bucket(board: CrashableBoard, j: int) -> None:
    partial: int = _crash(board, j)
    for bucket in Lifecycle:
        assert partial not in {item.item_id for item in board.items_in_state(bucket)}
    (record,) = _records(board, "A:I2")
    assert (record.title, record.body, record.partial) == ("I2", "b", True)
    assert board.state_writes == []


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_1_the_partial_item_carries_exactly_the_writes_before_the_crash(
    board: CrashableBoard, j: int
) -> None:
    _crash(board, j)
    (record,) = _records(board, "A:I2")
    assert record.parent == (P if j >= 2 else None)
    assert record.predecessors == REQUEST.predecessors[: max(j - 2, 0)]


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_2_increments_by_origin_omits_it_and_raises_no_duplicate(
    board: CrashableBoard, j: int
) -> None:
    verbs: IncrementBoard = _verbs(board)
    a_i1: int = verbs.queue_increment("A:I1", P, (), "I1", "")
    _crash(board, j)
    assert verbs.increments_by_origin() == {
        "A:I1": Increment(a_i1, P, Lifecycle.QUEUED, in_claim_scope=True)
    }


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_3_a_same_argument_retry_returns_the_same_item_now_complete(
    board: CrashableBoard, j: int
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


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_3_a_retry_that_crashes_too_still_leaves_one_item_to_finish(
    board: CrashableBoard, j: int
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


@pytest.mark.parametrize(
    ("board", "j"), [(shape, j) for shape, j in PARTIAL_STEPS if j >= 2], indirect=["board"]
)
def test_4_a_different_parent_retry_is_refused_naming_the_item(
    board: CrashableBoard, j: int
) -> None:
    partial: int = _crash(board, j)
    moved: IncrementRequest = IncrementRequest("A:I2", Q, REQUEST.predecessors, "I2", "b")
    with pytest.raises(QueueRefusedError, match=f"item {partial}.*parent {P} != {Q}"):
        _queue(_verbs(board), moved)
    (record,) = _records(board, "A:I2")
    assert (record.partial, record.parent) == (True, P)


def test_4_before_the_parent_link_a_different_parent_is_not_a_disagreement(
    board: CrashableBoard,
) -> None:
    """Crashed after write 1, nothing on the board names a parent: the retry's is taken (N14)."""
    partial: int = _crash(board, 1)
    moved: IncrementRequest = IncrementRequest("A:I2", Q, REQUEST.predecessors, "I2", "b")
    assert _queue(_verbs(board), moved) == partial
    assert board.item_links(partial).parent_id == Q


@pytest.mark.parametrize(("board", "j"), PARTIAL_STEPS, indirect=["board"])
def test_5_a_complete_and_a_partial_item_with_one_origin_raise_naming_both(
    board: CrashableBoard, j: int
) -> None:
    partial: int = _crash(board, j)
    board.seed_origin(QUEUED_ID, "A:I2")
    verbs: IncrementBoard = _verbs(board)
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {partial}"):
        verbs.increments_by_origin()
    with pytest.raises(DuplicateOriginError, match=f"items {QUEUED_ID} and {partial}"):
        _queue(verbs, REQUEST)


def test_a_crash_after_the_commit_is_a_lost_response_and_the_retry_returns_the_item(
    board: CrashableBoard,
) -> None:
    board.arm_create_fault(_k(board))
    with pytest.raises(InjectedCrashError):
        _queue(_verbs(board), REQUEST)
    (record,) = _records(board, "A:I2")
    assert record.lifecycle is Lifecycle.QUEUED
    assert _queue(_verbs(board), REQUEST) == record.item_id


def test_a_fault_past_the_last_write_is_refused_before_any_write(tmp_path: Path) -> None:
    """The fake's arming refuses it (N17); the stub's fault is armed stub-wide instead."""
    board: RecordingFileBoard = seed_file_board(tmp_path)
    board.arm_create_fault(K["file"] + 1)
    with pytest.raises(ValueError, match=f"has {K['file']}"):
        _queue(_verbs(board), REQUEST)
    assert _records(board, "A:I2") == []


# --- GitHub: Juval's interloper (ledger N20) ----------------------------------------

QUEUED_STATUSES: tuple[str, ...] = GH_STATES[Lifecycle.QUEUED]


@pytest.mark.parametrize("status", QUEUED_STATUSES)
@pytest.mark.parametrize("j", range(1, K["gh"]))
def test_an_outsider_putting_a_partial_issue_in_a_queued_column_does_not_queue_it(
    j: int, status: str
) -> None:
    board: StubbedGitHubBoard = seed_stubbed_github()
    partial: int = _crash(board, j)
    issue = board.github.issues[partial]
    issue.on_project, issue.status = True, status  # a workflow, or a human
    assert partial not in {item.item_id for item in board.items_in_state(Lifecycle.QUEUED)}
    (record,) = _records(board, "A:I2")
    assert (record.item_id, record.lifecycle) == (partial, None)
    with pytest.raises(BoardValidationError, match="partial"):
        board.item_state(partial)
    assert _queue(_verbs(board), REQUEST) == partial  # the retry still finishes it
    assert board.item_state(partial) is Lifecycle.QUEUED


@pytest.mark.parametrize("j", range(1, K["gh"]))
def test_finishing_a_github_partial_issue_rewrites_nothing_already_there(j: int) -> None:
    """Each write is idempotent under ``partial_item``: the retry makes only the missing ones."""
    board: StubbedGitHubBoard = seed_stubbed_github()
    partial: int = _crash(board, j)
    before: int = len(board.github.writes)
    _queue(_verbs(board), REQUEST)
    finishing: list[str] = [op for op, number in board.github.writes[before:] if number == partial]
    made: list[str] = ["create", "parent", "blocked_by", "blocked_by", "project", "status"][:j]
    assert (
        finishing
        == ["create", "parent", "blocked_by", "blocked_by", "project", "status", "label"][
            len(made) :
        ]
    )
