"""Pure decision functions for the supervisor tick.

Data in, decision out — no I/O, no board calls. Branch naming, the
:class:`~squadra.domain.Lifecycle` transition rule and the subsuming
derived-state FSM live here; the orchestration and I/O that consume them stay in
``supervisor``.

:class:`LifecycleEngine` is the subsuming, derived-state FSM (ADR-0002 decision
3): one pure ``decide(facts) -> LifecycleDecision`` that projects an increment's
observed reality onto a :class:`~squadra.domain.State` plus the orchestrator
intents to execute. It subsumes the legacy finalize / reap / claim passes — and
the old ``is_parked`` / ``is_failed_park`` predicates they used — by folding the
deliberate-park / failed-park distinction directly into its fact-derivation
(:func:`_is_deliberate_park` / :func:`_is_failed_park`). The F4 cutover wired this
engine into the live tick (``squadra.supervisor.run_tick``).
"""

from collections.abc import Sequence
import re

from squadra.config import DEFAULT_BRANCH_TEMPLATE, ClaimScope
from squadra.domain import (
    AwaitAgent,
    EscalateEgressDenied,
    EscalateExhausted,
    FailureEdge,
    FinalizeCleanup,
    HandoffAgentDone,
    Increment,
    IncrementRequest,
    Lifecycle,
    LifecycleDecision,
    LifecycleFacts,
    NoAction,
    OriginRecord,
    ParkNeedsDecision,
    RetryIncrement,
    SignalClaimable,
    State,
    StopContainer,
    SweepLeak,
)


def increment_branch(
    item_id: int, title: str, attempt: int, template: str = DEFAULT_BRANCH_TEMPLATE
) -> str:
    """Derive the increment's branch name from ``template`` plus the retry suffix.

    The slug is the text after the first ``":"`` (else the whole title),
    lowercased, with runs of non-``[a-z0-9]`` collapsed to ``"-"``, capped at 32
    chars and stripped of leading/trailing ``"-"`` (``"increment"`` when empty). The
    template owns the ``{id}``/``{slug}`` layout; squadra owns the retry rule —
    a ``-a{attempt}`` suffix is appended only when ``attempt > 1``.
    """
    base: str = title.split(":", 1)[1] if ":" in title else title
    slug: str = re.sub(r"[^a-z0-9]+", "-", base.lower())[:32].strip("-")
    if not slug:
        slug = "increment"
    suffix: str = f"-a{attempt}" if attempt > 1 else ""
    return f"{template.format(id=item_id, slug=slug)}{suffix}"


class TransitionRefusedError(ValueError):
    """Raised for a :class:`~squadra.domain.Lifecycle` transition the model forbids."""


def check_transition(current: Lifecycle, target: Lifecycle) -> None:
    """Refuse a lifecycle transition that the four-bucket model does not allow.

    Only the edges touching ``WITHDRAWN`` are constrained (ADR-0004); every
    transition among QUEUED, ACTIVE and DONE stays permitted, as before:

    - QUEUED → WITHDRAWN is allowed: the increment was never started.
    - DONE → WITHDRAWN is refused: a delivered increment cannot be undelivered.
    - ACTIVE → WITHDRAWN is refused: squadra has no cancel path for a claimed,
      in-flight increment (claude-skills DB-D2). Let the attempt finish, or stop
      it by hand, then withdraw or re-plan.
    - WITHDRAWN is terminal: no transition leaves it. Withdrawing a withdrawn
      increment again is a no-op, so WITHDRAWN → WITHDRAWN is allowed.
    """
    if current is Lifecycle.WITHDRAWN and target is not Lifecycle.WITHDRAWN:
        raise TransitionRefusedError(
            f"cannot move a withdrawn increment to {target.value}: withdrawn is terminal"
        )
    if target is not Lifecycle.WITHDRAWN:
        return
    if current is Lifecycle.DONE:
        raise TransitionRefusedError("cannot withdraw a done increment: it was delivered")
    if current is Lifecycle.ACTIVE:
        raise TransitionRefusedError(
            "cannot withdraw an active increment: it is claimed and in flight; let the "
            "attempt finish (or stop it by hand), then re-plan"
        )


# --- the increment verbs' rules (squadra.increments applies them) -------------


class QueueRefusedError(ValueError):
    """Raised when ``queue_increment`` refuses its arguments; nothing was written."""


class ClaimScopeRefusedError(ValueError):
    """Raised when a verb would write to an item outside the claim scope."""


class DuplicateOriginError(ValueError):
    """Raised when two board items carry one Origin (claude-skills DB-D4, A2)."""


def parent_in_claim_scope(
    parent: int | None, claim_scope: ClaimScope, parent_scope_ids: tuple[int, ...]
) -> bool:
    """Whether an item under ``parent`` is inside ``[board].claim_scope``.

    The one definition of claim scope: the tick's claim gate, the increment
    query and the increment writes all use it, so they cannot disagree.
    """
    return claim_scope is ClaimScope.WHOLE_BOARD or parent in parent_scope_ids


def index_by_origin(records: Sequence[OriginRecord]) -> dict[str, OriginRecord]:
    """Key ``records`` by Origin; raise on a duplicate, naming both items.

    A dict cannot hold two items with one Origin, so keying silently would
    drop one. Only squadra sees the unkeyed records, so the check lives here.
    """
    by_origin: dict[str, OriginRecord] = {}
    for record in records:
        seen: OriginRecord | None = by_origin.get(record.origin)
        if seen is not None:
            raise DuplicateOriginError(
                f"origin {record.origin!r} is carried by items {seen.item_id} and "
                f"{record.item_id}; an Origin is unique on the board"
            )
        by_origin[record.origin] = record
    return by_origin


def as_increment(
    record: OriginRecord, claim_scope: ClaimScope, parent_scope_ids: tuple[int, ...]
) -> Increment:
    """Project one origin record onto what ``increments_by_origin`` reports."""
    return Increment(
        item_id=record.item_id,
        parent=record.parent,
        lifecycle=record.lifecycle,
        in_claim_scope=parent_in_claim_scope(record.parent, claim_scope, parent_scope_ids),
    )


def check_queue_matches(existing: OriginRecord, request: IncrementRequest) -> None:
    """Refuse a ``queue_increment`` retry whose arguments differ from the item on the board.

    claude-skills DB-D4 (A3): an Origin already on the board returns its item
    only when every argument matches; any difference names the fields and
    writes nothing, so squadra never quietly re-parents or re-links. A
    withdrawn Origin is refused too: an Origin is never reused.
    Predecessors compare as a set, since a board keeps no order of its links.
    """
    if existing.lifecycle is Lifecycle.WITHDRAWN:
        raise QueueRefusedError(
            f"origin {request.origin!r} was withdrawn (item {existing.item_id}); "
            "an Origin is never reused"
        )
    differs: list[str] = []
    if existing.parent != request.parent:
        differs.append(f"parent {existing.parent} != {request.parent}")
    if set(existing.predecessors) != set(request.predecessors):
        differs.append(
            f"predecessors {sorted(existing.predecessors)} != {sorted(request.predecessors)}"
        )
    if existing.title != request.title:
        differs.append(f"title {existing.title!r} != {request.title!r}")
    if existing.body != request.body:
        differs.append("body differs")
    if differs:
        raise QueueRefusedError(
            f"origin {request.origin!r} is already item {existing.item_id} with different "
            f"arguments: {'; '.join(differs)}"
        )


# --- LifecycleEngine: the derived-state FSM (ADR-0002 decision 3) -------------


class LifecycleEngine:
    """A pure, zero-I/O, derived-state FSM over one increment's observed facts.

    ``decide(facts)`` projects :class:`~squadra.domain.LifecycleFacts` onto
    exactly one :class:`~squadra.domain.State` and the ordered, closed set of
    :data:`~squadra.domain.LifecycleAction` intents the orchestrator must run.
    The engine performs **no I/O** and holds **no state** between calls — the
    same facts always yield the same decision, which is what preserves the
    crash-only idempotence of the passes this engine subsumes (the state is a
    projection of board/container reality, never an independently persisted
    source of truth; ADR-0002 decision 3).

    ``decide`` is **total**: every combination of facts yields a decision and
    none raises. Guards are evaluated in a fixed priority order — already-decided
    board tags first (escalated / decision parks are terminal), then the
    withdrawn bucket (terminal), then the done bucket (finalize vs. await PR),
    then the in-flight classification (the completion triple, the failure edges,
    liveness), then the queued bucket (out of scope, then a withdrawn
    predecessor, then claimable vs. blocked). An orthogonal failed-teardown leak is appended to
    whatever the primary lifecycle decision was, since a leak never blocks the
    increment's board lifecycle (ADR-0002 decision 6).

    This engine folds the legacy ``is_parked`` / ``is_failed_park`` predicates
    (now retired, ADR-0002 decision 3) into its fact-derivation
    (:func:`_is_deliberate_park` / :func:`_is_failed_park`): a deliberate park (a parked tag, a
    ``phase=done`` status, or a ``phase=parked`` status whose ``parked_state`` is
    not ``failed``) is a quiescent in-flight state, while a failed park
    (``phase=parked`` + ``parked_state=failed``) is positive failure evidence
    that classifies as an agent crash.
    """

    def decide(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Return the increment's derived state and the orchestrator intents to run.

        Total over the fact space (no input raises). The orthogonal teardown
        leak is layered on top of the primary lifecycle decision so a leak is
        swept without blocking the increment's lifecycle.
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

        # 2. The withdrawn bucket is terminal: never claimed, never finalized,
        #    whatever else is true of it.
        if facts.lifecycle is Lifecycle.WITHDRAWN:
            return _terminal(State.WITHDRAWN)

        # 3. The done bucket: a fleet-claimed increment whose PR completed finalizes
        #    (deterministic cleanup); otherwise it parks awaiting the merge. A
        #    done item the fleet never claimed is a human's — invisible/terminal.
        if facts.lifecycle is Lifecycle.DONE:
            return self._classify_done(facts)

        # 4. The active bucket: an in-flight, fleet-claimed increment is classified
        #    by its container / manifest / liveness / failure-edge facts. A
        #    human's active item (unclaimed) is invisible to the fleet.
        if facts.lifecycle is Lifecycle.ACTIVE:
            if not facts.is_fleet_claimed:
                return _terminal(State.RUNNING)
            return self._classify_inflight(facts)

        # 5. The queued bucket, out of the declared claim scope: not the fleet's,
        #    whatever its predecessors. Gated on the queued bucket only (steps 2-4
        #    returned already), so an in-flight item reparented out of
        #    scope still finalizes or reaps instead of leaking.
        if not facts.in_claim_scope:
            return _terminal(State.OUT_OF_SCOPE)

        # 6. The queued bucket, behind a withdrawn predecessor: blocked for good,
        #    reported apart from an in-flight block because no tick can clear it.
        if facts.predecessor_withdrawn:
            return _terminal(State.PREDECESSOR_WITHDRAWN)

        # 7. The queued bucket: claimable when unblocked, else blocked.
        if facts.predecessors_done:
            return LifecycleDecision(state=State.CLAIMABLE, actions=(SignalClaimable(),))
        return _terminal(State.BLOCKED)

    def _classify_done(self, facts: LifecycleFacts) -> LifecycleDecision:
        """Classify an increment in the done bucket (finalize vs. await PR vs. terminal)."""
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
        """Classify a fleet-claimed, in-flight increment from container/manifest facts.

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

        The state is :attr:`~squadra.domain.State.AGENT_TIMEOUT`; the actions
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
        on exhaustion (``attempt >= max_attempts``) the increment escalates with the
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
            actions=(RetryIncrement(edge=edge, attempt=facts.attempt),),
        )


def _terminal(state: State) -> LifecycleDecision:
    """Build a decision for a state with nothing for the orchestrator to do."""
    return LifecycleDecision(state=state, actions=(NoAction(),))


def _is_deliberate_park(facts: LifecycleFacts) -> bool:
    """Whether the increment is deliberately parked (folds the legacy ``is_parked``).

    True when the item carries any parked tag, when its status phase is ``done``
    (a finalized increment is never requeued), or when its status phase is ``parked``
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
