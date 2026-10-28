"""Unit tests for the tick's claim-only remote auth preflight (#201, SQ7, ADR-0002 §4/§11).

Claiming an increment does host-side git remote ops (worktree create off ``origin/main``,
then push) with the target remote's credential — the path that 401'd on 2026-06-22
when the ``fleet-ado-pat`` Key Vault secret expired. The remote preflight mirrors the
claude auth preflight: a tick with a claimable increment within budget probes the
remote first (``git ls-remote`` against the target remote); a failed probe drops the
claim/launch decisions (finalize + reap still run) and retries next tick. The remote
probe runs *before* the claude probe and short-circuits, so a dead credential never
pays to spawn claude. Idle, saturated, and finalize-only ticks never probe. The
rejection line names the credential tried, per host.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from squadra.config import SquadraConfig
from squadra.domain import Finalized, Lifecycle, SandboxRunning
from squadra.remote_auth import RemoteAuthProbe, RemoteCredential, credential_for
from squadra.secrets import secret_names_from_env
from squadra.status import FleetStatus, write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# fake_worktree, make_seams, make_config are provided by tests/conftest.py


_ADO_URL = "https://dev.azure.com/your-org/example-project/_git/app-backend"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _RecordingProbe:
    """Auth-probe stub with a fixed verdict, recording how often it ran."""

    def __init__(self, result: bool, credential: RemoteCredential | None = None) -> None:
        self.result = result
        self.credential: RemoteCredential | None = (
            credential if credential is not None else credential_for(_ADO_URL)
        )
        self.calls = 0

    def __call__(self) -> RemoteAuthProbe:
        self.calls += 1
        host: str | None = self.credential.host if self.credential is not None else None
        return RemoteAuthProbe(ok=self.result, host=host, credential=self.credential)


@pytest.fixture
def make_probe() -> Callable[[bool], _RecordingProbe]:
    """Factory fixture — recording auth-probe stub returning ``result``."""

    def _factory(result: bool) -> _RecordingProbe:
        return _RecordingProbe(result)

    return _factory


def _must_not_run() -> bool:
    raise AssertionError("this probe must not run when the remote preflight fails first")


# --- tick gating: claim-only ---------------------------------------------------


def test_claimable_work_triggers_the_remote_probe(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh increment")  # claimable within budget
    probe: _RecordingProbe = make_probe(True)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 1


def test_dead_credential_skips_claim_but_the_increment_stays_queued(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    make_issue(50, title="feat: fresh increment")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 1
    # No claim happened — the increment is untouched, no launch, no board mutation.
    assert fake_board.item_state(50) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[50].tags
    assert fake_sandbox.launches == []


def test_dead_credential_short_circuits_before_the_claude_probe(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # A dead credential must not pay to spawn the (more expensive) claude probe.
    make_issue(50, title="feat: fresh increment")
    assert (
        run_tick(make_seams(remote_auth=make_probe(False), auth_ok=_must_not_run), make_config())
        == 0
    )
    assert fake_board.item_state(50) == Lifecycle.QUEUED


def test_dead_credential_still_finalizes_a_merged_increment(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Finalize is deterministic (no claude, no remote claim), so a dead-credential tick
    # still finalizes — the remote gate is claim-only, exactly like the claude gate.
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/increment-40-merged"), fleet_root)
    fake_board.completed_prs["feat/increment-40-merged"] = "https://pr/40"
    make_issue(50, title="feat: fresh increment")  # claim work makes the tick probe
    probe: _RecordingProbe = make_probe(False)

    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0

    assert probe.calls == 1
    assert fake_cleanup.deleted_branches == ["feat/increment-40-merged"]
    assert any(isinstance(event, Finalized) for event in fake_board.comments[40])
    assert fake_board.item_state(50) == Lifecycle.QUEUED  # claim skipped


def test_healthy_credential_proceeds_to_claim_and_launch(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # A healthy credential (and healthy claude) lets the claim proceed end-to-end.
    make_issue(50, title="feat: fresh increment")
    probe: _RecordingProbe = make_probe(True)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 1
    assert fake_board.item_state(50) == Lifecycle.ACTIVE
    assert "fleet:claimed" in fake_board.issues[50].tags
    assert [spec.item_id for spec in fake_sandbox.launches] == [50]


def test_idle_tick_never_probes_the_remote(
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 0


def test_finalize_only_tick_never_probes_the_remote(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Only finalize work (no claimable increment): finalize claims nothing, so the
    # tick must not pay for the PAT probe.
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(make_status(issue_id=40, runner_id="r", branch="feat/increment-40-merged"), fleet_root)
    fake_board.completed_prs["feat/increment-40-merged"] = "https://pr/40"
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 0


def test_saturated_tick_never_probes_the_remote(
    fleet_root: Path,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    # Cap 2, two fresh claimed runners in flight: zero claim budget, so a queued
    # candidate alone must not trigger the remote probe.
    make_issue(41, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed("squadra-increment-41", SandboxRunning())
    make_issue(42, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(issue_id=42, runner_id="runner-42-a1", last_heartbeat=_now()), fleet_root)
    fake_sandbox.seed("squadra-increment-42", SandboxRunning())
    make_issue(50, title="feat: fresh increment")
    probe: _RecordingProbe = make_probe(False)
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    assert probe.calls == 0


def _rejection_line(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    probe: _RecordingProbe,
) -> str:
    """Run a tick with pending claim work and a failing probe; return its one rejection line."""
    make_issue(50, title="feat: fresh increment")  # pending claim work
    assert run_tick(make_seams(remote_auth=probe), make_config()) == 0
    out: str = capsys.readouterr().out
    lines: list[str] = [line for line in out.splitlines() if "skipped the claim/launch" in line]
    assert len(lines) == 1
    return lines[0]


def test_ado_remote_rejection_is_the_key_vault_rotation_line(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    make_probe: Callable[[bool], _RecordingProbe],
) -> None:
    line: str = _rejection_line(capsys, make_issue, make_seams, make_config, make_probe(False))
    secret: str = secret_names_from_env().ado_pat
    # Byte-for-byte the pre-SQ7 ADO line: an ADO remote's behaviour is unchanged.
    assert line.endswith(
        "ado PAT rejected on the git auth path (git ls-remote against the target "
        "remote failed) — skipped the claim/launch decisions; running the non-claim "
        f"decisions only, retrying next tick. Rotate the {secret!r} Key Vault secret "
        "(it is expired or lacks Code (Read & Write)): see the README 'Supervisor' "
        "section and the consuming repo's runbook docs/contributing/afk-fleet.md -> "
        "'Key Vault secrets & PAT rotation'."
    )


def test_github_https_rejection_names_the_gh_credential_not_key_vault(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    probe = _RecordingProbe(False, credential_for("https://github.com/owner/app.git"))
    line: str = _rejection_line(capsys, make_issue, make_seams, make_config, probe)
    assert "github.com token from the gh CLI rejected" in line
    assert "gh auth status" in line
    assert "GH_TOKEN" in line
    assert "Key Vault" not in line
    assert "ado PAT" not in line


def test_ssh_rejection_names_the_hosts_ssh_key(
    capsys: pytest.CaptureFixture[str],
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    probe = _RecordingProbe(False, credential_for("git@github.com:owner/app.git"))
    line: str = _rejection_line(capsys, make_issue, make_seams, make_config, probe)
    assert "ssh key for github.com rejected" in line
    assert "ssh -T git@github.com" in line
    assert "Key Vault" not in line


def test_no_credential_rejection_names_the_host_and_skips_the_claim(
    capsys: pytest.CaptureFixture[str],
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # An unknown host fails closed: the probe never ran ls-remote, and no claim.
    def _unknown() -> RemoteAuthProbe:
        return RemoteAuthProbe(ok=False, host="gitlab.example.com")

    make_issue(50, title="feat: fresh increment")
    assert run_tick(make_seams(remote_auth=_unknown), make_config()) == 0
    out: str = capsys.readouterr().out
    assert "no git credential for the target remote (host: gitlab.example.com)" in out
    assert "Key Vault" not in out
    assert fake_board.item_state(50) == Lifecycle.QUEUED
