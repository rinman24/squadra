"""Unit tests for the deterministic ``CleanupAccess`` adapter (no live git/docker).

The adapter is exercised against a fake command runner (the injected DI seam,
mirroring ``AzCliAdo``'s ``run``), so no live git or docker daemon is required.
The contract behavior — that finalize is LLM-free and a pure sequence of
deterministic git/compose commands — is covered by the contract suite in
``tests/contract/test_cleanup_contract.py``.
"""

from collections.abc import Sequence

from flotilla.cleanup import CleanupResult, DeterministicCleanup


class _RecordingRunner:
    """Configurable exit codes per command; records every argv it is handed."""

    def __init__(self, exit_codes: dict[tuple[str, ...], int] | None = None) -> None:
        self.exit_codes = exit_codes or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], cwd: object = None) -> int:
        argv: list[str] = list(args)
        self.calls.append(argv)
        return self.exit_codes.get(tuple(argv), 0)


def test_delete_branch_runs_git_branch_delete() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.delete_branch("feat/slice-9-x") is True
    assert runner.calls[-1] == ["git", "-C", "/repo", "branch", "-D", "feat/slice-9-x"]


def test_delete_branch_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner({("git", "-C", "/repo", "branch", "-D", "feat/gone"): 1})
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.delete_branch("feat/gone") is False


def test_remove_worktree_runs_git_worktree_remove_force() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.remove_worktree("/wt/slice-9") is True
    assert runner.calls[-1] == [
        "git",
        "-C",
        "/repo",
        "worktree",
        "remove",
        "--force",
        "/wt/slice-9",
    ]


def test_prune_worktrees_runs_git_worktree_prune() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.prune_worktrees() is True
    assert runner.calls[-1] == ["git", "-C", "/repo", "worktree", "prune"]


def test_compose_down_runs_docker_compose_down_with_volumes() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.compose_down("slice-9") is True
    assert runner.calls[-1] == ["docker", "compose", "-p", "slice-9", "down", "-v"]


def test_compose_down_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingRunner({("docker", "compose", "-p", "slice-9", "down", "-v"): 1})
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    assert cleanup.compose_down("slice-9") is False


def test_finalize_runs_branch_then_worktree_then_compose_in_order() -> None:
    runner = _RecordingRunner()
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    result: CleanupResult = cleanup.finalize(
        branch="feat/slice-9-x", worktree="/wt/slice-9", project="slice-9"
    )
    assert result == CleanupResult(
        branch_deleted=True, worktree_removed=True, pruned=True, compose_down=True
    )
    # git branch -D ; git worktree remove ; git worktree prune ; docker compose down -v
    assert runner.calls == [
        ["git", "-C", "/repo", "branch", "-D", "feat/slice-9-x"],
        ["git", "-C", "/repo", "worktree", "remove", "--force", "/wt/slice-9"],
        ["git", "-C", "/repo", "worktree", "prune"],
        ["docker", "compose", "-p", "slice-9", "down", "-v"],
    ]


def test_finalize_reports_per_step_failure_without_aborting_the_sequence() -> None:
    # The worktree remove fails, but prune + compose-down still run (best effort,
    # deterministic): a leftover worktree must not block tearing the project down.
    runner = _RecordingRunner(
        {("git", "-C", "/repo", "worktree", "remove", "--force", "/wt/slice-9"): 1}
    )
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
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
    cleanup = DeterministicCleanup(fleet_home="/repo", run=runner)
    cleanup.finalize(branch="feat/x", worktree="/wt/x", project="x")
    assert all(call[0] in ("git", "docker") for call in runner.calls)
    assert not any("claude" in token for call in runner.calls for token in call)
