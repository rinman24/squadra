"""Unit tests for the F3 dry-run wrappers (write-blocking, log-only).

The ``DryRunCleanup`` / ``DryRunWorktree`` wrappers mirror the supervisor's
``ReadOnlyBoard`` boundary: every mutating operation is absorbed — it logs a
``[dry-run] WOULD …`` line and performs no side effect — so a finalize/reap tick
in dry-run mode physically cannot delete a branch, remove a worktree, tear a
project down, or move a directory. These tests prove the wrappers never reach
their wrapped inner Access (which is a recording fake that would record any leak)
and that each emits a ``[dry-run] WOULD`` line.
"""

import pytest

from flotilla.cleanup import CleanupResult
from flotilla.dry_run import DryRunCleanup, DryRunWorktree
from flotilla.worktree import WorktreeCreateResult
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.worktree_fakes import FakeWorktree

# FakeCleanup / FakeWorktree are provided by tests/helpers/


# --- DryRunCleanup ------------------------------------------------------------


def test_dry_run_cleanup_finalize_mutates_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    inner = FakeCleanup()
    dry = DryRunCleanup(inner)
    result: CleanupResult = dry.finalize(branch="feat/x", worktree="/wt/x", project="x")
    # reports a fully-successful would-be cleanup without touching the inner
    assert result == CleanupResult(
        branch_deleted=True, worktree_removed=True, pruned=True, compose_down=True
    )
    assert inner.deleted_branches == []
    assert inner.removed_worktrees == []
    assert inner.composed_down == []
    assert inner.prune_count == 0
    assert "[dry-run] WOULD" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("delete_branch", ("feat/x",)),
        ("remove_worktree", ("/wt/x",)),
        ("prune_worktrees", ()),
        ("compose_down", ("x",)),
    ],
)
def test_dry_run_cleanup_each_op_logs_and_returns_true(
    op: str, args: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    inner = FakeCleanup()
    dry = DryRunCleanup(inner)
    assert getattr(dry, op)(*args) is True
    # the inner fake recorded nothing — every op was absorbed at the boundary
    assert inner.deleted_branches == []
    assert inner.removed_worktrees == []
    assert inner.composed_down == []
    assert inner.prune_count == 0
    assert "[dry-run] WOULD" in capsys.readouterr().out


# --- DryRunWorktree -----------------------------------------------------------


def test_dry_run_worktree_create_mutates_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    inner = FakeWorktree()
    dry = DryRunWorktree(inner)
    result: WorktreeCreateResult = dry.create(branch="feat/x", worktree="/wt/x")
    assert result == WorktreeCreateResult(created=True, branch="feat/x")
    assert inner.created == []
    assert "[dry-run] WOULD" in capsys.readouterr().out


def test_dry_run_worktree_archive_returns_destination_without_moving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inner = FakeWorktree()
    dry = DryRunWorktree(inner)
    archived = dry.archive(worktree="/wt/9", archive_root="/fleet/9/archive", attempt=2)
    assert str(archived) == "/fleet/9/archive/attempt-2"
    assert inner.archived == []
    assert "[dry-run] WOULD" in capsys.readouterr().out


def test_dry_run_worktree_prune_logs_and_returns_true(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inner = FakeWorktree()
    dry = DryRunWorktree(inner)
    assert dry.prune() is True
    assert inner.prune_count == 0
    assert "[dry-run] WOULD" in capsys.readouterr().out
