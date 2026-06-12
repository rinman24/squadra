"""Unit tests for the supervisor claim pass + tick lock (ADR-0007 addendum §§1–2)."""

from collections.abc import Callable
import fcntl
from pathlib import Path

from flotilla.constants import SUPERVISOR_LOCK_FILENAME
from flotilla.status import FleetStatus, write
from flotilla.supervisor import (
    CLAIMED_AT_FILENAME,
    ClaimOutcome,
    SupervisorConfig,
    TickSeams,
    claim_pass,
    run_tick,
    slice_branch,
)
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue, FakeLauncher

# fleet_root, make_status, fake_board, make_issue, fake_launcher, make_seams,
# make_supervisor_config are provided by tests/conftest.py


def test_tick_skips_when_lock_held(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    config: SupervisorConfig = make_supervisor_config()
    fleet_root.mkdir(parents=True)
    with (fleet_root / SUPERVISOR_LOCK_FILENAME).open("w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        assert run_tick(make_seams(), config) == 0
    assert fake_board.calls == []
    assert fake_launcher.launches == []


def test_claims_unblocked_up_to_cap_in_id_order(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(7)
    make_issue(5, title="feat: add scope revocation")
    make_issue(6)
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.claimed == (5, 6)
    assert fake_launcher.launches == [
        (5, "feat/slice-5-add-scope-revocation", 1),
        (6, "feat/slice-6-slice-6", 1),
    ]
    assert fake_board.issue_state(5) == "Doing"
    assert "fleet:claimed" in fake_board.issues[5].tags
    assert any("runner-5-a1" in comment for comment in fake_board.comments[5])
    assert fake_board.issue_state(7) == "To Do"


def test_inflight_claimed_issues_count_against_cap(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(4, state="Doing", tags=["fleet:claimed"])
    make_issue(5)
    make_issue(6)
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.inflight == (4,)
    assert outcome.claimed == (5,)


def test_human_doing_issue_does_not_count_against_cap(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(4, state="Doing")  # a human moved this; no fleet:claimed tag
    make_issue(5)
    make_issue(6)
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.inflight == ()
    assert outcome.claimed == (5, 6)


def test_blocked_issue_is_skipped_until_predecessors_done(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(4, state="Doing")  # predecessor still in flight (not Done)
    make_issue(5, predecessor_ids=(4,))
    make_issue(6, predecessor_ids=(3,))
    make_issue(3, state="Done")
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.skipped_blocked == (5,)
    assert outcome.claimed == (6,)


def test_fleet_tagged_todo_issue_is_never_claimed(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(5, tags=["fleet:failed"])
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.claimed == ()
    assert fake_launcher.launches == []
    assert fake_board.issue_state(5) == "To Do"


def test_launch_failure_rolls_the_claim_back(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(5)
    make_issue(6)
    fake_launcher.fail_for.add(5)
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.rolled_back == (5,)
    assert outcome.claimed == (6,)
    assert fake_board.issue_state(5) == "To Do"
    assert "fleet:claimed" not in fake_board.issues[5].tags
    assert any("rolled back" in comment for comment in fake_board.comments[5])
    # Rollback order: tag removed, then the state transition back to To Do.
    rollback_calls: list[tuple[str, int, str]] = [
        call for call in fake_board.calls if call[1] == 5 and call[0] != "add_comment"
    ]
    assert rollback_calls == [
        ("set_state", 5, "Doing"),
        ("add_tag", 5, "fleet:claimed"),
        ("remove_tag", 5, "fleet:claimed"),
        ("set_state", 5, "To Do"),
    ]


def test_claim_protocol_order_state_then_tag_then_comment_then_launch(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(5)
    claim_pass(make_seams(), make_supervisor_config())
    mutations: list[str] = [call[0] for call in fake_board.calls if call[1] == 5]
    assert mutations == ["set_state", "add_tag", "add_comment"]
    assert fake_launcher.launches == [(5, "feat/slice-5-slice-5", 1)]


def test_claim_writes_the_claimed_at_marker(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(5)
    claim_pass(make_seams(), make_supervisor_config())
    marker: Path = fleet_root / "5" / CLAIMED_AT_FILENAME
    assert marker.is_file()
    assert marker.read_text().strip().startswith("20")


def test_retry_uses_next_attempt_and_suffixes_branch(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, title="feat: example")
    write(make_status(attempt=1), fleet_root)  # the reaped previous attempt
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.claimed == (41,)
    assert fake_launcher.launches == [(41, "feat/slice-41-example-a2", 2)]


def test_exhausted_retries_escalate_to_fleet_failed(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41)
    write(make_status(attempt=3), fleet_root)
    outcome: ClaimOutcome = claim_pass(make_seams(), make_supervisor_config())
    assert outcome.escalated == (41,)
    assert outcome.claimed == ()
    assert fake_launcher.launches == []
    assert "fleet:failed" in fake_board.issues[41].tags
    assert fake_board.issue_state(41) == "To Do"
    assert any("retry cap exhausted" in comment for comment in fake_board.comments[41])


def test_epic_scope_filter_limits_claims(
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(5, parent_id=68)
    make_issue(6, parent_id=99)
    config: SupervisorConfig = make_supervisor_config(epic_ids=(68,))
    outcome: ClaimOutcome = claim_pass(make_seams(), config)
    assert outcome.claimed == (5,)
    assert fake_board.issue_state(6) == "To Do"


def test_slice_branch_derivation() -> None:
    assert slice_branch(12, "feat: Add scope revocation", 1) == "feat/slice-12-add-scope-revocation"
    assert slice_branch(12, "feat: Add scope revocation", 2) == (
        "feat/slice-12-add-scope-revocation-a2"
    )
    assert slice_branch(12, "!!!", 1) == "feat/slice-12-slice"
    long_branch: str = slice_branch(12, "feat: " + "very " * 20 + "long title", 1)
    assert len(long_branch) <= len("feat/slice-12-") + 32
    assert not long_branch.endswith("-")
