"""Shared fixtures for flotilla fleet unit tests (ADR-0007 tooling)."""

from collections.abc import Callable
import dataclasses
from pathlib import Path

import pytest

from flotilla.status import FleetStatus
from flotilla.supervisor import SupervisorConfig, TickSeams
from tests.helpers.fleet_fakes import FakeBoard, FakeCleaner, FakeIssue, FakeLauncher


@pytest.fixture
def fleet_root(tmp_path: Path) -> Path:
    """Isolated fleet root so tests never touch a real .claude/fleet."""
    return tmp_path / "fleet"


@pytest.fixture
def default_status() -> FleetStatus:
    """Stable, valid status instance for slice Issue #41."""
    return FleetStatus(
        issue_id=41,
        runner_id="runner-41-a1",
        branch="feat/slice-41-example",
        worktree="/work/.claude/worktrees/feat+slice-41-example",
        pr_url=None,
        phase="claiming",
        parked_state=None,
        worker_roster=(),
        started_at="2026-06-10T12:00:00+00:00",
        last_heartbeat="2026-06-10T12:00:00+00:00",
        attempt=1,
        last_error=None,
    )


@pytest.fixture
def make_status(default_status: FleetStatus) -> Callable[..., FleetStatus]:
    """Factory fixture — call with field overrides to produce a FleetStatus."""

    def _factory(**overrides: object) -> FleetStatus:
        return dataclasses.replace(default_status, **overrides)

    return _factory


@pytest.fixture
def fake_board() -> FakeBoard:
    """Empty in-memory ADO board; seed with .add_issue(...)."""
    return FakeBoard()


@pytest.fixture
def make_issue(fake_board: FakeBoard) -> Callable[..., FakeIssue]:
    """Factory fixture — build a board Issue (To Do, untagged) and seed it."""

    def _factory(issue_id: int, **overrides: object) -> FakeIssue:
        base = FakeIssue(issue_id=issue_id, title=f"feat: slice {issue_id}", state="To Do")
        issue: FakeIssue = dataclasses.replace(base, **overrides)
        fake_board.add_issue(issue)
        return issue

    return _factory


@pytest.fixture
def fake_launcher() -> FakeLauncher:
    """Spy launcher recording (issue_id, branch, attempt) per launch."""
    return FakeLauncher()


@pytest.fixture
def fake_cleaner() -> FakeCleaner:
    """Spy cleaner recording the branches it was asked to clean."""
    return FakeCleaner()


def _always_ok() -> bool:
    """Auth-probe stub that always passes — tests never run the real probe."""
    return True


@pytest.fixture
def make_seams(
    fake_board: FakeBoard, fake_launcher: FakeLauncher, fake_cleaner: FakeCleaner
) -> Callable[..., TickSeams]:
    """Factory fixture — tick seams over the shared fakes (override per test)."""

    def _factory(**overrides: object) -> TickSeams:
        base = TickSeams(
            ado=fake_board,
            launcher=fake_launcher,
            cleaner=fake_cleaner,
            auth_ok=_always_ok,
        )
        return dataclasses.replace(base, **overrides)

    return _factory


@pytest.fixture
def make_supervisor_config(fleet_root: Path, tmp_path: Path) -> Callable[..., SupervisorConfig]:
    """Factory fixture — supervisor config bound to the isolated fleet root."""

    def _factory(**overrides: object) -> SupervisorConfig:
        base = SupervisorConfig(
            fleet_root=fleet_root,
            fleet_home=tmp_path,
            cap=2,
            max_attempts=3,
            epic_ids=(),
        )
        return dataclasses.replace(base, **overrides)

    return _factory
