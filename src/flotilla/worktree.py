"""The host-side worktree ResourceAccess seam (Resource = git worktrees).

``WorktreeAccess`` is the contract the supervisor uses to own git host-side
(ADR-0002 decision 2): because the sandboxed agent has no remote, the supervisor
fetches ``origin/main``, creates the slice worktree on the branch, and bind-mounts
it before launch — the agent starts already on the branch (no ``EnterWorktree``
in the agent). The same seam archives a dead attempt's worktree for inspection
and prunes stale administrative entries (the reap path's worktree work).

``GitWorktreeAccess`` is the concrete adapter. The git command runner and the
filesystem move are injected as constructor seams (mirroring ``AzCliAdo.run``),
so unit and contract tests drive it against fakes — no live git checkout or real
disk moves are required. The dry-run wrapper lives alongside in
:mod:`flotilla.dry_run` so a tick physically cannot mutate.

The orchestration wiring (F4) — mapping the engine's launch / retry actions onto
this seam — is out of scope for this slice.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Protocol

DEFAULT_BASE_REF: str = "origin/main"


@dataclass(frozen=True, slots=True)
class WorktreeCreateResult:
    """The outcome of one host-side worktree creation.

    ``created`` reports whether the worktree now exists on its branch (both the
    fetch and the ``worktree add`` exited 0); ``branch`` echoes the branch the
    worktree was created on, for the orchestrator's bookkeeping.
    """

    created: bool
    branch: str


class WorktreeAccess(Protocol):
    """Host-side create / archive / prune of slice worktrees."""

    def create(
        self, branch: str, worktree: str, base_ref: str = DEFAULT_BASE_REF
    ) -> WorktreeCreateResult:
        """Fetch the remote, then create the slice worktree on ``branch`` off ``base_ref``."""
        ...

    def archive(self, worktree: str, archive_root: str, attempt: int) -> Path:
        """Move a dead attempt's worktree under its archive slot; return the destination."""
        ...

    def prune(self) -> bool:
        """Prune stale worktree administrative entries; return ``False`` on failure."""
        ...


def _run_quiet(args: Sequence[str]) -> int:
    """Run a command, discarding output; return its exit code."""
    completed: subprocess.CompletedProcess[bytes] = subprocess.run(
        list(args), capture_output=True, check=False
    )
    return completed.returncode


def _move(src: str, dest: str) -> None:
    """Move a path, creating parent directories as needed."""
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)


class GitWorktreeAccess:
    """``WorktreeAccess`` backed by git worktrees + a filesystem move.

    The command runner and the move callable are injected so tests drive fakes;
    in production they are :func:`_run_quiet` and :func:`_move`. ``fleet_home``
    is the repo flotilla operates on (the ``git -C`` target).
    """

    def __init__(
        self,
        fleet_home: str | Path,
        run: Callable[[Sequence[str]], int] = _run_quiet,
        *,
        move: Callable[[str, str], None] = _move,
    ) -> None:
        """Bind the access to its repo root, a git runner, and a move callable."""
        self._fleet_home = str(fleet_home)
        self._run = run
        self._move = move

    def create(
        self, branch: str, worktree: str, base_ref: str = DEFAULT_BASE_REF
    ) -> WorktreeCreateResult:
        """Fetch ``origin`` then ``git worktree add -b <branch> <worktree> <base_ref>``.

        The fetch runs first so the worktree branches off a freshly-fetched base;
        a failed fetch short-circuits (the worktree is never added off a stale
        base). This matches the "always rebase onto current main" semantics —
        the eventual rebase is a no-op.
        """
        if self._run(["git", "-C", self._fleet_home, "fetch", "origin"]) != 0:
            return WorktreeCreateResult(created=False, branch=branch)
        added: int = self._run(
            ["git", "-C", self._fleet_home, "worktree", "add", "-b", branch, worktree, base_ref]
        )
        return WorktreeCreateResult(created=added == 0, branch=branch)

    def archive(self, worktree: str, archive_root: str, attempt: int) -> Path:
        """Move the dead attempt's worktree under ``<archive_root>/attempt-<attempt>``.

        Returns the destination path. The move callable owns parent-directory
        creation; the caller follows with :meth:`prune` to drop the now-stale
        administrative entry.
        """
        destination: Path = Path(archive_root) / f"attempt-{attempt}"
        self._move(worktree, str(destination))
        return destination

    def prune(self) -> bool:
        """Prune stale worktree entries with ``git worktree prune``."""
        return self._run(["git", "-C", self._fleet_home, "worktree", "prune"]) == 0
