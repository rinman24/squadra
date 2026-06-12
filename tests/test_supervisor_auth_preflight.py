"""Unit tests for the tick's lazy claude-auth preflight (ADR-0007, Task #98).

A tick with pending claude-dependent work (finalize or claim) probes ``claude``
auth first; a failed probe degrades the tick to reap-only (az + git) so no
board state is mutated by passes that would need a live claude. Idle and
saturated ticks never probe.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from typing import Final

import pytest

from flotilla.constants import FLEET_MODEL
from flotilla.status import FleetStatus, write
from flotilla.supervisor import (
    SupervisorConfig,
    TickSeams,
    _claude_auth_ok,  # pyright: ignore[reportPrivateUsage]
    run_tick,
)
from tests.helpers.fleet_fakes import FakeBoard, FakeCleaner, FakeIssue, FakeLauncher

# fleet_root, make_status, fake_board, make_issue, fake_launcher, fake_cleaner,
# make_seams, make_supervisor_config are provided by tests/conftest.py

_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock (run_tick uses now())
_MUTATING_CALLS: Final = ("set_state", "add_tag", "remove_tag", "add_comment")
# The probe is pinned to the fleet model so a bad FLEET_MODEL fails the
# preflight rather than every claimed runner; no --effort (it does no reasoning).
_PROBE_COMMAND: Final = [
    "claude",
    "-p",
    "reply READY",
    "--dangerously-skip-permissions",
    "--model",
    FLEET_MODEL,
]


def _never_alive(_pid: int) -> bool:
    return False


def _noop_git(_args: Sequence[str]) -> int:
    return 0


def _fresh_heartbeat() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _mutations(board: FakeBoard) -> list[tuple[str, int, str]]:
    return [call for call in board.calls if call[0] in _MUTATING_CALLS]


class _RecordingProbe:
    """Auth-probe stub with a fixed verdict, recording how often it ran."""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.result


@pytest.fixture
def make_probe() -> Callable[[bool], _RecordingProbe]:
    """Factory fixture — recording auth-probe stub returning ``result``."""

    def _factory(result: bool) -> _RecordingProbe:
        return _RecordingProbe(result)

    return _factory


class _FakeProbeRunner:
    """Injected probe runner returning a canned CompletedProcess or raising."""

    def __init__(self, returncode: int, stdout: str, raises: Exception | None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            args=list(args), returncode=self.returncode, stdout=self.stdout, stderr=""
        )


@pytest.fixture
def make_probe_runner() -> Callable[..., _FakeProbeRunner]:
    """Factory fixture — canned subprocess seam for the real probe function."""

    def _factory(
        returncode: int = 0, stdout: str = "", raises: Exception | None = None
    ) -> _FakeProbeRunner:
        return _FakeProbeRunner(returncode=returncode, stdout=stdout, raises=raises)

    return _factory


# --- the real probe function (never spawns a real claude) ---------------------


def test_probe_passes_on_rc_zero_with_ready_in_output(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(returncode=0, stdout="READY\n")
    assert _claude_auth_ok(runner) is True
    assert runner.calls == [_PROBE_COMMAND]


def test_probe_pins_an_explicit_model_into_the_argv(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(returncode=0, stdout="READY\n")
    assert _claude_auth_ok(runner, model="claude-sonnet-4-6") is True
    assert runner.calls[0][-2:] == ["--model", "claude-sonnet-4-6"]


def test_probe_fails_on_nonzero_returncode(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(returncode=1, stdout="READY\n")
    assert _claude_auth_ok(runner) is False


def test_probe_fails_when_ready_is_missing_from_output(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(returncode=0, stdout="Invalid API key\n")
    assert _claude_auth_ok(runner) is False


def test_probe_fails_on_timeout(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(
        raises=subprocess.TimeoutExpired(cmd="claude", timeout=120)
    )
    assert _claude_auth_ok(runner) is False


def test_probe_fails_when_claude_is_not_installed(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(raises=FileNotFoundError("claude"))
    assert _claude_auth_ok(runner) is False


# --- tick gating ---------------------------------------------------------------


def test_dead_auth_tick_mutates_no_board_state_when_nothing_is_reapable(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Pending finalize work (Done + claimed + merged PR) AND pending claim work
    # (budget 1, untagged To Do candidate); the only Doing slice is fresh.
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(41, title="feat: example", state="Doing", tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_fresh_heartbeat()), fleet_root)
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    seams: TickSeams = make_seams(pid_alive=_never_alive, auth_ok=probe)
    assert run_tick(seams, make_supervisor_config()) == 0
    assert probe.calls == 1
    assert _mutations(fake_board) == []
    assert fake_launcher.launches == []
    assert fake_cleaner.cleaned == []
    assert ("issues_in_state", 0, "Doing") in fake_board.calls  # reap still ran


def test_dead_auth_tick_still_reaps_a_dead_runner(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(41, title="feat: example", state="Doing", tags=["fleet:claimed"])
    write(
        make_status(phase="tdd", last_heartbeat=_ANCIENT, worktree=str(tmp_path / "gone")),
        fleet_root,
    )
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    seams: TickSeams = make_seams(pid_alive=_never_alive, run_git=_noop_git, auth_ok=probe)
    assert run_tick(seams, make_supervisor_config()) == 0
    # Reap (az + git only) still requeued the dead runner...
    assert fake_board.issue_state(41) == "To Do"
    assert "fleet:claimed" not in fake_board.issues[41].tags
    assert any("reaped" in comment for comment in fake_board.comments[41])
    # ...while the claude-dependent passes were skipped untouched.
    assert fake_cleaner.cleaned == []
    assert fake_launcher.launches == []
    assert fake_board.issue_state(40) == "Done"
    assert "fleet:claimed" in fake_board.issues[40].tags


def test_idle_tick_never_invokes_the_probe(
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    probe: _RecordingProbe = make_probe(False)
    seams: TickSeams = make_seams(auth_ok=probe)
    assert run_tick(seams, make_supervisor_config()) == 0
    assert probe.calls == 0


def test_saturated_tick_never_invokes_the_probe(
    fleet_root: Path,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Cap 2, two fresh claimed runners in flight: zero claim budget, nothing
    # to finalize — a To Do candidate alone must not trigger the probe.
    make_issue(41, state="Doing", tags=["fleet:claimed"])
    write(make_status(last_heartbeat=_fresh_heartbeat()), fleet_root)
    make_issue(42, state="Doing", tags=["fleet:claimed"])
    write(
        make_status(issue_id=42, runner_id="runner-42-a1", last_heartbeat=_fresh_heartbeat()),
        fleet_root,
    )
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    seams: TickSeams = make_seams(pid_alive=_never_alive, auth_ok=probe)
    assert run_tick(seams, make_supervisor_config()) == 0
    assert probe.calls == 0


def test_passing_probe_runs_finalize_then_reap_then_claim_unchanged(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_launcher: FakeLauncher,
    fake_cleaner: FakeCleaner,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Mirrors the existing ordering test: with the probe passing, the tick
    # finalizes the merged slice, reaps the dead one, then claims both.
    make_issue(40, title="feat: merged", state="Done", tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(41, title="feat: example", state="Doing", tags=["fleet:claimed"])
    write(
        make_status(phase="tdd", last_heartbeat=_ANCIENT, worktree=str(tmp_path / "gone")),
        fleet_root,
    )
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(True)
    seams: TickSeams = make_seams(pid_alive=_never_alive, run_git=_noop_git, auth_ok=probe)
    assert run_tick(seams, make_supervisor_config()) == 0
    assert probe.calls == 1
    assert fake_cleaner.cleaned == ["feat/slice-40-merged"]
    assert fake_launcher.launches == [
        (41, "feat/slice-41-example-a2", 2),
        (50, "feat/slice-50-fresh-slice", 1),
    ]


def test_dead_auth_emits_one_log_line_naming_the_skipped_passes(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_supervisor_config: Callable[..., SupervisorConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")  # pending claim work
    seams: TickSeams = make_seams(auth_ok=make_probe(False))
    assert run_tick(seams, make_supervisor_config()) == 0
    out: str = capsys.readouterr().out
    auth_lines: list[str] = [line for line in out.splitlines() if "auth-unavailable" in line]
    assert len(auth_lines) == 1
    assert "finalize" in auth_lines[0]
    assert "claim" in auth_lines[0]
