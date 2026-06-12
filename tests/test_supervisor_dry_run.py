"""Regression tests for the dry-run tick: it physically cannot mutate.

The 2026-06-11 incident this guards against: a ``FLEET_MAX_RUNNERS=0`` tick
billed as a "read-only smoke" finalized two already-Done Issues, because the
cap only zeroes the *claim* budget — finalize and reap still mutate ADO. The
fix is a boundary, not a flag: ``dry_run_seams`` wraps every side-effecting
seam, so a dry-run tick with finalize-, reap- AND claim-eligible work performs
zero writes anywhere while still reporting every would-be action. Any future
call site that mutates during a dry-run tick fails the test below.
"""

from collections.abc import Callable
import copy
from pathlib import Path

import pytest

from flotilla import supervisor
from flotilla.status import FleetStatus, load, write
from flotilla.supervisor import (
    AzCliAdo,
    ClaudeCleanup,
    DryRunCleaner,
    DryRunLauncher,
    ReadOnlyAdoClient,
    SupervisorConfig,
    TickSeams,
    TmuxLauncher,
    build_seams,
    dry_run_seams,
    run_tick,
)
from tests.helpers.fleet_fakes import FakeBoard, FakeCleaner, FakeIssue, FakeLauncher

# fleet_root, make_status, fake_board, make_issue, fake_launcher, fake_cleaner,
# make_seams, make_supervisor_config are provided by tests/conftest.py

_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock (run_tick uses now())

_MUTATING_CALLS: tuple[str, ...] = ("set_state", "add_tag", "remove_tag", "add_comment")


def _never_alive(_pid: int) -> bool:
    return False


def test_dry_run_tick_mutates_nothing_and_reports_every_would_be_action(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Seed work for all three passes:
    # #40 finalize-eligible (Done + fleet:claimed + completed PR),
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    # #41 reap-eligible (Doing + fleet:claimed, ancient heartbeat, dead pid,
    # a real worktree directory tempting the archiver),
    make_issue(41, title="feat: example", state="Doing", tags=["fleet:claimed"])
    worktree: Path = tmp_path / "worktrees" / "feat+slice-41-example"
    worktree.mkdir(parents=True)
    (worktree / "scratch.txt").write_text("evidence")
    write(
        make_status(phase="tdd", last_heartbeat=_ANCIENT, worktree=str(worktree)), fleet_root
    )
    # #50 claim-eligible (To Do, untagged, unblocked).
    make_issue(50, title="feat: fresh slice")
    issues_before = copy.deepcopy(fake_board.issues)
    status_before: FleetStatus = load(41, fleet_root)

    seams: TickSeams = dry_run_seams(make_seams(pid_alive=_never_alive))
    assert run_tick(seams, make_supervisor_config()) == 0

    # Zero board mutations: no state change, no tag add/remove, no comment.
    assert [call for call in fake_board.calls if call[0] in _MUTATING_CALLS] == []
    assert fake_board.issues == issues_before
    assert fake_board.comments == {}
    # Zero runner launches, zero claude spawns (cleanup), zero local writes.
    assert fake_launcher.launches == []
    assert fake_cleaner.cleaned == []
    assert (worktree / "scratch.txt").read_text() == "evidence"  # not archived
    assert not (fleet_root / "41" / "archive").exists()
    assert load(41, fleet_root) == status_before  # status file untouched
    assert not (fleet_root / "50").exists()  # no claimed-at marker

    # The would-be actions are still planned and reported.
    out: str = capsys.readouterr().out
    assert "WOULD run /cleanup-merged-branches for feat/slice-40-merged" in out
    assert "WOULD remove tag 'fleet:claimed' from #40" in out
    assert "WOULD comment on #40" in out
    assert "finalize pass: finalized=[40]" in out
    assert "WOULD archive worktree" in out
    assert "WOULD move #41 to 'To Do'" in out
    assert "reap pass: reaped=[41]" in out
    assert "WOULD move #50 to 'Doing'" in out
    assert "WOULD add tag 'fleet:claimed' to #50" in out
    assert "WOULD launch runner for #50" in out
    # The unmutated board still shows #41 in Doing, so the dry-run claim plan
    # counts it inflight (cap 2 -> budget 1): #50 is the one claim reported.
    assert "claim pass: inflight=[41] claimed=[50]" in out


def test_dry_run_tick_skips_the_auth_probe_but_still_runs_all_passes(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Claude-dependent work pending (a claimable To Do Issue) normally
    # triggers the auth preflight; in dry-run even the probe is a side effect
    # (a spawned `claude -p`), so it must be skipped-and-logged instead.
    make_issue(50, title="feat: fresh slice")

    def _probe_must_not_run() -> bool:
        raise AssertionError("dry-run must never invoke the real auth probe")

    seams: TickSeams = dry_run_seams(make_seams(auth_ok=_probe_must_not_run))
    assert run_tick(seams, make_supervisor_config()) == 0
    out: str = capsys.readouterr().out
    assert "WOULD run the claude auth preflight" in out
    assert "claim pass: inflight=[] claimed=[50]" in out


def test_build_seams_wraps_every_side_effecting_seam_in_dry_run(
    make_supervisor_config: Callable[..., SupervisorConfig],
) -> None:
    config: SupervisorConfig = make_supervisor_config()
    real: TickSeams = build_seams(config)
    assert isinstance(real.ado, AzCliAdo)
    assert isinstance(real.launcher, TmuxLauncher)
    assert isinstance(real.cleaner, ClaudeCleanup)

    dry: TickSeams = build_seams(config, dry_run=True)
    assert isinstance(dry.ado, ReadOnlyAdoClient)
    assert isinstance(dry.launcher, DryRunLauncher)
    assert isinstance(dry.cleaner, DryRunCleaner)
    # The wrapped collaborators must differ from the production defaults for
    # every side-effecting seam; pid_alive stays real (a pure read).
    assert dry.run_git is not real.run_git
    assert dry.auth_ok is not real.auth_ok
    assert dry.archive_worktree is not real.archive_worktree
    assert dry.update_status is not real.update_status
    assert dry.write_claimed_at is not real.write_claimed_at
    assert dry.pid_alive is real.pid_alive


def test_read_only_ado_client_passes_reads_through(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
) -> None:
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    client = ReadOnlyAdoClient(fake_board)
    assert [ref.issue_id for ref in client.issues_in_state("Done")] == [40]
    assert client.issue_state(40) == "Done"
    assert client.completed_pr_url("feat/slice-40-merged") == "https://pr/40"
    assert client.issue_links(40).parent_id is None


def test_main_engages_dry_run_from_the_flag_and_the_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: list[bool] = []

    def _spy_build_seams(config: SupervisorConfig, *, dry_run: bool = False) -> TickSeams:
        seen.append(dry_run)
        raise SystemExit(0)  # never reach the real tick

    monkeypatch.setattr(supervisor, "build_seams", _spy_build_seams)
    argv: list[str] = ["--fleet-root", str(tmp_path), "--fleet-home", str(tmp_path)]
    with pytest.raises(SystemExit):
        supervisor.main([*argv, "--dry-run"])
    monkeypatch.setattr(supervisor, "FLEET_DRY_RUN", True)
    with pytest.raises(SystemExit):
        supervisor.main(argv)
    monkeypatch.setattr(supervisor, "FLEET_DRY_RUN", False)
    with pytest.raises(SystemExit):
        supervisor.main(argv)
    assert seen == [True, True, False]
