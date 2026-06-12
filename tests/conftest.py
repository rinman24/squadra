"""Shared fixtures for flotilla fleet unit tests (ADR-0001 tooling)."""

from collections.abc import Callable
import dataclasses
from pathlib import Path

import pytest

from flotilla.config import (
    ADO_BASIC_STATES,
    DEFAULT_BASE_BRANCH,
    DEFAULT_BRANCH_TEMPLATE,
    DEFAULT_CLEANUP_SKILL,
    DEFAULT_PROVIDER,
    DEFAULT_QA_SKILL,
    DEFAULT_RUNNER_SKILL,
    DEFAULT_TDD_SKILL,
    DEFAULT_WORKTREE_DIR,
    FlotillaConfig,
)
from flotilla.constants import (
    DEFAULT_TAG_PREFIX,
    FLEET_EFFORT,
    FLEET_MODEL,
    HEARTBEAT_INTERVAL_SECONDS,
    STALENESS_THRESHOLD_SECONDS,
)
from flotilla.domain import Lifecycle
from flotilla.status import FleetStatus
from flotilla.supervisor import TickSeams
from tests.helpers.fleet_fakes import FakeBoard, FakeCleaner, FakeIssue, FakeLauncher


@pytest.fixture
def fleet_root(tmp_path: Path) -> Path:
    """Isolated fleet root so tests never touch a real .claude/fleet."""
    return tmp_path / "fleet"


@pytest.fixture
def default_status() -> FleetStatus:
    """Stable, valid status instance for slice item #41."""
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
    """Empty in-memory board; seed with .add_issue(...)."""
    return FakeBoard()


@pytest.fixture
def make_issue(fake_board: FakeBoard) -> Callable[..., FakeIssue]:
    """Factory fixture — build a board work item (QUEUED, untagged) and seed it."""

    def _factory(item_id: int, **overrides: object) -> FakeIssue:
        base = FakeIssue(item_id=item_id, title=f"feat: slice {item_id}", state=Lifecycle.QUEUED)
        issue: FakeIssue = dataclasses.replace(base, **overrides)
        fake_board.add_issue(issue)
        return issue

    return _factory


@pytest.fixture
def fake_launcher() -> FakeLauncher:
    """Spy launcher recording (item_id, branch, attempt) per launch."""
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
def make_config(fleet_root: Path, tmp_path: Path) -> Callable[..., FlotillaConfig]:
    """Factory fixture — a FlotillaConfig bound to the isolated fleet root.

    Built explicitly (not via ``load_config``) so tests are hermetic — never
    perturbed by ``FLEET_*`` env vars present in the dev container.
    """

    def _factory(**overrides: object) -> FlotillaConfig:
        base = FlotillaConfig(
            provider=DEFAULT_PROVIDER,
            base_branch=DEFAULT_BASE_BRANCH,
            tag_prefix=DEFAULT_TAG_PREFIX,
            parent_scope_ids=(),
            states=ADO_BASIC_STATES,
            branch_template=DEFAULT_BRANCH_TEMPLATE,
            worktree_dir=DEFAULT_WORKTREE_DIR,
            runner_skill=DEFAULT_RUNNER_SKILL,
            tdd_skill=DEFAULT_TDD_SKILL,
            qa_skill=DEFAULT_QA_SKILL,
            cleanup_skill=DEFAULT_CLEANUP_SKILL,
            fleet_root=fleet_root,
            fleet_home=tmp_path,
            cap=2,
            max_attempts=3,
            model=FLEET_MODEL,
            effort=FLEET_EFFORT,
            heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
            staleness_threshold_seconds=STALENESS_THRESHOLD_SECONDS,
        )
        return dataclasses.replace(base, **overrides)

    return _factory
