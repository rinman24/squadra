"""Unit tests for the pure decision functions in :mod:`squadra.engines`.

The ``is_parked`` / ``is_failed_park`` predicates were retired in the F4 cutover
(ADR-0002 decision 3): the :class:`~squadra.engines.LifecycleEngine` folds the
deliberate-park / failed-park distinction directly into its fact-derivation, and
``tests/test_lifecycle_engine.py`` (#151) exercises that folding. What survives
here is ``increment_branch``, the branch-naming rule the orchestrator still calls.
"""

from squadra.engines import increment_branch


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
