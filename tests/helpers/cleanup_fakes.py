"""An in-memory ``CleanupAccess`` fake for the deterministic-cleanup contract suite.

:class:`FakeCleanup` conforms structurally to :class:`squadra.cleanup.CleanupAccess`
and records what it was asked to do, so the contract suite can assert the
deterministic behavior (branch deleted, worktree removed/pruned, project torn
down) without a live git checkout or docker daemon — and without any LLM call.
Per-operation failures are seedable so the contract can pin the best-effort,
partial-result semantics.
"""

from dataclasses import dataclass, field

from squadra.cleanup import CleanupResult


@dataclass
class FakeCleanup:
    """``CleanupAccess`` fake: records operations, seedable per-step failures."""

    # branches / worktrees / projects this fake should report as failing to act on
    fail_branches: set[str] = field(default_factory=set[str])
    fail_worktrees: set[str] = field(default_factory=set[str])
    fail_projects: set[str] = field(default_factory=set[str])
    prune_fails: bool = False

    # recorded effects, for assertions
    deleted_branches: list[str] = field(default_factory=list[str])
    removed_worktrees: list[str] = field(default_factory=list[str])
    composed_down: list[str] = field(default_factory=list[str])
    prune_count: int = 0

    def delete_branch(self, branch: str) -> bool:
        """Record the branch deletion; honor a seeded failure."""
        self.deleted_branches.append(branch)
        return branch not in self.fail_branches

    def remove_worktree(self, worktree: str) -> bool:
        """Record the worktree removal; honor a seeded failure."""
        self.removed_worktrees.append(worktree)
        return worktree not in self.fail_worktrees

    def prune_worktrees(self) -> bool:
        """Record one prune; honor a seeded failure."""
        self.prune_count += 1
        return not self.prune_fails

    def compose_down(self, project: str) -> bool:
        """Record the compose teardown; honor a seeded failure."""
        self.composed_down.append(project)
        return project not in self.fail_projects

    def finalize(self, branch: str, worktree: str, project: str) -> CleanupResult:
        """Run the deterministic sequence; every step runs (best-effort)."""
        return CleanupResult(
            branch_deleted=self.delete_branch(branch),
            worktree_removed=self.remove_worktree(worktree),
            pruned=self.prune_worktrees(),
            compose_down=self.compose_down(project),
        )
