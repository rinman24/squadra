"""Unit tests for the pure decision functions in :mod:`squadra.engines`.

The ``is_parked`` / ``is_failed_park`` predicates were retired in the F4 cutover
(ADR-0002 decision 3): the :class:`~squadra.engines.LifecycleEngine` folds the
deliberate-park / failed-park distinction directly into its fact-derivation, and
``tests/test_lifecycle_engine.py`` (#151) exercises that folding. What survives
here is ``increment_branch``, the branch-naming rule the orchestrator still calls,
and ``check_transition``, the four-bucket lifecycle transition rule (ADR-0004).
"""

from dataclasses import replace

import pytest

from squadra.config import ClaimScope
from squadra.domain import IncrementRequest, Lifecycle, OriginRecord
from squadra.engines import (
    DuplicateOriginError,
    QueueRefusedError,
    TransitionRefusedError,
    as_increment,
    check_queue_finishes,
    check_queue_matches,
    check_transition,
    increment_branch,
    index_by_origin,
    parent_in_claim_scope,
)


def test_increment_branch_kebabs_title_after_colon() -> None:
    assert (
        increment_branch(12, "feat: Add scope revocation", 1)
        == "feat/increment-12-add-scope-revocation"
    )


def test_increment_branch_appends_retry_suffix_only_when_attempt_gt_1() -> None:
    assert (
        increment_branch(12, "feat: Add scope revocation", 2)
        == "feat/increment-12-add-scope-revocation-a2"
    )


def test_increment_branch_falls_back_to_increment_for_empty_slug() -> None:
    assert increment_branch(12, "!!!", 1) == "feat/increment-12-increment"


def test_increment_branch_uses_whole_title_when_no_colon() -> None:
    assert increment_branch(5, "Add scope revocation", 1) == "feat/increment-5-add-scope-revocation"


def test_increment_branch_caps_slug_and_never_trails_a_dash() -> None:
    title = "feat: " + "word " * 40
    result = increment_branch(12, title, 1)
    prefix = "feat/increment-12-"
    assert len(result) <= len(prefix) + 32
    assert not result.endswith("-")
    assert result.startswith(prefix)


def test_increment_branch_honors_a_custom_template() -> None:
    assert increment_branch(7, "feat: x", 1, template="wip/{id}/{slug}") == "wip/7/x"


def test_increment_branch_custom_template_keeps_retry_suffix_outside_template() -> None:
    assert increment_branch(7, "feat: x", 3, template="wip/{id}/{slug}") == "wip/7/x-a3"


# --- check_transition: the edges touching WITHDRAWN (ADR-0004) ----------------

_NOT_WITHDRAWN: tuple[Lifecycle, ...] = (Lifecycle.QUEUED, Lifecycle.ACTIVE, Lifecycle.DONE)


def test_withdrawn_is_reachable_from_queued() -> None:
    check_transition(Lifecycle.QUEUED, Lifecycle.WITHDRAWN)  # must not raise


def test_withdrawn_is_never_reachable_from_done() -> None:
    with pytest.raises(TransitionRefusedError, match="delivered"):
        check_transition(Lifecycle.DONE, Lifecycle.WITHDRAWN)


def test_withdrawing_an_active_increment_is_refused_with_no_cancel_path() -> None:
    # claude-skills DB-D2: no cancel path; the attempt finishes before a re-plan.
    with pytest.raises(TransitionRefusedError, match="in flight; let the attempt finish"):
        check_transition(Lifecycle.ACTIVE, Lifecycle.WITHDRAWN)


@pytest.mark.parametrize("target", _NOT_WITHDRAWN)
def test_withdrawn_is_terminal(target: Lifecycle) -> None:
    with pytest.raises(TransitionRefusedError, match="terminal"):
        check_transition(Lifecycle.WITHDRAWN, target)


def test_withdrawing_a_withdrawn_increment_again_is_a_noop() -> None:
    check_transition(Lifecycle.WITHDRAWN, Lifecycle.WITHDRAWN)  # must not raise


@pytest.mark.parametrize("current", _NOT_WITHDRAWN)
@pytest.mark.parametrize("target", _NOT_WITHDRAWN)
def test_transitions_among_the_other_buckets_are_unconstrained(
    current: Lifecycle, target: Lifecycle
) -> None:
    check_transition(current, target)  # must not raise


# --- the increment verbs' rules (board-writes SQ2) ------------------------------

_RECORD: OriginRecord = OriginRecord(
    item_id=7,
    origin="A:I1",
    parent=200,
    predecessors=(150, 151),
    title="t",
    body="b",
    lifecycle=Lifecycle.QUEUED,
)
_REQUEST: IncrementRequest = IncrementRequest(
    origin="A:I1", parent=200, predecessors=(151, 150), title="t", body="b"
)


@pytest.mark.parametrize(
    ("parent", "scope", "ids", "expected"),
    [
        (200, ClaimScope.PARENTS, (200,), True),
        (201, ClaimScope.PARENTS, (200,), False),
        (None, ClaimScope.PARENTS, (200,), False),
        (201, ClaimScope.WHOLE_BOARD, (), True),
        (None, ClaimScope.WHOLE_BOARD, (), True),
    ],
)
def test_parent_in_claim_scope(
    parent: int | None, scope: ClaimScope, ids: tuple[int, ...], expected: bool
) -> None:
    assert parent_in_claim_scope(parent, scope, ids) is expected


def test_index_by_origin_keys_each_record() -> None:
    second: OriginRecord = replace(_RECORD, item_id=8, origin="A:I2")
    assert index_by_origin([_RECORD, second]) == {"A:I1": _RECORD, "A:I2": second}


def test_index_by_origin_raises_on_a_duplicate_naming_both_items() -> None:
    with pytest.raises(DuplicateOriginError, match=r"items 7 and 8"):
        index_by_origin([_RECORD, replace(_RECORD, item_id=8)])


def test_as_increment_reports_scope_without_filtering() -> None:
    reported = as_increment(replace(_RECORD, parent=999), ClaimScope.PARENTS, (200,))
    assert (reported.item_id, reported.parent, reported.in_claim_scope) == (7, 999, False)


def test_check_queue_matches_accepts_identical_arguments_in_any_link_order() -> None:
    check_queue_matches(_RECORD, _REQUEST)  # must not raise


@pytest.mark.parametrize(
    ("request_", "named"),
    [
        (replace(_REQUEST, parent=201), "parent 200 != 201"),
        (replace(_REQUEST, predecessors=(150,)), "predecessors"),
        (replace(_REQUEST, title="u"), "title"),
        (replace(_REQUEST, body="c"), "body differs"),
    ],
)
def test_check_queue_matches_refuses_any_difference_naming_it(
    request_: IncrementRequest, named: str
) -> None:
    with pytest.raises(QueueRefusedError, match=named):
        check_queue_matches(_RECORD, request_)


def test_check_queue_matches_refuses_a_withdrawn_origin_naming_the_way_out() -> None:
    with pytest.raises(QueueRefusedError, match="never reused: queue the work again under a new"):
        check_queue_matches(replace(_RECORD, lifecycle=Lifecycle.WITHDRAWN), _REQUEST)


# --- finishing a partial item (SQ2c, ledger N8) -----------------------------------

_PARTIAL: OriginRecord = replace(_RECORD, parent=None, predecessors=(), lifecycle=None)


def test_a_record_with_no_lifecycle_is_partial() -> None:
    assert (_PARTIAL.partial, _RECORD.partial) == (True, False)


def test_as_increment_refuses_a_partial_record() -> None:
    with pytest.raises(ValueError, match="item 7 is partial"):
        as_increment(_PARTIAL, ClaimScope.WHOLE_BOARD, ())


@pytest.mark.parametrize(
    "partial",
    [
        _PARTIAL,
        replace(_PARTIAL, parent=200),
        replace(_PARTIAL, parent=200, predecessors=(151,)),
        replace(_PARTIAL, parent=200, predecessors=(150, 151)),
    ],
)
def test_check_queue_finishes_accepts_what_is_on_the_board_so_far(partial: OriginRecord) -> None:
    check_queue_finishes(partial, _REQUEST)  # must not raise


@pytest.mark.parametrize(
    ("partial", "named"),
    [
        (replace(_PARTIAL, parent=201), "parent 201 != 200"),
        (replace(_PARTIAL, predecessors=(150, 152)), r"predecessors \[152\] on the item"),
        (replace(_PARTIAL, title="u"), "title"),
        (replace(_PARTIAL, body="c"), "body differs"),
    ],
)
def test_check_queue_finishes_refuses_disagreement_naming_the_item(
    partial: OriginRecord, named: str
) -> None:
    with pytest.raises(QueueRefusedError, match=f"partial item 7, .*{named}"):
        check_queue_finishes(partial, _REQUEST)
