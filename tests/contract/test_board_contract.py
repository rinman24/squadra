"""The provider-agnostic ``BoardAccess`` conformance suite.

Every test here runs against BOTH fakes (via the parametrized ``board`` fixture)
and asserts only neutral behavior — never a native state string or markup
dialect. Any implementation that passes this suite satisfies the seam contract.
"""

import pytest

from squadra.board import BoardAccess, BoardValidationError
from squadra.domain import (
    Claimed,
    CommentEvent,
    Escalated,
    Finalized,
    Lifecycle,
    Reaped,
    RolledBack,
    Tags,
    WorkItem,
)
from tests.contract.conftest import (
    ACTIVE_ID,
    DONE_ID,
    LINKED_ID,
    PARENT_ID,
    PR_BRANCH,
    PR_URL,
    PRED_IDS,
    QUEUED_ID,
)
from tests.helpers.board_fakes import AdoShapedFakeBoard, GitHubShapedFakeBoard

TAGS: Tags = Tags()


def _ids(items: tuple[WorkItem, ...]) -> set[int]:
    return {item.item_id for item in items}


# --- items_in_state -----------------------------------------------------------


def test_items_in_state_returns_only_that_buckets_items(board: BoardAccess) -> None:
    assert _ids(board.items_in_state(Lifecycle.QUEUED)) == {QUEUED_ID, LINKED_ID}
    assert _ids(board.items_in_state(Lifecycle.ACTIVE)) == {ACTIVE_ID}
    assert _ids(board.items_in_state(Lifecycle.DONE)) == {DONE_ID}


def test_items_in_state_empty_bucket_returns_empty_tuple(board: BoardAccess) -> None:
    # move the lone active item out; the ACTIVE bucket must then be ()
    board.set_state(ACTIVE_ID, Lifecycle.DONE)
    assert board.items_in_state(Lifecycle.ACTIVE) == ()


# --- set_state / item_state round-trips ---------------------------------------


@pytest.mark.parametrize("target", [Lifecycle.DONE, Lifecycle.QUEUED, Lifecycle.ACTIVE])
def test_set_state_roundtrips_and_moves_buckets(board: BoardAccess, target: Lifecycle) -> None:
    old: Lifecycle = board.item_state(QUEUED_ID)
    board.set_state(QUEUED_ID, target)

    assert board.item_state(QUEUED_ID) == target
    assert QUEUED_ID in _ids(board.items_in_state(target))
    if target != old:
        assert QUEUED_ID not in _ids(board.items_in_state(old))


def test_secondary_native_done_name_reports_done(board: BoardAccess) -> None:
    # DONE_ID is seeded under a secondary native done-name on the GitHub fake;
    # both shapes must still report it as DONE.
    assert board.item_state(DONE_ID) == Lifecycle.DONE
    assert DONE_ID in _ids(board.items_in_state(Lifecycle.DONE))


# --- tags ---------------------------------------------------------------------


def test_add_tag_is_idempotent_and_visible(board: BoardAccess) -> None:
    tag: str = TAGS.qa_ready
    board.add_tag(QUEUED_ID, tag)
    board.add_tag(QUEUED_ID, tag)  # second add must be a no-op

    item: WorkItem = next(
        i for i in board.items_in_state(Lifecycle.QUEUED) if i.item_id == QUEUED_ID
    )
    assert item.tags.count(tag) == 1


def test_remove_tag_removes_and_is_noop_when_absent(board: BoardAccess) -> None:
    tag: str = TAGS.needs_decision
    board.add_tag(QUEUED_ID, tag)
    board.remove_tag(QUEUED_ID, tag)

    item: WorkItem = next(
        i for i in board.items_in_state(Lifecycle.QUEUED) if i.item_id == QUEUED_ID
    )
    assert tag not in item.tags

    # removing again is a no-op (must not raise)
    board.remove_tag(QUEUED_ID, tag)


def test_tag_prefix_is_opaque_to_board(board: BoardAccess) -> None:
    # A custom-prefixed tag round-trips unchanged; the board never inspects it.
    custom: str = Tags("custom/").claimed
    board.add_tag(QUEUED_ID, custom)
    item: WorkItem = next(
        i for i in board.items_in_state(Lifecycle.QUEUED) if i.item_id == QUEUED_ID
    )
    assert custom in item.tags


# --- comments -----------------------------------------------------------------

_ALL_EVENTS: tuple[CommentEvent, ...] = (
    Claimed(runner_id="r1", branch="feat/x", when="2026-06-12T00:00:00Z"),
    RolledBack(reason="launch failed"),
    Finalized(pr_url="https://example.invalid/pr/1", branch="feat/x"),
    Reaped(evidence="no heartbeat 30m", attempt=2),
    Escalated(attempt=3, cap=3),
)


@pytest.mark.parametrize("event", _ALL_EVENTS)
def test_add_comment_accepts_every_event_variant(board: BoardAccess, event: CommentEvent) -> None:
    # must accept each variant without error (dialect is not asserted)
    board.add_comment(ACTIVE_ID, event)


def test_add_comment_records_one_comment_per_call(board: BoardAccess) -> None:
    for event in _ALL_EVENTS:
        board.add_comment(ACTIVE_ID, event)
    # both fakes expose a per-item comment log (assert count, not dialect)
    assert isinstance(board, AdoShapedFakeBoard | GitHubShapedFakeBoard)
    assert len(board.comments[ACTIVE_ID]) == len(_ALL_EVENTS)


# --- completed_pr_url ----------------------------------------------------------


def test_completed_pr_url_returns_seeded_and_none_for_unknown(board: BoardAccess) -> None:
    assert board.completed_pr_url(PR_BRANCH) == PR_URL
    assert board.completed_pr_url("feat/never-existed") is None


# --- item_links ---------------------------------------------------------------


def test_item_links_returns_seeded_parent_and_predecessors(board: BoardAccess) -> None:
    links = board.item_links(LINKED_ID)
    assert links.parent_id == PARENT_ID
    assert links.predecessor_ids == PRED_IDS


def test_item_links_unlinked_item_has_no_relations(board: BoardAccess) -> None:
    links = board.item_links(QUEUED_ID)
    assert links.parent_id is None
    assert links.predecessor_ids == ()


# --- validate_config ----------------------------------------------------------


def test_validate_config_passes_for_correctly_seeded_board(board: BoardAccess) -> None:
    board.validate_config()  # must not raise


def test_validate_config_raises_on_state_mismatch_ado() -> None:
    # configured map references a state the board does not advertise
    board = AdoShapedFakeBoard(available_states=("To Do", "Doing"))  # missing "Done"
    with pytest.raises(BoardValidationError):
        board.validate_config()


def test_validate_config_raises_on_state_mismatch_github() -> None:
    board = GitHubShapedFakeBoard(available_statuses=("Backlog", "Triage", "In Progress"))
    with pytest.raises(BoardValidationError):
        board.validate_config()
