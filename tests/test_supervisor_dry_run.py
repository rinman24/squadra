"""Regression tests for the dry-run tick: it physically cannot mutate (F4, #168).

The 2026-06-11 incident this guards against: a ``FLEET_MAX_RUNNERS=0`` tick billed
as a "read-only smoke" finalized two already-done items, because the cap only zeroes
the *claim* budget — the non-claim decisions still mutate. The fix is a boundary,
not a flag: ``dry_run_seams`` wraps every mutating seam, so a dry-run tick with
finalize-, reap- AND claim-eligible work performs zero writes anywhere (board,
sandbox, cleanup, worktree, slice-context, local fleet state) while still reporting
every would-be action. The exhaustiveness invariant is checked directly: every
mutating ``TickSeams`` field differs from the production default under dry-run, and
the read seams stay real.
"""

from collections.abc import Callable
import copy
from pathlib import Path

import pytest

from flotilla import supervisor
from flotilla.board import AzCliAdo
from flotilla.cleanup import DeterministicCleanup
from flotilla.config import FlotillaConfig
from flotilla.domain import Lifecycle, SandboxExited
from flotilla.dry_run import DryRunCleanup, DryRunWorktree
from flotilla.sandbox import ComposeSandbox, DryRunSandbox
from flotilla.status import FleetStatus, load, write
from flotilla.supervisor import (
    ReadOnlyBoard,
    TickSeams,
    build_seams,
    dry_run_seams,
    run_tick,
)
from flotilla.worktree import GitWorktreeAccess
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# fake_worktree, make_seams, make_config are provided by tests/conftest.py

_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock
_MUTATING_CALLS: tuple[str, ...] = ("set_state", "add_tag", "remove_tag", "add_comment")


def test_dry_run_tick_mutates_nothing_and_reports_every_would_be_action(
    fleet_root: Path,
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_cleanup: FakeCleanup,
    fake_worktree: FakeWorktree,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., FlotillaConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Seed work that exercises every seam in one tick:
    # #40 finalize-eligible (done + fleet:claimed + completed PR),
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(
        make_status(issue_id=40, runner_id="r-40", branch="feat/slice-40-merged"),
        fleet_root,
    )
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    # #41 reap-eligible (active + fleet:claimed, container exited non-zero -> crash),
    make_issue(41, title="feat: example", state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(phase="tdd", last_heartbeat=_ANCIENT), fleet_root)
    fake_sandbox.seed("flotilla-slice-41", SandboxExited(exit_code=1))
    # #50 claim-eligible (queued, untagged, unblocked).
    make_issue(50, title="feat: fresh slice")

    issues_before = copy.deepcopy(fake_board.issues)
    status_before: FleetStatus = load(41, fleet_root)

    seams: TickSeams = dry_run_seams(make_seams())
    assert run_tick(seams, make_config()) == 0

    # Zero board mutations.
    assert [call for call in fake_board.calls if call[0] in _MUTATING_CALLS] == []
    assert fake_board.issues == issues_before
    assert fake_board.comments == {}
    # Zero sandbox mutations (no launch / teardown), zero cleanup, zero worktree change.
    assert fake_sandbox.launches == []
    assert fake_sandbox.teardowns == []
    assert fake_cleanup.deleted_branches == []
    assert fake_cleanup.composed_down == []
    assert fake_worktree.created == []
    assert fake_worktree.archived == []
    assert fake_worktree.prune_count == 0
    # Zero local fleet-state writes.
    assert load(41, fleet_root) == status_before  # status file untouched
    assert not (fleet_root / "50").exists()  # no claimed-at marker

    # The would-be actions are still planned and reported.
    out: str = capsys.readouterr().out
    assert "WOULD delete branch 'feat/slice-40-merged'" in out
    assert "WOULD compose down -v project 'flotilla-slice-40'" in out
    assert "WOULD remove tag 'fleet:claimed' from #40" in out
    assert "WOULD comment on #40" in out
    assert "WOULD archive worktree" in out  # reap of #41
    assert "WOULD move #41 to queued" in out
    assert "WOULD tear down sandbox flotilla-slice-41" in out
    assert "WOULD create worktree" in out  # claim of #50
    assert "WOULD inject slice context" in out
    assert "WOULD move #50 to active" in out
    assert "WOULD add tag 'fleet:claimed' to #50" in out
    assert "WOULD launch sandbox flotilla-slice-50" in out


def test_dry_run_skips_the_auth_probe_but_still_plans_the_claim(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., FlotillaConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Claimable work normally triggers the auth preflight; in dry-run even the probe
    # is a side effect (a spawned `claude -p`), so it must be skipped-and-logged.
    make_issue(50, title="feat: fresh slice")

    def _probe_must_not_run() -> bool:
        raise AssertionError("dry-run must never invoke the real auth probe")

    seams: TickSeams = dry_run_seams(make_seams(auth_ok=_probe_must_not_run))
    assert run_tick(seams, make_config()) == 0
    out: str = capsys.readouterr().out
    assert "WOULD run the claude auth preflight" in out
    assert "WOULD launch sandbox flotilla-slice-50" in out
    assert fake_sandbox.launches == []


def test_dry_run_skips_the_pat_probe_but_still_plans_the_claim(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., FlotillaConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Claimable work normally triggers the PAT preflight; in dry-run even the probe
    # is a side effect (a spawned `git ls-remote`), so it must be skipped-and-logged.
    make_issue(50, title="feat: fresh slice")

    def _probe_must_not_run() -> bool:
        raise AssertionError("dry-run must never invoke the real PAT probe")

    seams: TickSeams = dry_run_seams(make_seams(pat_ok=_probe_must_not_run))
    assert run_tick(seams, make_config()) == 0
    out: str = capsys.readouterr().out
    assert "WOULD run the ADO PAT auth preflight" in out
    assert "WOULD launch sandbox flotilla-slice-50" in out
    assert fake_sandbox.launches == []


def test_build_seams_wraps_every_mutating_seam_in_dry_run(
    make_config: Callable[..., FlotillaConfig],
) -> None:
    config: FlotillaConfig = make_config()
    real: TickSeams = build_seams(config)
    assert isinstance(real.ado, AzCliAdo)
    assert isinstance(real.sandbox, ComposeSandbox)
    assert isinstance(real.cleanup, DeterministicCleanup)
    assert isinstance(real.worktree, GitWorktreeAccess)

    dry: TickSeams = build_seams(config, dry_run=True)
    # Every mutating seam is wrapped by its write-blocking decorator.
    assert isinstance(dry.ado, ReadOnlyBoard)
    assert isinstance(dry.sandbox, DryRunSandbox)
    assert isinstance(dry.cleanup, DryRunCleanup)
    assert isinstance(dry.worktree, DryRunWorktree)
    # The callable mutating seams differ from the production defaults...
    assert dry.pat_ok is not real.pat_ok
    assert dry.auth_ok is not real.auth_ok
    assert dry.write_context is not real.write_context
    assert dry.update_status is not real.update_status
    assert dry.write_claimed_at is not real.write_claimed_at
    # ...while the fact-gathering READ seams stay real (pure reads, plan needs them).
    assert dry.read_manifest is real.read_manifest
    assert dry.commits_present is real.commits_present


def test_read_only_board_passes_reads_through(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
) -> None:
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"
    client = ReadOnlyBoard(fake_board)
    assert [ref.item_id for ref in client.items_in_state(Lifecycle.DONE)] == [40]
    assert client.item_state(40) == Lifecycle.DONE
    assert client.completed_pr_url("feat/slice-40-merged") == "https://pr/40"
    assert client.item_links(40).parent_id is None


def test_main_engages_dry_run_from_the_flag_and_the_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: list[bool] = []

    def _spy_build_seams(config: FlotillaConfig, *, dry_run: bool = False) -> TickSeams:
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
