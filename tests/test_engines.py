"""Unit tests for the pure decision functions in :mod:`squadra.engines`.

The ``is_parked`` / ``is_failed_park`` predicates were retired in the F4 cutover
(ADR-0002 decision 3): the :class:`~squadra.engines.LifecycleEngine` folds the
deliberate-park / failed-park distinction directly into its fact-derivation, and
``tests/test_lifecycle_engine.py`` (#151) exercises that folding. What survives
here is ``increment_branch``, the branch-naming rule the orchestrator still calls,
and ``check_transition``, the four-bucket lifecycle transition rule (ADR-0004).
"""

import pytest

from squadra.domain import Lifecycle
from squadra.engines import TransitionRefusedError, check_transition, increment_branch


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


def test_withdrawing_an_active_increment_is_refused_until_decided() -> None:
    with pytest.raises(TransitionRefusedError, match="active"):
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
