"""Provider-blindness proof: drive the REAL engine-driven tick against a fake
whose native dialect is nothing like ADO's.

The conformance tests prove the seam *contract* holds across shapes; this module
proves the thing that contract exists for — that the supervisor's gather→decide→
execute tick contains no hardcoded native state string or markup. It runs the
shipped ``run_tick`` against ``GitHubShapedFakeBoard`` (arbitrary statuses like
"Backlog"/"In Progress"/"Closed-merged", label tags, Markdown comments). Had core
named "Doing" or emitted HTML, these would fail; they pass because core speaks only
``Lifecycle`` + the configured tag vocabulary + structured ``CommentEvent``s.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from squadra.config import SquadraConfig
from squadra.domain import Lifecycle, SandboxExited, Tags
from squadra.status import write
from squadra.supervisor import TickSeams, run_tick
from tests.helpers.board_fakes import GITHUB_STATES, GitHubShapedFakeBoard
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root + make_config + make_status come from the repo-root tests/conftest.py.


def _seams(board: GitHubShapedFakeBoard, sandbox: FakeSandbox | None = None) -> TickSeams:
    # Stub BOTH claim-path preflight probes so the tick never spawns a real
    # `git ls-remote` (pat_ok) or `claude` (auth_ok) — the claim must hinge on
    # the board, not on ambient host auth.
    return TickSeams(
        ado=board,
        sandbox=sandbox if sandbox is not None else FakeSandbox(),
        cleanup=FakeCleanup(),
        worktree=FakeWorktree(),
        pat_ok=lambda: True,
        auth_ok=lambda: True,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def test_tick_claims_a_github_native_item_with_no_state_or_markup_leak(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())  # native "Backlog"/"In Progress"/...
    board.add(7, "feat: ship it", Lifecycle.QUEUED)
    config: SquadraConfig = make_config(fleet_root=fleet_root)

    assert run_tick(_seams(board), config) == 0

    assert board.item_state(7) == Lifecycle.ACTIVE
    # The supervisor wrote the GitHub-native ACTIVE column without naming it.
    assert board.items[7].status == GITHUB_STATES[Lifecycle.ACTIVE][0] == "In Progress"
    assert "fleet:claimed" in board.items[7].labels
    # The comment is the adapter's Markdown, never core HTML.
    assert board.comments[7][0].startswith("**fleet")
    assert "<p>" not in board.comments[7][0]


def test_tick_finalizes_a_github_native_done_item(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_status: Callable[..., object],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())
    # Seeded under the SECONDARY native done name to exercise many-native→one.
    board.add(
        7, "feat: ship it", Lifecycle.DONE, native_status="Closed-merged", tags=("fleet:claimed",)
    )
    board.seed_pr("feat/slice-7-ship-it", "https://example.invalid/pr/7")
    write(
        make_status(issue_id=7, runner_id="r-7", branch="feat/slice-7-ship-it"),  # type: ignore[arg-type]
        fleet_root,
    )
    config: SquadraConfig = make_config(fleet_root=fleet_root)

    assert run_tick(_seams(board), config) == 0

    assert "fleet:claimed" not in board.items[7].labels  # fleet labels dropped
    assert board.comments[7][0].startswith("**fleet")  # Markdown render


def test_tick_escalates_a_github_native_item_at_the_cap(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_status: Callable[..., object],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())
    board.add(7, "feat: ship it", Lifecycle.ACTIVE, tags=("fleet:claimed",))
    # A crashed container at the attempt cap -> escalate immediately.
    write(
        make_status(  # type: ignore[arg-type]
            issue_id=7,
            runner_id="r-7",
            branch="feat/slice-7-ship-it",
            attempt=1,
            last_heartbeat=_now(),
        ),
        fleet_root,
    )
    sandbox = FakeSandbox()
    sandbox.seed("squadra-slice-7", SandboxExited(exit_code=1))
    config: SquadraConfig = make_config(fleet_root=fleet_root, max_attempts=1)

    assert run_tick(_seams(board, sandbox), config) == 0

    assert "fleet:failed" in board.items[7].labels
    assert "fleet:claimed" not in board.items[7].labels
    assert board.comments[7][0].startswith("**fleet")
