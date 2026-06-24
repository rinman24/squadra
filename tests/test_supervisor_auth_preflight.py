"""Unit tests for the tick's claim-only claude-auth preflight (F4, ADR-0002 §4).

The F4 cutover made finalize cleanup deterministic (no LLM), so the auth probe now
guards the **claim/launch path only** — the contained runner is the fleet's single
claude call. A tick with a claimable slice within budget probes ``claude`` auth
first; a failed probe drops the claim/launch decisions (board + sandbox teardown /
finalize still run) and retries next tick. Idle, saturated, and finalize-only ticks
never probe.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from typing import Final

import pytest

from squadra.config import SquadraConfig
from squadra.constants import FLEET_MODEL
from squadra.domain import Finalized, Lifecycle, SandboxRunning
from squadra.status import FleetStatus, write
from squadra.supervisor import (
    TickSeams,
    _claude_auth_ok,  # pyright: ignore[reportPrivateUsage]
    run_tick,
)
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# make_seams, make_config are provided by tests/conftest.py

_PROBE_COMMAND: Final = [
    "claude",
    "-p",
    "reply READY",
    "--dangerously-skip-permissions",
    "--model",
    FLEET_MODEL,
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def test_probe_fails_on_timeout(make_probe_runner: Callable[..., _FakeProbeRunner]) -> None:
    runner: _FakeProbeRunner = make_probe_runner(
        raises=subprocess.TimeoutExpired(cmd="claude", timeout=120)
    )
    assert _claude_auth_ok(runner) is False


def test_probe_fails_when_claude_is_not_installed(
    make_probe_runner: Callable[..., _FakeProbeRunner],
) -> None:
    runner: _FakeProbeRunner = make_probe_runner(raises=FileNotFoundError("claude"))
    assert _claude_auth_ok(runner) is False


# --- tick gating: claim-only ---------------------------------------------------


def test_claimable_work_triggers_the_probe(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")  # claimable within budget
    probe: _RecordingProbe = make_probe(True)
    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0
    assert probe.calls == 1


def test_dead_auth_skips_claim_but_the_slice_stays_queued(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0
    assert probe.calls == 1
    # No claim happened — the slice is untouched, no launch.
    assert fake_board.item_state(50) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[50].tags
    assert fake_sandbox.launches == []


def test_dead_auth_still_finalizes_a_merged_slice(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Finalize cleanup is deterministic (no claude), so a dead-auth tick still
    # finalizes — the auth gate is claim-only now (ADR-0002 decision 4).
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/slice-40-merged"), fleet_root)
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(50, title="feat: fresh slice")  # claim work makes the tick probe
    probe: _RecordingProbe = make_probe(False)

    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0

    assert probe.calls == 1
    assert fake_cleanup.deleted_branches == ["feat/slice-40-merged"]
    assert any(isinstance(event, Finalized) for event in fake_board.comments[40])
    assert fake_board.item_state(50) == Lifecycle.QUEUED  # claim skipped


def test_idle_tick_never_probes(
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_finalize_only_tick_never_probes(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Only finalize work (no claimable slice): the deterministic finalize needs no
    # claude, so the tick must not pay for the probe.
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/slice-40-merged"), fleet_root)
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_saturated_tick_never_probes(
    fleet_root: Path,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Cap 2, two fresh claimed runners in flight: zero claim budget, a queued
    # candidate alone must not trigger the probe.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed("squadra-slice-41", SandboxRunning())
    make_issue(42, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(
        make_status(issue_id=42, runner_id="runner-42-a1", last_heartbeat=_now()),
        fleet_root,
    )
    fake_sandbox.seed("squadra-slice-42", SandboxRunning())
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(auth_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_dead_auth_emits_one_log_line_naming_the_skipped_claim(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")  # pending claim work
    assert run_tick(make_seams(auth_ok=make_probe(False)), make_config()) == 0
    out: str = capsys.readouterr().out
    auth_lines: list[str] = [line for line in out.splitlines() if "auth-unavailable" in line]
    assert len(auth_lines) == 1
    assert "claim" in auth_lines[0]
