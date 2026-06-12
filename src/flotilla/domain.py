"""Provider-neutral domain types for the fleet supervisor.

Pure data, no I/O and no provider specifics: the work-item DTOs the board
passes operate on (``IssueRef`` / ``IssueLinks``) and the per-pass outcome
records returned for logging and tests. PR2 renames these to the neutral
``WorkItem`` / ``WorkItemLinks`` vocabulary as the ``BoardAccess`` seam
generalizes; PR1 keeps the names unchanged.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IssueRef:
    """A board Issue as returned by a state query."""

    issue_id: int
    title: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IssueLinks:
    """The relations of one Issue that the claim pass cares about."""

    parent_id: int | None
    predecessor_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """What one claim pass did — returned for logging and tests."""

    inflight: tuple[int, ...]
    claimed: tuple[int, ...]
    skipped_blocked: tuple[int, ...]
    escalated: tuple[int, ...]
    rolled_back: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FinalizeOutcome:
    """What one finalize pass did."""

    finalized: tuple[int, ...]
    awaiting_merge: tuple[int, ...]
    cleanup_failed: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReapOutcome:
    """What one reap (watchdog) pass did."""

    reaped: tuple[int, ...]
    escalated: tuple[int, ...]
    skipped_alive: tuple[int, ...]
    skipped_parked: tuple[int, ...]
