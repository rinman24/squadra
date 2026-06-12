"""Fleet supervisor tick — ADR-0007 decision 1, addendum §§1–5.

A deterministic, stateless, token-free scheduled script (no LLM). Each tick
reconstructs its entire view from board truth and runs three ordered passes
under one lock (addendum §5) — finalize and reap before claim, so the cap
accounting is fresh:

1. **finalize** — a slice whose item is ``DONE`` and whose PR has completed
   gets the configured cleanup skill run headlessly for its branch, its fleet
   tags dropped, and its status set to ``done``.
2. **reap** (watchdog) — a claimed slice whose heartbeat is stale (10 min) and
   whose runner process is confirmed dead is requeued: worktree archived for
   inspection, ``ACTIVE → QUEUED``, the claimed tag dropped; the next claim
   retries from a fresh worktree with attempt+1, and exhausted retries escalate
   to the failed tag instead. A failed park (``parked_state=failed`` plus a dead
   runner pid) is requeued immediately — positive failure evidence, no staleness
   wait — while deliberate parks are never reaped.
3. **claim** — unblocked + unclaimed ``QUEUED`` items are claimed up to the cap
   (``QUEUED → ACTIVE`` + the claimed tag + a stamped comment; the board state
   arbitrates) and one runner pane is launched per slice.

The supervisor and these passes are **provider-blind**: they speak the neutral
``Lifecycle`` state and emit structured ``CommentEvent`` values; the
``BoardAccess`` adapter (chosen by the provider registry) maps to native states
and renders the markup. State names, tag prefix, base branch, and skill names
are configuration.

Finalize and claim depend on a working ``claude`` (the headless cleanup skill
and the runner itself), so a tick with such work pending first preflights
claude auth with a throwaway prompt. When the probe fails — dead auth and a
transient API outage read identically — the tick degrades to the reap pass only
(board + git) and retries next tick; idle and saturated ticks never probe.

Overlapping ticks serialize via a non-blocking ``flock`` on
``<fleet-root>/supervisor.lock`` — the losing tick exits cleanly without
touching the board.

A tick can be a **dry run** (``--dry-run`` / ``FLEET_DRY_RUN=1``): the full
finalize/reap/claim read+plan logic runs and reports what a real tick WOULD do,
but every side effect — board writes, runner launches, claude spawns (cleanup
and the auth probe), git, worktree moves, local status/marker writes — is
suppressed at the ``TickSeams`` boundary (``dry_run_seams``), so the tick
physically cannot mutate. ``FLEET_MAX_RUNNERS=0`` is *not* a safe smoke: it only
zeroes the claim budget, while finalize and reap still mutate. Use a dry run.

Run one tick: ``python -m flotilla.supervisor`` (normally via ``flotilla tick``
from a ticker loop or cron; see the flotilla README).
"""

import argparse
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Final, Protocol

from flotilla._resources import resolve_script
from flotilla.board import BoardAccess, BoardValidationError, TagWriteError, build_board
from flotilla.config import (
    DEFAULT_CLEANUP_SKILL,
    DEFAULT_QA_SKILL,
    DEFAULT_RUNNER_SKILL,
    DEFAULT_TDD_SKILL,
    FlotillaConfig,
    load_config,
)
from flotilla.constants import (
    FLEET_DRY_RUN,
    FLEET_EFFORT,
    FLEET_MODEL,
    HEARTBEAT_INTERVAL_SECONDS,
    SUPERVISOR_LOCK_FILENAME,
)
from flotilla.domain import (
    Claimed,
    ClaimOutcome,
    CommentEvent,
    Escalated,
    Finalized,
    FinalizeOutcome,
    Lifecycle,
    Reaped,
    ReapOutcome,
    RolledBack,
    Tags,
    WorkItem,
    WorkItemLinks,
)
from flotilla.engines import is_failed_park, is_parked, slice_branch
from flotilla.status import FleetStatus, StatusUpdate, load_or_none, update

CLAIMED_AT_FILENAME: Final[str] = "claimed-at"
RUNNER_PID_FILENAME: Final[str] = "runner.pid"


class Launcher(Protocol):
    """Starts one runner per claimed slice (tmux-backed in prod)."""

    def launch(self, item_id: int, branch: str, attempt: int) -> bool:
        """Start a runner; return False when the launch failed."""
        ...


class Cleaner(Protocol):
    """Runs branch cleanup for a merged slice (headless skill in prod)."""

    def cleanup(self, branch: str) -> bool:
        """Clean the merged branch/worktree; return False on failure."""
        ...


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


def _archive_worktree(worktree: Path, item_id: int, attempt: int, config: FlotillaConfig) -> None:
    """Move the dead worktree under the slice's archive/ for inspection."""
    if not worktree.is_dir():
        return
    archive_dir: Path = config.fleet_root / str(item_id) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination: Path = archive_dir / f"attempt-{attempt}"
    counter: int = 2
    while destination.exists():
        destination = archive_dir / f"attempt-{attempt}-{counter}"
        counter += 1
    shutil.move(str(worktree), str(destination))


def _write_claimed_at(item_id: int, fleet_root: Path, timestamp: str) -> None:
    """Record the claim time so the reap pass can age claims that never started."""
    directory: Path = fleet_root / str(item_id)
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

    ado: BoardAccess
    launcher: Launcher
    cleaner: Cleaner
    pid_alive: Callable[[int], bool] = field(default=_pid_alive)
    run_git: Callable[[Sequence[str]], int] = field(default=_run_quiet)
    auth_ok: Callable[[], bool] = field(default=_claude_auth_ok)
    archive_worktree: Callable[[Path, int, int, FlotillaConfig], None] = field(
        default=_archive_worktree
    )
    update_status: Callable[[int, StatusUpdate, Path], object] = field(default=update)
    write_claimed_at: Callable[[int, Path, str], None] = field(default=_write_claimed_at)


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


def run_tick(seams: TickSeams, config: FlotillaConfig) -> int:
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


def _reap_and_log(seams: TickSeams, config: FlotillaConfig) -> None:
    """Run the reap pass and emit its outcome log line."""
    reaped: ReapOutcome = reap_pass(seams, config)
    _log(
        f"reap pass: reaped={list(reaped.reaped)} escalated={list(reaped.escalated)} "
        f"alive={list(reaped.skipped_alive)} parked={list(reaped.skipped_parked)}"
    )


def _claude_work_pending(ado: BoardAccess, config: FlotillaConfig) -> bool:
    """Report whether this tick has claude-dependent work (finalize or claim).

    Only such a tick pays for the auth probe — idle and saturated ticks skip
    it. Claim eligibility is a cheap over-approximation on purpose (budget +
    an untagged queued item); parent-scope/predecessor filtering stays in
    claim_pass.
    """
    claimed: str = config.tags.claimed
    if any(claimed in item.tags for item in ado.items_in_state(Lifecycle.DONE)):
        return True
    inflight: int = sum(1 for item in ado.items_in_state(Lifecycle.ACTIVE) if claimed in item.tags)
    if config.cap - inflight <= 0:
        return False
    return any(
        not any(config.tags.is_fleet_tag(tag) for tag in item.tags)
        for item in ado.items_in_state(Lifecycle.QUEUED)
    )


def finalize_pass(seams: TickSeams, config: FlotillaConfig) -> FinalizeOutcome:
    """Retire merged slices: cleanup branch/worktree, drop tags, status → done.

    Merged-ness is derived from truth (a completed PR for the slice branch + the
    item in the done bucket), not from the runner's recorded mapping — the
    status file only provides the branch fast-path (ADR-0007 decision 5).
    """
    ado: BoardAccess = seams.ado
    cleaner: Cleaner = seams.cleaner
    tags: Tags = config.tags
    finalized: list[int] = []
    awaiting: list[int] = []
    failed: list[int] = []
    for item in sorted(ado.items_in_state(Lifecycle.DONE), key=lambda ref: ref.item_id):
        if tags.claimed not in item.tags:
            continue
        status: FleetStatus | None = load_or_none(item.item_id, config.fleet_root)
        branch: str = (
            status.branch
            if status is not None
            else slice_branch(item.item_id, item.title, 1, config.branch_template)
        )
        pr_url: str | None = ado.completed_pr_url(branch)
        if pr_url is None:
            awaiting.append(item.item_id)
            continue
        if not cleaner.cleanup(branch):
            _log(f"finalize: cleanup failed for #{item.item_id} ({branch}); will retry")
            failed.append(item.item_id)
            continue
        for tag in item.tags:
            if tags.is_fleet_tag(tag):
                ado.remove_tag(item.item_id, tag)
        ado.add_comment(item.item_id, Finalized(pr_url=pr_url, branch=branch))
        if status is not None:
            seams.update_status(
                item.item_id,
                StatusUpdate(phase="done", parked_state=None, pr_url=pr_url),
                config.fleet_root,
            )
        finalized.append(item.item_id)
    return FinalizeOutcome(
        finalized=tuple(finalized),
        awaiting_merge=tuple(awaiting),
        cleanup_failed=tuple(failed),
    )


def reap_pass(seams: TickSeams, config: FlotillaConfig, now: datetime | None = None) -> ReapOutcome:
    """Requeue claimed slices whose runner is stale *and* confirmed dead."""
    moment: datetime = now if now is not None else datetime.now(UTC)
    tags: Tags = config.tags
    reaped: list[int] = []
    escalated: list[int] = []
    alive: list[int] = []
    parked: list[int] = []
    for item in sorted(seams.ado.items_in_state(Lifecycle.ACTIVE), key=lambda ref: ref.item_id):
        if tags.claimed not in item.tags:
            continue  # a human's active item — invisible to the fleet
        status: FleetStatus | None = load_or_none(item.item_id, config.fleet_root)
        if is_parked(item, status, tags):
            parked.append(item.item_id)
            continue
        failed_park: bool = is_failed_park(status)
        if not failed_park:
            age: float | None = _liveness_age_seconds(item.item_id, status, config, moment)
            if age is not None and age <= config.staleness_threshold_seconds:
                continue  # heartbeat fresh enough
        if _runner_alive(seams, item.item_id, config):
            alive.append(item.item_id)
            continue
        evidence: str = (
            "runner parked failed and process dead"
            if failed_park
            else "heartbeat stale and runner process dead"
        )
        _reap_one(seams, config, item, status, evidence)
        attempt: int = status.attempt if status is not None else 1
        if attempt >= config.max_attempts:
            _escalate_exhausted(seams.ado, item.item_id, attempt + 1, config.max_attempts, tags)
            seams.ado.remove_tag(item.item_id, tags.claimed)
            escalated.append(item.item_id)
        else:
            seams.ado.remove_tag(item.item_id, tags.claimed)
            seams.ado.set_state(item.item_id, Lifecycle.QUEUED)
            seams.ado.add_comment(item.item_id, Reaped(evidence=evidence, attempt=attempt))
            reaped.append(item.item_id)
    return ReapOutcome(
        reaped=tuple(reaped),
        escalated=tuple(escalated),
        skipped_alive=tuple(alive),
        skipped_parked=tuple(parked),
    )


def _liveness_age_seconds(
    item_id: int, status: FleetStatus | None, config: FlotillaConfig, now: datetime
) -> float | None:
    """Age of the best liveness evidence; ``None`` when there is none at all."""
    raw: str | None = None
    if status is not None:
        raw = status.last_heartbeat
    else:
        marker: Path = config.fleet_root / str(item_id) / CLAIMED_AT_FILENAME
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
    if raw is None:
        return None
    try:
        stamped: datetime = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (now - stamped).total_seconds()


def _runner_alive(seams: TickSeams, item_id: int, config: FlotillaConfig) -> bool:
    """Confirm via the pid sidecar whether the runner process still exists."""
    pid_file: Path = config.fleet_root / str(item_id) / RUNNER_PID_FILENAME
    if not pid_file.is_file():
        return False
    raw: str = pid_file.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return False
    return seams.pid_alive(int(raw))


def _reap_one(
    seams: TickSeams,
    config: FlotillaConfig,
    item: WorkItem,
    status: FleetStatus | None,
    evidence: str,
) -> None:
    """Archive the dead attempt's worktree and record the reap in the status."""
    attempt: int = status.attempt if status is not None else 1
    if status is not None:
        seams.archive_worktree(Path(status.worktree), item.item_id, attempt, config)
        seams.run_git(["git", "-C", str(config.fleet_home), "worktree", "prune"])
        seams.update_status(
            item.item_id,
            StatusUpdate(
                phase="parked",
                parked_state="failed",
                last_error=f"reaped: {evidence} (attempt {attempt})",
            ),
            config.fleet_root,
        )


def claim_pass(seams: TickSeams, config: FlotillaConfig) -> ClaimOutcome:
    """Claim unblocked, unclaimed slices up to the cap and launch their runners."""
    ado: BoardAccess = seams.ado
    tags: Tags = config.tags
    inflight: tuple[int, ...] = tuple(
        item.item_id for item in ado.items_in_state(Lifecycle.ACTIVE) if tags.claimed in item.tags
    )
    budget: int = config.cap - len(inflight)
    claimed: list[int] = []
    blocked: list[int] = []
    escalated: list[int] = []
    rolled_back: list[int] = []

    if budget > 0:
        candidates: list[WorkItem] = sorted(
            ado.items_in_state(Lifecycle.QUEUED), key=lambda item: item.item_id
        )
        for item in candidates:
            if budget == 0:
                break
            if any(tags.is_fleet_tag(tag) for tag in item.tags):
                continue
            links: WorkItemLinks = ado.item_links(item.item_id)
            if config.parent_scope_ids and links.parent_id not in config.parent_scope_ids:
                continue
            if not all(
                ado.item_state(predecessor) == Lifecycle.DONE
                for predecessor in links.predecessor_ids
            ):
                blocked.append(item.item_id)
                continue
            attempt: int = _next_attempt(item.item_id, config.fleet_root)
            if attempt > config.max_attempts:
                _escalate_exhausted(ado, item.item_id, attempt, config.max_attempts, tags)
                escalated.append(item.item_id)
                continue
            if _claim_and_launch(seams, config, item, attempt):
                claimed.append(item.item_id)
                budget -= 1
            else:
                rolled_back.append(item.item_id)

    return ClaimOutcome(
        inflight=inflight,
        claimed=tuple(claimed),
        skipped_blocked=tuple(blocked),
        escalated=tuple(escalated),
        rolled_back=tuple(rolled_back),
    )


def _next_attempt(item_id: int, fleet_root: Path) -> int:
    """1 for a first claim; previous attempt + 1 when a status file exists."""
    previous = load_or_none(item_id, fleet_root)
    return 1 if previous is None else previous.attempt + 1


def _claim_and_launch(
    seams: TickSeams,
    config: FlotillaConfig,
    item: WorkItem,
    attempt: int,
) -> bool:
    """Run the claim protocol for one slice; roll back if the launch fails."""
    ado: BoardAccess = seams.ado
    branch: str = slice_branch(item.item_id, item.title, attempt, config.branch_template)
    now: str = _utcnow_iso()
    runner_id: str = f"runner-{item.item_id}-a{attempt}"
    ado.set_state(item.item_id, Lifecycle.ACTIVE)
    ado.add_tag(item.item_id, config.tags.claimed)
    ado.add_comment(item.item_id, Claimed(runner_id=runner_id, branch=branch, when=now))
    seams.write_claimed_at(item.item_id, config.fleet_root, now)
    if seams.launcher.launch(item.item_id, branch, attempt):
        return True
    ado.remove_tag(item.item_id, config.tags.claimed)
    ado.set_state(item.item_id, Lifecycle.QUEUED)
    ado.add_comment(item.item_id, RolledBack(reason="runner launch failed"))
    return False


def _escalate_exhausted(ado: BoardAccess, item_id: int, attempt: int, cap: int, tags: Tags) -> None:
    """Tag a slice whose transient retries are exhausted (addendum §4)."""
    ado.add_tag(item_id, tags.failed)
    ado.add_comment(item_id, Escalated(attempt=attempt, cap=cap))


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 form (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(message: str) -> None:
    """Emit one timestamped log line (fleet-tick.sh appends stdout to the log)."""
    print(f"[{_utcnow_iso()}] supervisor: {message}")


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
        runner_skill: str = DEFAULT_RUNNER_SKILL,
        tdd_skill: str = DEFAULT_TDD_SKILL,
        qa_skill: str = DEFAULT_QA_SKILL,
        python_executable: str = sys.executable,
    ) -> None:
        """Bind the launcher to the repo root, fleet root, and a command runner."""
        self._fleet_home = fleet_home
        self._fleet_root = fleet_root
        self._run = run
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._model = model
        self._effort = effort
        self._runner_skill = runner_skill
        self._tdd_skill = tdd_skill
        self._qa_skill = qa_skill
        self._python = python_executable

    def launch(self, item_id: int, branch: str, attempt: int) -> bool:
        """Start the slice's runner pane; return False if tmux refuses.

        The configured skill names are threaded into the pane env so the runner
        skill is invoked by its configured name and receives the tdd/qa skill
        names as prompt arguments (the wrapper stops hardcoding ``/afk-slice-runner``).
        """
        wrap: Path = resolve_script("runner-wrap.sh")
        command: str = (
            f"FLEET_HOME={shlex.quote(str(self._fleet_home))} "
            f"FLEET_ROOT={shlex.quote(str(self._fleet_root))} "
            f"FLEET_PYTHON={shlex.quote(self._python)} "
            f"FLEET_HEARTBEAT_INTERVAL_SECONDS="
            f"{shlex.quote(str(self._heartbeat_interval_seconds))} "
            f"FLEET_MODEL={shlex.quote(self._model)} "
            f"FLEET_EFFORT={shlex.quote(self._effort)} "
            f"FLEET_RUNNER_SKILL={shlex.quote(self._runner_skill)} "
            f"FLEET_TDD_SKILL={shlex.quote(self._tdd_skill)} "
            f"FLEET_QA_SKILL={shlex.quote(self._qa_skill)} "
            f"{shlex.quote(str(wrap))} {item_id} {shlex.quote(branch)} {attempt}"
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
    """Run the configured cleanup skill headlessly per branch (``/cleanup-merged-branches``).

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
        cleanup_skill: str = DEFAULT_CLEANUP_SKILL,
    ) -> None:
        """Bind the cleaner to the repo root, a command runner, and the cleanup skill."""
        self._fleet_home = fleet_home
        self._run = run
        self._model = model
        self._effort = effort
        self._cleanup_skill = cleanup_skill

    def cleanup(self, branch: str) -> bool:
        """Clean one merged branch; return False when the session failed."""
        return (
            self._run(
                [
                    "claude",
                    "-p",
                    f"{self._cleanup_skill} {branch}",
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


class ReadOnlyBoard:
    """``BoardAccess`` decorator that physically cannot write to the board.

    Reads delegate to the wrapped client; every write logs the action a real
    tick WOULD have performed and does nothing. Dry-run safety is this
    boundary, not a flag threaded through the passes — a pass (present or
    future) that reaches ``seams.ado`` with a write cannot mutate the board
    while dry-run is active, because the write never leaves this class.
    """

    def __init__(self, inner: BoardAccess) -> None:
        """Wrap ``inner``, passing its reads through and absorbing its writes."""
        self._inner = inner

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Pass the read through to the wrapped client."""
        return self._inner.items_in_state(state)

    def completed_pr_url(self, branch: str) -> str | None:
        """Pass the read through to the wrapped client."""
        return self._inner.completed_pr_url(branch)

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Pass the read through to the wrapped client."""
        return self._inner.item_links(item_id)

    def item_state(self, item_id: int) -> Lifecycle:
        """Pass the read through to the wrapped client."""
        return self._inner.item_state(item_id)

    def validate_config(self) -> None:
        """Pass the validation read through (a live-board read, no mutation)."""
        self._inner.validate_config()

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Absorb the write, logging the would-be state transition."""
        _log(f"[dry-run] WOULD move #{item_id} to {state.value}")

    def add_tag(self, item_id: int, tag: str) -> None:
        """Absorb the write, logging the would-be tag addition."""
        _log(f"[dry-run] WOULD add tag '{tag}' to #{item_id}")

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Absorb the write, logging the would-be tag removal."""
        _log(f"[dry-run] WOULD remove tag '{tag}' from #{item_id}")

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Absorb the write, logging the would-be discussion event."""
        _log(f"[dry-run] WOULD comment on #{item_id}: {event}")


class DryRunLauncher:
    """``Launcher`` stand-in: reports the runner it would start, starts nothing."""

    def launch(self, item_id: int, branch: str, attempt: int) -> bool:
        """Log the would-be runner pane and report success."""
        _log(f"[dry-run] WOULD launch runner for #{item_id} (branch {branch}, attempt {attempt})")
        return True


class DryRunCleaner:
    """``Cleaner`` stand-in: reports the would-be cleanup, spawns no claude."""

    def cleanup(self, branch: str) -> bool:
        """Log the would-be headless cleanup session and report success."""
        _log(f"[dry-run] WOULD run the cleanup skill for {branch}")
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
    worktree: Path, item_id: int, attempt: int, _config: FlotillaConfig
) -> None:
    """Absorb the worktree archive move, logging it."""
    _log(f"[dry-run] WOULD archive worktree {worktree} of #{item_id} (attempt {attempt})")


def _dry_run_update_status(item_id: int, _changes: StatusUpdate, _fleet_root: Path) -> None:
    """Absorb the status-file write, logging it."""
    _log(f"[dry-run] WOULD update the status file of #{item_id}")


def _dry_run_write_claimed_at(item_id: int, _fleet_root: Path, _timestamp: str) -> None:
    """Absorb the claimed-at marker write, logging it."""
    _log(f"[dry-run] WOULD write the claimed-at marker of #{item_id}")


def dry_run_seams(seams: TickSeams) -> TickSeams:
    """Wrap every side-effecting seam so the tick cannot mutate anything.

    Reads pass through — the tick still runs the full finalize/reap/claim
    read+plan logic and reports the would-be actions — but every write (board,
    tmux panes, claude spawns including the auth probe, git, worktree moves,
    local status/marker files) becomes a logged ``[dry-run] WOULD …`` no-op.
    ``pid_alive`` stays real: it is a pure read (signal 0) and the reap plan is
    meaningless without it. The tick lock and the supervisor log are still
    written — they are coordination artifacts, not fleet state.
    """
    return replace(
        seams,
        ado=ReadOnlyBoard(seams.ado),
        launcher=DryRunLauncher(),
        cleaner=DryRunCleaner(),
        run_git=_dry_run_git,
        auth_ok=_dry_run_auth_ok,
        archive_worktree=_dry_run_archive_worktree,
        update_status=_dry_run_update_status,
        write_claimed_at=_dry_run_write_claimed_at,
    )


def build_seams(config: FlotillaConfig, *, dry_run: bool = False) -> TickSeams:
    """Build the production seams; with ``dry_run``, wrap them so nothing can mutate."""
    seams = TickSeams(
        ado=build_board(config),
        launcher=TmuxLauncher(
            config.fleet_home,
            config.fleet_root,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            model=config.model,
            effort=config.effort,
            runner_skill=config.runner_skill,
            tdd_skill=config.tdd_skill,
            qa_skill=config.qa_skill,
        ),
        cleaner=ClaudeCleanup(
            config.fleet_home,
            model=config.model,
            effort=config.effort,
            cleanup_skill=config.cleanup_skill,
        ),
    )
    if not dry_run:
        return seams
    _log(
        "DRY-RUN tick: reads and planning only — every board write, runner launch, "
        "claude spawn, and local fleet-state write is suppressed and logged as "
        "'[dry-run] WOULD …'"
    )
    return dry_run_seams(seams)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supervisor tick against the configured board and tmux."""
    parser = argparse.ArgumentParser(
        prog="flotilla-supervisor",
        description="One AFK-fleet supervisor tick (ADR-0007 / ADR-0001).",
    )
    parser.add_argument("--fleet-root", type=Path, default=None)
    parser.add_argument("--fleet-home", type=Path, default=None)
    parser.add_argument("--provider", default=None, help="override the board provider")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full tick read+plan logic but suppress every side effect "
        "(board writes, runner launches, claude spawns, local fleet-state writes); "
        "FLEET_DRY_RUN=1 is equivalent",
    )
    args: argparse.Namespace = parser.parse_args(argv)
    config: FlotillaConfig = load_config(
        fleet_root=args.fleet_root, fleet_home=args.fleet_home, provider=args.provider
    )
    dry_run: bool = bool(args.dry_run) or FLEET_DRY_RUN
    seams: TickSeams = build_seams(config, dry_run=dry_run)
    try:
        seams.ado.validate_config()
        return run_tick(seams, config)
    except BoardValidationError as exc:
        print(f"supervisor: board configuration invalid: {exc}", file=sys.stderr)
        return 1
    except TagWriteError as exc:
        print(f"supervisor: tag write failed verification: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        stderr: str = exc.stderr if isinstance(exc.stderr, str) else ""
        print(f"supervisor: board/tmux call failed: {exc} {stderr}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
