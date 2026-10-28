"""An increment withdrawn in flight is relinquished, once (ADR-0004 decision 1).

The real ``run_tick`` drives a board on which someone else withdraws an
increment the fleet holds in flight, *between* ticks: on GitHub a human closes
the issue (each withdrawing reason, and the reasonless close); on a fake with a
withdrawn state mapped, the item moves to it. The next tick relinquishes the
claim and writes no lifecycle state; the tick after it leaves the item alone.
Closed as completed is delivery, not withdrawal: the item awaits its PR with
its sandbox stopped, and finalizes once the PR merges. N10's race (a claim
landing between ``withdraw_increment``'s re-read and its write) leaves the same
facts, so the same rule covers it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re

import pytest

from squadra.board import BoardAccess
from squadra.config import ClaimScope, SquadraConfig
from squadra.domain import Lifecycle, SandboxRunning, Tags
from squadra.increments import IncrementBoard
from squadra.remote_auth import RemoteAuthProbe
from squadra.status import FleetStatus, load_or_none, write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.board_fakes import ADO_STATES_WITH_WITHDRAWN, AdoShapedFakeBoard
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.github_stub import REPOSITORY, StubbedGitHubBoard, StubIssue, StubPull
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root + make_config + make_status come from the repo-root tests/conftest.py.

TAGS: Tags = Tags()
ITEM: int = 7
TITLE: str = "feat: ship it"
BRANCH: str = "feat/increment-7-ship-it-a2"
PROJECT: str = f"squadra-test-fleet-increment-{ITEM}"
ATTEMPT: int = 2
OTHER_LABEL: str = "area:billing"  # not the fleet's: the relinquish keeps it


@dataclass
class Shape:
    """A board shape and the outside actor's withdrawal of an in-flight item on it."""

    board: BoardAccess
    withdraw: Callable[[], None]
    labels: Callable[[], tuple[str, ...]]
    comments: Callable[[], list[str]]
    state_writes: Callable[[], list[int]]


def _gh(reason: str | None, labels: tuple[str, ...] = (TAGS.needs_decision, OTHER_LABEL)) -> Shape:
    board = StubbedGitHubBoard(tags=TAGS)
    board.github.add(StubIssue(ITEM, TITLE, status="In Progress", labels=[TAGS.claimed, *labels]))
    issue: StubIssue = board.github.issues[ITEM]

    def withdraw() -> None:
        issue.is_open = False
        issue.state_reason = reason

    return Shape(
        board=board,
        withdraw=withdraw,
        labels=lambda: tuple(issue.labels),
        comments=lambda: list(issue.comments),
        state_writes=lambda: [item for item, _ in board.state_writes],
    )


def _ado() -> Shape:
    board = AdoShapedFakeBoard(states=ADO_STATES_WITH_WITHDRAWN, tags=TAGS)
    board.add(ITEM, TITLE, Lifecycle.ACTIVE, tags=(TAGS.claimed, TAGS.needs_decision, OTHER_LABEL))

    def withdraw() -> None:
        board.items[ITEM].state = "Removed"

    return Shape(
        board=board,
        withdraw=withdraw,
        labels=lambda: tuple(item.tags for item in board.items_in_state(Lifecycle.WITHDRAWN))[0],
        comments=lambda: list(board.comments.get(ITEM, [])),
        state_writes=lambda: [item for item, _ in board.state_writes],
    )


# Each way someone else withdraws an in-flight item: GitHub's withdrawing close
# reasons (closed as completed is DONE, below) and a fake's mapped withdrawn state.
WITHDRAWALS: dict[str, Callable[[], Shape]] = {
    "gh-not-planned": lambda: _gh("NOT_PLANNED"),
    "gh-duplicate": lambda: _gh("DUPLICATE"),
    "gh-no-reason": lambda: _gh(None),
    "ado-removed": _ado,
}


@dataclass
class Fleet:
    """The tick's non-board seams and config, seeded with one in-flight attempt."""

    sandbox: FakeSandbox
    worktree: FakeWorktree
    cleanup: FakeCleanup
    config: SquadraConfig

    def seams(self, board: BoardAccess) -> TickSeams:
        """The tick's seams over ``board``, with both auth probes passing."""
        return TickSeams(
            board=board,
            sandbox=self.sandbox,
            cleanup=self.cleanup,
            worktree=self.worktree,
            remote_auth=lambda: RemoteAuthProbe(ok=True),
            auth_ok=lambda: True,
        )

    def tick(self, board: BoardAccess) -> int:
        """Run one real tick against ``board``."""
        return run_tick(self.seams(board), self.config)


@pytest.fixture
def fleet(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_status: Callable[..., FleetStatus],
) -> Fleet:
    """Attempt 2 of :data:`ITEM` in flight: its status, worktree and running sandbox."""
    config: SquadraConfig = make_config(fleet_root=fleet_root)
    write(
        make_status(
            issue_id=ITEM,
            runner_id=f"runner-{ITEM}-a{ATTEMPT}",
            branch=BRANCH,
            phase="tdd",
            attempt=ATTEMPT,
            last_heartbeat=datetime.now(UTC).isoformat(timespec="seconds"),
        ),
        fleet_root,
    )
    _worktree(config).mkdir(parents=True)
    sandbox = FakeSandbox()
    sandbox.seed(PROJECT, SandboxRunning())
    return Fleet(sandbox=sandbox, worktree=FakeWorktree(), cleanup=FakeCleanup(), config=config)


def _worktree(config: SquadraConfig) -> Path:
    return config.fleet_home / config.worktree_dir / BRANCH.replace("/", "+")


def _states(out: str) -> str:
    """The last tick's ``states={…}`` mapping, as logged."""
    found: list[str] = re.findall(r"states=(\{.*\})", out)
    return found[-1]


@pytest.mark.parametrize("withdrawal", WITHDRAWALS)
def test_closed_between_ticks_is_relinquished_once_and_never_written(
    withdrawal: str, fleet: Fleet, capsys: pytest.CaptureFixture[str]
) -> None:
    shape: Shape = WITHDRAWALS[withdrawal]()
    shape.withdraw()

    # Tick 1: the claim is relinquished; the item's lifecycle is not written.
    assert fleet.tick(shape.board) == 0
    out: str = capsys.readouterr().out
    assert shape.board.item_state(ITEM) is Lifecycle.WITHDRAWN
    assert ITEM not in shape.state_writes()
    assert [spec.project for spec in fleet.sandbox.teardowns] == [PROJECT]
    archive_root: str = str(fleet.config.fleet_root / str(ITEM) / "archive")
    assert fleet.worktree.archived == [(str(_worktree(fleet.config)), archive_root, ATTEMPT)]
    assert set(shape.labels()) == {OTHER_LABEL}
    assert len(shape.comments()) == 1
    assert "relinquished" in shape.comments()[0]
    assert BRANCH in shape.comments()[0]
    status: FleetStatus | None = load_or_none(ITEM, fleet.config.fleet_root)
    assert status is not None  # kept: its attempt count holds the retry cap on a reopen
    assert (status.attempt, status.phase, status.parked_state) == (ATTEMPT, "parked", "withdrawn")
    assert _states(out) == f"{{{ITEM}: 'withdrawn-in-flight'}}"
    assert f"relinquish: #{ITEM} withdrawn in flight (attempt {ATTEMPT})" in out
    assert fleet.cleanup.deleted_branches == []  # the branch is finalize's, gated on a merge

    # Tick 2: no write of any kind, and the item is no longer gathered.
    labels: tuple[str, ...] = shape.labels()
    assert fleet.tick(shape.board) == 0
    assert _states(capsys.readouterr().out) == "{}"
    assert shape.labels() == labels
    assert len(shape.comments()) == 1
    assert ITEM not in shape.state_writes()
    assert len(fleet.sandbox.teardowns) == 1
    assert len(fleet.worktree.archived) == 1


def test_closed_as_completed_between_ticks_awaits_its_pr_with_the_sandbox_stopped(
    fleet: Fleet, capsys: pytest.CaptureFixture[str]
) -> None:
    shape: Shape = _gh("COMPLETED", labels=())
    shape.withdraw()  # closed as completed: delivered, so DONE, not withdrawn

    assert fleet.tick(shape.board) == 0
    assert _states(capsys.readouterr().out) == f"{{{ITEM}: 'awaiting-pr'}}"
    assert [spec.project for spec in fleet.sandbox.teardowns] == [PROJECT]
    assert TAGS.claimed in shape.labels()  # finalize needs the tag, worktree and status
    assert fleet.worktree.archived == []
    status: FleetStatus | None = load_or_none(ITEM, fleet.config.fleet_root)
    assert status is not None and status.phase == "tdd"
    assert shape.comments() == []
    assert shape.state_writes() == []

    # The branch's PR merges: the next tick finalizes.
    assert isinstance(shape.board, StubbedGitHubBoard)
    owner: str = REPOSITORY.split("/")[0]
    shape.board.github.pulls.append(
        StubPull(f"{owner}:{BRANCH}", "main", "https://example.invalid/pr/9", merged=True)
    )
    assert fleet.tick(shape.board) == 0
    assert _states(capsys.readouterr().out) == f"{{{ITEM}: 'finalizing'}}"
    assert fleet.cleanup.deleted_branches == [BRANCH]
    assert TAGS.claimed not in shape.labels()
    assert len(shape.comments()) == 1 and "finalized" in shape.comments()[0]


class ClaimsAfterTheReRead(AdoShapedFakeBoard):
    """N10: a tick claims the item after ``withdraw_increment`` re-reads it as QUEUED."""

    def __init__(self, claim: Callable[["ClaimsAfterTheReRead"], None]) -> None:
        super().__init__(states=ADO_STATES_WITH_WITHDRAWN, tags=TAGS)
        self._claim: Callable[[ClaimsAfterTheReRead], None] | None = claim

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Let the armed claim land first when the withdrawal writes :data:`ITEM`."""
        if item_id == ITEM and state is Lifecycle.WITHDRAWN and self._claim is not None:
            claim, self._claim = self._claim, None
            claim(self)  # lands after the re-read, before the write
        super().set_state(item_id, state)


def test_a_claim_racing_a_withdrawal_is_relinquished_next_tick(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config: SquadraConfig = make_config(fleet_root=fleet_root, cap=1)
    sandbox = FakeSandbox()
    worktree = FakeWorktree()

    def tick(board: BoardAccess) -> None:
        seams = TickSeams(
            board=board,
            sandbox=sandbox,
            cleanup=FakeCleanup(),
            worktree=worktree,
            remote_auth=lambda: RemoteAuthProbe(ok=True),
            auth_ok=lambda: True,
        )
        assert run_tick(seams, config) == 0

    board = ClaimsAfterTheReRead(tick)
    board.add(ITEM, TITLE, Lifecycle.QUEUED)
    board.seed_origin(ITEM, "design#7")

    IncrementBoard(board, ClaimScope.WHOLE_BOARD, ()).withdraw_increment("design#7")

    # The race: the claim launched a sandbox, then the withdrawal landed on it.
    assert [spec.project for spec in sandbox.launches] == [PROJECT]
    assert board.item_state(ITEM) is Lifecycle.WITHDRAWN
    assert TAGS.claimed in board.items_in_state(Lifecycle.WITHDRAWN)[0].tags
    writes: int = len(board.state_writes)
    capsys.readouterr()

    tick(board)
    assert _states(capsys.readouterr().out) == f"{{{ITEM}: 'withdrawn-in-flight'}}"
    assert len(board.state_writes) == writes  # no lifecycle write
    assert [spec.project for spec in sandbox.teardowns] == [PROJECT]
    assert board.items_in_state(Lifecycle.WITHDRAWN)[0].tags == ()
    assert len(board.comments[ITEM]) == 2  # the claim's, then one relinquish
    assert "relinquished" in board.comments[ITEM][1]

    tick(board)
    assert _states(capsys.readouterr().out) == "{}"
    assert len(board.comments[ITEM]) == 2
