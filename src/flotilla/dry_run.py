"""Dry-run wrappers for the deterministic F3 ResourceAccess seams.

``DryRunCleanup`` / ``DryRunWorktree`` give :class:`flotilla.cleanup.CleanupAccess`
and :class:`flotilla.worktree.WorktreeAccess` the same ``ReadOnlyBoard`` treatment
the board seam gets (ADR-0002 decision 8): every operation on these seams is a
mutation (deleting a branch, removing/pruning a worktree, tearing a project down,
creating/archiving a worktree), so each is absorbed — it logs a
``[dry-run] WOULD …`` line and performs no side effect, returning the optimistic
"it would have worked" result. A finalize/reap tick in dry-run mode therefore
physically cannot mutate git, docker, or the filesystem through these seams.

These wrappers live here, alongside the board's ``ReadOnlyBoard`` analogue, rather
than in ``supervisor.py``; the orchestration that wires them into ``dry_run_seams``
(extending the ``TickSeams`` exhaustiveness invariant) is the F4 cutover and is out
of scope for this slice.
"""

from datetime import UTC, datetime
from pathlib import Path

from flotilla.cleanup import CleanupAccess, CleanupResult
from flotilla.worktree import DEFAULT_BASE_REF, WorktreeAccess, WorktreeCreateResult


def _log(message: str) -> None:
    """Emit one timestamped supervisor log line (matches ``supervisor._log``)."""
    stamp: str = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"[{stamp}] supervisor: {message}")


class DryRunCleanup:
    """``CleanupAccess`` decorator that physically cannot mutate git or docker.

    Every operation logs the action a real finalize WOULD have performed and
    does nothing — dry-run safety is this boundary, not a flag threaded through
    finalize. The wrapped ``inner`` is never called, so a write cannot leak past
    this class while dry-run is active.
    """

    def __init__(self, inner: CleanupAccess) -> None:
        """Wrap ``inner``, absorbing every cleanup mutation."""
        self._inner = inner

    def delete_branch(self, branch: str) -> bool:
        """Absorb the branch deletion, logging the would-be ``git branch -D``."""
        _log(f"[dry-run] WOULD delete branch '{branch}'")
        return True

    def remove_worktree(self, worktree: str) -> bool:
        """Absorb the worktree removal, logging the would-be ``git worktree remove``."""
        _log(f"[dry-run] WOULD remove worktree {worktree}")
        return True

    def prune_worktrees(self) -> bool:
        """Absorb the prune, logging the would-be ``git worktree prune``."""
        _log("[dry-run] WOULD prune worktrees")
        return True

    def compose_down(self, project: str) -> bool:
        """Absorb the teardown, logging the would-be ``docker compose down -v``."""
        _log(f"[dry-run] WOULD compose down -v project '{project}'")
        return True

    def finalize(self, branch: str, worktree: str, project: str) -> CleanupResult:
        """Absorb the full cleanup sequence, logging each would-be step."""
        return CleanupResult(
            branch_deleted=self.delete_branch(branch),
            worktree_removed=self.remove_worktree(worktree),
            pruned=self.prune_worktrees(),
            compose_down=self.compose_down(project),
        )


class DryRunWorktree:
    """``WorktreeAccess`` decorator that physically cannot mutate git or the disk.

    Every operation logs the action a real tick WOULD have performed and does
    nothing — the wrapped ``inner`` is never called. ``archive`` still returns
    the destination path it WOULD have moved to (a pure computation, no I/O), so
    the orchestrator's bookkeeping reads the same shape under dry-run.
    """

    def __init__(self, inner: WorktreeAccess) -> None:
        """Wrap ``inner``, absorbing every worktree mutation."""
        self._inner = inner

    def create(
        self, branch: str, worktree: str, base_ref: str = DEFAULT_BASE_REF
    ) -> WorktreeCreateResult:
        """Absorb the create, logging the would-be fetch + ``git worktree add``."""
        _log(f"[dry-run] WOULD create worktree {worktree} on branch '{branch}' off {base_ref}")
        return WorktreeCreateResult(created=True, branch=branch)

    def archive(self, worktree: str, archive_root: str, attempt: int) -> Path:
        """Absorb the archive move, logging it; return the would-be destination."""
        destination: Path = Path(archive_root) / f"attempt-{attempt}"
        _log(f"[dry-run] WOULD archive worktree {worktree} to {destination}")
        return destination

    def prune(self) -> bool:
        """Absorb the prune, logging the would-be ``git worktree prune``."""
        _log("[dry-run] WOULD prune worktrees")
        return True
