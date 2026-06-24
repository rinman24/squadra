"""Fleet supervisor tick — the facts→engine→execute orchestration (ADR-0002).

A deterministic, stateless, token-free scheduled script (no LLM in the tick
itself). Each tick reconstructs its entire view from observed reality and drives
one pure, subsuming FSM (:class:`squadra.engines.LifecycleEngine`, ADR-0002
decision 3) per slice:

1. **gather facts** — for every fleet-relevant slice (the fleet-claimed ACTIVE /
   DONE items and the QUEUED claim candidates) the orchestrator assembles a
   :class:`~squadra.domain.LifecycleFacts` host-side from board truth, the slice
   ``status.json`` breadcrumb, ``SandboxAccess.inspect`` (container liveness/exit,
   subsuming the old ``pid_alive``), the heartbeat age, the bind-mounted
   ``outcome.json`` manifest, the commit count in ``base..HEAD``, the egress-proxy
   deny log, and the completed-PR query.
2. **decide** — ``LifecycleEngine.decide(facts)`` projects each slice onto one
   :class:`~squadra.domain.State` plus the ordered :data:`~squadra.domain.
   LifecycleAction` intents to run. This single engine subsumes the legacy
   finalize / reap / claim passes.
3. **execute** — the orchestrator maps each intent onto the ``BoardAccess`` /
   ``SandboxAccess`` / ``CleanupAccess`` / ``WorktreeAccess`` seams. The claim
   **budget** is the one cross-slice constraint that stays orchestrator-side: the
   engine emits a per-slice :class:`~squadra.domain.SignalClaimable`; the
   orchestrator claims up to ``cap`` (``FLEET_MAX_RUNNERS=0`` suppresses claims
   only — finalize/reap still mutate; for a tick that cannot mutate, dry-run).

The supervisor is **provider-blind**: it speaks the neutral ``Lifecycle`` state
and emits structured ``CommentEvent`` values; the ``BoardAccess`` adapter maps to
native states and renders markup. State names, tag prefix, base branch, and skill
names are configuration.

Only the **claim/launch** path now needs a working ``claude`` (the contained
runner is the fleet's single LLM call — finalize cleanup is deterministic, ADR-
0002 decision 4) *and* a working Azure DevOps PAT (claiming a slice does host-side
git remote ops — worktree create off ``origin/main``, then push — over HTTPS+PAT,
no SSH key). A tick with claim work pending preflights both before it claims: the
PAT via a ``git ls-remote`` against the target remote, then claude via a throwaway
prompt. On a failed probe (a rejected/expired PAT, dead claude auth, or a transient
outage all read identically) the tick degrades to the non-claim decisions — the
finalize + reap pass, which never claim and so never touch that auth — and retries
next tick. The PAT probe runs first and short-circuits, so a dead PAT does not pay
to spawn claude.

Overlapping ticks serialize via a non-blocking ``flock`` on
``<fleet-root>/supervisor.lock`` — the losing tick exits cleanly without touching
the board.

A tick can be a **dry run** (``--dry-run`` / ``FLEET_DRY_RUN=1``): the full
fact-gather + decide logic runs and reports what a real tick WOULD do, but every
side effect — board writes, sandbox launch/teardown/exec, deterministic cleanup,
worktree create/archive/prune, slice-context injection, the PAT + claude auth
probes, and local status/marker writes — is suppressed at the ``TickSeams`` boundary
(``dry_run_seams``), so the tick physically cannot mutate. ``FLEET_MAX_RUNNERS=0``
is *not* a safe smoke: it only zeroes the claim budget. Use a dry run.

Run one tick: ``python -m squadra.supervisor`` (normally via ``squadra tick``).
"""

import argparse
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from typing import Final

from squadra.board import BoardAccess, BoardValidationError, TagWriteError, build_board
from squadra.cleanup import CleanupAccess, DeterministicCleanup
from squadra.config import SquadraConfig, load_config
from squadra.constants import FLEET_DRY_RUN, FLEET_MODEL
from squadra.domain import (
    AwaitAgent,
    Claimed,
    CommentEvent,
    Escalated,
    EscalateEgressDenied,
    EscalateExhausted,
    FinalizeCleanup,
    Finalized,
    HandoffAgentDone,
    LaunchSandbox,
    Lifecycle,
    LifecycleAction,
    LifecycleDecision,
    LifecycleFacts,
    NoAction,
    ParkNeedsDecision,
    Reaped,
    RetrySlice,
    RolledBack,
    SandboxAbsent,
    SandboxExited,
    SandboxRunning,
    SandboxSpec,
    SandboxStatus,
    SignalClaimable,
    SliceContext,
    StopContainer,
    SweepLeak,
    Tags,
    WorkItem,
    WorkItemLinks,
)
from squadra.dry_run import DryRunCleanup, DryRunWorktree
from squadra.engines import LifecycleEngine, slice_branch
from squadra.git_host import host_git_argv
from squadra.manifest import ManifestRead, read_manifest, write_slice_context
from squadra.repo import remote_auth_ok, target_remote_url
from squadra.sandbox import ComposeSandbox, DryRunSandbox, SandboxAccess
from squadra.secrets import secret_names_from_env
from squadra.status import FleetStatus, StatusUpdate, load_or_none, update
from squadra.worktree import GitWorktreeAccess, WorktreeAccess

CLAIMED_AT_FILENAME: Final[str] = "claimed-at"
COMPOSE_FILENAME: Final[str] = "compose.yaml"
AGENT_SERVICE: Final[str] = "agent"
AUTH_PROBE_TIMEOUT_SECONDS: Final[float] = 120.0


# --- auth probe (now guards the claim/launch path only, ADR-0002 decision 4) --


def _auth_probe_command(model: str) -> tuple[str, ...]:
    """Build the throwaway probe argv, pinned to the fleet model.

    The probe exercises the same ``--model`` the runner uses, so a
    misconfigured/unavailable FLEET_MODEL fails the preflight here rather than
    silently failing every claimed runner. No ``--effort`` — the probe does no
    reasoning, so the runner's effort tier is irrelevant to it.
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

    Any failure mode (dead auth, transient API outage, missing binary, timeout,
    unavailable model) reads as unavailable — the tick degrades to the non-claim
    decisions either way and retries next tick.
    """
    try:
        completed: subprocess.CompletedProcess[str] = run(list(_auth_probe_command(model)))
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0 and "READY" in completed.stdout


# --- ADO PAT probe (guards the claim/launch path's host-side git remote ops) ---


def _resolve_fleet_home() -> Path:
    """Resolve ``FLEET_HOME`` for the default probe (the repo squadra operates on)."""
    raw: str | None = os.environ.get("FLEET_HOME")
    return Path(raw) if raw is not None and raw.strip() else Path.cwd()


def _ado_pat_ok(fleet_home: Path | None = None) -> bool:
    """Actively probe the ADO PAT on the git auth path the fleet's claims use.

    Resolves the target remote from ``FLEET_HOME``'s ``origin`` (else
    ``FLEET_APP_REPO_URL``) and runs ``git ls-remote`` against it with the
    env-var PAT credential helper — the exact path of every host-side clone/
    fetch/push, so it cannot pass while a real claim's git op fails. A missing
    remote, a rejected/expired/wrong-scope PAT, a timeout, or no ``git`` all read
    as unavailable; the tick degrades to the non-claim decisions and retries.
    """
    home: Path = fleet_home if fleet_home is not None else _resolve_fleet_home()
    remote: str | None = target_remote_url(home)
    if remote is None:
        return False
    return remote_auth_ok(remote)


def _ado_pat_rejected_message() -> str:
    """Build the actionable one-line error logged when the PAT preflight fails."""
    secret: str = secret_names_from_env().ado_pat
    return (
        "ado PAT rejected on the git auth path (git ls-remote against the target "
        "remote failed) — skipped the claim/launch decisions; running the non-claim "
        f"decisions only, retrying next tick. Rotate the {secret!r} Key Vault secret "
        "(it is expired or lacks Code (Read & Write)): see the README 'Supervisor' "
        "section and the consuming repo's runbook docs/contributing/afk-fleet.md -> "
        "'Key Vault secrets & PAT rotation'."
    )


# --- fact-gathering read seams (host-side I/O; pass through in dry-run) --------


def _read_commits_present(worktree: Path, base_ref: str) -> bool:
    """Whether ``base_ref..HEAD`` has at least one commit in the slice worktree.

    The substance half of the completion triple; ``base_ref`` is the ref the slice
    branched off (``origin/main``). A worktree that does not exist yet (never
    launched) or a git failure reads as "no commits" — never a crash.
    """
    if not worktree.is_dir():
        return False
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        host_git_argv("rev-list", "--count", f"{base_ref}..HEAD", work_dir=worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    count: str = completed.stdout.strip()
    return count.isdigit() and int(count) > 0


def _read_egress_denied_host(sandbox: SandboxAccess, spec: SandboxSpec) -> str | None:
    """Extract the host named in an egress-proxy CONNECT denial, or ``None``.

    Reads the proxy sidecar's captured logs via the sandbox seam and applies the
    deny-line regex resolved in the egress-proxy spike. A denial is a security
    signal the engine escalates immediately (ADR-0002 decisions 5–6).
    """
    return egress_denied_host_from_logs(sandbox.logs(spec))


_EGRESS_DENY_PATTERN: Final[str] = r'Proxying refused on filtered (?:domain|url) "([^"]+)"'


def egress_denied_host_from_logs(logs: str) -> str | None:
    """Return the first denied host named in proxy logs, or ``None``."""
    import re  # noqa: PLC0415 - local: the only egress-parse site

    match: re.Match[str] | None = re.search(_EGRESS_DENY_PATTERN, logs)
    return match.group(1) if match is not None else None


def _write_claimed_at(item_id: int, fleet_root: Path, timestamp: str) -> None:
    """Record the claim time so a never-started claim can be aged out and reaped."""
    directory: Path = fleet_root / str(item_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CLAIMED_AT_FILENAME).write_text(timestamp + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class TickSeams:
    """The tick's seams, injectable for tests.

    The **mutating** seams are what ``dry_run_seams`` must wrap so a dry-run tick
    physically cannot write: board writes via ``ado``; sandbox launch/teardown/
    exec via ``sandbox``; deterministic cleanup via ``cleanup``; worktree create/
    archive/prune via ``worktree``; the PAT probe via ``pat_ok`` and the claude
    auth probe via ``auth_ok`` (each spawns a process and so is itself a side
    effect a dry run must not perform); slice-context injection via
    ``write_context``; the local fleet-state writes via ``update_status`` /
    ``write_claimed_at``. Keeping this exhaustive is what makes ``dry_run_seams`` a
    write-blocking boundary rather than a flag — never add a side effect without
    routing it through a seam.

    The **read** seams (``inspect`` / ``read_logs`` / ``read_manifest`` /
    ``commits_present``) gather facts and pass through unchanged under dry-run;
    they perform no mutation, so they are real even in a dry run (the plan is
    meaningless without them, exactly as ``pid_alive`` was).
    """

    ado: BoardAccess
    sandbox: SandboxAccess
    cleanup: CleanupAccess
    worktree: WorktreeAccess
    pat_ok: Callable[[], bool] = field(default=_ado_pat_ok)
    auth_ok: Callable[[], bool] = field(default=_claude_auth_ok)
    write_context: Callable[..., object] = field(default=write_slice_context)
    read_manifest: Callable[[Path], ManifestRead] = field(default=read_manifest)
    commits_present: Callable[[Path, str], bool] = field(default=_read_commits_present)
    update_status: Callable[[int, StatusUpdate, Path], object] = field(default=update)
    write_claimed_at: Callable[[int, Path, str], None] = field(default=_write_claimed_at)


@contextmanager
def supervisor_lock(fleet_root: Path) -> Generator[bool, None, None]:
    """Try to take the tick lock; yield whether this tick may proceed."""
    fleet_root.mkdir(parents=True, exist_ok=True)
    with (fleet_root / "supervisor.lock").open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --- a slice's gathered facts + its derived decision --------------------------


@dataclass(frozen=True, slots=True)
class SliceView:
    """One slice's gathered view this tick.

    Carries the board item, the bucket it was read from, its ``status.json``
    breadcrumb, the gathered :class:`~squadra.domain.LifecycleFacts`, and the
    engine's decision.
    """

    item: WorkItem
    lifecycle: Lifecycle
    status: FleetStatus | None
    facts: LifecycleFacts
    decision: LifecycleDecision

    def branch(self, config: SquadraConfig) -> str:
        """Return the slice's current branch (status fast-path, else derived)."""
        return _branch_for(self.item, self.status, config)


def run_tick(seams: TickSeams, config: SquadraConfig) -> int:
    """Run one serialized supervisor tick (gather facts → decide → execute)."""
    with supervisor_lock(config.fleet_root) as acquired:
        if not acquired:
            _log("tick skipped — another tick holds the lock")
            return 0
        engine = LifecycleEngine()
        views: list[SliceView] = _gather(seams, config, engine)
        if _claim_work_pending(views, config):
            # Both probes guard the claim/launch path only (finalize + reap never
            # claim and never touch this auth). The PAT probe runs first and
            # short-circuits, so a dead PAT never pays to spawn the claude probe.
            if not seams.pat_ok():
                _log(_ado_pat_rejected_message())
                _execute_non_claim(seams, config, views)
                return 0
            if not seams.auth_ok():
                _log(
                    "claude auth-unavailable — skipped the claim/launch decisions; "
                    "running the non-claim decisions only, retrying next tick"
                )
                _execute_non_claim(seams, config, views)
                return 0
        _execute(seams, config, views)
    return 0


def _execute_non_claim(seams: TickSeams, config: SquadraConfig, views: Sequence[SliceView]) -> None:
    """Run the finalize + reap decisions only — the degraded auth-probe path.

    Every claim/launch decision is dropped so no slice is claimed, but in-flight
    finalize and reap still proceed (they never claim and never touch that auth).
    """
    _execute(seams, config, [v for v in views if not _is_claim(v)])


def _gather(seams: TickSeams, config: SquadraConfig, engine: LifecycleEngine) -> list[SliceView]:
    """Build the per-slice fact set + decision for every fleet-relevant slice.

    The fleet-relevant set is the union of the ACTIVE and DONE buckets (in-flight
    and finalize-eligible) and the QUEUED bucket (claim candidates). Each item is
    projected to facts host-side, then the engine decides. Items are sorted by id
    for deterministic ordering (the legacy passes' ordering).
    """
    items: dict[int, tuple[WorkItem, Lifecycle]] = {}
    for bucket in (Lifecycle.DONE, Lifecycle.ACTIVE, Lifecycle.QUEUED):
        for item in seams.ado.items_in_state(bucket):
            items[item.item_id] = (item, bucket)
    views: list[SliceView] = []
    for item_id in sorted(items):
        item, bucket = items[item_id]
        status: FleetStatus | None = load_or_none(item_id, config.fleet_root)
        facts: LifecycleFacts = _build_facts(seams, config, item, bucket, status)
        views.append(
            SliceView(
                item=item,
                lifecycle=bucket,
                status=status,
                facts=facts,
                decision=engine.decide(facts),
            )
        )
    return views


def _build_facts(
    seams: TickSeams,
    config: SquadraConfig,
    item: WorkItem,
    lifecycle: Lifecycle,
    status: FleetStatus | None,
) -> LifecycleFacts:
    """Project one slice's observed reality onto the engine's ``LifecycleFacts``."""
    tags: Tags = config.tags
    is_claimed: bool = tags.claimed in item.tags
    branch: str = _branch_for(item, status, config)
    spec: SandboxSpec = _spec_for(item.item_id, branch, config)
    worktree: Path = _worktree_for(branch, config)

    # Container liveness/exit — only meaningful for an in-flight, fleet-claimed
    # slice; gathering it for the queued/done buckets is pointless I/O.
    sandbox_status: SandboxStatus = (
        seams.sandbox.inspect(spec)
        if lifecycle is Lifecycle.ACTIVE and is_claimed
        else SandboxAbsent()
    )
    present, running, exit_code = _container_facts(sandbox_status)
    egress_denied: str | None = _read_egress_denied_host(seams.sandbox, spec) if present else None
    manifest: ManifestRead = (
        seams.read_manifest(worktree) if present and not running else _absent_manifest()
    )
    commits: bool = (
        seams.commits_present(worktree, f"origin/{config.base_branch}")
        if present and not running
        else False
    )

    return LifecycleFacts(
        lifecycle=lifecycle,
        is_fleet_claimed=is_claimed,
        predecessors_done=_predecessors_done(seams, config, item, lifecycle),
        parked_tagged=any(tag in tags.parked for tag in item.tags),
        failed_tagged=tags.failed in item.tags,
        needs_decision_tagged=tags.needs_decision in item.tags,
        phase=status.phase if status is not None else None,
        parked_state=status.parked_state if status is not None else None,
        container_present=present,
        container_running=running,
        container_exit_code=exit_code,
        heartbeat_stale=_heartbeat_stale(item.item_id, status, config),
        manifest_present=manifest.present,
        manifest_valid=manifest.valid,
        manifest_needs_decision=manifest.needs_decision,
        commits_present=commits,
        completed_pr_url=(
            seams.ado.completed_pr_url(branch)
            if lifecycle is Lifecycle.DONE and is_claimed
            else None
        ),
        build_failed=False,
        egress_denied_host=egress_denied,
        teardown_failed=False,
        attempt=status.attempt if status is not None else 1,
        max_attempts=config.max_attempts,
    )


def _absent_manifest() -> ManifestRead:
    """Return a neutral "no manifest" read for a slice whose container isn't exited."""
    return ManifestRead(present=False, valid=False, needs_decision=False, manifest=None)


def _container_facts(status: SandboxStatus) -> tuple[bool, bool, int | None]:
    """Project a ``SandboxStatus`` onto ``(present, running, exit_code)`` facts."""
    if isinstance(status, SandboxRunning):
        return True, True, None
    if isinstance(status, SandboxExited):
        return True, False, status.exit_code
    return False, False, None


def _predecessors_done(
    seams: TickSeams, config: SquadraConfig, item: WorkItem, lifecycle: Lifecycle
) -> bool:
    """Whether a queued candidate is unblocked (all predecessors done, in scope).

    Only the queued bucket gates on this; for any other bucket the field is moot
    (the engine ignores it). An out-of-parent-scope item is reported blocked so it
    is never claimed (parity with the legacy claim filter).
    """
    if lifecycle is not Lifecycle.QUEUED:
        return True
    links: WorkItemLinks = seams.ado.item_links(item.item_id)
    if config.parent_scope_ids and links.parent_id not in config.parent_scope_ids:
        return False
    return all(
        seams.ado.item_state(predecessor) == Lifecycle.DONE for predecessor in links.predecessor_ids
    )


def _heartbeat_stale(item_id: int, status: FleetStatus | None, config: SquadraConfig) -> bool:
    """Whether the best liveness evidence is older than the staleness threshold.

    Mirrors the legacy reap age check: the status heartbeat, else the claimed-at
    marker (a claim whose runner never started). No evidence at all reads as not
    stale here — a just-claimed slice with no breadcrumb is provisioning, not
    timed out (the engine routes that to PROVISIONING).
    """
    age: float | None = _liveness_age_seconds(item_id, status, config)
    return age is not None and age > config.staleness_threshold_seconds


def _liveness_age_seconds(
    item_id: int, status: FleetStatus | None, config: SquadraConfig
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
    return (datetime.now(UTC) - stamped).total_seconds()


def _branch_for(item: WorkItem, status: FleetStatus | None, config: SquadraConfig) -> str:
    """Resolve the slice's current branch (status fast-path, else derived from id+title)."""
    if status is not None:
        return status.branch
    return slice_branch(item.item_id, item.title, 1, config.branch_template)


def _spec_for(item_id: int, branch: str, config: SquadraConfig) -> SandboxSpec:
    """Build the per-slice ephemeral sandbox spec for ``item_id`` on ``branch``."""
    worktree: Path = _worktree_for(branch, config)
    return SandboxSpec(
        item_id=item_id,
        project=f"squadra-slice-{item_id}",
        compose_file=worktree / ".squadra" / COMPOSE_FILENAME,
        worktree=worktree,
        agent_service=AGENT_SERVICE,
    )


def _worktree_for(branch: str, config: SquadraConfig) -> Path:
    """Return the host path bind-mounted as the agent's ``/work`` for ``branch``."""
    return config.fleet_home / config.worktree_dir / branch.replace("/", "+")


# --- execution: map each engine intent onto the Access seams ------------------


def _execute(seams: TickSeams, config: SquadraConfig, views: Sequence[SliceView]) -> None:
    """Run every slice's decided actions, enforcing the cross-slice claim budget.

    Non-claim actions run first (finalize, retry, escalate, handoff, teardown);
    the claim budget is then applied over the engine's ``SignalClaimable`` set so
    the orchestrator claims at most ``cap - inflight`` slices this tick.
    """
    inflight: tuple[int, ...] = tuple(v.item.item_id for v in views if _is_inflight(v, config.tags))
    claimable: list[SliceView] = []
    for view in views:
        for action in view.decision.actions:
            if isinstance(action, SignalClaimable):
                claimable.append(view)
            else:
                _run_action(seams, config, view, action)
    _claim_up_to_budget(seams, config, claimable, inflight)
    _log(
        f"tick: inflight={list(inflight)} "
        f"claimable={[v.item.item_id for v in claimable]} "
        f"states={ {v.item.item_id: v.decision.state.value for v in views} }"
    )


def _run_action(
    seams: TickSeams, config: SquadraConfig, view: SliceView, action: LifecycleAction
) -> None:
    """Dispatch one non-claim engine intent onto the Access seams."""
    match action:
        case NoAction() | AwaitAgent():
            return
        case FinalizeCleanup(pr_url=pr_url):
            _finalize(seams, config, view, pr_url)
        case HandoffAgentDone():
            _handoff(seams, config, view)
        case ParkNeedsDecision():
            _park_needs_decision(seams, config, view)
        case StopContainer():
            seams.sandbox.teardown(_spec(view, config))
        case RetrySlice(edge=_, attempt=attempt):
            _retry(seams, config, view, attempt)
        case EscalateExhausted(edge=_, attempt=attempt, cap=cap):
            _escalate(seams, config, view, attempt, cap)
        case EscalateEgressDenied(denied_host=host):
            _escalate_egress(seams, config, view, host)
        case SweepLeak():
            seams.sandbox.teardown(_spec(view, config))
        case SignalClaimable() | LaunchSandbox():
            return  # claim/launch handled in the budget pass


def _finalize(seams: TickSeams, config: SquadraConfig, view: SliceView, pr_url: str) -> None:
    """Deterministically retire a merged slice: cleanup, drop fleet tags, status→done.

    Cleanup is LLM-free (ADR-0002 decision 4): the supervisor knows the merged
    branch, so it deletes the branch, prunes/removes the worktree, and tears the
    compose project down via ``CleanupAccess``. The board lifecycle proceeds even
    on a partial cleanup result (a leftover artifact is retried next tick, not a
    blocker), exactly as the legacy finalize retried — except a *failed* cleanup
    keeps the fleet tags so the next tick retries it.
    """
    branch: str = view.branch(config)
    spec: SandboxSpec = _spec(view, config)
    result = seams.cleanup.finalize(branch, str(spec.worktree), spec.project)
    if not (result.branch_deleted and result.worktree_removed and result.compose_down):
        _log(f"finalize: cleanup partial for #{view.item.item_id} ({branch}); will retry")
        return
    for tag in view.item.tags:
        if config.tags.is_fleet_tag(tag):
            seams.ado.remove_tag(view.item.item_id, tag)
    seams.ado.add_comment(view.item.item_id, Finalized(pr_url=pr_url, branch=branch))
    if view.status is not None:
        seams.update_status(
            view.item.item_id,
            StatusUpdate(phase="done", parked_state=None, pr_url=pr_url),
            config.fleet_root,
        )


def _handoff(seams: TickSeams, config: SquadraConfig, view: SliceView) -> None:
    """Park a cleanly-finished agent run awaiting its PR + tear the sandbox down.

    The agent committed and wrote a valid handoff manifest; the supervisor parks
    the slice ``awaiting-pr-approval`` (a deliberate park, never reaped) and tears
    the project down. The push + PR open + QA Task + work-item links the agent
    used to do itself are the remaining host-side write-tail consumed by G2 (the
    PR title/body travel in the manifest); F4 wires the park + teardown that
    bracket them so the slice lifecycle is correct end-to-end.
    """
    seams.ado.add_tag(view.item.item_id, config.tags.awaiting_pr_approval)
    if view.status is not None:
        seams.update_status(
            view.item.item_id,
            StatusUpdate(phase="parked", parked_state="awaiting-pr-approval"),
            config.fleet_root,
        )
    seams.sandbox.teardown(_spec(view, config))


def _park_needs_decision(seams: TickSeams, config: SquadraConfig, view: SliceView) -> None:
    """Park a ``needs-decision`` run for a human: tag it, no PR, tear the sandbox down."""
    seams.ado.add_tag(view.item.item_id, config.tags.needs_decision)
    if view.status is not None:
        seams.update_status(
            view.item.item_id,
            StatusUpdate(phase="parked", parked_state="needs-decision"),
            config.fleet_root,
        )
    seams.sandbox.teardown(_spec(view, config))


def _retry(seams: TickSeams, config: SquadraConfig, view: SliceView, attempt: int) -> None:
    """Requeue a failed slice for another attempt (archive worktree, tear down, requeue).

    Mirrors the legacy reap's board-side outcome: the dead attempt's worktree is
    archived for inspection and pruned, the sandbox is torn down, the status
    records the failure, the fleet ``claimed`` tag is dropped, and the item is
    moved back to QUEUED with a ``Reaped`` comment — so the next claim derives
    ``attempt + 1`` from the status and runs from a fresh worktree.
    """
    spec: SandboxSpec = _spec(view, config)
    if view.status is not None:
        archive_root: str = str(config.fleet_root / str(view.item.item_id) / "archive")
        seams.worktree.archive(str(spec.worktree), archive_root, attempt)
        seams.worktree.prune()
        seams.update_status(
            view.item.item_id,
            StatusUpdate(
                phase="parked",
                parked_state="failed",
                last_error=f"reaped: agent failed (attempt {attempt})",
            ),
            config.fleet_root,
        )
    seams.sandbox.teardown(spec)
    seams.ado.remove_tag(view.item.item_id, config.tags.claimed)
    seams.ado.set_state(view.item.item_id, Lifecycle.QUEUED)
    seams.ado.add_comment(
        view.item.item_id, Reaped(evidence="agent failed and container exited", attempt=attempt)
    )


def _escalate(
    seams: TickSeams, config: SquadraConfig, view: SliceView, attempt: int, cap: int
) -> None:
    """Escalate a slice whose retries are exhausted: tag failed, drop claimed, comment.

    Parity with the legacy reap exhaustion path: the slice keeps its native state
    (it is not requeued), gains the ``failed`` tag, loses the ``claimed`` tag, and
    gets an ``Escalated`` comment. The dead attempt's resources are reclaimed.
    """
    spec: SandboxSpec = _spec(view, config)
    if view.status is not None:
        archive_root: str = str(config.fleet_root / str(view.item.item_id) / "archive")
        seams.worktree.archive(str(spec.worktree), archive_root, attempt - 1)
        seams.worktree.prune()
    seams.sandbox.teardown(spec)
    seams.ado.add_tag(view.item.item_id, config.tags.failed)
    seams.ado.add_comment(view.item.item_id, Escalated(attempt=attempt, cap=cap))
    seams.ado.remove_tag(view.item.item_id, config.tags.claimed)


def _escalate_egress(seams: TickSeams, config: SquadraConfig, view: SliceView, host: str) -> None:
    """Escalate immediately on an egress-denied security signal, naming the host.

    Never retried (ADR-0002 decision 6): the slice is tagged failed and gets an
    ``Escalated`` comment carrying the denied host in its evidence; the sandbox is
    torn down. The fleet ``claimed`` tag is dropped so the item is no longer
    fleet-owned in flight.
    """
    seams.sandbox.teardown(_spec(view, config))
    seams.ado.add_tag(view.item.item_id, config.tags.failed)
    seams.ado.add_comment(
        view.item.item_id,
        Escalated(attempt=view.facts.attempt, cap=config.max_attempts),
    )
    seams.ado.remove_tag(view.item.item_id, config.tags.claimed)
    _log(f"escalate: #{view.item.item_id} egress-denied to {host} — security signal, not retried")


def _claim_up_to_budget(
    seams: TickSeams,
    config: SquadraConfig,
    claimable: Sequence[SliceView],
    inflight: Sequence[int],
) -> None:
    """Claim+launch at most ``cap - inflight`` slices from the engine's claimable set.

    The cross-slice claim budget stays orchestrator-side (the engine emits a
    per-slice ``SignalClaimable``; this is the single place the cap is applied).
    ``cap == 0`` claims nothing (claim-suppression only — the non-claim decisions
    already ran). Candidates are claimed in id order (deterministic).
    """
    budget: int = config.cap - len(inflight)
    for view in claimable:
        if budget <= 0:
            break
        attempt: int = _next_attempt(view.status)
        if attempt > config.max_attempts:
            _escalate_at_claim(seams, config, view, attempt)
            continue
        if _claim_and_launch(seams, config, view, attempt):
            budget -= 1


def _next_attempt(status: FleetStatus | None) -> int:
    """1 for a first claim; the previous attempt + 1 when a prior status exists.

    The engine emits ``SignalClaimable`` without an attempt (claimability is
    attempt-blind); the orchestrator owns the retry numbering, so a slice with a
    reaped prior attempt (its status records that attempt) claims ``attempt + 1``
    from a fresh worktree — the legacy claim pass's accounting.
    """
    return 1 if status is None else status.attempt + 1


def _escalate_at_claim(
    seams: TickSeams, config: SquadraConfig, view: SliceView, attempt: int
) -> None:
    """Escalate a claimable slice whose next attempt would exceed the cap.

    A defensive parity backstop for the legacy claim pass: in the engine-driven
    tick a reap escalates at retry-time (tagging ``failed``, which makes the item
    terminal and never claimable), so this fires only for a manually-requeued
    over-cap slice — it must never silently claim past the budget.
    """
    seams.ado.add_tag(view.item.item_id, config.tags.failed)
    seams.ado.add_comment(view.item.item_id, Escalated(attempt=attempt, cap=config.max_attempts))


def _claim_and_launch(
    seams: TickSeams, config: SquadraConfig, view: SliceView, attempt: int
) -> bool:
    """Run the claim protocol for one slice; roll back if create/launch fails.

    Protocol parity with the legacy claim pass plus the new commit-only setup: the
    supervisor creates the slice worktree off fresh ``origin/main`` host-side and
    injects the read-only slice context *before* the launch (ADR-0002 §§1–2), then
    moves the board (state→active, claimed tag, ``Claimed`` comment, claimed-at
    marker) and launches the sandbox. A failed worktree-create or launch rolls the
    claim back to QUEUED.
    """
    item: WorkItem = view.item
    branch: str = slice_branch(item.item_id, item.title, attempt, config.branch_template)
    spec: SandboxSpec = _spec_for(item.item_id, branch, config)
    now: str = _utcnow_iso()
    runner_id: str = f"runner-{item.item_id}-a{attempt}"

    if not seams.worktree.create(
        branch, str(spec.worktree), f"origin/{config.base_branch}"
    ).created:
        _log(f"claim: worktree create failed for #{item.item_id} ({branch}); not claimed")
        return False
    seams.write_context(spec.worktree, _slice_context(seams, item))

    seams.ado.set_state(item.item_id, Lifecycle.ACTIVE)
    seams.ado.add_tag(item.item_id, config.tags.claimed)
    seams.ado.add_comment(item.item_id, Claimed(runner_id=runner_id, branch=branch, when=now))
    seams.write_claimed_at(item.item_id, config.fleet_root, now)
    if seams.sandbox.launch(spec):
        return True
    seams.ado.remove_tag(item.item_id, config.tags.claimed)
    seams.ado.set_state(item.item_id, Lifecycle.QUEUED)
    seams.ado.add_comment(item.item_id, RolledBack(reason="sandbox launch failed"))
    return False


def _slice_context(seams: TickSeams, item: WorkItem) -> SliceContext:
    """Read the slice's Issue + Tasks + predecessor states for the agent's context.

    The contained agent has no board, so the supervisor reads what the agent would
    have queried and injects it as ``slice.json``. The Tasks listing is left empty
    here — the board seam exposes links, not per-Task detail; G2's runner consumes
    the Issue + predecessor states, and the Task detail is a later seam extension.
    """
    links: WorkItemLinks = seams.ado.item_links(item.item_id)
    predecessor_states: dict[int, str] = {
        predecessor: seams.ado.item_state(predecessor).value
        for predecessor in links.predecessor_ids
    }
    return SliceContext(
        issue_id=item.item_id,
        title=item.title,
        tasks=(),
        predecessor_states=predecessor_states,
    )


# --- claim-work gating for the auth probe (claim-only, ADR-0002 decision 4) ----


def _claim_work_pending(views: Sequence[SliceView], config: SquadraConfig) -> bool:
    """Whether this tick will try to claim+launch — the only claude-dependent work.

    Finalize cleanup is deterministic now, so only a claim within budget pays for
    the auth probe. Idle and saturated ticks never probe (budget <= 0 ⇒ no claim).
    """
    inflight: int = sum(1 for v in views if _is_inflight(v, config.tags))
    if config.cap - inflight <= 0:
        return False
    return any(_is_claim(v) for v in views)


def _is_claim(view: SliceView) -> bool:
    """Whether a slice's decision is to claim (emits ``SignalClaimable``)."""
    return any(isinstance(action, SignalClaimable) for action in view.decision.actions)


def _is_inflight(view: SliceView, tags: Tags) -> bool:
    """Whether a slice counts against the claim budget (fleet-claimed + ACTIVE)."""
    return view.lifecycle is Lifecycle.ACTIVE and tags.claimed in view.item.tags


def _spec(view: SliceView, config: SquadraConfig) -> SandboxSpec:
    """Build the sandbox spec for a slice's current branch."""
    return _spec_for(view.item.item_id, view.branch(config), config)


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 form (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(message: str) -> None:
    """Emit one timestamped log line (fleet-tick.sh appends stdout to the log)."""
    print(f"[{_utcnow_iso()}] supervisor: {message}")


# --- dry-run boundary ----------------------------------------------------------


class ReadOnlyBoard:
    """``BoardAccess`` decorator that physically cannot write to the board.

    Reads delegate to the wrapped client; every write logs the action a real tick
    WOULD have performed and does nothing. Dry-run safety is this boundary, not a
    flag threaded through the tick — an execution path (present or future) that
    reaches ``seams.ado`` with a write cannot mutate while dry-run is active.
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


def _dry_run_pat_ok() -> bool:
    """Skip the PAT preflight — a ``git ls-remote`` probe is itself a network side effect."""
    _log("[dry-run] WOULD run the ADO PAT auth preflight; assuming it passes")
    return True


def _dry_run_auth_ok() -> bool:
    """Skip the auth preflight — a spawned ``claude -p`` probe is itself a side effect."""
    _log("[dry-run] WOULD run the claude auth preflight; assuming it passes")
    return True


def _dry_run_write_context(worktree: Path, _context: object) -> Path:
    """Absorb the slice-context injection, logging it; return the would-be path."""
    from squadra.manifest import slice_context_path  # noqa: PLC0415

    path: Path = slice_context_path(worktree)
    _log(f"[dry-run] WOULD inject slice context at {path}")
    return path


def _dry_run_update_status(item_id: int, _changes: StatusUpdate, _fleet_root: Path) -> None:
    """Absorb the status-file write, logging it."""
    _log(f"[dry-run] WOULD update the status file of #{item_id}")


def _dry_run_write_claimed_at(item_id: int, _fleet_root: Path, _timestamp: str) -> None:
    """Absorb the claimed-at marker write, logging it."""
    _log(f"[dry-run] WOULD write the claimed-at marker of #{item_id}")


def dry_run_seams(seams: TickSeams) -> TickSeams:
    """Wrap every mutating seam so the tick cannot mutate anything.

    Reads pass through — the tick still gathers facts and decides, reporting the
    would-be actions — but every write (board, sandbox launch/teardown/exec,
    deterministic cleanup, worktree create/archive/prune, the PAT + claude auth
    probes, slice-context injection, local status/marker files) becomes a logged
    ``[dry-run] WOULD …`` no-op. The fact-gathering read seams (``sandbox.inspect``
    / ``logs``, ``read_manifest``, ``commits_present``) stay real: they are pure
    reads and the plan is meaningless without them. The tick lock and supervisor
    log are still written — coordination artifacts, not fleet state.
    """
    return replace(
        seams,
        ado=ReadOnlyBoard(seams.ado),
        sandbox=DryRunSandbox(seams.sandbox),
        cleanup=DryRunCleanup(seams.cleanup),
        worktree=DryRunWorktree(seams.worktree),
        pat_ok=_dry_run_pat_ok,
        auth_ok=_dry_run_auth_ok,
        write_context=_dry_run_write_context,
        update_status=_dry_run_update_status,
        write_claimed_at=_dry_run_write_claimed_at,
    )


def build_seams(config: SquadraConfig, *, dry_run: bool = False) -> TickSeams:
    """Build the production seams; with ``dry_run``, wrap them so nothing can mutate."""
    seams = TickSeams(
        ado=build_board(config),
        sandbox=ComposeSandbox(),
        cleanup=DeterministicCleanup(config.fleet_home),
        worktree=GitWorktreeAccess(config.fleet_home),
        pat_ok=lambda: _ado_pat_ok(config.fleet_home),
    )
    if not dry_run:
        return seams
    _log(
        "DRY-RUN tick: reads and planning only — every board write, sandbox launch/"
        "teardown, deterministic cleanup, worktree change, PAT + claude probe, slice-context "
        "injection, and local fleet-state write is suppressed and logged as '[dry-run] WOULD …'"
    )
    return dry_run_seams(seams)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supervisor tick against the configured board and sandbox substrate."""
    parser = argparse.ArgumentParser(
        prog="squadra-supervisor",
        description="One AFK-fleet supervisor tick (ADR-0002 / ADR-0001).",
    )
    parser.add_argument("--fleet-root", type=Path, default=None)
    parser.add_argument("--fleet-home", type=Path, default=None)
    parser.add_argument("--provider", default=None, help="override the board provider")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full tick fact-gather + plan logic but suppress every side "
        "effect (board writes, sandbox launch/teardown, deterministic cleanup, "
        "worktree changes, claude spawns, local fleet-state writes); "
        "FLEET_DRY_RUN=1 is equivalent",
    )
    args: argparse.Namespace = parser.parse_args(argv)
    config: SquadraConfig = load_config(
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
        print(f"supervisor: board call failed: {exc} {stderr}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
