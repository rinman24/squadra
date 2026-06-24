"""Unit tests for the tick's claim-only ADO-PAT preflight (#201, ADR-0002 §4/§11).

Claiming a slice does host-side git remote ops (worktree create off ``origin/main``,
then push) over HTTPS+PAT — the exact path that 401'd on 2026-06-22 when the
``fleet-ado-pat`` Key Vault secret expired. The PAT preflight mirrors the claude
auth preflight: a tick with a claimable slice within budget probes the PAT first
(``git ls-remote`` against the target remote); a failed probe drops the
claim/launch decisions (finalize + reap still run) and retries next tick. The PAT
probe runs *before* the claude probe and short-circuits, so a dead PAT never pays
to spawn claude. Idle, saturated, and finalize-only ticks never probe.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from squadra.config import SquadraConfig
from squadra.domain import Finalized, Lifecycle, SandboxRunning
from squadra.secrets import secret_names_from_env
from squadra.status import FleetStatus, write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# fake_worktree, make_seams, make_config are provided by tests/conftest.py


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def _must_not_run() -> bool:
    raise AssertionError("this probe must not run when the PAT preflight fails first")


# --- tick gating: claim-only ---------------------------------------------------


def test_claimable_work_triggers_the_pat_probe(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")  # claimable within budget
    probe: _RecordingProbe = make_probe(True)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 1


def test_dead_pat_skips_claim_but_the_slice_stays_queued(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 1
    # No claim happened — the slice is untouched, no launch, no board mutation.
    assert fake_board.item_state(50) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[50].tags
    assert fake_sandbox.launches == []


def test_dead_pat_short_circuits_before_the_claude_probe(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # A dead PAT must not pay to spawn the (more expensive) claude probe.
    make_issue(50, title="feat: fresh slice")
    assert run_tick(make_seams(pat_ok=make_probe(False), auth_ok=_must_not_run), make_config()) == 0
    assert fake_board.item_state(50) == Lifecycle.QUEUED


def test_dead_pat_still_finalizes_a_merged_slice(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Finalize is deterministic (no claude, no remote claim), so a dead-PAT tick
    # still finalizes — the PAT gate is claim-only, exactly like the claude gate.
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/slice-40-merged"), fleet_root)
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    make_issue(50, title="feat: fresh slice")  # claim work makes the tick probe
    probe: _RecordingProbe = make_probe(False)

    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0

    assert probe.calls == 1
    assert fake_cleanup.deleted_branches == ["feat/slice-40-merged"]
    assert any(isinstance(event, Finalized) for event in fake_board.comments[40])
    assert fake_board.item_state(50) == Lifecycle.QUEUED  # claim skipped


def test_healthy_pat_proceeds_to_claim_and_launch(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # A healthy PAT (and healthy claude) lets the claim proceed end-to-end.
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(True)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 1
    assert fake_board.item_state(50) == Lifecycle.ACTIVE
    assert "fleet:claimed" in fake_board.issues[50].tags
    assert [spec.item_id for spec in fake_sandbox.launches] == [50]


def test_idle_tick_never_probes_the_pat(
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_finalize_only_tick_never_probes_the_pat(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Only finalize work (no claimable slice): finalize claims nothing, so the
    # tick must not pay for the PAT probe.
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/slice-40-merged"), fleet_root)
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_saturated_tick_never_probes_the_pat(
    fleet_root: Path,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Cap 2, two fresh claimed runners in flight: zero claim budget, so a queued
    # candidate alone must not trigger the PAT probe.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed("squadra-slice-41", SandboxRunning())
    make_issue(42, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(issue_id=42, runner_id="runner-42-a1", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed("squadra-slice-42", SandboxRunning())
    make_issue(50, title="feat: fresh slice")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(pat_ok=probe), make_config()) == 0
    assert probe.calls == 0


def test_dead_pat_emits_one_actionable_log_line(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh slice")  # pending claim work
    assert run_tick(make_seams(pat_ok=make_probe(False)), make_config()) == 0
    out: str = capsys.readouterr().out
    pat_lines: list[str] = [line for line in out.splitlines() if "ado PAT rejected" in line]
    assert len(pat_lines) == 1
    line: str = pat_lines[0]
    assert secret_names_from_env().ado_pat in line  # names the Key Vault secret
    assert "Key Vault" in line
    assert "Read & Write" in line  # points at the likely-wrong scope
    assert "claim" in line  # makes clear claim/launch was skipped
