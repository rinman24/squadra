"""Unit tests for the host-side ``WorktreeAccess`` adapter (no live git/disk).

The adapter is driven against a fake git runner and a fake move callable (its
two injected DI seams), so neither a live git checkout nor real filesystem moves
are required. The contract behavior lives in
``tests/contract/test_worktree_contract.py``.

Every host-side git argv is built through :mod:`squadra.git_host` (#193), so it
carries ``-c core.hooksPath=/dev/null`` and a narrowly-scoped ``safe.directory``;
these tests assert that hardening as well as the git sub-command.
"""

from collections.abc import Sequence

from squadra.worktree import GitWorktreeAccess, WorktreeCreateResult

_REPO = "/repo"


def _is_guarded(argv: Sequence[str], repo: str = _REPO) -> bool:
    """Whether a host-side argv carries the #193 hardening for ``repo``."""
    tokens: list[str] = list(argv)
    return (
        tokens[0] == "git"
        and "core.hooksPath=/dev/null" in tokens
        and f"safe.directory={repo}" in tokens
        and "safe.directory=*" not in tokens
        and "-C" in tokens
        and tokens[tokens.index("-C") + 1] == repo
    )


def _subcommand(argv: Sequence[str]) -> list[str]:
    """The tokens after ``-C <repo>`` — the git verb and its args."""
    tokens: list[str] = list(argv)
    return tokens[tokens.index("-C") + 2 :]


class _RecordingRunner:
    """Configurable exit codes keyed on a sub-command token; records every argv."""

    def __init__(self, fail_on: tuple[tuple[str, ...], ...] = ()) -> None:
        # fail_on: tuples of git verb tokens whose op should return non-zero.
        self.fail_on: set[tuple[str, ...]] = set(fail_on)
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        argv: list[str] = list(args)
        self.calls.append(argv)
        sub: tuple[str, ...] = tuple(_subcommand(argv))
        return 1 if sub in self.fail_on else 0


class _RecordingMover:
    """Records (src, dest) move requests; never touches the disk."""

    def __init__(self) -> None:
        self.moves: list[tuple[str, str]] = []

    def __call__(self, src: str, dest: str) -> None:
        self.moves.append((src, dest))


def test_create_fetches_then_adds_a_branch_worktree_off_origin_main() -> None:
    runner = _RecordingRunner()
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner)
    result: WorktreeCreateResult = wt.create(
        branch="feat/slice-9-x", worktree="/wt/slice-9", base_ref="origin/main"
    )
    assert result == WorktreeCreateResult(created=True, branch="feat/slice-9-x")
    # fetch first (the worktree must branch off a freshly-fetched origin/main),
    # then `git worktree add -b <branch> <path> <base_ref>` — both hardened.
    assert all(_is_guarded(call) for call in runner.calls)
    assert [_subcommand(call) for call in runner.calls] == [
        ["fetch", "origin"],
        ["worktree", "add", "-b", "feat/slice-9-x", "/wt/slice-9", "origin/main"],
    ]


def test_create_defaults_the_base_ref_to_origin_main() -> None:
    runner = _RecordingRunner()
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner)
    wt.create(branch="feat/slice-1-a", worktree="/wt/slice-1")
    assert runner.calls[-1][-1] == "origin/main"


def test_create_reports_failure_when_worktree_add_fails() -> None:
    runner = _RecordingRunner(
        fail_on=(("worktree", "add", "-b", "feat/x", "/wt/x", "origin/main"),)
    )
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner)
    result: WorktreeCreateResult = wt.create(branch="feat/x", worktree="/wt/x")
    assert result.created is False


def test_create_does_not_add_when_the_fetch_fails() -> None:
    runner = _RecordingRunner(fail_on=(("fetch", "origin"),))
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner)
    result: WorktreeCreateResult = wt.create(branch="feat/x", worktree="/wt/x")
    assert result.created is False
    # a failed fetch short-circuits — the worktree is never added off a stale base
    assert [_subcommand(call)[0] for call in runner.calls] == ["fetch"]


def test_archive_moves_the_dead_worktree_under_the_attempt_slot() -> None:
    runner = _RecordingRunner()
    mover = _RecordingMover()
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner, move=mover)
    archived = wt.archive(worktree="/wt/slice-9", archive_root="/fleet/9/archive", attempt=2)
    assert str(archived) == "/fleet/9/archive/attempt-2"
    assert mover.moves == [("/wt/slice-9", "/fleet/9/archive/attempt-2")]


def test_archive_then_prune_drops_the_stale_administrative_entry() -> None:
    runner = _RecordingRunner()
    mover = _RecordingMover()
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner, move=mover)
    wt.archive(worktree="/wt/slice-9", archive_root="/fleet/9/archive", attempt=1)
    assert wt.prune() is True
    assert _is_guarded(runner.calls[-1])
    assert _subcommand(runner.calls[-1]) == ["worktree", "prune"]


def test_prune_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner(fail_on=(("worktree", "prune"),))
    wt = GitWorktreeAccess(fleet_home=_REPO, run=runner)
    assert wt.prune() is False
