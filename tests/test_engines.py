"""Unit tests for the pure decision functions in :mod:`flotilla.engines`."""

from collections.abc import Callable

from flotilla.domain import Tags, WorkItem
from flotilla.engines import is_failed_park, is_parked, slice_branch
from flotilla.status import FleetStatus


def _item(*tags: str) -> WorkItem:
    """Build a minimal work item carrying ``tags``."""
    return WorkItem(item_id=41, title="feat: slice 41", tags=tags)


# --- slice_branch -------------------------------------------------------------


def test_slice_branch_kebabs_title_after_colon() -> None:
    assert slice_branch(12, "feat: Add scope revocation", 1) == "feat/slice-12-add-scope-revocation"


def test_slice_branch_appends_retry_suffix_only_when_attempt_gt_1() -> None:
    assert (
        slice_branch(12, "feat: Add scope revocation", 2) == "feat/slice-12-add-scope-revocation-a2"
    )


def test_slice_branch_falls_back_to_slice_for_empty_slug() -> None:
    assert slice_branch(12, "!!!", 1) == "feat/slice-12-slice"


def test_slice_branch_uses_whole_title_when_no_colon() -> None:
    assert slice_branch(5, "Add scope revocation", 1) == "feat/slice-5-add-scope-revocation"


def test_slice_branch_caps_slug_and_never_trails_a_dash() -> None:
    title = "feat: " + "word " * 40
    result = slice_branch(12, title, 1)
    prefix = "feat/slice-12-"
    assert len(result) <= len(prefix) + 32
    assert not result.endswith("-")
    assert result.startswith(prefix)


def test_slice_branch_honors_a_custom_template() -> None:
    assert slice_branch(7, "feat: x", 1, template="wip/{id}/{slug}") == "wip/7/x"


def test_slice_branch_custom_template_keeps_retry_suffix_outside_template() -> None:
    assert slice_branch(7, "feat: x", 3, template="wip/{id}/{slug}") == "wip/7/x-a3"


# --- is_parked ----------------------------------------------------------------


def test_is_parked_true_for_a_parked_tag_default_prefix() -> None:
    tags = Tags()
    item = _item(tags.needs_decision)
    assert is_parked(item, None, tags) is True


def test_is_parked_true_for_failed_tag_default_prefix() -> None:
    tags = Tags()
    item = _item(tags.failed)
    assert is_parked(item, None, tags) is True


def test_is_parked_true_for_a_parked_tag_custom_prefix() -> None:
    tags = Tags("track:")
    item = _item(tags.qa_ready)
    assert is_parked(item, None, tags) is True


def test_is_parked_false_when_status_none_and_no_tag() -> None:
    assert is_parked(_item(), None, Tags()) is False


def test_is_parked_true_when_phase_done(make_status: Callable[..., FleetStatus]) -> None:
    status = make_status(phase="done")
    assert is_parked(_item(), status, Tags()) is True


def test_is_parked_true_when_parked_state_not_failed(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="parked", parked_state="needs-decision")
    assert is_parked(_item(), status, Tags()) is True


def test_is_parked_false_when_parked_state_failed(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="parked", parked_state="failed")
    assert is_parked(_item(), status, Tags()) is False


def test_is_parked_false_for_a_non_parked_phase(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="tdd")
    assert is_parked(_item(), status, Tags()) is False


# --- is_failed_park -----------------------------------------------------------


def test_is_failed_park_true_for_parked_failed(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="parked", parked_state="failed")
    assert is_failed_park(status) is True


def test_is_failed_park_false_when_status_none() -> None:
    assert is_failed_park(None) is False


def test_is_failed_park_false_when_parked_but_not_failed(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="parked", parked_state="qa-ready")
    assert is_failed_park(status) is False


def test_is_failed_park_false_for_a_non_parked_phase(
    make_status: Callable[..., FleetStatus],
) -> None:
    status = make_status(phase="done")
    assert is_failed_park(status) is False
