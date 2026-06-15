"""Unit tests for the host-side ``WorktreeAccess`` adapter (no live git/disk).

The adapter is driven against a fake git runner and a fake move callable (its
two injected DI seams), so neither a live git checkout nor real filesystem moves
are required. The contract behavior lives in
``tests/contract/test_worktree_contract.py``.
"""

from collections.abc import Sequence
from pathlib import Path

from flotilla.worktree import GitWorktreeAccess, WorktreeCreateResult


class _RecordingRunner:
    """Configurable exit codes per command; records every argv it is handed."""

    def __init__(self, exit_codes: dict[tuple[str, ...], int] | None = None) -> None:
        self.exit_codes = exit_codes or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        argv: list[str] = list(args)
        self.calls.append(argv)
        return self.exit_codes.get(tuple(argv), 0)


class _RecordingMover:
    """Records (src, dest) move requests; never touches the disk."""

    def __init__(self) -> None:
        self.moves: list[tuple[str, str]] = []

    def __call__(self, src: str, dest: str) -> None:
        self.moves.append((src, dest))


def test_create_fetches_then_adds_a_branch_worktree_off_origin_main() -> None:
    runner = _RecordingRunner()
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner)
    result: WorktreeCreateResult = wt.create(
        branch="feat/slice-9-x", worktree="/wt/slice-9", base_ref="origin/main"
    )
    assert result == WorktreeCreateResult(created=True, branch="feat/slice-9-x")
    # fetch first (the worktree must branch off a freshly-fetched origin/main),
    # then `git worktree add -b <branch> <path> <base_ref>`.
    assert runner.calls == [
        ["git", "-C", "/repo", "fetch", "origin"],
        [
            "git",
            "-C",
            "/repo",
            "worktree",
            "add",
            "-b",
            "feat/slice-9-x",
            "/wt/slice-9",
            "origin/main",
        ],
    ]


def test_create_defaults_the_base_ref_to_origin_main() -> None:
    runner = _RecordingRunner()
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner)
    wt.create(branch="feat/slice-1-a", worktree="/wt/slice-1")
    assert runner.calls[-1][-1] == "origin/main"


def test_create_reports_failure_when_worktree_add_fails() -> None:
    add_argv: tuple[str, ...] = (
        "git",
        "-C",
        "/repo",
        "worktree",
        "add",
        "-b",
        "feat/x",
        "/wt/x",
        "origin/main",
    )
    runner = _RecordingRunner({add_argv: 1})
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner)
    result: WorktreeCreateResult = wt.create(branch="feat/x", worktree="/wt/x")
    assert result.created is False


def test_create_does_not_add_when_the_fetch_fails() -> None:
    runner = _RecordingRunner({("git", "-C", "/repo", "fetch", "origin"): 1})
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner)
    result: WorktreeCreateResult = wt.create(branch="feat/x", worktree="/wt/x")
    assert result.created is False
    # a failed fetch short-circuits — the worktree is never added off a stale base
    assert [call[3] for call in runner.calls] == ["fetch"]


def test_archive_moves_the_dead_worktree_under_the_attempt_slot() -> None:
    runner = _RecordingRunner()
    mover = _RecordingMover()
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner, move=mover)
    archived: Path = wt.archive(worktree="/wt/slice-9", archive_root="/fleet/9/archive", attempt=2)
    assert archived == Path("/fleet/9/archive/attempt-2")
    assert mover.moves == [("/wt/slice-9", "/fleet/9/archive/attempt-2")]


def test_archive_then_prune_drops_the_stale_administrative_entry() -> None:
    runner = _RecordingRunner()
    mover = _RecordingMover()
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner, move=mover)
    wt.archive(worktree="/wt/slice-9", archive_root="/fleet/9/archive", attempt=1)
    assert wt.prune() is True
    assert runner.calls[-1] == ["git", "-C", "/repo", "worktree", "prune"]


def test_prune_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner({("git", "-C", "/repo", "worktree", "prune"): 1})
    wt = GitWorktreeAccess(fleet_home="/repo", run=runner)
    assert wt.prune() is False
