"""A closed GitHub issue is never reopened, and the tick goes on (ledger N20, ruling 6).

The real ``run_tick`` drives the GitHub adapter over its stubbed transport. Each
case closes an issue *within* the tick, after the gather read it open and
before the supervisor writes its state: the requeue of a reaped run, the
rollback of a failed launch, and the claim itself. GitHub's ``set_state``
refuses (``StateWriteRefusedError``); the supervisor reports the refusal for
that item and goes on, so a second item in the same tick is still claimed.
Every close reason is covered, the reasonless close included.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from squadra.config import SquadraConfig
from squadra.domain import Lifecycle, SandboxExited
from squadra.status import FleetStatus, write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.github_stub import StubbedGitHubBoard, StubIssue
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root + make_config + make_status come from the repo-root tests/conftest.py.

# GitHub's spelling of every close reason, and the reasonless close.
CLOSE_REASONS: tuple[str | None, ...] = ("COMPLETED", "NOT_PLANNED", "DUPLICATE", None)
CLOSING: int = 7
BYSTANDER: int = 8


class ClosesMidTick(StubbedGitHubBoard):
    """The adapter over a stub where a human closes one issue as the tick drops its claim tag."""

    def __init__(self, reason: str | None) -> None:
        """Close issue :data:`CLOSING` with ``reason`` on its next tag removal."""
        super().__init__()
        self.reason: str | None = reason

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Remove the tag, then close the issue: the window before the requeue's write."""
        super().remove_tag(item_id, tag)
        if item_id == CLOSING:
            close(self, CLOSING, self.reason)


def close(board: StubbedGitHubBoard, item_id: int, reason: str | None) -> None:
    """Close one stub issue with ``reason``, as a human does on github.com."""
    issue: StubIssue = board.github.issues[item_id]
    issue.is_open = False
    issue.state_reason = reason


def _seams(board: StubbedGitHubBoard, sandbox: FakeSandbox, **overrides: object) -> TickSeams:
    return TickSeams(
        ado=board,
        sandbox=sandbox,
        cleanup=FakeCleanup(),
        worktree=FakeWorktree(),
        pat_ok=lambda: True,
        auth_ok=lambda: True,
        **overrides,  # type: ignore[arg-type]
    )


def _seed_bystander(board: StubbedGitHubBoard) -> None:
    board.github.add(StubIssue(BYSTANDER, "feat: the other one", status="Todo"))


def _claimed(board: StubbedGitHubBoard, item_id: int) -> bool:
    return board.item_state(item_id) is Lifecycle.ACTIVE and "fleet:claimed" in (
        board.github.issues[item_id].labels
    )


@pytest.mark.parametrize("reason", CLOSE_REASONS)
def test_a_requeue_that_finds_the_issue_closed_is_reported_and_the_tick_goes_on(
    reason: str | None,
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_status: Callable[..., FleetStatus],
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = ClosesMidTick(reason)
    board.github.add(
        StubIssue(CLOSING, "feat: ship it", status="In Progress", labels=["fleet:claimed"])
    )
    _seed_bystander(board)
    branch: str = f"feat/increment-{CLOSING}-ship-it"
    write(
        make_status(
            issue_id=CLOSING,
            runner_id=f"runner-{CLOSING}-a1",
            branch=branch,
            phase="tdd",
            last_heartbeat=datetime.now(UTC).isoformat(timespec="seconds"),
        ),
        fleet_root,
    )
    sandbox = FakeSandbox()
    sandbox.seed(f"squadra-increment-{CLOSING}", SandboxExited(exit_code=1))  # a crash: retry

    assert run_tick(_seams(board, sandbox), make_config(fleet_root=fleet_root, cap=2)) == 0

    issue: StubIssue = board.github.issues[CLOSING]
    assert not issue.is_open and issue.state_reason == reason  # never reopened
    assert (CLOSING, Lifecycle.QUEUED) not in board.state_writes
    assert CLOSING not in board.comments  # no Reaped: it was not requeued
    assert any(spec.project == f"squadra-increment-{CLOSING}" for spec in sandbox.teardowns)
    assert f"requeue refused for #{CLOSING}" in capsys.readouterr().out
    assert _claimed(board, BYSTANDER)  # the tick went on


@pytest.mark.parametrize("reason", CLOSE_REASONS)
def test_a_rollback_that_finds_the_issue_closed_is_reported_and_the_tick_goes_on(
    reason: str | None,
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = ClosesMidTick(reason)
    board.github.add(StubIssue(CLOSING, "feat: ship it", status="Todo"))
    _seed_bystander(board)
    sandbox = FakeSandbox()
    sandbox.fail_launch.add(f"squadra-increment-{CLOSING}")

    assert run_tick(_seams(board, sandbox), make_config(fleet_root=fleet_root, cap=1)) == 0

    issue: StubIssue = board.github.issues[CLOSING]
    assert not issue.is_open and issue.state_reason == reason
    assert board.state_writes.count((CLOSING, Lifecycle.QUEUED)) == 0
    assert "fleet:claimed" not in issue.labels
    assert len(board.comments[CLOSING]) == 1  # the Claimed comment only, no RolledBack
    assert f"rollback refused for #{CLOSING}" in capsys.readouterr().out
    assert _claimed(board, BYSTANDER)  # the failed launch spent no budget


@pytest.mark.parametrize("reason", CLOSE_REASONS)
def test_a_claim_that_finds_the_issue_closed_is_reported_and_the_tick_goes_on(
    reason: str | None,
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = StubbedGitHubBoard()
    board.github.add(StubIssue(CLOSING, "feat: ship it", status="Todo"))
    _seed_bystander(board)
    sandbox = FakeSandbox()

    def closes_while_injecting(worktree: Path, context: object) -> Path:
        # The context write is the claim's last step before its state write.
        if f"increment-{CLOSING}-" in str(worktree):
            close(board, CLOSING, reason)
        return worktree

    seams: TickSeams = _seams(board, sandbox, write_context=closes_while_injecting)
    assert run_tick(seams, make_config(fleet_root=fleet_root, cap=1)) == 0

    issue: StubIssue = board.github.issues[CLOSING]
    assert not issue.is_open and issue.state_reason == reason
    assert "fleet:claimed" not in issue.labels
    assert CLOSING not in board.comments
    assert not any(spec.project == f"squadra-increment-{CLOSING}" for spec in sandbox.launches)
    assert f"claim refused for #{CLOSING}" in capsys.readouterr().out
    assert _claimed(board, BYSTANDER)
