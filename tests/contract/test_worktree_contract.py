"""The ``WorktreeAccess`` conformance suite (host-side create / archive / prune).

Runs against BOTH the real :class:`squadra.worktree.GitWorktreeAccess` (driven by
a fake git runner + fake mover — no live git/disk) and an in-memory
:class:`tests.helpers.worktree_fakes.FakeWorktree`, via the parametrized
``worktree`` fixture. Any implementation that passes this suite satisfies the
seam contract: a worktree is created on its branch off the base ref, a dead
attempt is archived under its attempt slot, and stale entries are pruned.
"""

from pathlib import Path

from squadra.worktree import WorktreeAccess, WorktreeCreateResult

# worktree is provided by tests/contract/conftest.py (parametrized over the real
# GitWorktreeAccess and FakeWorktree)


def test_create_reports_success_on_the_branch(worktree: WorktreeAccess) -> None:
    result: WorktreeCreateResult = worktree.create(
        branch="feat/slice-1-x", worktree="/wt/slice-1", base_ref="origin/main"
    )
    assert result == WorktreeCreateResult(created=True, branch="feat/slice-1-x")


def test_archive_returns_the_attempt_slot_destination(worktree: WorktreeAccess) -> None:
    archived: Path = worktree.archive(
        worktree="/wt/slice-2", archive_root="/fleet/2/archive", attempt=3
    )
    assert archived == Path("/fleet/2/archive/attempt-3")


def test_prune_roundtrips_success(worktree: WorktreeAccess) -> None:
    assert worktree.prune() is True
