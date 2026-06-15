"""Unit tests for :class:`flotilla.engines.LifecycleEngine` — the derived-state FSM.

The engine is pure and zero-I/O, so these are plain table-style transition tests:
build a :class:`~flotilla.domain.LifecycleFacts` via the ``make_facts`` factory,
call :meth:`~flotilla.engines.LifecycleEngine.decide`, and assert the derived
:class:`~flotilla.domain.State` and the ordered action intents. The suite is
organized by the F1 behaviors (one section per ADO Task):

- the typed ``State`` enum + the ``decide`` shape (#149)
- state derivation from observed facts (#150)
- folding ``is_parked`` / ``is_failed_park`` into fact-derivation (#151)
- differentiated failure-edge classification (#152)
- attempt / escalation accounting as transitions (#153)
- the totality property: ``decide`` never raises over the fact space (#154)
"""

from collections.abc import Callable
import dataclasses
import itertools
import random
from typing import cast

import pytest

from flotilla.domain import (
    AwaitAgent,
    EscalateEgressDenied,
    EscalateExhausted,
    FailureEdge,
    FinalizeCleanup,
    HandoffAgentDone,
    LaunchSandbox,
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
)
from flotilla.engines import LifecycleEngine

# default_facts, make_facts are provided by tests/conftest.py

# A typed alias for the factory fixture's call signature.
MakeFacts = Callable[..., LifecycleFacts]


@pytest.fixture
def engine() -> LifecycleEngine:
    """The stateless engine under test (no construction args)."""
    return LifecycleEngine()


def _decide(engine: LifecycleEngine, facts: LifecycleFacts) -> LifecycleDecision:
    """Run one decision (a tiny shim that keeps the call sites uniform)."""
    return engine.decide(facts)


# --- #149: State enum + decide shape ------------------------------------------


def test_state_enum_has_the_full_derived_table() -> None:
    # The 13 derived states from ADR-0002 decision 3 / plan §4 — guarding against
    # an accidental rename or drop that would silently change a transition target.
    assert {state.value for state in State} == {
        "blocked",
        "claimable",
        "provisioning",
        "running",
        "agent-done",
        "agent-decision",
        "agent-failed",
        "agent-timeout",
        "awaiting-pr",
        "finalizing",
        "done",
        "escalated",
        "parked-decision",
    }


def test_decide_returns_a_state_and_a_nonempty_action_tuple(
    engine: LifecycleEngine, default_facts: LifecycleFacts
) -> None:
    decision = _decide(engine, default_facts)
    assert isinstance(decision.state, State)
    assert isinstance(decision.actions, tuple)
    assert decision.actions  # never empty — a do-nothing state still emits NoAction


# --- #150: state derivation from observed facts -------------------------------


def test_queued_unblocked_is_claimable(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    facts = make_facts(lifecycle=Lifecycle.QUEUED, is_fleet_claimed=False, predecessors_done=True)
    decision = _decide(engine, facts)
    assert decision.state is State.CLAIMABLE
    assert decision.actions == (SignalClaimable(),)


def test_queued_blocked_is_blocked(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    facts = make_facts(lifecycle=Lifecycle.QUEUED, is_fleet_claimed=False, predecessors_done=False)
    decision = _decide(engine, facts)
    assert decision.state is State.BLOCKED
    assert decision.actions == (NoAction(),)


def test_claimed_no_container_yet_is_provisioning(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(phase="claiming", container_present=False, container_running=False)
    decision = _decide(engine, facts)
    assert decision.state is State.PROVISIONING
    assert decision.actions == (NoAction(),)


def test_running_container_fresh_heartbeat_awaits_the_agent(
    engine: LifecycleEngine, default_facts: LifecycleFacts
) -> None:
    # A live, fresh-heartbeat container is the quiescent observe-state: the
    # engine's intent is to await the agent (no mutation), not nothing-at-all.
    decision = _decide(engine, default_facts)
    assert decision.state is State.RUNNING
    assert decision.actions == (AwaitAgent(),)


def test_unclaimed_active_item_is_invisible_to_the_fleet(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # A human moved an item to Doing without the fleet claiming it — the engine
    # reports RUNNING with nothing to do (the fleet must not touch a human's item,
    # so it is a terminal NoAction, distinct from awaiting a fleet-run agent).
    facts = make_facts(is_fleet_claimed=False)
    decision = _decide(engine, facts)
    assert decision.state is State.RUNNING
    assert decision.actions == (NoAction(),)


def test_agent_done_clean_completion_triple(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    facts = make_facts(
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        manifest_needs_decision=False,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_DONE
    assert decision.actions == (HandoffAgentDone(),)


def test_agent_decision_when_manifest_parks_needs_decision(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        manifest_needs_decision=True,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_DECISION
    assert decision.actions == (ParkNeedsDecision(),)


def test_done_bucket_fleet_claimed_pr_completed_finalizes(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(lifecycle=Lifecycle.DONE, completed_pr_url="https://pr/42")
    decision = _decide(engine, facts)
    assert decision.state is State.FINALIZING
    assert decision.actions == (FinalizeCleanup(pr_url="https://pr/42"),)


def test_done_bucket_fleet_claimed_pr_not_completed_awaits_pr(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(lifecycle=Lifecycle.DONE, completed_pr_url=None)
    decision = _decide(engine, facts)
    assert decision.state is State.AWAITING_PR
    assert decision.actions == (NoAction(),)


def test_done_bucket_not_fleet_claimed_is_terminal_done(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(
        lifecycle=Lifecycle.DONE, is_fleet_claimed=False, completed_pr_url="https://pr/9"
    )
    decision = _decide(engine, facts)
    assert decision.state is State.DONE
    assert decision.actions == (NoAction(),)


def test_failed_tagged_item_is_terminal_escalated(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # A board tag dominates: an item already escalated must never be re-driven,
    # even if its other facts look like a clean completion.
    facts = make_facts(
        failed_tagged=True,
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.ESCALATED
    assert decision.actions == (NoAction(),)


def test_needs_decision_tagged_item_is_terminal_parked_decision(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(needs_decision_tagged=True, lifecycle=Lifecycle.ACTIVE)
    decision = _decide(engine, facts)
    assert decision.state is State.PARKED_DECISION
    assert decision.actions == (NoAction(),)


# --- #151: fold is_parked / is_failed_park into fact-derivation ----------------


def test_parked_tag_is_a_deliberate_park_awaiting_pr(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # is_parked fold: a parked tag → quiescent, never reaped. Even with a stale
    # heartbeat the slice is not timed out.
    facts = make_facts(parked_tagged=True, heartbeat_stale=True)
    decision = _decide(engine, facts)
    assert decision.state is State.AWAITING_PR
    assert decision.actions == (NoAction(),)


def test_status_phase_done_is_a_deliberate_park(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # is_parked fold: a finalized status (phase done) is never requeued.
    facts = make_facts(phase="done", container_present=False, container_running=False)
    decision = _decide(engine, facts)
    assert decision.state is State.AWAITING_PR


def test_status_phase_parked_non_failed_is_a_deliberate_park(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # is_parked fold: phase parked + parked_state != failed → deliberate park.
    facts = make_facts(
        phase="parked",
        parked_state="qa-ready",
        container_present=False,
        container_running=False,
        heartbeat_stale=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AWAITING_PR


def test_failed_park_status_with_no_container_is_a_crash(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # is_failed_park fold: phase parked + parked_state failed is positive failure
    # evidence (not a deliberate park) → an agent crash, retried under budget.
    facts = make_facts(
        phase="parked",
        parked_state="failed",
        container_present=False,
        container_running=False,
        attempt=1,
        max_attempts=3,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=1),)


# --- #152: differentiated failure-edge classification --------------------------


def test_build_failed_edge_retries_under_budget(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(build_failed=True, attempt=1, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.BUILD_FAILED, attempt=1),)


def test_agent_crash_nonzero_exit_retries(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    facts = make_facts(container_running=False, container_exit_code=1, attempt=1, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=1),)


def test_agent_crash_exit_zero_but_no_manifest_retries(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # Exit 0 is not enough: a missing manifest means the agent died before its
    # final act — the completion triple is incomplete → crash.
    facts = make_facts(
        container_running=False,
        container_exit_code=0,
        manifest_present=False,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=1),)


def test_agent_crash_exit_zero_invalid_manifest_retries(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=False,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=1),)


def test_agent_crash_exit_zero_valid_manifest_but_no_commits_retries(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # Manifest = intent, commits = substance: a valid manifest with no commits is
    # not a completion — there is nothing to push.
    facts = make_facts(
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        commits_present=False,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=1),)


def test_agent_timeout_stops_then_retries(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    # A live container with a stale heartbeat is hung: stop it, then retry.
    facts = make_facts(
        container_present=True, container_running=True, heartbeat_stale=True, attempt=1
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_TIMEOUT
    assert decision.actions == (
        StopContainer(),
        RetrySlice(edge=FailureEdge.AGENT_TIMEOUT, attempt=1),
    )


def test_agent_timeout_on_exhaustion_stops_then_escalates(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(
        container_present=True,
        container_running=True,
        heartbeat_stale=True,
        attempt=3,
        max_attempts=3,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_TIMEOUT
    assert decision.actions == (
        StopContainer(),
        EscalateExhausted(edge=FailureEdge.AGENT_TIMEOUT, attempt=4, cap=3),
    )


def test_egress_denied_escalates_immediately_naming_the_host(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # The security signal: escalate on first sight, never retried, even with
    # retries remaining and even alongside a clean-looking completion triple.
    facts = make_facts(
        egress_denied_host="evil.example.com",
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        commits_present=True,
        attempt=1,
        max_attempts=3,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.ESCALATED
    assert decision.actions == (EscalateEgressDenied(denied_host="evil.example.com"),)


def test_teardown_failed_appends_a_nonblocking_leak_sweep(
    engine: LifecycleEngine, default_facts: LifecycleFacts
) -> None:
    # teardown_failed is orthogonal: it never changes the primary state, only
    # appends a SweepLeak the orchestrator runs out-of-band.
    facts = dataclasses.replace(default_facts, teardown_failed=True)
    decision = _decide(engine, facts)
    assert decision.state is State.RUNNING  # unchanged primary state
    assert decision.actions == (AwaitAgent(), SweepLeak())


def test_teardown_failed_layers_on_top_of_a_real_lifecycle_action(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(
        teardown_failed=True,
        container_running=False,
        container_exit_code=0,
        manifest_present=True,
        manifest_valid=True,
        commits_present=True,
    )
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_DONE
    assert decision.actions == (HandoffAgentDone(), SweepLeak())


# --- #153: attempt / escalation accounting expressed as transitions ------------


def test_crash_retries_while_attempt_below_max(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(container_running=False, container_exit_code=2, attempt=2, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=2),)


def test_crash_escalates_on_the_final_attempt(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # attempt == max_attempts is exhaustion: escalate with the attempt+1 it would
    # have reached (mirrors the legacy reap pass's accounting).
    facts = make_facts(container_running=False, container_exit_code=2, attempt=3, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.ESCALATED
    assert decision.actions == (EscalateExhausted(edge=FailureEdge.AGENT_CRASH, attempt=4, cap=3),)


def test_build_failed_escalates_on_exhaustion(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    facts = make_facts(build_failed=True, attempt=3, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.ESCALATED
    assert decision.actions == (EscalateExhausted(edge=FailureEdge.BUILD_FAILED, attempt=4, cap=3),)


def test_attempt_beyond_max_still_escalates_not_retries(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # Defensive: attempt > max_attempts (>=) never retries.
    facts = make_facts(container_running=False, container_exit_code=1, attempt=5, max_attempts=3)
    decision = _decide(engine, facts)
    assert decision.state is State.ESCALATED
    assert decision.actions == (EscalateExhausted(edge=FailureEdge.AGENT_CRASH, attempt=6, cap=3),)


def test_higher_max_attempts_keeps_retrying(engine: LifecycleEngine, make_facts: MakeFacts) -> None:
    facts = make_facts(container_running=False, container_exit_code=1, attempt=3, max_attempts=5)
    decision = _decide(engine, facts)
    assert decision.state is State.AGENT_FAILED
    assert decision.actions == (RetrySlice(edge=FailureEdge.AGENT_CRASH, attempt=3),)


# --- every State is reachable -------------------------------------------------


def test_every_state_is_reachable_by_some_fact_combination(
    engine: LifecycleEngine, make_facts: MakeFacts
) -> None:
    # A coverage guard: each declared State must be the output of decide() for at
    # least one fact set, so no state is dead code (and the table stays honest).
    cases: dict[State, LifecycleFacts] = {
        State.BLOCKED: make_facts(lifecycle=Lifecycle.QUEUED, predecessors_done=False),
        State.CLAIMABLE: make_facts(lifecycle=Lifecycle.QUEUED, predecessors_done=True),
        State.PROVISIONING: make_facts(container_present=False, container_running=False),
        State.RUNNING: make_facts(),
        State.AGENT_DONE: make_facts(
            container_running=False,
            container_exit_code=0,
            manifest_present=True,
            manifest_valid=True,
            commits_present=True,
        ),
        State.AGENT_DECISION: make_facts(
            container_running=False,
            container_exit_code=0,
            manifest_present=True,
            manifest_valid=True,
            manifest_needs_decision=True,
            commits_present=True,
        ),
        State.AGENT_FAILED: make_facts(
            container_running=False, container_exit_code=1, attempt=1, max_attempts=3
        ),
        State.AGENT_TIMEOUT: make_facts(container_running=True, heartbeat_stale=True),
        State.AWAITING_PR: make_facts(lifecycle=Lifecycle.DONE, completed_pr_url=None),
        State.FINALIZING: make_facts(lifecycle=Lifecycle.DONE, completed_pr_url="https://pr/1"),
        State.DONE: make_facts(
            lifecycle=Lifecycle.DONE, is_fleet_claimed=False, completed_pr_url="https://pr/1"
        ),
        State.ESCALATED: make_facts(failed_tagged=True),
        State.PARKED_DECISION: make_facts(needs_decision_tagged=True),
    }
    assert set(cases) == set(State)  # every state has a case
    for expected_state, facts in cases.items():
        assert _decide(engine, facts).state is expected_state


# --- #154: totality property — decide never raises over the fact space ---------

# The closed action union, as a runtime tuple for isinstance assertions.
_ALLOWED_ACTIONS = (
    SignalClaimable,
    LaunchSandbox,
    AwaitAgent,
    HandoffAgentDone,
    ParkNeedsDecision,
    StopContainer,
    RetrySlice,
    EscalateExhausted,
    EscalateEgressDenied,
    SweepLeak,
    FinalizeCleanup,
    NoAction,
)

# Per-field domains spanning every value class the engine branches on (the
# unbounded ints are sampled across the attempt-budget boundary, both signs of
# the exit code, plus the common SIGKILL 137).
_BOOLS: tuple[bool, ...] = (True, False)
_PHASES: tuple[str | None, ...] = (None, "claiming", "seams", "tdd", "qa", "parked", "done")
_PARKED_STATES: tuple[str | None, ...] = (
    None,
    "needs-decision",
    "qa-ready",
    "awaiting-pr-approval",
    "failed",
)
_EXIT_CODES: tuple[int | None, ...] = (None, 0, 1, 137)
_HOSTS: tuple[str | None, ...] = (None, "evil.example.com")
_PRS: tuple[str | None, ...] = (None, "https://pr/1")
_ATTEMPTS: tuple[int, ...] = (1, 2, 3, 4)


def _assert_decision_well_formed(decision: LifecycleDecision) -> None:
    """A decision must be a ``State`` plus a non-empty tuple of union actions."""
    assert isinstance(decision.state, State)
    assert decision.actions, "actions must never be empty"
    assert all(isinstance(action, _ALLOWED_ACTIONS) for action in decision.actions)


def _facts_from_row(row: tuple[object, ...]) -> LifecycleFacts:
    """Build a ``LifecycleFacts`` from a positional product/sample row.

    The product/sample iterables are heterogeneous, so the row elements are
    typed ``object``; this single boundary casts each to its field type, keeping
    the totality tests free of per-call casts. Field order matches ``_FIELDS``.
    """
    (
        lifecycle,
        is_claimed,
        preds,
        parked_tag,
        failed_tag,
        decision_tag,
        phase,
        parked_state,
        present,
        running,
        exit_code,
        stale,
        man_present,
        man_valid,
        man_needs,
        commits,
        pr,
        build_failed,
        host,
        teardown,
        attempt,
    ) = row
    return LifecycleFacts(
        lifecycle=cast(Lifecycle, lifecycle),
        is_fleet_claimed=cast(bool, is_claimed),
        predecessors_done=cast(bool, preds),
        parked_tagged=cast(bool, parked_tag),
        failed_tagged=cast(bool, failed_tag),
        needs_decision_tagged=cast(bool, decision_tag),
        phase=cast("str | None", phase),
        parked_state=cast("str | None", parked_state),
        container_present=cast(bool, present),
        container_running=cast(bool, running),
        container_exit_code=cast("int | None", exit_code),
        heartbeat_stale=cast(bool, stale),
        manifest_present=cast(bool, man_present),
        manifest_valid=cast(bool, man_valid),
        manifest_needs_decision=cast(bool, man_needs),
        commits_present=cast(bool, commits),
        completed_pr_url=cast("str | None", pr),
        build_failed=cast(bool, build_failed),
        egress_denied_host=cast("str | None", host),
        teardown_failed=cast(bool, teardown),
        attempt=cast(int, attempt),
        max_attempts=3,
    )


# Per-field value domains, in ``LifecycleFacts`` constructor order, used by both
# totality tests. ``manifest_present`` and ``manifest_valid`` share the bool
# domain (the crash tests cover the present-but-invalid split explicitly).
_FIELD_DOMAINS: tuple[tuple[object, ...], ...] = (
    tuple(Lifecycle),  # lifecycle
    _BOOLS,  # is_fleet_claimed
    _BOOLS,  # predecessors_done
    _BOOLS,  # parked_tagged
    _BOOLS,  # failed_tagged
    _BOOLS,  # needs_decision_tagged
    _PHASES,  # phase
    _PARKED_STATES,  # parked_state
    _BOOLS,  # container_present
    _BOOLS,  # container_running
    _EXIT_CODES,  # container_exit_code
    _BOOLS,  # heartbeat_stale
    _BOOLS,  # manifest_present
    _BOOLS,  # manifest_valid
    _BOOLS,  # manifest_needs_decision
    _BOOLS,  # commits_present
    _PRS,  # completed_pr_url
    _BOOLS,  # build_failed
    _HOSTS,  # egress_denied_host
    _BOOLS,  # teardown_failed
    _ATTEMPTS,  # attempt
)


def test_decide_is_total_over_a_seeded_random_sample(engine: LifecycleEngine) -> None:
    """``decide`` returns a valid decision for a large seeded random fact sample.

    Each field is drawn independently from its full value domain (so any
    cross-field interaction is reachable); a fixed seed makes the sample
    reproducible. No draw may raise; every result is a ``(State, non-empty
    union-actions)`` pair. The full Cartesian product is ~10^8 combinations —
    intractable to enumerate, so totality is proven by a dense sample here plus
    the exhaustive branch-driving product below.
    """
    rng = random.Random(20260615)
    for _ in range(30_000):
        row: tuple[object, ...] = tuple(rng.choice(domain) for domain in _FIELD_DOMAINS)
        _assert_decision_well_formed(engine.decide(_facts_from_row(row)))


def test_decide_is_total_over_branch_driving_dimensions(engine: LifecycleEngine) -> None:
    """``decide`` is total over an exhaustive product of the branch-driving fields.

    Reduces each field to representatives spanning its branch classes and
    enumerates their full Cartesian product, so every combination of the value
    *classes* the guard ladder keys on is exercised: lifecycle, the short-circuit
    tags, claim/predecessor status, the park-folding (phase/parked_state), the
    container triple (present/running/exit), liveness, the completion triple, the
    failure inputs, and the attempt-vs-budget boundary. The orthogonal
    ``parked_tagged`` and ``teardown_failed`` flags (each handled by a dedicated
    short-circuit, not interacting with the deep ladder) are held fixed here and
    exercised by their own tests plus the random sample. ``manifest_present`` and
    ``manifest_valid`` move together (the present-but-invalid split has its own
    crash test). Every combination yields a well-formed decision without raising.
    """
    reduced: tuple[tuple[object, ...], ...] = (
        tuple(Lifecycle),  # lifecycle
        _BOOLS,  # is_fleet_claimed
        _BOOLS,  # predecessors_done
        _BOOLS,  # failed_tagged
        _BOOLS,  # needs_decision_tagged
        (None, "parked", "done"),  # phase reps
        (None, "qa-ready", "failed"),  # parked_state reps
        _BOOLS,  # container_present
        _BOOLS,  # container_running
        (None, 0, 1),  # exit code reps
        _BOOLS,  # heartbeat_stale
        _BOOLS,  # manifest present-and-valid (tied)
        _BOOLS,  # manifest_needs_decision
        _BOOLS,  # commits_present
        _PRS,  # completed_pr_url
        _BOOLS,  # build_failed
        _HOSTS,  # egress_denied_host
        (1, 3),  # attempt vs the budget boundary (max_attempts=3)
    )
    count = 0
    for (
        lifecycle,
        is_claimed,
        preds,
        failed_tag,
        decision_tag,
        phase,
        parked_state,
        present,
        running,
        exit_code,
        stale,
        manifest_ok,
        man_needs,
        commits,
        pr,
        build_failed,
        host,
        attempt,
    ) in itertools.product(*reduced):
        row: tuple[object, ...] = (
            lifecycle,
            is_claimed,
            preds,
            False,  # parked_tagged (held fixed; covered elsewhere)
            failed_tag,
            decision_tag,
            phase,
            parked_state,
            present,
            running,
            exit_code,
            stale,
            manifest_ok,  # manifest_present
            manifest_ok,  # manifest_valid (tied)
            man_needs,
            commits,
            pr,
            build_failed,
            host,
            False,  # teardown_failed (held fixed; covered elsewhere)
            attempt,
        )
        _assert_decision_well_formed(engine.decide(_facts_from_row(row)))
        count += 1

    assert count > 0
