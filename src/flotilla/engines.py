"""Pure decision functions for the supervisor passes.

Data in, decision out — no I/O, no board calls. Branch naming and the reap
pass's park / failed-park eligibility predicates live here; the orchestration
and I/O that consume them stay in ``supervisor``.

:class:`LifecycleEngine` is the subsuming, derived-state FSM (ADR-0002 decision
3): one pure ``decide(facts) -> LifecycleDecision`` that projects a slice's
observed reality onto a :class:`~flotilla.domain.State` plus the orchestrator
intents to execute. It folds the legacy :func:`is_parked` / :func:`is_failed_park`
predicates into its fact-derivation as derived states; those free functions are
kept unchanged because the live tick still consumes them until the orchestration
cutover (F4). Nothing here is wired into the live tick yet.
"""

import re

from flotilla.config import DEFAULT_BRANCH_TEMPLATE
from flotilla.domain import (
    AwaitAgent,
    EscalateEgressDenied,
    EscalateExhausted,
    FailureEdge,
    FinalizeCleanup,
    HandoffAgentDone,
    Lifecycle,
    LifecycleDecision,
    LifecycleFacts,
    NoAction,
    ParkNeedsDecision,
    RetrySlice,
    SignalClaimable,
    State,
    StopContainer,
    SweepLeak,
    Tags,
    WorkItem,
)
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


# --- LifecycleEngine: the derived-state FSM (ADR-0002 decision 3) -------------


class LifecycleEngine:
    """A pure, zero-I/O, derived-state FSM over one slice's observed facts.

    ``decide(facts)`` projects :class:`~flotilla.domain.LifecycleFacts` onto
    exactly one :class:`~flotilla.domain.State` and the ordered, closed set of
    :data:`~flotilla.domain.LifecycleAction` intents the orchestrator must run.
    The engine performs **no I/O** and holds **no state** between calls — the
    same facts always yield the same decision, which is what preserves the
    crash-only idempotence of the passes this engine subsumes (the state is a
    projection of board/container reality, never an independently persisted
    source of truth; ADR-0002 decision 3).

    ``decide`` is **total**: every combination of facts yields a decision and
    none raises. Guards are evaluated in a fixed priority order — already-decided
    board tags first (escalated / decision parks are terminal), then the done
    bucket (finalize vs. await PR), then the in-flight classification (the
    completion triple, the failure edges, liveness), then the queued bucket
    (claimable vs. blocked). An orthogonal failed-teardown leak is appended to
    whatever the primary lifecycle decision was, since a leak never blocks the
    slice's board lifecycle (ADR-0002 decision 6).

    This engine folds the legacy :func:`is_parked` / :func:`is_failed_park`
    predicates into its fact-derivation: a deliberate park (a parked tag, a
    ``phase=done`` status, or a ``phase=parked`` status whose ``parked_state`` is
    not ``failed``) is a quiescent in-flight state, while a failed park
    (``phase=parked`` + ``parked_state=failed``) is positive failure evidence
    that classifies as an agent crash.
    """

    def decide(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Return the slice's derived state and the orchestrator intents to run.

        Total over the fact space (no input raises). The orthogonal teardown
        leak is layered on top of the primary lifecycle decision so a leak is
        swept without blocking the slice's lifecycle.
        """
        primary: LifecycleDecision = self._classify(facts)
        if facts.teardown_failed:
            return LifecycleDecision(
                state=primary.state,
                actions=(*primary.actions, SweepLeak()),
            )
        return primary

    def _classify(self, facts: LifecycleFacts) -> LifecycleDecision:  # noqa: PLR0911 - guard ladder
        """Derive the primary state + actions, ignoring the orthogonal leak sweep."""
        # 1. Already-escalated / already-decided board tags are terminal. A
        #    tagged failed item is escalated and never auto-retried; a tagged
        #    needs-decision item is parked for a human. These dominate every
        #    other fact so a terminal item is never re-driven.
        if facts.failed_tagged:
            return _terminal(State.ESCALATED)
        if facts.needs_decision_tagged:
            return _terminal(State.PARKED_DECISION)

        # 2. The done bucket: a fleet-claimed slice whose PR completed finalizes
        #    (deterministic cleanup); otherwise it parks awaiting the merge. A
        #    done item the fleet never claimed is a human's — invisible/terminal.
        if facts.lifecycle is Lifecycle.DONE:
            return self._classify_done(facts)

        # 3. The active bucket: an in-flight, fleet-claimed slice is classified
        #    by its container / manifest / liveness / failure-edge facts. A
        #    human's active item (unclaimed) is invisible to the fleet.
        if facts.lifecycle is Lifecycle.ACTIVE:
            if not facts.is_fleet_claimed:
                return _terminal(State.RUNNING)
            return self._classify_inflight(facts)

        # 4. The queued bucket: claimable when unblocked, else blocked.
        if facts.predecessors_done:
            return LifecycleDecision(state=State.CLAIMABLE, actions=(SignalClaimable(),))
        return _terminal(State.BLOCKED)

    def _classify_done(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Classify a slice in the done bucket (finalize vs. await PR vs. terminal)."""
        if not facts.is_fleet_claimed:
            return _terminal(State.DONE)
        if facts.completed_pr_url is not None:
            return LifecycleDecision(
                state=State.FINALIZING,
                actions=(FinalizeCleanup(pr_url=facts.completed_pr_url),),
            )
        return _terminal(State.AWAITING_PR)

    def _classify_inflight(  # noqa: PLR0911 - the in-flight guard ladder
        self, facts: LifecycleFacts
    ) -> LifecycleDecision:
        """Classify a fleet-claimed, in-flight slice from container/manifest facts.

        Guard order (each returns early, keeping the function total):

        1. egress-denied — a security signal; escalate immediately.
        2. a deliberate park — quiescent (awaiting decision / QA / PR approval).
        3. build-failed — a retryable failure edge.
        4. container exited — the completion triple decides done / decision /
           crash; a missing-or-invalid manifest or no commits is a crash.
        5. container running — stale heartbeat is a timeout; otherwise running.
        6. container absent — a failed-park status is crash evidence; otherwise
           the runner is still provisioning.
        """
        # 1. Egress denial is a security signal: escalate immediately, naming the
        #    host, regardless of any other in-flight fact (never retried).
        if facts.egress_denied_host is not None:
            return LifecycleDecision(
                state=State.ESCALATED,
                actions=(EscalateEgressDenied(denied_host=facts.egress_denied_host),),
            )

        # 2. A deliberate park (folded from is_parked): a parked tag, a finalized
        #    status (phase done), or a parked status whose parked_state is not
        #    failed. Quiescent — never reaped, awaiting its human/PR signal.
        if _is_deliberate_park(facts):
            return _terminal(State.AWAITING_PR)

        # 3. A failed image build is a retryable failure edge.
        if facts.build_failed:
            return self._retry_or_escalate(facts, FailureEdge.BUILD_FAILED)

        # 4. The container exited: the (exit, manifest, commits) completion triple.
        if facts.container_present and not facts.container_running:
            return self._classify_exited(facts)

        # 5. The container is running: stale heartbeat → timeout; else running.
        if facts.container_present and facts.container_running:
            if facts.heartbeat_stale:
                return self._timeout(facts)
            return LifecycleDecision(state=State.RUNNING, actions=(AwaitAgent(),))

        # 6. No container yet. A failed-park status is positive crash evidence
        #    (folded from is_failed_park); otherwise the runner is provisioning.
        if _is_failed_park(facts):
            return self._retry_or_escalate(facts, FailureEdge.AGENT_CRASH)
        return _terminal(State.PROVISIONING)

    def _classify_exited(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Apply the completion triple to an exited container (done/decision/crash).

        Clean completion requires exit 0 **and** a present-and-valid manifest
        **and** commits. ``needs-decision`` in the manifest routes to the
        decision park (no PR). Anything else — a non-zero exit, a missing or
        malformed manifest, or no commits — is an agent crash.
        """
        clean_exit: bool = facts.container_exit_code == 0
        if clean_exit and facts.manifest_present and facts.manifest_valid and facts.commits_present:
            if facts.manifest_needs_decision:
                return LifecycleDecision(state=State.AGENT_DECISION, actions=(ParkNeedsDecision(),))
            return LifecycleDecision(state=State.AGENT_DONE, actions=(HandoffAgentDone(),))
        return self._retry_or_escalate(facts, FailureEdge.AGENT_CRASH)

    def _timeout(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Stop a hung-but-alive container, then retry-or-escalate the timeout.

        The state is :attr:`~flotilla.domain.State.AGENT_TIMEOUT`; the actions
        stop the container first, then carry the same retry/escalate decision the
        timeout edge resolves to (so the orchestrator both reclaims the resources
        and advances the attempt budget in one tick).
        """
        followup: LifecycleDecision = self._retry_or_escalate(facts, FailureEdge.AGENT_TIMEOUT)
        return LifecycleDecision(
            state=State.AGENT_TIMEOUT,
            actions=(StopContainer(), *followup.actions),
        )

    def _retry_or_escalate(self, facts: LifecycleFacts, edge: FailureEdge) -> LifecycleDecision:
        """Resolve a retryable failure edge against the attempt budget.

        Attempt accounting is expressed *as transitions*, not side-channel
        bookkeeping: the just-failed ``attempt`` is retried while
        ``attempt < max_attempts`` (the next claim runs ``attempt + 1``);
        on exhaustion (``attempt >= max_attempts``) the slice escalates with the
        ``attempt + 1`` it would have reached, mirroring the legacy reap pass.
        """
        if facts.attempt >= facts.max_attempts:
            return LifecycleDecision(
                state=State.ESCALATED,
                actions=(
                    EscalateExhausted(edge=edge, attempt=facts.attempt + 1, cap=facts.max_attempts),
                ),
            )
        return LifecycleDecision(
            state=State.AGENT_FAILED,
            actions=(RetrySlice(edge=edge, attempt=facts.attempt),),
        )


def _terminal(state: State) -> LifecycleDecision:
    """Build a decision for a state with nothing for the orchestrator to do."""
    return LifecycleDecision(state=state, actions=(NoAction(),))


def _is_deliberate_park(facts: LifecycleFacts) -> bool:
    """Whether the slice is deliberately parked (folds the legacy ``is_parked``).

    True when the item carries any parked tag, when its status phase is ``done``
    (a finalized slice is never requeued), or when its status phase is ``parked``
    with a ``parked_state`` other than ``failed`` (a deliberate stop awaiting a
    human/PR signal). A ``parked_state=failed`` status is *not* a deliberate park
    — it is positive failure evidence (see :func:`_is_failed_park`).
    """
    if facts.parked_tagged:
        return True
    if facts.phase == "done":
        return True
    return facts.phase == "parked" and facts.parked_state != "failed"


def _is_failed_park(facts: LifecycleFacts) -> bool:
    """Whether the status records a failed park (folds the legacy ``is_failed_park``).

    A ``phase=parked`` + ``parked_state=failed`` status is positive failure
    evidence (a crash, OOM, dead auth, or unhandled runner error) — reap-eligible,
    not a deliberate stop.
    """
    return facts.phase == "parked" and facts.parked_state == "failed"
