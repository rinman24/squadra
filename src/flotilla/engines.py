"""Pure decision functions for the supervisor passes.

Data in, decision out — no I/O, no board calls. Branch naming and the reap
pass's park / failed-park eligibility predicates live here; the orchestration
and I/O that consume them stay in ``supervisor``.
"""

import re
from typing import Final

from flotilla.constants import (
    TAG_AWAITING_PR_APPROVAL,
    TAG_FAILED,
    TAG_NEEDS_DECISION,
    TAG_QA_READY,
)
from flotilla.domain import IssueRef
from flotilla.status import FleetStatus

# A runner carrying any of these tags stopped heartbeating on purpose — it is
# parked, not dead, and must never be reaped (addendum §3-4). TAG_FAILED stays
# here because a *tagged* fleet:failed slice is already escalated and terminal
# (never auto-retried); an untagged ``parked_state="failed"`` status, by
# contrast, is positive failure evidence and reap-eligible (see is_parked).
_PARKED_TAGS: Final[tuple[str, ...]] = (
    TAG_NEEDS_DECISION,
    TAG_QA_READY,
    TAG_AWAITING_PR_APPROVAL,
    TAG_FAILED,
)


def slice_branch(issue_id: int, title: str, attempt: int) -> str:
    """Derive the slice's branch name (`feat/slice-<id>-<kebab>[ -aN ]`)."""
    base: str = title.split(":", 1)[1] if ":" in title else title
    slug: str = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:32].strip("-")
    if not slug:
        slug = "slice"
    suffix: str = f"-a{attempt}" if attempt > 1 else ""
    return f"feat/slice-{issue_id}-{slug}{suffix}"


def is_parked(issue: IssueRef, status: FleetStatus | None) -> bool:
    """Report whether the runner stopped heartbeating on purpose (deliberate park).

    A ``parked_state="failed"`` status is excluded: it is positive failure
    evidence (a crash, OOM, dead auth, or any unhandled runner error), not a
    deliberate stop, so the slice stays reap-eligible. A finalized slice
    (phase ``done``) is always treated as parked — it must never be requeued.
    """
    if any(tag in _PARKED_TAGS for tag in issue.tags):
        return True
    if status is None:
        return False
    if status.phase == "done":
        return True
    return status.phase == "parked" and status.parked_state != "failed"


def is_failed_park(status: FleetStatus | None) -> bool:
    """Report whether the status records a failed park (positive failure evidence).

    A failed park skips the staleness wait — the pid-aliveness check alone
    decides whether the slice is reaped immediately.
    """
    return status is not None and status.phase == "parked" and status.parked_state == "failed"
