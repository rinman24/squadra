"""Pure decision functions for the supervisor passes.

Data in, decision out — no I/O, no board calls. Branch naming and the reap
pass's park / failed-park eligibility predicates live here; the orchestration
and I/O that consume them stay in ``supervisor``.
"""

import re

from flotilla.config import DEFAULT_BRANCH_TEMPLATE
from flotilla.domain import Tags, WorkItem
from flotilla.status import FleetStatus


def slice_branch(
    item_id: int, title: str, attempt: int, template: str = DEFAULT_BRANCH_TEMPLATE
) -> str:
    """Derive the slice's branch name from ``template`` plus the retry suffix.

    The slug is the text after the first ``":"`` (else the whole title),
    lowercased, with runs of non-``[a-z0-9]`` collapsed to ``"-"``, capped at 32
    chars and stripped of leading/trailing ``"-"`` (``"slice"`` when empty). The
    template owns the ``{id}``/``{slug}`` layout; flotilla owns the retry rule —
    a ``-a{attempt}`` suffix is appended only when ``attempt > 1``.
    """
    base: str = title.split(":", 1)[1] if ":" in title else title
    slug: str = re.sub(r"[^a-z0-9]+", "-", base.lower())[:32].strip("-")
    if not slug:
        slug = "slice"
    suffix: str = f"-a{attempt}" if attempt > 1 else ""
    return f"{template.format(id=item_id, slug=slug)}{suffix}"


def is_parked(item: WorkItem, status: FleetStatus | None, tags: Tags) -> bool:
    """Report whether the runner stopped heartbeating on purpose (deliberate park).

    A runner carrying any tag in ``tags.parked`` stopped heartbeating on purpose
    — it is parked, not dead, and must never be reaped (addendum §3-4). The
    failed tag is in that set because a *tagged* ``<prefix>failed`` slice is
    already escalated and terminal (never auto-retried); an untagged
    ``parked_state="failed"`` status, by contrast, is positive failure evidence
    (a crash, OOM, dead auth, or any unhandled runner error), not a deliberate
    stop, so the slice stays reap-eligible. A finalized slice (phase ``done``) is
    always treated as parked — it must never be requeued.
    """
    if any(tag in tags.parked for tag in item.tags):
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
