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
from pathlib import Path

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


# --- LifecycleEngine: derived-state FSM vocabulary (ADR-0002 decision 3) ------
#
# The :class:`LifecycleEngine` (in :mod:`flotilla.engines`) is a pure, zero-I/O,
# *derived-state* FSM: each tick it projects one slice's observed reality
# (:class:`LifecycleFacts`) onto exactly one :class:`State` and the closed set of
# :data:`LifecycleAction` intents the orchestrator must execute. ``State`` is
# never persisted — it is recomputed from facts every tick, which preserves the
# crash-only idempotence of the original finalize/reap/claim passes (no drift
# between a stored state and board/container reality). These types are the FSM's
# vocabulary; the engine that consumes them is wired into the live tick in a
# later slice (F4), not here.


class State(Enum):
    """The derived lifecycle state of one slice, projected from observed facts.

    Subsumes the implicit states of today's finalize/reap/claim passes plus the
    new contained-runner states. A *projection of reality each tick*, never an
    independently persisted source of truth (ADR-0002 decision 3):

    - :attr:`BLOCKED` — predecessors not all done; not yet claimable.
    - :attr:`CLAIMABLE` — unblocked, unclaimed, queued; the engine emits the
      per-slice signal and the orchestrator claims up to the cross-slice cap.
    - :attr:`PROVISIONING` — claimed, runner not yet observed running (no
      container / no liveness evidence yet).
    - :attr:`RUNNING` — the contained agent is alive and heartbeating.
    - :attr:`AGENT_DONE` — clean handoff: container exited 0, a valid manifest is
      present with a non-``needs-decision`` park, and commits exist. Triggers the
      push + PR + QA + park + teardown handoff the agent used to do itself.
    - :attr:`AGENT_DECISION` — exited 0 with a valid ``needs-decision`` manifest:
      push the WIP, open no PR, tag needs-decision (a dependency-change park).
    - :attr:`AGENT_FAILED` — a retry-eligible failure edge fired (build-failed,
      agent-crash, or a stopped timed-out container) with retries remaining.
    - :attr:`AGENT_TIMEOUT` — the container is still alive but its heartbeat is
      stale; the agent is stopped (``docker stop``) before being treated as
      failed.
    - :attr:`AWAITING_PR` — the slice handed off and now waits for its PR to
      complete (a deliberate park, never reaped).
    - :attr:`FINALIZING` — the slice's PR has completed; deterministic cleanup
      (branch delete + worktree prune + ``compose down -v``) runs.
    - :attr:`DONE` — terminal: finalized and retired.
    - :attr:`ESCALATED` — terminal: retries exhausted, or an immediate-escalation
      edge (egress-denied) fired; tagged failed for a human.
    - :attr:`PARKED_DECISION` — terminal-for-the-fleet: parked awaiting a human
      decision (the ``needs-decision`` park persisted on the board).
    """

    BLOCKED = "blocked"
    CLAIMABLE = "claimable"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    AGENT_DONE = "agent-done"
    AGENT_DECISION = "agent-decision"
    AGENT_FAILED = "agent-failed"
    AGENT_TIMEOUT = "agent-timeout"
    AWAITING_PR = "awaiting-pr"
    FINALIZING = "finalizing"
    DONE = "done"
    ESCALATED = "escalated"
    PARKED_DECISION = "parked-decision"


class FailureEdge(Enum):
    """A classified failure of a contained run, each with a distinct policy.

    The classification is the security-sensitive heart of the FSM (ADR-0002
    decision 6 / plan §7). Three edges are *transient* and retry under the
    attempt budget; one is a *security signal* that escalates immediately; one is
    *orthogonal* and never blocks the slice's board lifecycle:

    - :attr:`BUILD_FAILED` — the sandbox image build failed (often transient: a
      cold pull or a flaky registry). Retry under ``max_attempts``.
    - :attr:`AGENT_CRASH` — the container exited non-zero, or exited zero without
      a present-and-valid manifest (crashed, OOM, or died mid-write). Retry under
      ``max_attempts``.
    - :attr:`AGENT_TIMEOUT` — the container is alive but its heartbeat is stale; a
      hung run. Stopped, then retried under ``max_attempts``.
    - :attr:`EGRESS_DENIED` — the egress proxy refused a ``CONNECT`` to a
      non-allowlisted host. A security signal, likely deterministic — **escalate
      immediately**, naming the denied host; never retried.
    - :attr:`TEARDOWN_FAILED` — ``compose down -v`` left resources behind. An
      orthogonal leak, swept (retried) on later ticks and alerted if persistent;
      it never blocks the slice's board lifecycle.
    """

    BUILD_FAILED = "build-failed"
    AGENT_CRASH = "agent-crash"
    AGENT_TIMEOUT = "agent-timeout"
    EGRESS_DENIED = "egress-denied"
    TEARDOWN_FAILED = "teardown-failed"


# Failure edges that retry under the attempt budget (escalate only on exhaustion)
# vs. the edge that escalates on first sight. ``TEARDOWN_FAILED`` is in neither —
# it is orthogonal to the slice lifecycle and handled as a non-blocking sweep.
RETRYABLE_FAILURE_EDGES: tuple[FailureEdge, ...] = (
    FailureEdge.BUILD_FAILED,
    FailureEdge.AGENT_CRASH,
    FailureEdge.AGENT_TIMEOUT,
)
IMMEDIATE_ESCALATION_EDGES: tuple[FailureEdge, ...] = (FailureEdge.EGRESS_DENIED,)


@dataclass(frozen=True, slots=True)
class LifecycleFacts:  # noqa: PLR0902 - a fact projection is intentionally wide
    """The observed reality of one slice this tick — the FSM's sole input.

    A pure projection assembled host-side by the orchestrator (F4) from board
    truth, the fleet tag set, the slice ``status.json`` breadcrumb, ``docker
    inspect``, the bind-mounted outcome manifest, the commit count in
    ``base..HEAD``, the egress-proxy deny log, and the completed-PR query. The
    engine reads *only* these fields — it performs no I/O and derives ``State``
    solely from them, so the same facts always yield the same decision.

    Fields, grouped by source:

    - ``lifecycle`` / ``is_fleet_claimed`` / ``predecessors_done`` — board truth:
      the neutral bucket, whether the fleet (not a human) claimed it, and whether
      every predecessor slice is done.
    - ``parked_tagged`` / ``failed_tagged`` / ``needs_decision_tagged`` — fleet
      tags already on the item (a deliberate park, an escalation, a decision
      park). ``parked_tagged`` is the prefix-based "carries any parked tag".
    - ``phase`` / ``parked_state`` — the runner's ``status.json`` breadcrumb (a
      *fact*, not authority); ``None`` when no status file exists yet.
    - ``container_present`` / ``container_running`` / ``container_exit_code`` —
      ``docker inspect``: whether the sandbox exists, is running, and (if exited)
      its exit code. ``container_exit_code`` is ``None`` while running or absent.
    - ``heartbeat_stale`` — liveness: the heartbeat is older than the staleness
      threshold (the orchestrator compares timestamps; the engine reads the bool).
    - ``manifest_present`` / ``manifest_valid`` / ``manifest_needs_decision`` —
      the outcome manifest: present at all, schema-valid, and whether it parks
      ``needs-decision``.
    - ``commits_present`` — at least one commit exists in ``base..HEAD`` (the
      substance half of the completion triple).
    - ``completed_pr_url`` — the slice branch's completed-PR url, or ``None``.
    - ``build_failed`` / ``egress_denied_host`` / ``teardown_failed`` — the
      classified failure inputs: an image build that failed, the host named in an
      egress-proxy denial (``None`` when none), and a teardown that left a leak.
    - ``attempt`` / ``max_attempts`` — the attempt-budget bookkeeping the engine
      reads to decide retry-vs-escalate on exhaustion.
    """

    # board truth
    lifecycle: Lifecycle
    is_fleet_claimed: bool
    predecessors_done: bool
    # fleet tags
    parked_tagged: bool
    failed_tagged: bool
    needs_decision_tagged: bool
    # status.json breadcrumb
    phase: str | None
    parked_state: str | None
    # docker inspect
    container_present: bool
    container_running: bool
    container_exit_code: int | None
    # liveness
    heartbeat_stale: bool
    # outcome manifest
    manifest_present: bool
    manifest_valid: bool
    manifest_needs_decision: bool
    # substance
    commits_present: bool
    # PR completion
    completed_pr_url: str | None
    # failure inputs
    build_failed: bool
    egress_denied_host: str | None
    teardown_failed: bool
    # attempt accounting
    attempt: int
    max_attempts: int


# --- LifecycleEngine actions (the closed set of orchestrator intents) ---------
#
# The engine emits *intents*, not effects — it performs no I/O. Each action is a
# frozen record the orchestrator (F4) translates into Access-seam calls. The
# union is closed so a future state cannot smuggle in an un-routed side effect
# (the same discipline ``CommentEvent`` and ``TickSeams`` enforce elsewhere).


@dataclass(frozen=True, slots=True)
class SignalClaimable:
    """Emit the per-slice claimable signal; the orchestrator claims up to the cap."""


@dataclass(frozen=True, slots=True)
class LaunchSandbox:
    """Build + launch the slice's sandbox (the provisioning step)."""


@dataclass(frozen=True, slots=True)
class AwaitAgent:
    """The agent is running and heartbeating — do nothing, observe next tick."""


@dataclass(frozen=True, slots=True)
class HandoffAgentDone:
    """Clean handoff: push, open the PR, run QA, park awaiting-PR, tear down."""


@dataclass(frozen=True, slots=True)
class ParkNeedsDecision:
    """Push the WIP, open no PR, tag/park ``needs-decision`` for a human."""


@dataclass(frozen=True, slots=True)
class StopContainer:
    """Stop the timed-out (hung-but-alive) container before treating it as failed."""


@dataclass(frozen=True, slots=True)
class RetrySlice:
    """Requeue the slice for another attempt (under the attempt budget).

    ``attempt`` is the attempt number that just failed; the next claim runs
    ``attempt + 1`` from a fresh worktree.
    """

    edge: FailureEdge
    attempt: int


@dataclass(frozen=True, slots=True)
class EscalateExhausted:
    """Escalate: the attempt budget is exhausted; tag failed for a human."""

    edge: FailureEdge
    attempt: int
    cap: int


@dataclass(frozen=True, slots=True)
class EscalateEgressDenied:
    """Escalate immediately on the egress-denied security signal, naming the host."""

    denied_host: str


@dataclass(frozen=True, slots=True)
class SweepLeak:
    """Non-blocking leak sweep for a failed teardown (retried on later ticks)."""


@dataclass(frozen=True, slots=True)
class FinalizeCleanup:
    """The PR completed — delete the branch, prune the worktree, compose down -v."""

    pr_url: str


@dataclass(frozen=True, slots=True)
class NoAction:
    """A terminal or wait state with nothing for the orchestrator to do."""


# The closed set of intents the engine can emit. The orchestrator pattern-matches
# this union to drive the Access seams; adding a state that needs a new effect
# means adding a member here (and routing it), never an inline side effect.
LifecycleAction = (
    SignalClaimable
    | LaunchSandbox
    | AwaitAgent
    | HandoffAgentDone
    | ParkNeedsDecision
    | StopContainer
    | RetrySlice
    | EscalateExhausted
    | EscalateEgressDenied
    | SweepLeak
    | FinalizeCleanup
    | NoAction
)


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    """The engine's verdict for one slice this tick: a state and its actions.

    ``actions`` is ordered — the orchestrator executes them in sequence (e.g. a
    timed-out slice yields ``(StopContainer, RetrySlice|EscalateExhausted)``, and
    a leaked teardown appends a :class:`SweepLeak` to whatever the primary
    lifecycle decision was). It is never empty: a state with nothing to do emits
    a single :class:`NoAction`.
    """

    state: State
    actions: tuple[LifecycleAction, ...]


# --- Sandbox vocabulary (the SandboxAccess seam — ADR-0002 decisions 1 & 5) ---
#
# The :class:`SandboxAccess` seam (in :mod:`flotilla.sandbox`) runs one slice's
# Claude agent as a per-slice ephemeral Docker compose project. These types are
# the seam's neutral vocabulary: a :class:`SandboxSpec` names the project the
# orchestrator launches; :data:`SandboxStatus` is the observed container state
# the agent-as-command model derives the agent's liveness/exit from; and an
# :class:`ExecResult` is the captured outcome of a one-off ``exec`` into the
# running sandbox. The seam is provider-blind in shape (Docker is the only
# Resource today); none of these carry Docker-native strings.


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """The per-slice ephemeral sandbox a launch targets (a compose project).

    A neutral descriptor, not a Docker artifact: the adapter maps it to its own
    ``compose -p <project> -f <compose_file>`` invocation and bind-mounts
    ``worktree`` as the agent's ``/work``.

    - ``item_id`` — the slice's board work-item id (also the launch attempt's
      identity in logs).
    - ``project`` — the compose project name (the unit ``teardown`` removes
      wholesale; one project per slice attempt keeps concurrent slices isolated).
    - ``compose_file`` — the target-repo-owned ``.flotilla/`` compose file the
      adapter runs (target repos own the compose by convention, ADR-0002 §15).
    - ``worktree`` — the host path bind-mounted as the agent's ``/work`` (the
      supervisor creates it off ``origin/main`` before launch, ADR-0002 §2).
    - ``agent_service`` — the compose service whose command *is* the runner
      (``runner-wrap → claude``); the inspect/exec/logs target.
    """

    item_id: int
    project: str
    compose_file: Path
    worktree: Path
    agent_service: str = "agent"


@dataclass(frozen=True, slots=True)
class SandboxRunning:
    """The sandbox's agent container exists and is currently running."""


@dataclass(frozen=True, slots=True)
class SandboxExited:
    """The sandbox's agent container has exited (the agent-as-command finished).

    ``exit_code`` is the container's ``.State.ExitCode`` — in the agent-as-command
    model it *is* the agent's exit code (ADR-0002 §5): 0 is a clean run, non-zero
    is the agent-crash failure edge.
    """

    exit_code: int


@dataclass(frozen=True, slots=True)
class SandboxAbsent:
    """No agent container exists for the sandbox (never launched, or torn down)."""


# The closed set of observed sandbox states. ``inspect`` returns exactly one; the
# orchestrator (F4) projects it into the ``container_running`` / ``container_exit_code``
# facts the :class:`LifecycleEngine` reads. Modeled as a union (not an enum) so the
# exit code travels with the ``exited`` case — the agent-as-command exit signal.
SandboxStatus = SandboxRunning | SandboxExited | SandboxAbsent


@dataclass(frozen=True, slots=True)
class ExecResult:
    """The captured outcome of one ``exec`` into a running sandbox.

    ``exit_code`` is the exec'd command's exit status (not the container's);
    ``stdout`` is its captured standard output (the attach/inspect convenience
    path — ``flotilla attach`` → ``docker exec``, ADR-0002 §5).
    """

    exit_code: int
    stdout: str
