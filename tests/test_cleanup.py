"""Unit tests for the deterministic ``CleanupAccess`` adapter (no live git/docker).

The adapter is exercised against a fake command runner (the injected DI seam,
mirroring ``AzCliAdo``'s ``run``), so no live git or docker daemon is required.
The contract behavior — that finalize is LLM-free and a pure sequence of
deterministic git/compose commands — is covered by the contract suite in
``tests/contract/test_cleanup_contract.py``.

Every host-side **git** argv is built through :mod:`flotilla.git_host` (#193), so
it carries ``-c core.hooksPath=/dev/null`` and a narrowly-scoped
``safe.directory``; these tests assert that hardening as well as the git verb.
The ``docker compose`` teardown is not a git op and is asserted unchanged.
"""

from collections.abc import Sequence

from flotilla.cleanup import CleanupResult, DeterministicCleanup

_REPO = "/repo"


def _is_guarded_git(argv: Sequence[str], repo: str = _REPO) -> bool:
    """Whether a host-side git argv carries the #193 hardening for ``repo``."""
    tokens: list[str] = list(argv)
    return (
        tokens[0] == "git"
        and "core.hooksPath=/dev/null" in tokens
        and f"safe.directory={repo}" in tokens
        and "safe.directory=*" not in tokens
        and "-C" in tokens
        and tokens[tokens.index("-C") + 1] == repo
    )


def _git_subcommand(argv: Sequence[str]) -> list[str]:
    """The tokens after ``-C <repo>`` — the git verb and its args."""
    tokens: list[str] = list(argv)
    return tokens[tokens.index("-C") + 2 :]


class _RecordingRunner:
    """Configurable exit codes keyed on a sub-command token; records every argv."""

    def __init__(self, fail_on: tuple[tuple[str, ...], ...] = ()) -> None:
        # fail_on: tuples of trailing tokens whose op should return non-zero.
        self.fail_on: tuple[tuple[str, ...], ...] = fail_on
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], cwd: object = None) -> int:
        argv: list[str] = list(args)
        self.calls.append(argv)
        for failing in self.fail_on:
            if tuple(argv[-len(failing) :]) == failing:
                return 1
        return 0


def test_delete_branch_runs_git_branch_delete() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.delete_branch("feat/slice-9-x") is True
    assert _is_guarded_git(runner.calls[-1])
    assert _git_subcommand(runner.calls[-1]) == ["branch", "-D", "feat/slice-9-x"]


def test_delete_branch_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner(fail_on=(("branch", "-D", "feat/gone"),))
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.delete_branch("feat/gone") is False


def test_remove_worktree_runs_git_worktree_remove_force() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.remove_worktree("/wt/slice-9") is True
    assert _is_guarded_git(runner.calls[-1])
    assert _git_subcommand(runner.calls[-1]) == ["worktree", "remove", "--force", "/wt/slice-9"]


def test_prune_worktrees_runs_git_worktree_prune() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.prune_worktrees() is True
    assert _is_guarded_git(runner.calls[-1])
    assert _git_subcommand(runner.calls[-1]) == ["worktree", "prune"]


def test_compose_down_runs_docker_compose_down_with_volumes() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.compose_down("slice-9") is True
    # docker is not a git op — asserted verbatim (no hooks guard).
    assert runner.calls[-1] == ["docker", "compose", "-p", "slice-9", "down", "-v"]


def test_compose_down_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner(fail_on=(("compose", "-p", "slice-9", "down", "-v"),))
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    assert cleanup.compose_down("slice-9") is False


def test_finalize_runs_branch_then_worktree_then_compose_in_order() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    result: CleanupResult = cleanup.finalize(
        branch="feat/slice-9-x", worktree="/wt/slice-9", project="slice-9"
    )
    assert result == CleanupResult(
        branch_deleted=True, worktree_removed=True, pruned=True, compose_down=True
    )
    # git branch -D ; git worktree remove ; git worktree prune ; docker compose down -v
    git_subs: list[list[str]] = [_git_subcommand(c) for c in runner.calls[:3]]
    assert all(_is_guarded_git(c) for c in runner.calls[:3])
    assert git_subs == [
        ["branch", "-D", "feat/slice-9-x"],
        ["worktree", "remove", "--force", "/wt/slice-9"],
        ["worktree", "prune"],
    ]
    assert runner.calls[3] == ["docker", "compose", "-p", "slice-9", "down", "-v"]


def test_finalize_reports_per_step_failure_without_aborting_the_sequence() -> None:
    # The worktree remove fails, but prune + compose-down still run (best effort,
    # deterministic): a leftover worktree must not block tearing the project down.
    runner = _RecordingRunner(fail_on=(("worktree", "remove", "--force", "/wt/slice-9"),))
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    result: CleanupResult = cleanup.finalize(
        branch="feat/slice-9-x", worktree="/wt/slice-9", project="slice-9"
    )
    assert result == CleanupResult(
        branch_deleted=True, worktree_removed=False, pruned=True, compose_down=True
    )
    # all four commands were attempted despite the worktree-remove failure
    assert len(runner.calls) == 4


def test_finalize_makes_no_llm_call() -> None:
    # The deterministic cleanup must never spawn claude: every command it issues
    # is git or docker, never a model call.
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home=_REPO, run=runner)
    cleanup.finalize(branch="feat/x", worktree="/wt/x", project="x")
    assert all(call[0] in ("git", "docker") for call in runner.calls)
    assert not any("claude" in token for call in runner.calls for token in call)
