"""Unit tests for the finalize + reap passes and pass ordering (addendum §§3–5)."""

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from flotilla.status import FleetStatus, load, write
from flotilla.supervisor import (
    FinalizeOutcome,
    ReapOutcome,
    SupervisorConfig,
    TickSeams,
    finalize_pass,
    reap_pass,
    run_tick,
)
from tests.helpers.fleet_fakes import FakeBoard, FakeCleaner, FakeIssue, FakeLauncher

# fleet_root, make_status, fake_board, make_issue, fake_launcher, fake_cleaner,
# make_seams, make_supervisor_config are provided by tests/conftest.py

_NOW: datetime = datetime.fromisoformat("2026-06-10T12:00:00+00:00")
_FRESH: str = "2026-06-10T11:58:00+00:00"  # 2 min before _NOW
_STALE: str = "2026-06-10T11:00:00+00:00"  # 60 min before _NOW
_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock (run_tick uses now())


def _never_alive(_pid: int) -> bool:
    return False


def _always_alive(_pid: int) -> bool:
    return True


class _RecordingGitRunner:
    """Records git invocations made by the reap pass."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return 0


def test_finalize_retires_a_merged_done_slice(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(
        41, state="Done", tags=["fleet:claimed", "fleet:awaiting-pr-approval", "regular-tag"]
    )
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)
    fake_board.completed_prs["feat/slice-41-example"] = "https://pr/41"
    outcome: FinalizeOutcome = finalize_pass(
        fake_board, fake_cleaner, make_supervisor_config()
    )
    assert outcome.finalized == (41,)
    assert fake_cleaner.cleaned == ["feat/slice-41-example"]
    assert fake_board.issues[41].tags == ["regular-tag"]
    assert any("finalized" in comment for comment in fake_board.comments[41])
    finalized_status: FleetStatus = load(41, fleet_root)
    assert finalized_status.phase == "done"
    assert finalized_status.parked_state is None
    assert finalized_status.pr_url == "https://pr/41"


def test_finalize_waits_for_the_pr_to_merge(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Done", tags=["fleet:claimed"])
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)
    outcome: FinalizeOutcome = finalize_pass(
        fake_board, fake_cleaner, make_supervisor_config()
    )
    assert outcome.awaiting_merge == (41,)
    assert fake_cleaner.cleaned == []
    assert "fleet:claimed" in fake_board.issues[41].tags


def test_finalize_ignores_done_slices_the_fleet_never_claimed(
    fake_board: FakeBoard,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(9, state="Done")  # a human-delivered slice
    outcome: FinalizeOutcome = finalize_pass(
        fake_board, fake_cleaner, make_supervisor_config()
    )
    assert outcome == FinalizeOutcome(finalized=(), awaiting_merge=(), cleanup_failed=())
    assert fake_cleaner.cleaned == []


def test_finalize_cleanup_failure_is_retried_next_tick(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Done", tags=["fleet:claimed"])
    write(make_status(phase="parked", parked_state="awaiting-pr-approval"), fleet_root)
    fake_board.completed_prs["feat/slice-41-example"] = "https://pr/41"
    fake_cleaner.fail_for.add("feat/slice-41-example")
    outcome: FinalizeOutcome = finalize_pass(
        fake_board, fake_cleaner, make_supervisor_config()
    )
    assert outcome.cleanup_failed == (41,)
    assert "fleet:claimed" in fake_board.issues[41].tags  # untouched -> retried
    assert load(41, fleet_root).phase == "parked"


def test_finalize_derives_the_branch_when_no_status_file_exists(
    fake_board: FakeBoard,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, title="feat: example", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-41-example"] = "https://pr/41"
    outcome: FinalizeOutcome = finalize_pass(
        fake_board, fake_cleaner, make_supervisor_config()
    )
    assert outcome.finalized == (41,)
    assert fake_cleaner.cleaned == ["feat/slice-41-example"]


def test_reap_requeues_a_stale_dead_runner_and_archives_the_worktree(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    worktree: Path = tmp_path / "worktrees" / "feat+slice-41-example"
    worktree.mkdir(parents=True)
    (worktree / "scratch.txt").write_text("evidence")
    write(
        make_status(phase="tdd", last_heartbeat=_STALE, worktree=str(worktree)), fleet_root
    )
    (fleet_root / "41" / "runner.pid").write_text("99999\n")
    git_runner = _RecordingGitRunner()
    seams: TickSeams = make_seams(pid_alive=_never_alive, run_git=git_runner)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.reaped == (41,)
    assert fake_board.issue_state(41) == "To Do"
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert any("reaped" in comment for comment in fake_board.comments[41])
    assert not worktree.exists()
    archived: Path = fleet_root / "41" / "archive" / "attempt-1"
    assert (archived / "scratch.txt").read_text() == "evidence"
    assert ["git", "-C", str(make_supervisor_config().fleet_home), "worktree", "prune"] in (
        git_runner.calls
    )
    reaped_status: FleetStatus = load(41, fleet_root)
    assert reaped_status.parked_state == "failed"
    assert reaped_status.last_error is not None
    assert "heartbeat stale" in reaped_status.last_error
    assert reaped_status.attempt == 1  # the next claim derives attempt 2 from this


def test_reap_leaves_fresh_runners_alone(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_FRESH), fleet_root)
    seams: TickSeams = make_seams(pid_alive=_never_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome == ReapOutcome(reaped=(), escalated=(), skipped_alive=(), skipped_parked=())
    assert fake_board.issue_state(41) == "Doing"


def test_reap_never_kills_a_stale_but_alive_runner(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_STALE), fleet_root)
    (fleet_root / "41" / "runner.pid").write_text("4242\n")
    seams: TickSeams = make_seams(pid_alive=_always_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.skipped_alive == (41,)
    assert fake_board.issue_state(41) == "Doing"
    assert "fleet:claimed" in fake_board.issues[41].tags


def test_reap_never_touches_parked_runners(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed", "fleet:needs-decision"])
    write(
        make_status(phase="parked", parked_state="needs-decision", last_heartbeat=_STALE),
        fleet_root,
    )
    seams: TickSeams = make_seams(pid_alive=_never_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.skipped_parked == (41,)
    assert fake_board.issue_state(41) == "Doing"


def test_reap_escalates_when_the_attempt_cap_is_exhausted(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(make_status(phase="tdd", attempt=3, last_heartbeat=_STALE), fleet_root)
    seams: TickSeams = make_seams(pid_alive=_never_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.escalated == (41,)
    assert outcome.reaped == ()
    assert fake_board.issue_state(41) == "Doing"  # stays parked-failed, not requeued
    assert "fleet:failed" in fake_board.issues[41].tags
    assert "fleet:claimed" not in fake_board.issues[41].tags


def test_reap_requeues_a_failed_park_immediately_despite_a_fresh_heartbeat(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    # parked_state=failed is positive failure evidence — no 600s staleness
    # wait, so a FRESH heartbeat must not protect the slice.
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(
        make_status(
            phase="parked",
            parked_state="failed",
            last_heartbeat=_FRESH,
            worktree=str(tmp_path / "wt-41"),
        ),
        fleet_root,
    )
    (fleet_root / "41" / "runner.pid").write_text("99999\n")
    seams: TickSeams = make_seams(pid_alive=_never_alive, run_git=_RecordingGitRunner())
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.reaped == (41,)
    assert outcome.skipped_parked == ()
    assert fake_board.issue_state(41) == "To Do"
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert any("reaped" in comment for comment in fake_board.comments[41])
    assert load(41, fleet_root).attempt == 1  # the next claim derives attempt 2 from this


def test_reap_escalates_a_failed_park_at_the_attempt_cap(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(
        make_status(
            phase="parked",
            parked_state="failed",
            attempt=3,
            last_heartbeat=_FRESH,
            worktree=str(tmp_path / "wt-41"),
        ),
        fleet_root,
    )
    (fleet_root / "41" / "runner.pid").write_text("99999\n")
    seams: TickSeams = make_seams(pid_alive=_never_alive, run_git=_RecordingGitRunner())
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.escalated == (41,)
    assert outcome.reaped == ()
    assert fake_board.issue_state(41) == "Doing"
    assert "fleet:failed" in fake_board.issues[41].tags
    assert "fleet:claimed" not in fake_board.issues[41].tags


def test_reap_leaves_a_failed_park_with_a_live_runner_alone(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    # The wrapper stamps parked_state=failed before the process exits; while
    # the pid is still alive there is no dead-runner evidence yet.
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(
        make_status(
            phase="parked",
            parked_state="failed",
            last_heartbeat=_FRESH,
            worktree=str(tmp_path / "wt-41"),
        ),
        fleet_root,
    )
    (fleet_root / "41" / "runner.pid").write_text("4242\n")
    seams: TickSeams = make_seams(pid_alive=_always_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.skipped_alive == (41,)
    assert outcome.reaped == ()
    assert fake_board.issue_state(41) == "Doing"
    assert "fleet:claimed" in fake_board.issues[41].tags


def test_reap_skips_a_deliberate_park_with_tag_even_when_stale_and_dead(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed", "fleet:awaiting-pr-approval"])
    write(
        make_status(
            phase="parked", parked_state="awaiting-pr-approval", last_heartbeat=_STALE
        ),
        fleet_root,
    )
    seams: TickSeams = make_seams(pid_alive=_never_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.skipped_parked == (41,)
    assert outcome.reaped == ()
    assert fake_board.issue_state(41) == "Doing"
    assert "fleet:claimed" in fake_board.issues[41].tags


def test_reap_skips_a_deliberate_park_without_tag_even_when_stale_and_dead(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    # No fleet:* parked tag, but a non-failed parked_state is still deliberate.
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(
        make_status(phase="parked", parked_state="qa-ready", last_heartbeat=_STALE),
        fleet_root,
    )
    seams: TickSeams = make_seams(pid_alive=_never_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.skipped_parked == (41,)
    assert outcome.reaped == ()
    assert fake_board.issue_state(41) == "Doing"


def test_reap_recovers_a_claim_whose_runner_never_started(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    marker_dir: Path = fleet_root / "41"
    marker_dir.mkdir(parents=True)
    (marker_dir / "claimed-at").write_text(_STALE + "\n")  # no status.json, no pid file
    seams: TickSeams = make_seams(pid_alive=_always_alive)
    outcome: ReapOutcome = reap_pass(seams, make_supervisor_config(), now=_NOW)
    assert outcome.reaped == (41,)
    assert fake_board.issue_state(41) == "To Do"


def test_tick_runs_finalize_then_reap_then_claim(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    # One merged slice to finalize, one dead runner to reap, cap 2: after the
    # ordered passes the tick claims the reaped slice again (attempt 2) plus a
    # fresh one — proof that finalize/reap run before claim.
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(41, title="feat: example", state="Doing", tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_ANCIENT, worktree=str(tmp_path / "gone")),
          fleet_root)
    make_issue(50, title="feat: fresh slice")
    seams: TickSeams = make_seams(
        pid_alive=_never_alive, run_git=_RecordingGitRunner()
    )
    assert run_tick(seams, make_supervisor_config()) == 0
    assert fake_cleaner.cleaned == ["feat/slice-40-merged"]
    assert fake_launcher.launches == [
        (41, "feat/slice-41-example-a2", 2),
        (50, "feat/slice-50-fresh-slice", 1),
    ]
    assert fake_board.issue_state(40) == "Done"
    assert fake_board.issue_state(41) == "Doing"
    assert fake_board.issue_state(50) == "Doing"
