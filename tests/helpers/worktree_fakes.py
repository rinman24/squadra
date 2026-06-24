"""An in-memory ``WorktreeAccess`` fake for the worktree contract suite.

:class:`FakeWorktree` conforms structurally to
:class:`squadra.worktree.WorktreeAccess` and records what it was asked to do, so
the contract suite can assert host-side create / archive / prune behavior without
a live git checkout or real disk moves. A failed-create is seedable so the
contract can pin the create-failure semantics.
"""

from dataclasses import dataclass, field
from pathlib import Path

from squadra.worktree import DEFAULT_BASE_REF, WorktreeCreateResult


@dataclass
class FakeWorktree:
    """``WorktreeAccess`` fake: records operations, seedable create failures."""

    fail_branches: set[str] = field(default_factory=set[str])
    prune_fails: bool = False

    created: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    archived: list[tuple[str, str, int]] = field(default_factory=list[tuple[str, str, int]])
    prune_count: int = 0

    def create(
        self, branch: str, worktree: str, base_ref: str = DEFAULT_BASE_REF
    ) -> WorktreeCreateResult:
        """Record the create; honor a seeded branch failure."""
        self.created.append((branch, worktree, base_ref))
        return WorktreeCreateResult(created=branch not in self.fail_branches, branch=branch)

    def archive(self, worktree: str, archive_root: str, attempt: int) -> Path:
        """Record the archive move; return the attempt-slot destination path."""
        self.archived.append((worktree, archive_root, attempt))
        return Path(archive_root) / f"attempt-{attempt}"

    def prune(self) -> bool:
        """Record one prune; honor a seeded failure."""
        self.prune_count += 1
        return not self.prune_fails
