"""The ``CleanupAccess`` conformance suite (deterministic, LLM-free).

Runs against BOTH the real :class:`squadra.cleanup.DeterministicCleanup` (driven
by a recording command runner — no live git/docker) and an in-memory
:class:`tests.helpers.cleanup_fakes.FakeCleanup`, via the parametrized ``cleanup``
fixture. Any implementation that passes this suite satisfies the seam contract:
finalize is a deterministic, best-effort sequence of git/docker operations with a
per-step result, and never an LLM call.
"""

from squadra.cleanup import CleanupAccess, CleanupResult

# cleanup is provided by tests/contract/conftest.py (parametrized over the real
# DeterministicCleanup and FakeCleanup)


def test_finalize_reports_all_steps_succeeding(cleanup: CleanupAccess) -> None:
    result: CleanupResult = cleanup.finalize(
        branch="feat/slice-1-x", worktree="/wt/slice-1", project="slice-1"
    )
    assert result == CleanupResult(
        branch_deleted=True, worktree_removed=True, pruned=True, compose_down=True
    )


def test_delete_branch_roundtrips_success(cleanup: CleanupAccess) -> None:
    assert cleanup.delete_branch("feat/slice-2-y") is True


def test_remove_worktree_roundtrips_success(cleanup: CleanupAccess) -> None:
    assert cleanup.remove_worktree("/wt/slice-2") is True


def test_prune_worktrees_roundtrips_success(cleanup: CleanupAccess) -> None:
    assert cleanup.prune_worktrees() is True


def test_compose_down_roundtrips_success(cleanup: CleanupAccess) -> None:
    assert cleanup.compose_down("slice-2") is True
