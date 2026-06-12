"""Fleet supervisor tick — ADR-0007 decision 1, addendum §§1–5.

A deterministic, stateless, token-free scheduled script (no LLM). Each tick
reconstructs its entire view from ADO truth and runs three ordered passes
under one lock (addendum §5) — finalize and reap before claim, so the cap
accounting is fresh:

1. **finalize** — a slice whose Issue is ``Done`` and whose PR has completed
   gets the existing ``/cleanup-merged-branches`` skill run headlessly for its
   branch, its ``fleet:*`` tags dropped, and its status set to ``done``.
2. **reap** (watchdog) — a claimed slice whose heartbeat is stale (10 min) and
   whose runner process is confirmed dead is requeued: worktree archived for
   inspection, ``Doing → To Do``, ``fleet:claimed`` dropped; the next claim
   retries from a fresh worktree with attempt+1, and exhausted retries
   escalate to ``fleet:failed`` instead. A failed park (``parked_state=failed``
   plus a dead runner pid) is requeued immediately — positive failure
   evidence, no staleness wait — while deliberate parks are never reaped.
3. **claim** — unblocked + unclaimed ``To Do`` Issues are claimed up to the
   cap (``To Do → Doing`` + tag ``fleet:claimed`` + a stamped comment;
   ``System.State`` arbitrates) and one runner pane is launched per slice.

Finalize and claim depend on a working ``claude`` (the headless cleanup skill
and the runner itself), so a tick with such work pending first preflights
claude auth with a throwaway prompt. When the probe fails — dead auth and a
transient API outage read identically — the tick degrades to the reap pass
only (az + git) and retries next tick; idle and saturated ticks never probe.

Overlapping ticks serialize via a non-blocking ``flock`` on
``<fleet-root>/supervisor.lock`` — the losing tick exits cleanly without
touching ADO.

A tick can be a **dry run** (``--dry-run`` / ``FLEET_DRY_RUN=1``): the full
finalize/reap/claim read+plan logic runs and reports what a real tick WOULD
do, but every side effect — ADO writes, runner launches, claude spawns
(cleanup and the auth probe), git, worktree moves, local status/marker
writes — is suppressed at the ``TickSeams`` boundary (``dry_run_seams``), so
the tick physically cannot mutate. ``FLEET_MAX_RUNNERS=0`` is *not* a safe
smoke: it only zeroes the claim budget, while finalize and reap still mutate
ADO. Use a dry run for that.

Run one tick: ``python -m flotilla.supervisor`` (normally via ``flotilla
tick`` from a ticker loop or cron; see the flotilla README).
"""

import argparse
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Final, Protocol, cast

from flotilla._resources import resolve_script
from flotilla.constants import (
    FLEET_DRY_RUN,
    FLEET_EFFORT,
    FLEET_MAX_RUNNERS,
    FLEET_MODEL,
    FLEET_ROOT,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    STALENESS_THRESHOLD_SECONDS,
    SUPERVISOR_LOCK_FILENAME,
    TAG_AWAITING_PR_APPROVAL,
    TAG_CLAIMED,
    TAG_FAILED,
    TAG_NEEDS_DECISION,
    TAG_QA_READY,
)
from flotilla.status import FleetStatus, StatusUpdate, load_or_none, update

STATE_TODO: Final[str] = "To Do"
STATE_DOING: Final[str] = "Doing"
STATE_DONE: Final[str] = "Done"

_PREDECESSOR_REL: Final[str] = "System.LinkTypes.Dependency-Reverse"
_PARENT_REL: Final[str] = "System.LinkTypes.Hierarchy-Reverse"

CLAIMED_AT_FILENAME: Final[str] = "claimed-at"
RUNNER_PID_FILENAME: Final[str] = "runner.pid"

# A runner carrying any of these tags stopped heartbeating on purpose — it is
# parked, not dead, and must never be reaped (addendum §3-4). TAG_FAILED stays
# here because a *tagged* fleet:failed slice is already escalated and terminal
# (never auto-retried); an untagged ``parked_state="failed"`` status, by
# contrast, is positive failure evidence and reap-eligible (see _is_parked).
_PARKED_TAGS: Final[tuple[str, ...]] = (
    TAG_NEEDS_DECISION,
    TAG_QA_READY,
    TAG_AWAITING_PR_APPROVAL,
    TAG_FAILED,
)


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


class AdoClient(Protocol):
    """The ADO operations the supervisor passes need (az-CLI-backed in prod)."""

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Return all Issues currently in ``state``."""
        ...

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the completed PR for ``branch`` targeting main, if any."""
        ...

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Return parent / predecessor links of one Issue."""
        ...

    def issue_state(self, issue_id: int) -> str:
        """Return the current ``System.State`` of a work item."""
        ...

    def set_state(self, issue_id: int, state: str) -> None:
        """Transition a work item to ``state``."""
        ...

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Add ``tag`` to the work item (read-append-write of System.Tags)."""
        ...

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Remove ``tag`` from the work item."""
        ...

    def add_comment(self, issue_id: int, html: str) -> None:
        """Add an HTML discussion comment to the work item."""
        ...


class Launcher(Protocol):
    """Starts one runner per claimed slice (tmux-backed in prod)."""

    def launch(self, issue_id: int, branch: str, attempt: int) -> bool:
        """Start a runner; return False when the launch failed."""
        ...


class Cleaner(Protocol):
    """Runs branch cleanup for a merged slice (headless skill in prod)."""

    def cleanup(self, branch: str) -> bool:
        """Clean the merged branch/worktree; return False on failure."""
        ...


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    """One tick's effective configuration (constants + env, frozen per run)."""

    fleet_root: Path
    fleet_home: Path
    cap: int
    max_attempts: int
    epic_ids: tuple[int, ...]


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


def _run_quiet(args: Sequence[str], cwd: Path | None = None) -> int:
    """Run a command, discarding output; return its exit code."""
    completed: subprocess.CompletedProcess[bytes] = subprocess.run(
        list(args), capture_output=True, check=False, cwd=cwd
    )
    return completed.returncode


def _pid_alive(pid: int) -> bool:
    """Report whether a process with ``pid`` currently exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


AUTH_PROBE_TIMEOUT_SECONDS: Final[float] = 120.0


def _auth_probe_command(model: str) -> tuple[str, ...]:
    """Build the throwaway probe argv, pinned to the fleet model.

    The probe exercises the same ``--model`` the runner and cleanup passes use,
    so a misconfigured/unavailable FLEET_MODEL fails the preflight here rather
    than silently failing every claimed runner. No ``--effort`` — the probe
    does no reasoning, so the runner's effort tier is irrelevant to it.
    """
    return ("claude", "-p", "reply READY", "--dangerously-skip-permissions", "--model", model)


def _run_auth_probe(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run the throwaway claude probe with a hard timeout."""
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
        timeout=AUTH_PROBE_TIMEOUT_SECONDS,
    )


def _claude_auth_ok(
    run: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_auth_probe,
    model: str = FLEET_MODEL,
) -> bool:
    """Actively probe claude auth with a throwaway prompt.

    Any failure mode (dead auth, transient API outage, missing binary,
    timeout, unavailable model) reads as unavailable — the tick degrades to
    reap-only either way and retries next tick.
    """
    try:
        completed: subprocess.CompletedProcess[str] = run(list(_auth_probe_command(model)))
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0 and "READY" in completed.stdout


def _archive_worktree(
    worktree: Path, issue_id: int, attempt: int, config: SupervisorConfig
) -> None:
    """Move the dead worktree under the slice's archive/ for inspection."""
    if not worktree.is_dir():
        return
    archive_dir: Path = config.fleet_root / str(issue_id) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination: Path = archive_dir / f"attempt-{attempt}"
    counter: int = 2
    while destination.exists():
        destination = archive_dir / f"attempt-{attempt}-{counter}"
        counter += 1
    shutil.move(str(worktree), str(destination))


def _write_claimed_at(issue_id: int, fleet_root: Path, timestamp: str) -> None:
    """Record the claim time so the reap pass can age claims that never started."""
    directory: Path = fleet_root / str(issue_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CLAIMED_AT_FILENAME).write_text(timestamp + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class TickSeams:
    """The tick's side-effect seams, injectable for tests.

    Every side effect a tick can perform flows through one of these fields —
    board writes via ``ado``, runner panes via ``launcher``, headless claude
    cleanups via ``cleaner``, git via ``run_git``, the auth probe via
    ``auth_ok``, and the local fleet-state writes via ``archive_worktree`` /
    ``update_status`` / ``write_claimed_at``. Keeping this exhaustive is what
    makes ``dry_run_seams`` a write-blocking boundary rather than a flag:
    never add a side effect to a pass without routing it through a seam.
    """

    ado: AdoClient
    launcher: Launcher
    cleaner: Cleaner
    pid_alive: Callable[[int], bool] = field(default=_pid_alive)
    run_git: Callable[[Sequence[str]], int] = field(default=_run_quiet)
    auth_ok: Callable[[], bool] = field(default=_claude_auth_ok)
    archive_worktree: Callable[[Path, int, int, SupervisorConfig], None] = field(
        default=_archive_worktree
    )
    update_status: Callable[[int, StatusUpdate, Path], object] = field(default=update)
    write_claimed_at: Callable[[int, Path, str], None] = field(default=_write_claimed_at)


def config_from_env(
    fleet_root: Path | None = None, fleet_home: Path | None = None
) -> SupervisorConfig:
    """Build the tick configuration from constants and the environment."""
    raw_epics: str = os.environ.get("FLEET_EPIC_IDS", "")
    epic_ids: tuple[int, ...] = tuple(int(part) for part in raw_epics.split(",") if part.strip())
    return SupervisorConfig(
        fleet_root=fleet_root if fleet_root is not None else FLEET_ROOT,
        fleet_home=fleet_home
        if fleet_home is not None
        else Path(os.environ.get("FLEET_HOME") or Path.cwd()),
        cap=FLEET_MAX_RUNNERS,
        max_attempts=MAX_ATTEMPTS,
        epic_ids=epic_ids,
    )


@contextmanager
def supervisor_lock(fleet_root: Path) -> Generator[bool, None, None]:
    """Try to take the tick lock; yield whether this tick may proceed."""
    fleet_root.mkdir(parents=True, exist_ok=True)
    with (fleet_root / SUPERVISOR_LOCK_FILENAME).open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_tick(seams: TickSeams, config: SupervisorConfig) -> int:
    """Run one serialized supervisor tick (auth preflight, finalize → reap → claim)."""
    with supervisor_lock(config.fleet_root) as acquired:
        if not acquired:
            _log("tick skipped — another tick holds the lock")
            return 0
        if _claude_work_pending(seams.ado, config) and not seams.auth_ok():
            _log(
                "claude auth-unavailable — skipped finalize and claim passes; "
                "running reap only, retrying next tick"
            )
            _reap_and_log(seams, config)
            return 0
        finalized: FinalizeOutcome = finalize_pass(seams, config)
        _log(
            f"finalize pass: finalized={list(finalized.finalized)} "
            f"awaiting_merge={list(finalized.awaiting_merge)} "
            f"cleanup_failed={list(finalized.cleanup_failed)}"
        )
        _reap_and_log(seams, config)
        outcome: ClaimOutcome = claim_pass(seams, config)
        _log(
            f"claim pass: inflight={list(outcome.inflight)} "
            f"claimed={list(outcome.claimed)} blocked={list(outcome.skipped_blocked)} "
            f"escalated={list(outcome.escalated)} rolled_back={list(outcome.rolled_back)}"
        )
    return 0


def _reap_and_log(seams: TickSeams, config: SupervisorConfig) -> None:
    """Run the reap pass and emit its outcome log line."""
    reaped: ReapOutcome = reap_pass(seams, config)
    _log(
        f"reap pass: reaped={list(reaped.reaped)} escalated={list(reaped.escalated)} "
        f"alive={list(reaped.skipped_alive)} parked={list(reaped.skipped_parked)}"
    )


def _claude_work_pending(ado: AdoClient, config: SupervisorConfig) -> bool:
    """Report whether this tick has claude-dependent work (finalize or claim).

    Only such a tick pays for the auth probe — idle and saturated ticks skip
    it. Claim eligibility is a cheap over-approximation on purpose (budget +
    an untagged To Do Issue); epic/predecessor filtering stays in claim_pass.
    """
    if any(TAG_CLAIMED in issue.tags for issue in ado.issues_in_state(STATE_DONE)):
        return True
    inflight: int = sum(
        1 for issue in ado.issues_in_state(STATE_DOING) if TAG_CLAIMED in issue.tags
    )
    if config.cap - inflight <= 0:
        return False
    return any(
        not any(tag.startswith("fleet:") for tag in issue.tags)
        for issue in ado.issues_in_state(STATE_TODO)
    )


def finalize_pass(seams: TickSeams, config: SupervisorConfig) -> FinalizeOutcome:
    """Retire merged slices: cleanup branch/worktree, drop tags, status → done.

    Merged-ness is derived from truth (a completed PR for the slice branch +
    the Issue in Done), not from the runner's recorded mapping — the status
    file only provides the branch fast-path (ADR-0007 decision 5).
    """
    ado: AdoClient = seams.ado
    cleaner: Cleaner = seams.cleaner
    finalized: list[int] = []
    awaiting: list[int] = []
    failed: list[int] = []
    for issue in sorted(ado.issues_in_state(STATE_DONE), key=lambda ref: ref.issue_id):
        if TAG_CLAIMED not in issue.tags:
            continue
        status: FleetStatus | None = load_or_none(issue.issue_id, config.fleet_root)
        branch: str = (
            status.branch if status is not None else slice_branch(issue.issue_id, issue.title, 1)
        )
        pr_url: str | None = ado.completed_pr_url(branch)
        if pr_url is None:
            awaiting.append(issue.issue_id)
            continue
        if not cleaner.cleanup(branch):
            _log(f"finalize: cleanup failed for #{issue.issue_id} ({branch}); will retry")
            failed.append(issue.issue_id)
            continue
        for tag in issue.tags:
            if tag.startswith("fleet:"):
                ado.remove_tag(issue.issue_id, tag)
        ado.add_comment(
            issue.issue_id,
            f'<p>fleet: finalized — PR completed (<a href="{pr_url}">{pr_url}</a>), '
            f"branch <code>{branch}</code> cleaned up.</p>",
        )
        if status is not None:
            seams.update_status(
                issue.issue_id,
                StatusUpdate(phase="done", parked_state=None, pr_url=pr_url),
                config.fleet_root,
            )
        finalized.append(issue.issue_id)
    return FinalizeOutcome(
        finalized=tuple(finalized),
        awaiting_merge=tuple(awaiting),
        cleanup_failed=tuple(failed),
    )


def reap_pass(
    seams: TickSeams, config: SupervisorConfig, now: datetime | None = None
) -> ReapOutcome:
    """Requeue claimed slices whose runner is stale *and* confirmed dead."""
    moment: datetime = now if now is not None else datetime.now(UTC)
    reaped: list[int] = []
    escalated: list[int] = []
    alive: list[int] = []
    parked: list[int] = []
    for issue in sorted(seams.ado.issues_in_state(STATE_DOING), key=lambda ref: ref.issue_id):
        if TAG_CLAIMED not in issue.tags:
            continue  # a human's Doing item — invisible to the fleet
        status: FleetStatus | None = load_or_none(issue.issue_id, config.fleet_root)
        if _is_parked(issue, status):
            parked.append(issue.issue_id)
            continue
        failed_park: bool = _is_failed_park(status)
        if not failed_park:
            age: float | None = _liveness_age_seconds(issue.issue_id, status, config, moment)
            if age is not None and age <= STALENESS_THRESHOLD_SECONDS:
                continue  # heartbeat fresh enough
        if _runner_alive(seams, issue.issue_id, config):
            alive.append(issue.issue_id)
            continue
        evidence: str = (
            "runner parked failed and process dead"
            if failed_park
            else "heartbeat stale and runner process dead"
        )
        _reap_one(seams, config, issue, status, evidence)
        attempt: int = status.attempt if status is not None else 1
        if attempt >= config.max_attempts:
            _escalate_exhausted(seams.ado, issue.issue_id, attempt + 1, config.max_attempts)
            seams.ado.remove_tag(issue.issue_id, TAG_CLAIMED)
            escalated.append(issue.issue_id)
        else:
            seams.ado.remove_tag(issue.issue_id, TAG_CLAIMED)
            seams.ado.set_state(issue.issue_id, STATE_TODO)
            seams.ado.add_comment(
                issue.issue_id,
                f"<p>fleet: reaped — {evidence} (attempt {attempt}); "
                f"worktree archived, requeued for retry.</p>",
            )
            reaped.append(issue.issue_id)
    return ReapOutcome(
        reaped=tuple(reaped),
        escalated=tuple(escalated),
        skipped_alive=tuple(alive),
        skipped_parked=tuple(parked),
    )


def _is_parked(issue: IssueRef, status: FleetStatus | None) -> bool:
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


def _is_failed_park(status: FleetStatus | None) -> bool:
    """Report whether the status records a failed park (positive failure evidence).

    A failed park skips the staleness wait — the pid-aliveness check alone
    decides whether the slice is reaped immediately.
    """
    return status is not None and status.phase == "parked" and status.parked_state == "failed"


def _liveness_age_seconds(
    issue_id: int, status: FleetStatus | None, config: SupervisorConfig, now: datetime
) -> float | None:
    """Age of the best liveness evidence; ``None`` when there is none at all."""
    raw: str | None = None
    if status is not None:
        raw = status.last_heartbeat
    else:
        marker: Path = config.fleet_root / str(issue_id) / CLAIMED_AT_FILENAME
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
    if raw is None:
        return None
    try:
        stamped: datetime = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (now - stamped).total_seconds()


def _runner_alive(seams: TickSeams, issue_id: int, config: SupervisorConfig) -> bool:
    """Confirm via the pid sidecar whether the runner process still exists."""
    pid_file: Path = config.fleet_root / str(issue_id) / RUNNER_PID_FILENAME
    if not pid_file.is_file():
        return False
    raw: str = pid_file.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return False
    return seams.pid_alive(int(raw))


def _reap_one(
    seams: TickSeams,
    config: SupervisorConfig,
    issue: IssueRef,
    status: FleetStatus | None,
    evidence: str,
) -> None:
    """Archive the dead attempt's worktree and record the reap in the status."""
    attempt: int = status.attempt if status is not None else 1
    if status is not None:
        seams.archive_worktree(Path(status.worktree), issue.issue_id, attempt, config)
        seams.run_git(["git", "-C", str(config.fleet_home), "worktree", "prune"])
        seams.update_status(
            issue.issue_id,
            StatusUpdate(
                phase="parked",
                parked_state="failed",
                last_error=f"reaped: {evidence} (attempt {attempt})",
            ),
            config.fleet_root,
        )


def claim_pass(seams: TickSeams, config: SupervisorConfig) -> ClaimOutcome:
    """Claim unblocked, unclaimed slices up to the cap and launch their runners."""
    ado: AdoClient = seams.ado
    inflight: tuple[int, ...] = tuple(
        issue.issue_id for issue in ado.issues_in_state(STATE_DOING) if TAG_CLAIMED in issue.tags
    )
    budget: int = config.cap - len(inflight)
    claimed: list[int] = []
    blocked: list[int] = []
    escalated: list[int] = []
    rolled_back: list[int] = []

    if budget > 0:
        candidates: list[IssueRef] = sorted(
            ado.issues_in_state(STATE_TODO), key=lambda issue: issue.issue_id
        )
        for issue in candidates:
            if budget == 0:
                break
            if any(tag.startswith("fleet:") for tag in issue.tags):
                continue
            links: IssueLinks = ado.issue_links(issue.issue_id)
            if config.epic_ids and links.parent_id not in config.epic_ids:
                continue
            if not all(
                ado.issue_state(predecessor) == STATE_DONE for predecessor in links.predecessor_ids
            ):
                blocked.append(issue.issue_id)
                continue
            attempt: int = _next_attempt(issue.issue_id, config.fleet_root)
            if attempt > config.max_attempts:
                _escalate_exhausted(ado, issue.issue_id, attempt, config.max_attempts)
                escalated.append(issue.issue_id)
                continue
            if _claim_and_launch(seams, config, issue, attempt):
                claimed.append(issue.issue_id)
                budget -= 1
            else:
                rolled_back.append(issue.issue_id)

    return ClaimOutcome(
        inflight=inflight,
        claimed=tuple(claimed),
        skipped_blocked=tuple(blocked),
        escalated=tuple(escalated),
        rolled_back=tuple(rolled_back),
    )


def slice_branch(issue_id: int, title: str, attempt: int) -> str:
    """Derive the slice's branch name (`feat/slice-<id>-<kebab>[ -aN ]`)."""
    base: str = title.split(":", 1)[1] if ":" in title else title
    slug: str = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:32].strip("-")
    if not slug:
        slug = "slice"
    suffix: str = f"-a{attempt}" if attempt > 1 else ""
    return f"feat/slice-{issue_id}-{slug}{suffix}"


def _next_attempt(issue_id: int, fleet_root: Path) -> int:
    """1 for a first claim; previous attempt + 1 when a status file exists."""
    previous = load_or_none(issue_id, fleet_root)
    return 1 if previous is None else previous.attempt + 1


def _claim_and_launch(
    seams: TickSeams,
    config: SupervisorConfig,
    issue: IssueRef,
    attempt: int,
) -> bool:
    """Run the claim protocol for one slice; roll back if the launch fails."""
    ado: AdoClient = seams.ado
    branch: str = slice_branch(issue.issue_id, issue.title, attempt)
    now: str = _utcnow_iso()
    ado.set_state(issue.issue_id, STATE_DOING)
    ado.add_tag(issue.issue_id, TAG_CLAIMED)
    ado.add_comment(
        issue.issue_id,
        f"<p>fleet: claimed by supervisor — runner "
        f"<code>runner-{issue.issue_id}-a{attempt}</code>, branch "
        f"<code>{branch}</code>, {now}.</p>",
    )
    seams.write_claimed_at(issue.issue_id, config.fleet_root, now)
    if seams.launcher.launch(issue.issue_id, branch, attempt):
        return True
    ado.remove_tag(issue.issue_id, TAG_CLAIMED)
    ado.set_state(issue.issue_id, STATE_TODO)
    ado.add_comment(issue.issue_id, "<p>fleet: runner launch failed — claim rolled back.</p>")
    return False


def _escalate_exhausted(ado: AdoClient, issue_id: int, attempt: int, cap: int) -> None:
    """Tag a slice whose transient retries are exhausted (addendum §4)."""
    ado.add_tag(issue_id, TAG_FAILED)
    ado.add_comment(
        issue_id,
        f"<p>fleet: retry cap exhausted (next attempt would be {attempt}, cap {cap}) "
        f"— escalated to <code>{TAG_FAILED}</code>. Triage via the status file, then "
        f"remove the tag (and the fleet status dir for a clean restart) to requeue.</p>",
    )


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 form (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(message: str) -> None:
    """Emit one timestamped log line (fleet-tick.sh appends stdout to the log)."""
    print(f"[{_utcnow_iso()}] supervisor: {message}")


# --- az CLI adapter ----------------------------------------------------------


def _run_az(args: Sequence[str]) -> str:
    """Run an az command and return stdout (raises on a non-zero exit)."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["az", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


class AzCliAdo:
    """``AdoClient`` backed by the az CLI (the devbox's authenticated transport)."""

    def __init__(
        self,
        run: Callable[[Sequence[str]], str] = _run_az,
        project: str | None = None,
    ) -> None:
        """Wire the adapter to a command runner (injectable for tests).

        ``project`` names the Azure DevOps project for the REST route
        parameter; when omitted it is resolved once from the configured az
        default on first use.
        """
        self._run = run
        self._project = project

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Return Issues in ``state`` with their tags, via the WIQL REST API.

        ``az boards query`` produces no output under the devbox's az-CLI /
        azure-devops extension pairing, so the query goes through ``az devops
        invoke`` instead: ``wit/wiql`` for the matching ids, then
        ``wit/workitemsbatch`` for their fields.
        """
        wiql: str = (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.TeamProject] = @project "
            "AND [System.WorkItemType] = 'Issue' "
            f"AND [System.State] = '{state}'"
        )
        ids: list[int] = _wiql_ids(self._invoke_json("wiql", {"query": wiql}))
        if not ids:
            return ()
        batch: str = self._invoke_json(
            "workitemsbatch",
            {"ids": ids, "fields": ["System.Id", "System.Title", "System.Tags"]},
        )
        value: object = _json_object(batch).get("value")
        items: list[object] = cast("list[object]", value) if isinstance(value, list) else []
        return _issue_refs_from_items(items)

    def _resolve_project(self) -> str:
        """Return the project for REST routes, resolving the az default once."""
        if self._project is None:
            self._project = _configured_project(self._run)
        return self._project

    def _invoke_json(self, resource: str, body: dict[str, object]) -> str:
        """POST ``body`` to a ``wit`` REST resource via ``az devops invoke``.

        The body is written to a temp file because ``az devops invoke`` reads
        its payload from ``--in-file`` only; the file is removed afterwards.
        """
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(body, handle)
            in_file: str = handle.name
        try:
            return self._run(
                [
                    "devops",
                    "invoke",
                    "--area",
                    "wit",
                    "--resource",
                    resource,
                    "--route-parameters",
                    f"project={self._resolve_project()}",
                    "--http-method",
                    "POST",
                    "--in-file",
                    in_file,
                    "--api-version",
                    "7.1",
                    "-o",
                    "json",
                ]
            )
        finally:
            os.unlink(in_file)

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Read parent / predecessor relations from the work item."""
        out: str = self._run(
            [
                "boards",
                "work-item",
                "show",
                "--id",
                str(issue_id),
                "--expand",
                "relations",
                "-o",
                "json",
            ]
        )
        return _parse_issue_links(out)

    def completed_pr_url(self, branch: str) -> str | None:
        """Find a completed PR from ``branch`` into main, if one exists."""
        out: str = self._run(
            [
                "repos",
                "pr",
                "list",
                "--source-branch",
                branch,
                "--target-branch",
                "main",
                "--status",
                "completed",
                "-o",
                "json",
            ]
        )
        if not out.strip():
            return None
        raw: object = json.loads(out)
        if not isinstance(raw, list) or not raw:
            return None
        first: object = cast("list[object]", raw)[0]
        if not isinstance(first, dict):
            return None
        pr: dict[str, object] = cast("dict[str, object]", first)
        url: object = pr.get("url")
        if isinstance(url, str):
            return url
        pr_id: object = pr.get("pullRequestId")
        return f"PR {pr_id}" if isinstance(pr_id, int) else None

    def issue_state(self, issue_id: int) -> str:
        """Read ``System.State`` of one work item."""
        fields: dict[str, object] = _show_fields(self._run, issue_id)
        state: object = fields.get("System.State")
        return state if isinstance(state, str) else ""

    def set_state(self, issue_id: int, state: str) -> None:
        """Transition the work item to ``state``."""
        self._run(
            ["boards", "work-item", "update", "--id", str(issue_id), "--state", state, "-o", "none"]
        )

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Append ``tag`` to System.Tags (read-append-write)."""
        tags: list[str] = self._current_tags(issue_id)
        if tag in tags:
            return
        self._write_tags(issue_id, [*tags, tag])

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Filter ``tag`` out of System.Tags."""
        tags: list[str] = self._current_tags(issue_id)
        if tag not in tags:
            return
        self._write_tags(issue_id, [item for item in tags if item != tag])

    def add_comment(self, issue_id: int, html: str) -> None:
        """Add an HTML discussion comment to the work item."""
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(issue_id),
                "--discussion",
                html,
                "-o",
                "none",
            ]
        )

    def _current_tags(self, issue_id: int) -> list[str]:
        fields: dict[str, object] = _show_fields(self._run, issue_id)
        raw: object = fields.get("System.Tags")
        if not isinstance(raw, str) or not raw.strip():
            return []
        return [part.strip() for part in raw.split(";") if part.strip()]

    def _write_tags(self, issue_id: int, tags: list[str]) -> None:
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(issue_id),
                "--fields",
                f"System.Tags={'; '.join(tags)}",
                "-o",
                "none",
            ]
        )


def _show_fields(run: Callable[[Sequence[str]], str], issue_id: int) -> dict[str, object]:
    """Fetch a work item's fields dict."""
    out: str = run(["boards", "work-item", "show", "--id", str(issue_id), "-o", "json"])
    data: dict[str, object] = _json_object(out)
    fields: object = data.get("fields")
    return cast("dict[str, object]", fields) if isinstance(fields, dict) else {}


def _wiql_ids(payload: str) -> list[int]:
    """Extract work-item ids from a ``wit/wiql`` REST response."""
    work_items: object = _json_object(payload).get("workItems")
    if not isinstance(work_items, list):
        return []
    ids: list[int] = []
    for entry in cast("list[object]", work_items):
        if not isinstance(entry, dict):
            continue
        wid: object = cast("dict[str, object]", entry).get("id")
        if isinstance(wid, int):
            ids.append(wid)
    return ids


def _configured_project(run: Callable[[Sequence[str]], str]) -> str:
    """Read the default Azure DevOps project from az configuration."""
    out: str = run(["devops", "configure", "--list"])
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "project":
            project: str = value.strip()
            if project:
                return project
    raise RuntimeError(
        "fleet supervisor: no default Azure DevOps project configured; "
        "set one with `az devops configure --defaults project=<name>`"
    )


def _issue_refs_from_items(items: Sequence[object]) -> tuple[IssueRef, ...]:
    """Build IssueRefs from work-item dicts (each an ``id`` + ``fields`` map)."""
    refs: list[IssueRef] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item: dict[str, object] = cast("dict[str, object]", entry)
        fields: object = item.get("fields")
        fields_map: dict[str, object] = (
            cast("dict[str, object]", fields) if isinstance(fields, dict) else {}
        )
        issue_id: object = item.get("id", fields_map.get("System.Id"))
        title: object = fields_map.get("System.Title", "")
        tags_raw: object = fields_map.get("System.Tags", "")
        if isinstance(issue_id, str) and issue_id.isdigit():
            issue_id = int(issue_id)
        if not isinstance(issue_id, int):
            continue
        refs.append(
            IssueRef(
                issue_id=issue_id,
                title=title if isinstance(title, str) else "",
                tags=_split_tags(tags_raw),
            )
        )
    return tuple(refs)


def _parse_issue_links(payload: str) -> IssueLinks:
    """Parse parent / predecessor ids out of an expanded work item."""
    data: dict[str, object] = _json_object(payload)
    relations: object = data.get("relations")
    parent_id: int | None = None
    predecessors: list[int] = []
    if isinstance(relations, list):
        for entry in cast("list[object]", relations):
            if not isinstance(entry, dict):
                continue
            relation: dict[str, object] = cast("dict[str, object]", entry)
            rel_type: object = relation.get("rel")
            url: object = relation.get("url")
            target: int | None = _id_from_url(url) if isinstance(url, str) else None
            if target is None:
                continue
            if rel_type == _PREDECESSOR_REL:
                predecessors.append(target)
            elif rel_type == _PARENT_REL:
                parent_id = target
    return IssueLinks(parent_id=parent_id, predecessor_ids=tuple(predecessors))


def _id_from_url(url: str) -> int | None:
    """Extract the trailing work-item id from a relation URL."""
    tail: str = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _split_tags(raw: object) -> tuple[str, ...]:
    """Split ADO's `;`-separated tag string into a tuple."""
    if not isinstance(raw, str):
        return ()
    return tuple(part.strip() for part in raw.split(";") if part.strip())


def _json_object(payload: str) -> dict[str, object]:
    """Parse a JSON object, returning {} for empty or non-object payloads.

    Tolerating an empty payload keeps a transient blank board read from
    crashing a whole tick (the failure mode that motivated routing reads
    through the REST API); genuinely malformed JSON still raises.
    """
    if not payload.strip():
        return {}
    raw: object = json.loads(payload)
    return cast("dict[str, object]", raw) if isinstance(raw, dict) else {}


# --- tmux launcher -------------------------------------------------------------


class TmuxLauncher:
    """Launch one ``runner-wrap.sh`` pane per slice in the detached ``fleet`` session.

    The pane grid (``tmux attach -t fleet``) is the live view and the babysit
    path (ADR-0007 decision 6) — plain tmux, not the experimental agent-teams
    feature. ``runner-wrap.sh`` is resolved from flotilla's packaged data (it
    lives inside the installed package, not under the repo flotilla operates on),
    and the pane is told which interpreter to use via ``FLEET_PYTHON`` so the
    runner reaches ``flotilla.*`` regardless of PATH.
    """

    def __init__(  # noqa: PLR0913 - Constructor is a DI seam
        self,
        fleet_home: Path,
        fleet_root: Path,
        run: Callable[[Sequence[str]], int] = _run_quiet,
        *,
        heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
        model: str = FLEET_MODEL,
        effort: str = FLEET_EFFORT,
        python_executable: str = sys.executable,
    ) -> None:
        """Bind the launcher to the repo root, fleet root, and a command runner."""
        self._fleet_home = fleet_home
        self._fleet_root = fleet_root
        self._run = run
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._model = model
        self._effort = effort
        self._python = python_executable

    def launch(self, issue_id: int, branch: str, attempt: int) -> bool:
        """Start the slice's runner pane; return False if tmux refuses."""
        wrap: Path = resolve_script("runner-wrap.sh")
        command: str = (
            f"FLEET_HOME={shlex.quote(str(self._fleet_home))} "
            f"FLEET_ROOT={shlex.quote(str(self._fleet_root))} "
            f"FLEET_PYTHON={shlex.quote(self._python)} "
            f"FLEET_HEARTBEAT_INTERVAL_SECONDS="
            f"{shlex.quote(str(self._heartbeat_interval_seconds))} "
            f"FLEET_MODEL={shlex.quote(self._model)} "
            f"FLEET_EFFORT={shlex.quote(self._effort)} "
            f"{shlex.quote(str(wrap))} {issue_id} {shlex.quote(branch)} {attempt}"
        )
        if self._run(["tmux", "has-session", "-t", "fleet"]) != 0:
            return (
                self._run(["tmux", "new-session", "-d", "-s", "fleet", "-n", "grid", command]) == 0
            )
        if self._run(["tmux", "split-window", "-d", "-t", "fleet:grid", command]) == 0:
            self._run(["tmux", "select-layout", "-t", "fleet:grid", "tiled"])
            return True
        # The session exists but the grid window is gone (e.g. closed by hand).
        return self._run(["tmux", "new-window", "-d", "-t", "fleet", "-n", "grid", command]) == 0


class ClaudeCleanup:
    """Run the existing ``/cleanup-merged-branches`` skill headlessly per branch.

    The skill derives merged-ness via patch-equivalence and drives the
    existing worktree hooks (ADR-0007 decision 5); its Step 3b sweep is
    explicitly non-interactive so the headless loop can use it.
    """

    def __init__(
        self,
        fleet_home: Path,
        run: Callable[[Sequence[str], Path | None], int] = _run_quiet,
        *,
        model: str = FLEET_MODEL,
        effort: str = FLEET_EFFORT,
    ) -> None:
        """Bind the cleaner to the repo root and a command runner."""
        self._fleet_home = fleet_home
        self._run = run
        self._model = model
        self._effort = effort

    def cleanup(self, branch: str) -> bool:
        """Clean one merged branch; return False when the session failed."""
        return (
            self._run(
                [
                    "claude",
                    "-p",
                    f"/cleanup-merged-branches {branch}",
                    "--dangerously-skip-permissions",
                    "--model",
                    self._model,
                    "--effort",
                    self._effort,
                ],
                self._fleet_home,
            )
            == 0
        )


# --- dry-run boundary ----------------------------------------------------------


class ReadOnlyAdoClient:
    """``AdoClient`` decorator that physically cannot write to the board.

    Reads delegate to the wrapped client; every write logs the action a real
    tick WOULD have performed and does nothing. Dry-run safety is this
    boundary, not a flag threaded through the passes — a pass (present or
    future) that reaches ``seams.ado`` with a write cannot mutate ADO while
    dry-run is active, because the write never leaves this class.
    """

    def __init__(self, inner: AdoClient) -> None:
        """Wrap ``inner``, passing its reads through and absorbing its writes."""
        self._inner = inner

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Pass the read through to the wrapped client."""
        return self._inner.issues_in_state(state)

    def completed_pr_url(self, branch: str) -> str | None:
        """Pass the read through to the wrapped client."""
        return self._inner.completed_pr_url(branch)

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Pass the read through to the wrapped client."""
        return self._inner.issue_links(issue_id)

    def issue_state(self, issue_id: int) -> str:
        """Pass the read through to the wrapped client."""
        return self._inner.issue_state(issue_id)

    def set_state(self, issue_id: int, state: str) -> None:
        """Absorb the write, logging the would-be state transition."""
        _log(f"[dry-run] WOULD move #{issue_id} to '{state}'")

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Absorb the write, logging the would-be tag addition."""
        _log(f"[dry-run] WOULD add tag '{tag}' to #{issue_id}")

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Absorb the write, logging the would-be tag removal."""
        _log(f"[dry-run] WOULD remove tag '{tag}' from #{issue_id}")

    def add_comment(self, issue_id: int, html: str) -> None:
        """Absorb the write, logging the would-be discussion comment."""
        _log(f"[dry-run] WOULD comment on #{issue_id}: {html}")


class DryRunLauncher:
    """``Launcher`` stand-in: reports the runner it would start, starts nothing."""

    def launch(self, issue_id: int, branch: str, attempt: int) -> bool:
        """Log the would-be runner pane and report success."""
        _log(f"[dry-run] WOULD launch runner for #{issue_id} (branch {branch}, attempt {attempt})")
        return True


class DryRunCleaner:
    """``Cleaner`` stand-in: reports the would-be cleanup, spawns no claude."""

    def cleanup(self, branch: str) -> bool:
        """Log the would-be headless cleanup session and report success."""
        _log(f"[dry-run] WOULD run /cleanup-merged-branches for {branch}")
        return True


def _dry_run_auth_ok() -> bool:
    """Skip the auth preflight — a spawned ``claude -p`` probe is itself a side effect."""
    _log("[dry-run] WOULD run the claude auth preflight; assuming it passes")
    return True


def _dry_run_git(args: Sequence[str]) -> int:
    """Absorb a git invocation (reap's ``worktree prune``), logging it."""
    _log(f"[dry-run] WOULD run: {' '.join(args)}")
    return 0


def _dry_run_archive_worktree(
    worktree: Path, issue_id: int, attempt: int, _config: SupervisorConfig
) -> None:
    """Absorb the worktree archive move, logging it."""
    _log(f"[dry-run] WOULD archive worktree {worktree} of #{issue_id} (attempt {attempt})")


def _dry_run_update_status(issue_id: int, _changes: StatusUpdate, _fleet_root: Path) -> None:
    """Absorb the status-file write, logging it."""
    _log(f"[dry-run] WOULD update the status file of #{issue_id}")


def _dry_run_write_claimed_at(issue_id: int, _fleet_root: Path, _timestamp: str) -> None:
    """Absorb the claimed-at marker write, logging it."""
    _log(f"[dry-run] WOULD write the claimed-at marker of #{issue_id}")


def dry_run_seams(seams: TickSeams) -> TickSeams:
    """Wrap every side-effecting seam so the tick cannot mutate anything.

    Reads pass through — the tick still runs the full finalize/reap/claim
    read+plan logic and reports the would-be actions — but every write (ADO,
    tmux panes, claude spawns including the auth probe, git, worktree moves,
    local status/marker files) becomes a logged ``[dry-run] WOULD …`` no-op.
    ``pid_alive`` stays real: it is a pure read (signal 0) and the reap plan
    is meaningless without it. The tick lock and the supervisor log are still
    written — they are coordination artifacts, not fleet state.
    """
    return replace(
        seams,
        ado=ReadOnlyAdoClient(seams.ado),
        launcher=DryRunLauncher(),
        cleaner=DryRunCleaner(),
        run_git=_dry_run_git,
        auth_ok=_dry_run_auth_ok,
        archive_worktree=_dry_run_archive_worktree,
        update_status=_dry_run_update_status,
        write_claimed_at=_dry_run_write_claimed_at,
    )


def build_seams(config: SupervisorConfig, *, dry_run: bool = False) -> TickSeams:
    """Build the production seams; with ``dry_run``, wrap them so nothing can mutate."""
    seams = TickSeams(
        ado=AzCliAdo(),
        launcher=TmuxLauncher(config.fleet_home, config.fleet_root),
        cleaner=ClaudeCleanup(config.fleet_home),
    )
    if not dry_run:
        return seams
    _log(
        "DRY-RUN tick: reads and planning only — every ADO write, runner launch, "
        "claude spawn, and local fleet-state write is suppressed and logged as "
        "'[dry-run] WOULD …'"
    )
    return dry_run_seams(seams)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supervisor tick against the real ADO board and tmux."""
    parser = argparse.ArgumentParser(
        prog="fleet-supervisor",
        description="One AFK-fleet supervisor tick (ADR-0007).",
    )
    parser.add_argument("--fleet-root", type=Path, default=None)
    parser.add_argument("--fleet-home", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full tick read+plan logic but suppress every side effect "
        "(board writes, runner launches, claude spawns, local fleet-state writes); "
        "FLEET_DRY_RUN=1 is equivalent",
    )
    args: argparse.Namespace = parser.parse_args(argv)
    config: SupervisorConfig = config_from_env(args.fleet_root, args.fleet_home)
    dry_run: bool = bool(args.dry_run) or FLEET_DRY_RUN
    seams: TickSeams = build_seams(config, dry_run=dry_run)
    try:
        return run_tick(seams, config)
    except subprocess.CalledProcessError as exc:
        stderr: str = exc.stderr if isinstance(exc.stderr, str) else ""
        print(f"supervisor: az/tmux call failed: {exc} {stderr}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
