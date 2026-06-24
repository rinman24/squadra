"""The deterministic cleanup ResourceAccess seam (Resource = git + docker compose).

``CleanupAccess`` is the contract finalize depends on to retire a merged slice:
the supervisor already knows the merged branch (it opened the PR;
``completed_pr_url`` returned a URL), so cleanup is a pure, deterministic
sequence of git/compose commands — **no LLM** (ADR-0002 decision 4). This
replaces the headless ``/cleanup-merged-branches`` claude call (``ClaudeCleanup``),
removing the ``finalize → claude`` coupling so the auth-probe guards the claim
path only and the fleet makes exactly one claude call (the contained runner).

``DeterministicCleanup`` is the concrete adapter. The git/docker command runner
is injected as a constructor seam (mirroring ``AzCliAdo.run`` in
:mod:`squadra.board`), so unit and contract tests drive it against a fake — no
live git or docker daemon is required. The dry-run wrapper lives alongside in
:mod:`squadra.dry_run` so a finalize tick physically cannot mutate.

The orchestration wiring (F4) — mapping the engine's ``FinalizeCleanup`` action
onto this seam — is out of scope for this slice.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol

from squadra.git_host import host_git_argv


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """The per-step outcome of one deterministic finalize cleanup.

    Each field reports whether that command exited 0. The steps are best-effort
    and independent: a failed worktree-remove does not abort the rest of the
    sequence (a leftover worktree must not block tearing the project down), so a
    partial-failure result is normal — the orchestrator can retry the failed
    steps on a later tick.
    """

    branch_deleted: bool
    worktree_removed: bool
    pruned: bool
    compose_down: bool


class CleanupAccess(Protocol):
    """Deterministic retirement of a merged slice's branch / worktree / sandbox."""

    def delete_branch(self, branch: str) -> bool:
        """Delete the known-merged branch; return ``False`` on a non-zero exit."""
        ...

    def remove_worktree(self, worktree: str) -> bool:
        """Remove the slice's worktree (force); return ``False`` on a non-zero exit."""
        ...

    def prune_worktrees(self) -> bool:
        """Prune stale worktree administrative entries; return ``False`` on failure."""
        ...

    def compose_down(self, project: str) -> bool:
        """``docker compose down -v`` the leftover project; ``False`` on failure."""
        ...

    def finalize(self, branch: str, worktree: str, project: str) -> CleanupResult:
        """Run the full deterministic cleanup sequence, returning per-step outcomes."""
        ...


def _run_quiet(args: Sequence[str], cwd: Path | None = None) -> int:
    """Run a command, discarding output; return its exit code."""
    completed: subprocess.CompletedProcess[bytes] = subprocess.run(
        list(args), capture_output=True, check=False, cwd=cwd
    )
    return completed.returncode


class DeterministicCleanup:
    """``CleanupAccess`` backed by git + ``docker compose``, no LLM.

    The command runner is injected so tests drive a fake; in production it is
    :func:`_run_quiet`. ``fleet_home`` is the repo squadra operates on (the
    ``git -C`` target for the worktree/branch operations).
    """

    def __init__(
        self,
        fleet_home: str | Path,
        run: Callable[[Sequence[str]], int] = _run_quiet,
    ) -> None:
        """Bind the cleaner to its repo root and an injected command runner."""
        self._fleet_home = str(fleet_home)
        self._run = run

    def delete_branch(self, branch: str) -> bool:
        """Delete the merged branch with ``git branch -D``."""
        return self._run(host_git_argv("branch", "-D", branch, work_dir=self._fleet_home)) == 0

    def remove_worktree(self, worktree: str) -> bool:
        """Remove the slice's worktree with ``git worktree remove --force``."""
        return (
            self._run(
                host_git_argv("worktree", "remove", "--force", worktree, work_dir=self._fleet_home)
            )
            == 0
        )

    def prune_worktrees(self) -> bool:
        """Prune stale worktree entries with ``git worktree prune``."""
        return self._run(host_git_argv("worktree", "prune", work_dir=self._fleet_home)) == 0

    def compose_down(self, project: str) -> bool:
        """Tear the leftover compose project down with volumes (``down -v``)."""
        return self._run(["docker", "compose", "-p", project, "down", "-v"]) == 0

    def finalize(self, branch: str, worktree: str, project: str) -> CleanupResult:
        """Run delete-branch → remove-worktree → prune → compose-down, best-effort.

        The steps run unconditionally and independently (a failure in one does
        not abort the rest) so a single stuck artifact cannot strand the others;
        the per-step booleans let the orchestrator retry only what failed.
        """
        branch_deleted: bool = self.delete_branch(branch)
        worktree_removed: bool = self.remove_worktree(worktree)
        pruned: bool = self.prune_worktrees()
        compose_down: bool = self.compose_down(project)
        return CleanupResult(
            branch_deleted=branch_deleted,
            worktree_removed=worktree_removed,
            pruned=pruned,
            compose_down=compose_down,
        )
