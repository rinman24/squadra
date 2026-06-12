"""Provider-neutral domain types for the fleet supervisor.

Pure data, no I/O and no provider specifics. The supervisor and the pure
engines speak this vocabulary; the ``BoardAccess`` adapter translates it to and
from a concrete board's native semantics at the boundary:

- :class:`Lifecycle` — the 3-bucket state invariant (QUEUED/ACTIVE/DONE) that
  replaces board-native state strings everywhere in core.
- :class:`WorkItem` / :class:`WorkItemLinks` — the work-item DTOs the passes
  operate on (were ``IssueRef`` / ``IssueLinks``).
- the :data:`CommentEvent` union — structured discussion events the adapter
  renders to native markup (ADO→HTML, GitHub→Markdown); core emits no markup.
- :class:`Tags` — the fleet's tag vocabulary under one configurable namespace
  prefix; the five suffixes are fixed canonical vocabulary.
- the per-pass outcome records returned for logging and tests.
"""

from dataclasses import dataclass
from enum import Enum

from flotilla.constants import (
    DEFAULT_TAG_PREFIX,
    PARKED_TAG_SUFFIXES,
    TAG_SUFFIX_AWAITING_PR_APPROVAL,
    TAG_SUFFIX_CLAIMED,
    TAG_SUFFIX_FAILED,
    TAG_SUFFIX_NEEDS_DECISION,
    TAG_SUFFIX_QA_READY,
)


class Lifecycle(Enum):
    """The neutral 3-bucket state a board column maps onto (a domain invariant).

    Many native states may map to one bucket (e.g. ADO ``Approved`` + ``Done``
    both → ``DONE``); the adapter owns that translation. No board-native state
    string ever appears in the supervisor or the engines.
    """

    QUEUED = "queued"  # claimable / not started
    ACTIVE = "active"  # claimed / in-flight
    DONE = "done"  # finalize-eligible


@dataclass(frozen=True, slots=True)
class WorkItem:
    """A board work item as returned by a state query (was ``IssueRef``)."""

    item_id: int
    title: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkItemLinks:
    """The relations of one work item that the claim pass cares about."""

    parent_id: int | None
    predecessor_ids: tuple[int, ...]


# --- structured comment events (the adapter renders these to native markup) ---


@dataclass(frozen=True, slots=True)
class Claimed:
    """The supervisor claimed an item and launched its runner."""

    runner_id: str
    branch: str
    when: str


@dataclass(frozen=True, slots=True)
class RolledBack:
    """A claim was rolled back (e.g. the runner launch failed)."""

    reason: str


@dataclass(frozen=True, slots=True)
class Finalized:
    """A merged slice was retired: its PR completed and its branch cleaned up."""

    pr_url: str
    branch: str


@dataclass(frozen=True, slots=True)
class Reaped:
    """A stale, dead runner was requeued for retry."""

    evidence: str
    attempt: int


@dataclass(frozen=True, slots=True)
class Escalated:
    """A slice exhausted its retry budget and was escalated to the failed tag."""

    attempt: int
    cap: int


# The closed set of discussion events core emits. The adapter pattern-matches
# this union to render native markup — a cleaner surface than inline f-strings.
CommentEvent = Claimed | RolledBack | Finalized | Reaped | Escalated


@dataclass(frozen=True, slots=True)
class Tags:
    """The fleet's tag vocabulary under one configurable namespace ``prefix``.

    The five suffixes are fixed canonical vocabulary; only the prefix is
    configurable. Detection of "a fleet tag" is prefix-based so an adopter's
    custom prefix still partitions fleet-owned tags from the board's own.
    """

    prefix: str = DEFAULT_TAG_PREFIX

    @property
    def claimed(self) -> str:
        """The tag a fleet-claimed item carries (vs a human's manual move)."""
        return f"{self.prefix}{TAG_SUFFIX_CLAIMED}"

    @property
    def failed(self) -> str:
        """The escalation tag for a slice whose retries are exhausted."""
        return f"{self.prefix}{TAG_SUFFIX_FAILED}"

    @property
    def needs_decision(self) -> str:
        """The deliberate-park tag for a slice awaiting a human decision."""
        return f"{self.prefix}{TAG_SUFFIX_NEEDS_DECISION}"

    @property
    def qa_ready(self) -> str:
        """The deliberate-park tag for a slice parked at QA."""
        return f"{self.prefix}{TAG_SUFFIX_QA_READY}"

    @property
    def awaiting_pr_approval(self) -> str:
        """The deliberate-park tag for a slice whose PR awaits approval."""
        return f"{self.prefix}{TAG_SUFFIX_AWAITING_PR_APPROVAL}"

    @property
    def parked(self) -> tuple[str, ...]:
        """The fully-qualified tags marking a deliberate park (never reaped)."""
        return tuple(f"{self.prefix}{suffix}" for suffix in PARKED_TAG_SUFFIXES)

    def is_fleet_tag(self, tag: str) -> bool:
        """Report whether ``tag`` is in the fleet's namespace (prefix-based)."""
        return tag.startswith(self.prefix)


# --- per-pass outcome records (returned for logging and tests) ----------------


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
