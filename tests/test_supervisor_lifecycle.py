"""Engine-driven tick: finalize / retry / escalate / handoff outcomes (F4).

The F4 cutover replaced the standalone finalize / reap / claim *passes* with one
``gather-facts → LifecycleEngine.decide → execute`` tick. These tests drive the
whole ``run_tick`` and assert the **board-side outcomes match the old passes** for
equivalent observed states (the parity requirement), plus the new contained-runner
outcomes the engine added (clean handoff, needs-decision park, egress escalation,
timeout). Container liveness is the agent-as-command ``SandboxAccess.inspect``
status (an exited container is the new "dead runner"), not the retired pid sidecar.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from squadra.config import SquadraConfig
from squadra.domain import (
    Escalated,
    Finalized,
    Lifecycle,
    Reaped,
    SandboxAbsent,
    SandboxExited,
    SandboxRunning,
)
from squadra.manifest import ManifestRead
from squadra.status import FleetStatus, load, write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# fake_worktree, make_seams, make_config are provided by tests/conftest.py

_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock (run_tick uses now())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _project(item_id: int) -> str:
    return f"squadra-slice-{item_id}"


# --- finalize parity ----------------------------------------------------------


def test_finalize_retires_a_merged_done_slice(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(
        41,
        state=Lifecycle.DONE,
        tags=["fleet:claimed", "fleet:awaiting-pr-approval", "regular-tag"],
    )
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)
    fake_board.completed_prs["feat/slice-41-example"] = "https://pr/41"

    assert run_tick(make_seams(), make_config()) == 0

    # Deterministic cleanup ran (no LLM) for the known merged branch.
    assert fake_cleanup.deleted_branches == ["feat/slice-41-example"]
    assert fake_cleanup.composed_down == [_project(41)]
    # Fleet tags dropped, the regular tag kept, a Finalized comment, status -> done.
    assert fake_board.issues[41].tags == ["regular-tag"]
    assert any(isinstance(event, Finalized) for event in fake_board.comments[41])
    final: FleetStatus = load(41, fleet_root)
    assert final.phase == "done"
    assert final.parked_state is None
    assert final.pr_url == "https://pr/41"


def test_finalize_waits_for_the_pr_to_merge(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # Done + claimed but no completed PR -> AWAITING_PR (deliberate park, no cleanup).
    make_issue(41, state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_cleanup.deleted_branches == []
    assert "fleet:claimed" in fake_board.issues[41].tags


def test_finalize_ignores_done_slices_the_fleet_never_claimed(
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(9, state=Lifecycle.DONE)  # a human-delivered slice
    assert run_tick(make_seams(), make_config()) == 0
    assert fake_cleanup.deleted_branches == []
    assert fake_board.comments == {}


def test_finalize_partial_cleanup_is_retried_next_tick(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(41, state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)
    fake_board.completed_prs["feat/slice-41-example"] = "https://pr/41"
    fake_cleanup.fail_branches.add("feat/slice-41-example")  # branch delete fails

    assert run_tick(make_seams(), make_config()) == 0

    # The fleet tags are untouched -> the next tick retries finalize.
    assert "fleet:claimed" in fake_board.issues[41].tags
    assert not any(isinstance(event, Finalized) for event in fake_board.comments.get(41, []))
    assert load(41, fleet_root).phase == "parked"


# --- reap parity: an exited container is the new "dead runner" -----------------


def test_exited_crash_requeues_and_archives_the_worktree(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_worktree: FakeWorktree,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_now()), fleet_root)
    # The agent-as-command container exited non-zero: a crash edge.
    fake_sandbox.seed(_project(41), SandboxExited(exit_code=1))

    assert run_tick(make_seams(), make_config()) == 0

    # Board-side parity with the old reap: requeued, claimed dropped, Reaped comment.
    assert fake_board.item_state(41) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert any(isinstance(event, Reaped) for event in fake_board.comments[41])
    # The dead attempt's worktree was archived + pruned and the sandbox torn down.
    assert fake_worktree.archived and fake_worktree.archived[0][2] == 1
    assert fake_worktree.prune_count == 1
    assert any(spec.project == _project(41) for spec in fake_sandbox.teardowns)
    reaped: FleetStatus = load(41, fleet_root)
    assert reaped.parked_state == "failed"
    assert reaped.attempt == 1  # next claim derives attempt 2 from this


def test_running_fresh_container_is_left_alone(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxRunning())

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_board.item_state(41) == Lifecycle.ACTIVE
    assert "fleet:claimed" in fake_board.issues[41].tags
    assert fake_board.comments == {}


def test_failed_park_with_absent_container_is_a_crash(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # parked_state=failed + no container (it died) is positive crash evidence,
    # even with a fresh heartbeat — parity with the legacy failed-park reap.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="parked", parked_state="failed", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxAbsent())

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_board.item_state(41) == Lifecycle.QUEUED
    assert any(isinstance(event, Reaped) for event in fake_board.comments[41])


def test_exhausted_attempt_escalates_to_fleet_failed(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", attempt=3, last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxExited(exit_code=1))

    assert run_tick(make_seams(), make_config()) == 0

    # Parity with the legacy reap exhaustion: failed tag, claimed dropped, not requeued.
    assert "fleet:failed" in fake_board.issues[41].tags
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert fake_board.item_state(41) == Lifecycle.ACTIVE  # stays, not requeued
    assert any(isinstance(event, Escalated) for event in fake_board.comments[41])


def test_deliberate_park_is_never_reaped(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # A qa-ready park with a stale heartbeat + an exited container is still a
    # deliberate park (folds the legacy is_parked): never requeued.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed", "fleet:qa-ready"])
    write(
        make_status(phase="parked", parked_state="qa-ready", last_heartbeat=_ANCIENT),
        fleet_root,
    )
    fake_sandbox.seed(_project(41), SandboxExited(exit_code=0))

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_board.item_state(41) == Lifecycle.ACTIVE
    assert "fleet:claimed" in fake_board.issues[41].tags
    assert fake_board.comments == {}


def test_human_active_item_is_invisible_to_the_fleet(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(4, state=Lifecycle.ACTIVE)  # no fleet:claimed tag — a human moved it
    assert run_tick(make_seams(), make_config()) == 0
    assert fake_board.comments == {}
    assert fake_sandbox.inspect_count_is_zero()


# --- new contained-runner outcomes the engine added --------------------------


def test_clean_handoff_parks_awaiting_pr_and_tears_down(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    tmp_path: Path,
) -> None:
    # Container exited 0, a valid handoff manifest, commits present -> AGENT_DONE:
    # park awaiting-pr-approval + tear the sandbox down (push/PR are G2's tail).
    make_issue(41, title="feat: example", state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxExited(exit_code=0))

    seams = make_seams(
        read_manifest=_manifest_stub(present=True, valid=True, needs_decision=False),
        commits_present=_has_commits,
    )
    assert run_tick(seams, make_config()) == 0

    assert "fleet:awaiting-pr-approval" in fake_board.issues[41].tags
    assert load(41, fleet_root).parked_state == "awaiting-pr-approval"
    assert any(spec.project == _project(41) for spec in fake_sandbox.teardowns)


def test_needs_decision_manifest_parks_for_a_human(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxExited(exit_code=0))

    seams = make_seams(
        read_manifest=_manifest_stub(present=True, valid=True, needs_decision=True),
        commits_present=_has_commits,
    )
    assert run_tick(seams, make_config()) == 0

    assert "fleet:needs-decision" in fake_board.issues[41].tags
    assert load(41, fleet_root).parked_state == "needs-decision"


def test_egress_denied_escalates_immediately_naming_the_host(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", attempt=1, last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed(_project(41), SandboxRunning())
    fake_sandbox.seed_logs(_project(41), 'Proxying refused on filtered domain "evil.example.com"')

    assert run_tick(make_seams(), make_config()) == 0

    # A security signal: escalate on first sight, never retried, name the host.
    assert "fleet:failed" in fake_board.issues[41].tags
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert fake_board.item_state(41) == Lifecycle.ACTIVE  # not requeued
    out: str = capsys.readouterr().out
    assert "egress-denied to evil.example.com" in out


def test_agent_timeout_stops_the_container_then_requeues(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # A live container with a stale heartbeat is hung: stop it, then retry.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_ANCIENT), fleet_root)
    fake_sandbox.seed(_project(41), SandboxRunning())

    assert run_tick(make_seams(), make_config()) == 0

    # StopContainer then RetrySlice -> torn down + requeued.
    assert any(spec.project == _project(41) for spec in fake_sandbox.teardowns)
    assert fake_board.item_state(41) == Lifecycle.QUEUED
    assert any(isinstance(event, Reaped) for event in fake_board.comments[41])


# --- helpers ------------------------------------------------------------------


def _has_commits(_worktree: Path, _base_branch: str) -> bool:
    """``commits_present`` stub — the substance half of the completion triple is met."""
    return True


def _manifest_stub(
    *, present: bool, valid: bool, needs_decision: bool
) -> Callable[[Path], ManifestRead]:
    def _read(_worktree: Path) -> ManifestRead:
        return ManifestRead(
            present=present, valid=valid, needs_decision=needs_decision, manifest=None
        )

    return _read
